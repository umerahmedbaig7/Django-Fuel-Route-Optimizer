from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from planner import routing
from planner.exceptions import (
    LocationNotFound,
    LocationOutsideCoverage,
    RouteNotFound,
    RoutingAuthFailed,
    RoutingNotConfigured,
    RoutingRateLimited,
    RoutingTimeout,
    RoutingUnavailable,
)

from .fixtures import geojson_for, simulated_route


class FakeResponse:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code = status
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


GEOCODE_OK = {
    "features": [
        {
            "geometry": {"coordinates": [-87.63, 41.88]},
            "properties": {"label": "Chicago, IL, USA", "country_a": "USA"},
        }
    ]
}


@override_settings(ORS_API_KEY="test-key", ORS_BASE_URL="https://ors.test")
class RoutingClientTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def patch(self, **kwargs):
        return mock.patch("planner.routing.requests.request", **kwargs)

    # ---- geocoding ----
    def test_geocode_parses_result_and_sends_key_and_us_filter(self):
        with self.patch(return_value=FakeResponse(200, GEOCODE_OK)) as request:
            place = routing.geocode("Chicago, IL")
        self.assertEqual((place.lat, place.lon, place.label), (41.88, -87.63, "Chicago, IL, USA"))
        args, kwargs = request.call_args
        self.assertEqual(args, ("GET", "https://ors.test/geocode/search"))
        self.assertEqual(kwargs["headers"], {"Authorization": "test-key"})
        self.assertEqual(kwargs["params"]["boundary.country"], "USA")
        self.assertIn("timeout", kwargs)

    def test_geocode_is_cached(self):
        with self.patch(return_value=FakeResponse(200, GEOCODE_OK)) as request:
            routing.geocode("Chicago, IL")
            routing.geocode("  chicago,   il ")
        self.assertEqual(request.call_count, 1)

    def test_geocode_no_match_is_location_not_found(self):
        with self.patch(return_value=FakeResponse(200, {"features": []})):
            with self.assertRaises(LocationNotFound):
                routing.geocode("asdfghjkl")

    def test_geocode_result_in_another_country_is_rejected(self):
        payload = {"features": [{"geometry": {"coordinates": [-0.12, 51.5]}, "properties": {"country_a": "GBR"}}]}
        with self.patch(return_value=FakeResponse(200, payload)):
            with self.assertRaises(LocationOutsideCoverage):
                routing.geocode("London")

    def test_geocode_result_outside_contiguous_us_is_rejected(self):
        payload = {"features": [{"geometry": {"coordinates": [-155.5, 19.6]}, "properties": {"country_a": "USA"}}]}
        with self.patch(return_value=FakeResponse(200, payload)):
            with self.assertRaises(LocationOutsideCoverage):
                routing.geocode("Hilo, HI")

    # ---- lat,lon input ----
    def test_lat_lon_text_needs_no_external_call(self):
        with self.patch() as request:
            place = routing.resolve_place("41.88, -87.63")
        self.assertEqual((place.lat, place.lon), (41.88, -87.63))
        request.assert_not_called()

    def test_lat_lon_outside_the_us_is_rejected(self):
        with self.assertRaises(LocationOutsideCoverage):
            routing.resolve_place("51.5,-0.12")

    # ---- directions ----
    def test_route_is_parsed_and_requested_with_lon_lat_order(self):
        route = simulated_route("chicago", "indianapolis")
        start, finish = routing.Place("a", "a", 41.85, -87.67), routing.Place("b", "b", 39.77, -86.16)
        with self.patch(return_value=FakeResponse(200, geojson_for(route))) as request:
            result = routing.get_route(start, finish)
        self.assertAlmostEqual(result.distance_miles, route.distance_miles)
        self.assertAlmostEqual(result.duration_hours, route.duration_hours)
        self.assertEqual(len(result.coordinates), len(route.coordinates))
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "https://ors.test/v2/directions/driving-car/geojson"))
        self.assertEqual(kwargs["json"]["coordinates"], [[-87.67, 41.85], [-86.16, 39.77]])
        self.assertIs(kwargs["json"]["instructions"], False)

    def test_route_is_cached(self):
        route = simulated_route("chicago", "indianapolis")
        a, b = routing.Place("a", "a", 41.85, -87.67), routing.Place("b", "b", 39.77, -86.16)
        with self.patch(return_value=FakeResponse(200, geojson_for(route))) as request:
            routing.get_route(a, b)
            routing.get_route(a, b)
        self.assertEqual(request.call_count, 1)

    def test_ors_404_becomes_route_not_found_with_the_service_message(self):
        payload = {"error": {"code": 2010, "message": "Could not find routable point within a radius"}}
        a, b = routing.Place("a", "a", 41.85, -87.67), routing.Place("b", "b", 39.77, -86.16)
        with self.patch(return_value=FakeResponse(404, payload)):
            with self.assertRaisesMessage(RouteNotFound, "routable point"):
                routing.get_route(a, b)

    def test_error_statuses_map_to_specific_errors(self):
        a, b = routing.Place("a", "a", 41.85, -87.67), routing.Place("b", "b", 39.77, -86.16)
        cases = [(401, RoutingAuthFailed), (403, RoutingAuthFailed), (429, RoutingRateLimited), (500, RoutingUnavailable), (503, RoutingUnavailable)]
        for status, error in cases:
            with self.subTest(status=status), self.patch(return_value=FakeResponse(status, {})):
                with self.assertRaises(error):
                    routing.get_route(a, b)

    def test_timeout_and_connection_errors(self):
        a, b = routing.Place("a", "a", 41.85, -87.67), routing.Place("b", "b", 39.77, -86.16)
        with self.patch(side_effect=requests.Timeout()):
            with self.assertRaises(RoutingTimeout):
                routing.get_route(a, b)
        with self.patch(side_effect=requests.ConnectionError()):
            with self.assertRaises(RoutingUnavailable):
                routing.get_route(a, b)

    def test_malformed_responses_are_routing_unavailable(self):
        a, b = routing.Place("a", "a", 41.85, -87.67), routing.Place("b", "b", 39.77, -86.16)
        for response in (FakeResponse(200, bad_json=True), FakeResponse(200, {"features": []}), FakeResponse(200, {})):
            with self.patch(return_value=response):
                with self.assertRaises(RoutingUnavailable):
                    routing.get_route(a, b)

    @override_settings(ORS_API_KEY="")
    def test_missing_api_key_fails_fast_without_a_request(self):
        with self.patch() as request:
            with self.assertRaises(RoutingNotConfigured):
                routing.geocode("Chicago, IL")
        request.assert_not_called()
