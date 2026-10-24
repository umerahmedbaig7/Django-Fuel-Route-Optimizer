import logging
import statistics
import time

from django.conf import settings

from .exceptions import NoFeasibleFuelPlan, SameLocation
from .geo import RouteIndex, sample_route
from .optimizer import (
    cheapest_per_location,
    find_candidates,
    largest_gap,
    plan_baseline,
    plan_optimal,
)
from .routing import ATTRIBUTION, get_route, resolve_place
from .stations import get_stations

logger = logging.getLogger(__name__)


def plan_trip(start_text, finish_text, include_geometry=True):
    """Geocode -> one route call -> pick stations near the route -> optimize -> response dict."""
    started = time.monotonic()
    start = resolve_place(start_text)
    finish = resolve_place(finish_text)
    route = get_route(start, finish)
    if route.distance_miles < 0.1:
        raise SameLocation()

    stations = get_stations()
    mpg = settings.VEHICLE_MPG
    tank = settings.VEHICLE_RANGE_MILES / mpg
    route_miles = route.distance_miles

    index = RouteIndex(sample_route(route.coordinates, route_miles, settings.ROUTE_SAMPLE_MILES))
    nearby = find_candidates(index, stations, max(settings.CORRIDOR_BUFFERS_MILES))

    plan = None
    for buffer_miles in settings.CORRIDOR_BUFFERS_MILES:
        corridor = [c for c in nearby if c.offset_miles <= buffer_miles]
        candidates = cheapest_per_location(corridor)
        reference_price = (
            statistics.fmean(c.station.price for c in corridor)
            if corridor
            else statistics.median(s.price for s in stations)
        )
        plan = plan_optimal(candidates, route_miles, tank, mpg, reference_price, settings.MIN_STOP_SAVING_USD)
        if plan:
            break
    if plan is None:
        raise NoFeasibleFuelPlan(_infeasible_message(candidates, route_miles, buffer_miles))

    baseline = plan_baseline(candidates, route_miles, tank, mpg, reference_price)
    response = _build_response(
        start, finish, route, plan, baseline, buffer_miles, len(corridor), len(candidates), include_geometry
    )
    logger.info(
        "planned %.0f miles with %d stops, total $%.2f, %d candidates, %.2fs",
        route_miles, len(plan.stops), plan.total_cost, len(candidates), time.monotonic() - started,
    )
    return response


def _infeasible_message(candidates, route_miles, buffer_miles):
    gap_start, gap_end = largest_gap(candidates, route_miles)
    gap = gap_end - gap_start
    if gap > settings.VEHICLE_RANGE_MILES:
        return (
            f"No station in the fuel dataset lies within {buffer_miles} miles of the route between mile "
            f"{gap_start:.0f} and mile {gap_end:.0f}. That {gap:.0f}-mile gap is longer than the "
            f"{settings.VEHICLE_RANGE_MILES}-mile range."
        )
    return (
        f"Stations exist along the route, but the detours needed to reach them make some stretch "
        f"longer than the {settings.VEHICLE_RANGE_MILES}-mile range."
    )


def _build_response(start, finish, route, plan, baseline, buffer_miles, corridor_count, candidate_count, include_geometry):
    mpg = settings.VEHICLE_MPG
    stops = []
    for k, stop in enumerate(plan.stops):
        c, s = stop.candidate, stop.candidate.station
        next_leg = plan.stops[k + 1].miles_from_previous if k + 1 < len(plan.stops) else plan.final_leg_miles
        stops.append(
            {
                "stop_number": k + 1,
                "station_id": s.station_id,
                "name": s.name,
                "address": s.address,
                "city": s.city,
                "state": s.state,
                "price_per_gallon": round(s.price, 3),
                "location": {"lat": round(s.lat, 5), "lon": round(s.lon, 5), "precision": "city_zip_centroid"},
                "route_mile": round(c.mile, 1),
                "off_route_miles_one_way": round(c.offset_miles, 1),
                "detour_miles_round_trip": round(2 * c.offset_miles, 1),
                "miles_from_previous_stop": round(stop.miles_from_previous, 1),
                "miles_to_next_stop": round(next_leg, 1),
                "fuel_on_arrival_gallons": round(stop.arrival_gallons, 2),
                "gallons_purchased": round(stop.gallons_bought, 2),
                "cost_usd": round(stop.cost, 2),
            }
        )

    summary = {
        "mpg": mpg,
        "max_range_miles": settings.VEHICLE_RANGE_MILES,
        "tank_capacity_gallons": plan.tank_gallons,
        "route_distance_miles": round(plan.route_miles, 1),
        "detour_miles": round(plan.detour_miles, 1),
        "total_driven_miles": round(plan.driven_miles, 1),
        "fuel_consumed_gallons": round(plan.gallons_consumed, 2),
        "gallons_purchased": round(plan.gallons_purchased, 2),
        "gallons_left_at_destination": round(plan.leftover_gallons, 2),
        "amount_paid_at_stops_usd": round(plan.amount_paid, 2),
        "initial_tank_reference_price_per_gallon": round(plan.reference_price, 3),
        "initial_tank_value_usd": round(plan.initial_tank_value, 2),
        "left_at_destination_value_usd": round(plan.leftover_value, 2),
        "total_fuel_cost_usd": round(plan.total_cost, 2),
        "cost_formula": "total_fuel_cost_usd = initial_tank_value_usd + amount_paid_at_stops_usd - left_at_destination_value_usd",
    }

    comparison = None
    if baseline:
        comparison = {
            "strategy": "Drive to the farthest reachable station, fill the tank, repeat (prices ignored).",
            "stops": len(baseline.stops),
            "amount_paid_at_stops_usd": round(baseline.amount_paid, 2),
            "gallons_left_at_destination": round(baseline.leftover_gallons, 2),
            "total_fuel_cost_usd": round(baseline.total_cost, 2),
            "optimized_saves_total_cost_usd": round(baseline.total_cost - plan.total_cost, 2),
            "optimized_saves_amount_paid_usd": round(baseline.amount_paid - plan.amount_paid, 2),
        }

    response = {
        "start": _place(start),
        "finish": _place(finish),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "geometry_points": len(route.coordinates),
        },
        "fuel_stops": stops,
        "fuel_summary": summary,
        "baseline_comparison": comparison,
        "search": {
            "corridor_miles": buffer_miles,
            "stations_in_corridor": corridor_count,
            "locations_considered": candidate_count,
        },
        "map": _feature_collection(start, finish, route, stops, include_geometry),
        "assumptions": _assumptions(plan),
        "attribution": ATTRIBUTION,
    }
    return response


def _place(place):
    return {"query": place.query, "resolved_as": place.label, "lat": round(place.lat, 5), "lon": round(place.lon, 5)}


def _feature_collection(start, finish, route, stops, include_geometry):
    """GeoJSON that can be pasted straight into geojson.io or a Leaflet/Mapbox layer."""
    features = []
    if include_geometry:
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "route", "distance_miles": round(route.distance_miles, 1)},
                "geometry": {"type": "LineString", "coordinates": [list(c) for c in route.coordinates]},
            }
        )
    for kind, place in (("start", start), ("finish", finish)):
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": kind, "label": place.label},
                "geometry": {"type": "Point", "coordinates": [round(place.lon, 5), round(place.lat, 5)]},
            }
        )
    for stop in stops:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "fuel_stop",
                    "stop_number": stop["stop_number"],
                    "name": stop["name"],
                    "price_per_gallon": stop["price_per_gallon"],
                    "gallons_purchased": stop["gallons_purchased"],
                },
                "geometry": {"type": "Point", "coordinates": [stop["location"]["lon"], stop["location"]["lat"]]},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _assumptions(plan):
    capacity = f"{plan.tank_gallons:g}-gallon"
    return [
        f"The vehicle starts with a full {capacity} tank ({settings.VEHICLE_RANGE_MILES} miles at {settings.VEHICLE_MPG} mpg). "
        "That fuel was not bought at a listed station, so it is valued at the average price of stations near the route "
        "(initial_tank_reference_price_per_gallon).",
        "Each stop buys only the fuel needed to reach a cheaper stop or the destination, or fills the tank when no cheaper "
        "stop is in range. amount_paid_at_stops_usd is cash spent at the pumps; total_fuel_cost_usd is the value of the fuel actually burned.",
        "The CSV has no coordinates. Each station is placed at its city's ZIP-code centroid, so route_mile and off-route "
        "distances are approximate.",
        "Visiting a station adds a round trip of twice its straight-line distance from the route. That extra driving burns fuel and counts against the range.",
        f"A station listed on several CSV rows is priced at the {settings.PRICE_AGGREGATION} of those rows. Canadian rows and prices outside "
        f"${settings.MIN_VALID_PRICE:g}-${settings.MAX_VALID_PRICE:g} are ignored.",
        "Prices come from the supplied CSV, not live data. Routes come from OpenRouteService (driving-car profile).",
    ]
