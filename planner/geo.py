"""Distances along a route and "which stations are near it" lookups."""
import math
from collections import defaultdict
from dataclasses import dataclass

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEGREE_LAT = 69.05


def haversine_miles(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def approx_miles(lat1, lon1, lat2, lon2):
    """Flat-earth distance. Accurate to well under 1% for the few-dozen-mile gaps we compare."""
    dy = (lat2 - lat1) * MILES_PER_DEGREE_LAT
    dx = (lon2 - lon1) * MILES_PER_DEGREE_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


@dataclass(frozen=True)
class RoutePoint:
    lat: float
    lon: float
    mile: float  # distance driven from the start


def sample_route(coordinates, distance_miles, step_miles=2.0):
    """Resample a (lon, lat) polyline into points roughly `step_miles` apart.

    Mile markers are scaled so the last point equals the routing service's own distance,
    which keeps every later number consistent with the distance we report.
    """
    cumulative = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:]):
        cumulative.append(cumulative[-1] + haversine_miles(lat1, lon1, lat2, lon2))
    polyline_miles = cumulative[-1]
    if polyline_miles == 0:
        lon, lat = coordinates[0]
        return [RoutePoint(lat, lon, 0.0)]
    scale = distance_miles / polyline_miles

    points = []
    segment = 0
    target = 0.0
    while target < polyline_miles:
        while cumulative[segment + 1] < target:
            segment += 1
        span = cumulative[segment + 1] - cumulative[segment]
        fraction = (target - cumulative[segment]) / span if span else 0.0
        (lon1, lat1), (lon2, lat2) = coordinates[segment], coordinates[segment + 1]
        points.append(RoutePoint(lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction, target * scale))
        target += step_miles
    lon, lat = coordinates[-1]
    points.append(RoutePoint(lat, lon, distance_miles))
    return points


class RouteIndex:
    """Grid over the sampled route points so a station only looks at points in nearby cells."""

    def __init__(self, points, cell_degrees=0.25):
        self.cell = cell_degrees
        self.grid = defaultdict(list)
        for p in points:
            self.grid[self._key(p.lat, p.lon)].append(p)
        self.min_lat = min(p.lat for p in points)
        self.max_lat = max(p.lat for p in points)
        self.min_lon = min(p.lon for p in points)
        self.max_lon = max(p.lon for p in points)

    def _key(self, lat, lon):
        return (math.floor(lat / self.cell), math.floor(lon / self.cell))

    def nearest(self, lat, lon, max_miles):
        """Closest route point within `max_miles`, as (RoutePoint, miles), or None."""
        margin_lat = max_miles / MILES_PER_DEGREE_LAT
        margin_lon = margin_lat / max(math.cos(math.radians(lat)), 0.1)
        if not (self.min_lat - margin_lat <= lat <= self.max_lat + margin_lat
                and self.min_lon - margin_lon <= lon <= self.max_lon + margin_lon):
            return None
        row, col = self._key(lat, lon)
        rows = math.ceil(margin_lat / self.cell)
        cols = math.ceil(margin_lon / self.cell)
        best, best_miles = None, max_miles
        for r in range(row - rows, row + rows + 1):
            for c in range(col - cols, col + cols + 1):
                for p in self.grid.get((r, c), ()):
                    miles = approx_miles(lat, lon, p.lat, p.lon)
                    if miles <= best_miles:
                        best, best_miles = p, miles
        return (best, best_miles) if best else None
