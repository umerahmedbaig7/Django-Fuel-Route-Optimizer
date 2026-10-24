"""Simulated routes for tests.

These are NOT real routing-service output. A route is a straight line between real city
coordinates (from the ZIP data), densified to a polyline, with the distance inflated by
ROAD_FACTOR to imitate road circuity.
"""
from planner.geo import haversine_miles
from planner.routing import Place, RouteResult
from planner.stations import locate_city

ROAD_FACTOR = 1.15

CITIES = {
    "chicago": ("Chicago", "IL"),
    "indianapolis": ("Indianapolis", "IN"),
    "dallas": ("Dallas", "TX"),
    "new york": ("New York", "NY"),
    "columbus": ("Columbus", "OH"),
    "st louis": ("Saint Louis", "MO"),
    "oklahoma city": ("Oklahoma City", "OK"),
    "albuquerque": ("Albuquerque", "NM"),
    "flagstaff": ("Flagstaff", "AZ"),
    "los angeles": ("Los Angeles", "CA"),
    "denver": ("Denver", "CO"),
    "salt lake city": ("Salt Lake City", "UT"),
}


def city_place(key):
    city, state = CITIES[key]
    lat, lon = locate_city(city, state)
    return Place(f"{city}, {state}", f"{city}, {state}, USA", lat, lon)


def simulated_route(*city_keys, step_miles=5.0):
    """Route through the given cities, in order."""
    points = [city_place(k) for k in city_keys]
    coordinates = []
    straight_miles = 0.0
    for a, b in zip(points, points[1:]):
        miles = haversine_miles(a.lat, a.lon, b.lat, b.lon)
        steps = max(1, round(miles / step_miles))
        for i in range(steps):
            f = i / steps
            coordinates.append((a.lon + (b.lon - a.lon) * f, a.lat + (b.lat - a.lat) * f))
        straight_miles += miles
    coordinates.append((points[-1].lon, points[-1].lat))
    distance = straight_miles * ROAD_FACTOR
    return RouteResult(distance_miles=distance, duration_hours=distance / 60, coordinates=coordinates)


# ---- helpers for scenario tests -------------------------------------------------------------

import csv  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from planner.routing import METERS_PER_MILE  # noqa: E402
from planner.stations import Station  # noqa: E402

CSV_HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


def write_csv(directory, rows, header=CSV_HEADER):
    """rows: iterable of (id, name, address, city, state, price). Returns the file path."""
    path = Path(directory) / "fuel.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(header)
        writer = csv.writer(handle)
        for sid, name, addr, city, state, price in rows:
            writer.writerow([sid, name, addr, city, state, 100, price])
    return path


def temp_dir():
    return tempfile.TemporaryDirectory()


def station_on_route(route, fraction, price, station_id, offset_miles=0.0):
    """A Station placed at `fraction` of the way along a simulated route, `offset_miles` north of it."""
    lon, lat = route.coordinates[int((len(route.coordinates) - 1) * fraction)]
    return Station(station_id, f"TEST STOP {station_id}", "I-00, EXIT 1", "Testville", "TX", price,
                   lat + offset_miles / 69.05, lon)


def geojson_for(route):
    return {
        "features": [
            {
                "properties": {
                    "summary": {"distance": route.distance_miles * METERS_PER_MILE, "duration": route.duration_hours * 3600}
                },
                "geometry": {"type": "LineString", "coordinates": [list(c) for c in route.coordinates]},
            }
        ]
    }


class FakeORS:
    """Stands in for planner.routing._ors_request and records which endpoints were called.

    Everything downstream of the HTTP call (parsing, caching, validation) runs for real.
    """

    def __init__(self, route, places=None):
        self.route = route
        self.places = {k.lower(): v for k, v in (places or {}).items()}
        self.calls = []

    def __call__(self, method, path, client_error, **kwargs):
        self.calls.append(path)
        if path == "/geocode/search":
            place = self.places.get(kwargs["params"]["text"].lower())
            if place is None:
                return {"features": []}
            return {
                "features": [
                    {
                        "geometry": {"type": "Point", "coordinates": [place.lon, place.lat]},
                        "properties": {"label": place.label, "country_a": "USA"},
                    }
                ]
            }
        return geojson_for(self.route)

    @property
    def geocode_calls(self):
        return self.calls.count("/geocode/search")

    @property
    def directions_calls(self):
        return sum(1 for c in self.calls if c.startswith("/v2/directions"))
