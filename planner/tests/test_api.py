from unittest import mock

import requests
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APISimpleTestCase

from planner.exceptions import RouteNotFound, RoutingTimeout
from planner.geo import haversine_miles

from .fixtures import FakeORS, city_place, simulated_route, station_on_route, temp_dir, write_csv

URL = "/api/route/"
TOP_LEVEL_KEYS = {
    "start", "finish", "route", "fuel_stops", "fuel_summary", "baseline_comparison",
    "search", "map", "assumptions", "attribution",
}
STOP_KEYS = {
    "stop_number", "station_id", "name", "address", "city", "state", "price_per_gallon", "location",
    "route_mile", "off_route_miles_one_way", "detour_miles_round_trip", "miles_from_previous_stop",
    "miles_to_next_stop", "fuel_on_arrival_gallons", "gallons_purchased", "cost_usd",
}


class ApiTestCase(APISimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def fake_ors(self, *city_keys):
        route = simulated_route(*city_keys)
        places = {city_place(k).query: city_place(k) for k in city_keys}
        return FakeORS(route, places)

    def post(self, body, fake=None):
        if fake is None:
            return self.client.post(URL, body, format="json")
        with mock.patch("planner.routing._ors_request", fake):
            return self.client.post(URL, body, format="json")

    def assertConsistent(self, data, tank=50.0, mpg=10):
        """Cross-check the numbers in a successful response against each other."""
        s, stops = data["fuel_summary"], data["fuel_stops"]
        max_range = s["max_range_miles"]
        self.assertAlmostEqual(s["fuel_consumed_gallons"], s["total_driven_miles"] / mpg, delta=0.02)
        self.assertAlmostEqual(s["total_driven_miles"], s["route_distance_miles"] + s["detour_miles"], delta=0.11)
        self.assertAlmostEqual(tank + s["gallons_purchased"] - s["gallons_left_at_destination"], s["fuel_consumed_gallons"], delta=0.05)
        self.assertAlmostEqual(s["amount_paid_at_stops_usd"], sum(x["cost_usd"] for x in stops), delta=0.02 * (len(stops) + 1))
        self.assertAlmostEqual(
            s["total_fuel_cost_usd"],
            s["initial_tank_value_usd"] + s["amount_paid_at_stops_usd"] - s["left_at_destination_value_usd"], delta=0.03,
        )
        miles = [x["miles_from_previous_stop"] for x in stops] + ([stops[-1]["miles_to_next_stop"]] if stops else [s["total_driven_miles"]])
        self.assertTrue(all(m <= max_range + 0.1 for m in miles), miles)
        self.assertAlmostEqual(sum(miles), s["total_driven_miles"], delta=0.1 * (len(miles) + 1))
        self.assertEqual([x["stop_number"] for x in stops], list(range(1, len(stops) + 1)))
        self.assertEqual([x["route_mile"] for x in stops], sorted(x["route_mile"] for x in stops))


class ShortRouteTests(ApiTestCase):
    def test_valid_route_under_500_miles_has_no_stops(self):
        r = self.post({"start": "Chicago, IL", "finish": "Indianapolis, IN"}, self.fake_ors("chicago", "indianapolis"))
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(set(d), TOP_LEVEL_KEYS)
        self.assertLess(d["route"]["distance_miles"], 500)
        self.assertEqual(d["fuel_stops"], [])
        s = d["fuel_summary"]
        self.assertAlmostEqual(s["fuel_consumed_gallons"], d["route"]["distance_miles"] / 10, delta=0.01)
        self.assertEqual(s["amount_paid_at_stops_usd"], 0)
        self.assertGreater(s["total_fuel_cost_usd"], 0)
        self.assertGreater(s["gallons_left_at_destination"], 0)
        self.assertConsistent(d)

    def test_response_is_map_ready_geojson(self):
        d = self.post({"start": "Chicago, IL", "finish": "Indianapolis, IN"}, self.fake_ors("chicago", "indianapolis")).json()
        m = d["map"]
        self.assertEqual(m["type"], "FeatureCollection")
        kinds = [f["properties"]["kind"] for f in m["features"]]
        self.assertEqual(kinds, ["route", "start", "finish"])
        line = m["features"][0]["geometry"]
        self.assertEqual(line["type"], "LineString")
        lon, lat = line["coordinates"][0]
        self.assertTrue(-125 < lon < -66 and 24 < lat < 50)  # GeoJSON order is [lon, lat]
        self.assertIn("openrouteservice", d["attribution"])

    def test_geometry_can_be_omitted(self):
        d = self.post({"start": "Chicago, IL", "finish": "Indianapolis, IN", "include_geometry": False},
                      self.fake_ors("chicago", "indianapolis")).json()
        self.assertNotIn("route", [f["properties"]["kind"] for f in d["map"]["features"]])


class LongRouteTests(ApiTestCase):
    """New York -> Los Angeles with the real fuel CSV and a SIMULATED route (see fixtures.py)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.keys = ["new york", "columbus", "st louis", "oklahoma city", "albuquerque", "flagstaff", "los angeles"]

    def get(self):
        fake = self.fake_ors(*self.keys)
        return self.post({"start": "New York, NY", "finish": "Los Angeles, CA"}, fake), fake

    def test_multiple_stops_each_within_range(self):
        r, _ = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertGreater(d["route"]["distance_miles"], 2500)
        self.assertGreaterEqual(len(d["fuel_stops"]), 5)
        for stop in d["fuel_stops"]:
            self.assertEqual(set(stop), STOP_KEYS)
            self.assertGreater(stop["gallons_purchased"], 0)
        self.assertConsistent(d)

    def test_stops_lie_near_the_route(self):
        d = self.get()[0].json()
        route = simulated_route(*self.keys)
        for stop in d["fuel_stops"]:
            nearest = min(
                haversine_miles(stop["location"]["lat"], stop["location"]["lon"], lat, lon)
                for lon, lat in route.coordinates
            )
            self.assertLess(nearest, 12, stop["city"])  # corridor is 10 miles; widened only if infeasible

    def test_optimized_plan_is_no_more_expensive_than_the_baseline(self):
        d = self.get()[0].json()
        b = d["baseline_comparison"]
        self.assertLessEqual(d["fuel_summary"]["total_fuel_cost_usd"], b["total_fuel_cost_usd"])
        self.assertGreater(b["optimized_saves_total_cost_usd"], 0)
        self.assertGreater(b["gallons_left_at_destination"], 0)

    def test_uses_one_directions_call_and_one_geocode_per_endpoint(self):
        _, fake = self.get()
        self.assertEqual(fake.directions_calls, 1)
        self.assertEqual(fake.geocode_calls, 2)

    def test_lat_lon_input_needs_only_the_directions_call(self):
        fake = self.fake_ors(*self.keys)
        a, b = city_place("new york"), city_place("los angeles")
        r = self.post({"start": f"{a.lat},{a.lon}", "finish": f"{b.lat},{b.lon}"}, fake)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((fake.geocode_calls, fake.directions_calls), (0, 1))

    def test_repeating_a_request_makes_no_new_external_calls(self):
        fake = self.fake_ors(*self.keys)
        body = {"start": "New York, NY", "finish": "Los Angeles, CA"}
        self.post(body, fake)
        self.post(body, fake)
        self.assertEqual((fake.geocode_calls, fake.directions_calls), (2, 1))

    @override_settings(VEHICLE_RANGE_MILES=300)
    def test_smaller_range_means_more_stops_and_shorter_legs(self):
        d = self.get()[0].json()
        self.assertEqual(d["fuel_summary"]["max_range_miles"], 300)
        self.assertGreater(len(d["fuel_stops"]), 8)
        self.assertConsistent(d, tank=30.0)


class StationPriceTests(ApiTestCase):
    def test_cheap_station_is_chosen_over_expensive_one_and_over_marginally_cheaper_detour(self):
        route = simulated_route("chicago", "dallas")  # ~1,060 simulated miles
        stations = [
            station_on_route(route, 0.25, 3.90, 1),
            station_on_route(route, 0.42, 3.10, 2),
            station_on_route(route, 0.80, 3.50, 3),
            station_on_route(route, 0.45, 3.05, 4, offset_miles=9.5),  # 5 cents cheaper, but a 19-mile detour
        ]
        fake = FakeORS(route, {"Chicago, IL": city_place("chicago"), "Dallas, TX": city_place("dallas")})
        with mock.patch("planner.services.get_stations", return_value=stations):
            d = self.post({"start": "Chicago, IL", "finish": "Dallas, TX"}, fake).json()
        ids = [s["station_id"] for s in d["fuel_stops"]]
        self.assertNotIn(1, ids)  # the $3.90 stop is never needed
        self.assertIn(2, ids)
        self.assertNotIn(4, ids)  # the 5-cent saving does not pay for 19 miles of extra driving
        self.assertConsistent(d)

    def test_a_much_cheaper_station_is_worth_a_detour(self):
        route = simulated_route("chicago", "dallas")
        stations = [
            station_on_route(route, 0.42, 3.10, 2),
            station_on_route(route, 0.80, 3.50, 3),
            station_on_route(route, 0.45, 2.00, 4, offset_miles=9.5),
        ]
        fake = FakeORS(route, {"Chicago, IL": city_place("chicago"), "Dallas, TX": city_place("dallas")})
        with mock.patch("planner.services.get_stations", return_value=stations):
            d = self.post({"start": "Chicago, IL", "finish": "Dallas, TX"}, fake).json()
        self.assertIn(4, [s["station_id"] for s in d["fuel_stops"]])
        self.assertGreater(d["fuel_summary"]["detour_miles"], 5)  # route runs diagonally, so less than 2 x 9.5
        self.assertConsistent(d)

    def test_route_with_no_stations_near_a_long_stretch_is_reported_clearly(self):
        route = simulated_route("chicago", "dallas")
        stations = [station_on_route(route, 0.05, 3.0, 1)]  # nothing after the first 5%
        fake = FakeORS(route, {"Chicago, IL": city_place("chicago"), "Dallas, TX": city_place("dallas")})
        with mock.patch("planner.services.get_stations", return_value=stations):
            r = self.post({"start": "Chicago, IL", "finish": "Dallas, TX"}, fake)
        self.assertEqual(r.status_code, 422)
        err = r.json()["error"]
        self.assertEqual(err["code"], "no_feasible_fuel_plan")
        self.assertIn("-mile gap is longer than the 500-mile range", err["message"])


class FuelDataTests(ApiTestCase):
    def test_missing_csv_is_a_503_with_a_clear_message(self):
        with override_settings(FUEL_CSV_PATH="/nonexistent/fuel.csv"):
            r = self.post({"start": "Chicago, IL", "finish": "Indianapolis, IN"}, self.fake_ors("chicago", "indianapolis"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "fuel_data_unavailable")
        self.assertIn("not found", r.json()["error"]["message"])

    def test_csv_with_only_invalid_prices_is_a_503(self):
        with temp_dir() as d:
            path = write_csv(d, [(1, "A", "I-80", "Chicago", "IL", "n/a"), (2, "B", "I-80", "Dallas", "TX", "0")])
            with override_settings(FUEL_CSV_PATH=str(path)):
                r = self.post({"start": "Chicago, IL", "finish": "Indianapolis, IN"}, self.fake_ors("chicago", "indianapolis"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "fuel_data_unavailable")

    def test_invalid_rows_are_ignored_when_valid_ones_remain(self):
        rows = [(1, "BAD", "I-80", "Gary", "IN", "abc"), (2, "GOOD", "I-65", "Lebanon", "IN", "3.20")]
        with temp_dir() as d:
            path = write_csv(d, rows)
            with override_settings(FUEL_CSV_PATH=str(path)):
                r = self.post({"start": "Chicago, IL", "finish": "Indianapolis, IN"}, self.fake_ors("chicago", "indianapolis"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["search"]["stations_in_corridor"], 1)  # only the valid record exists


class ValidationTests(ApiTestCase):
    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(set(response.json()), {"error"})
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertTrue(response.json()["error"]["message"])

    def test_missing_and_blank_fields(self):
        for body in ({}, {"start": "Chicago, IL"}, {"finish": "Dallas, TX"}, {"start": "", "finish": "Dallas, TX"},
                     {"start": "   ", "finish": "Dallas, TX"}, {"start": {"a": 1}, "finish": "Dallas, TX"}):
            with self.subTest(body=body):
                r = self.post(body)
                self.assertError(r, 400, "invalid_request")
                self.assertIn("details", r.json()["error"])

    def test_same_start_and_finish(self):
        self.assertError(self.post({"start": "Dallas, TX", "finish": " dallas, tx"}), 400, "invalid_request")

    def test_overlong_value(self):
        self.assertError(self.post({"start": "x" * 201, "finish": "Dallas, TX"}), 400, "invalid_request")

    def test_unknown_location(self):
        fake = FakeORS(simulated_route("chicago", "dallas"), {"Dallas, TX": city_place("dallas")})
        self.assertError(self.post({"start": "Zzyzx Nowhere", "finish": "Dallas, TX"}, fake), 422, "location_not_found")

    def test_location_outside_the_usa(self):
        self.assertError(self.post({"start": "51.5,-0.12", "finish": "Dallas, TX"}), 422, "location_outside_coverage")

    def test_start_and_finish_resolving_to_the_same_place(self):
        route = simulated_route("dallas", "dallas")
        route = route.__class__(distance_miles=0.0, duration_hours=0.0, coordinates=route.coordinates)
        places = {"Dallas, TX": city_place("dallas"), "Dallas Texas": city_place("dallas")}
        self.assertError(self.post({"start": "Dallas, TX", "finish": "Dallas Texas"}, FakeORS(route, places)), 422, "same_location")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(URL).status_code, 405)


class ExternalFailureTests(ApiTestCase):
    BODY = {"start": "41.88,-87.63", "finish": "39.77,-86.16"}

    def failing(self, error):
        with mock.patch("planner.routing._ors_request", side_effect=error):
            return self.client.post(URL, self.BODY, format="json")

    def test_no_route_between_locations(self):
        r = self.failing(RouteNotFound("The service rejected the request: no road found"))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "route_not_found")
        self.assertIn("no road found", r.json()["error"]["message"])

    def test_routing_timeout_is_a_504(self):
        r = self.failing(RoutingTimeout())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (504, "routing_timeout"))

    @override_settings(ORS_API_KEY="k")
    def test_real_client_maps_connection_failure_to_502(self):
        with mock.patch("planner.routing.requests.request", side_effect=requests.ConnectionError()):
            r = self.client.post(URL, self.BODY, format="json")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (502, "routing_unavailable"))

    @override_settings(ORS_API_KEY="")
    def test_missing_api_key_is_a_503_that_names_the_setting(self):
        r = self.client.post(URL, self.BODY, format="json")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "routing_not_configured"))
        self.assertIn("ORS_API_KEY", r.json()["error"]["message"])


class HealthTests(ApiTestCase):
    def test_health_reports_loaded_stations(self):
        r = self.client.get("/api/health/")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["stations_loaded"], 6000)


class ThrottleTests(ApiTestCase):
    def test_too_many_requests_get_a_429_in_the_standard_error_shape(self):
        from rest_framework.throttling import AnonRateThrottle

        fake = self.fake_ors("chicago", "indianapolis")
        body = {"start": "Chicago, IL", "finish": "Indianapolis, IN", "include_geometry": False}
        with mock.patch.dict(AnonRateThrottle.THROTTLE_RATES, {"anon": "2/min"}):
            codes = [self.post(body, fake).status_code for _ in range(3)]
            last = self.post(body, fake)
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(last.json()["error"]["code"], "throttled")
