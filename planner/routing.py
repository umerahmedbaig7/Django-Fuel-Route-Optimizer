"""OpenRouteService client.

Per trip this makes at most three external calls: one geocode per start/finish that is
not already "lat,lon", and exactly one directions call. Results are cached.
"""
import hashlib
import logging
import re
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .exceptions import (
    LocationNotFound,
    LocationOutsideCoverage,
    RouteNotFound,
    RoutingAuthFailed,
    RoutingNotConfigured,
    RoutingRateLimited,
    RoutingTimeout,
    RoutingUnavailable,
)

logger = logging.getLogger(__name__)

ATTRIBUTION = "© openrouteservice.org by HeiGIT | Map data © OpenStreetMap contributors"
METERS_PER_MILE = 1609.344
# Contiguous USA. The fuel dataset has no Alaska or Hawaii stations.
US_LAT_RANGE = (24.3, 49.6)
US_LON_RANGE = (-125.0, -66.5)
LAT_LON_PATTERN = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Place:
    query: str
    label: str
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteResult:
    distance_miles: float
    duration_hours: float
    coordinates: list  # [(lon, lat), ...]


def _check_in_usa(lat, lon, query):
    if not (US_LAT_RANGE[0] <= lat <= US_LAT_RANGE[1] and US_LON_RANGE[0] <= lon <= US_LON_RANGE[1]):
        raise LocationOutsideCoverage(f"'{query}' is outside the contiguous USA, which is the only area covered.")


def _ors_request(method, path, client_error, **kwargs):
    """One HTTP call to ORS. Translates every failure into a PlannerError.

    `client_error` is the exception raised for 4xx answers that are about the user's input.
    """
    if not settings.ORS_API_KEY:
        raise RoutingNotConfigured()
    url = f"{settings.ORS_BASE_URL}{path}"
    started = time.monotonic()
    try:
        response = requests.request(
            method, url, headers={"Authorization": settings.ORS_API_KEY}, timeout=settings.ORS_TIMEOUT, **kwargs
        )
    except requests.Timeout:
        logger.warning("ORS timeout on %s", path)
        raise RoutingTimeout()
    except requests.RequestException as exc:
        logger.warning("ORS connection error on %s: %s", path, exc.__class__.__name__)
        raise RoutingUnavailable("Could not reach the routing service.")
    logger.info("ORS %s -> HTTP %s in %.2fs", path, response.status_code, time.monotonic() - started)

    if response.status_code in (401, 403):
        raise RoutingAuthFailed()
    if response.status_code == 429:
        raise RoutingRateLimited()
    if response.status_code >= 500:
        raise RoutingUnavailable(f"The routing service returned HTTP {response.status_code}.")
    if response.status_code >= 400:
        raise client_error(_ors_message(response, client_error))
    try:
        return response.json()
    except ValueError:
        raise RoutingUnavailable("The routing service returned invalid JSON.")


def _ors_message(response, client_error):
    try:
        error = response.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except (ValueError, AttributeError):
        message = None
    if not message:
        return client_error.default_detail
    return f"The service rejected the request: {message}"


def geocode(text):
    """Resolve free text such as "Austin, TX" to a Place inside the USA."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    cache_key = "geocode:" + hashlib.sha256(normalized.encode()).hexdigest()
    place = cache.get(cache_key)
    if place:
        return place

    data = _ors_request(
        "GET", "/geocode/search", LocationNotFound, params={"text": text, "size": 1, "boundary.country": "USA"}
    )
    features = data.get("features") if isinstance(data, dict) else None
    if not features:
        raise LocationNotFound(f"Could not find '{text}' in the USA. Try a city and state, e.g. 'Austin, TX'.")
    try:
        lon, lat = features[0]["geometry"]["coordinates"][:2]
        properties = features[0].get("properties", {})
    except (KeyError, TypeError, ValueError):
        raise RoutingUnavailable("The geocoding service returned an unexpected response.")
    if properties.get("country_a") not in (None, "USA"):
        raise LocationOutsideCoverage(f"'{text}' is not in the USA.")
    _check_in_usa(lat, lon, text)

    place = Place(text, properties.get("label", text), lat, lon)
    cache.set(cache_key, place, settings.GEOCODE_CACHE_SECONDS)
    return place


def resolve_place(text):
    """Accept "lat,lon" directly (no external call) or geocode free text."""
    match = LAT_LON_PATTERN.match(text)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        _check_in_usa(lat, lon, text)
        return Place(text, f"{lat:.5f}, {lon:.5f}", lat, lon)
    return geocode(text)


def get_route(start, finish):
    """Driving route as one directions call. Cached by rounded coordinates."""
    cache_key = f"route:{start.lat:.4f},{start.lon:.4f}:{finish.lat:.4f},{finish.lon:.4f}"
    route = cache.get(cache_key)
    if route:
        return route

    body = {
        "coordinates": [[start.lon, start.lat], [finish.lon, finish.lat]],
        "radiuses": [settings.ORS_SNAP_RADIUS_METERS] * 2,
        "instructions": False,
        "geometry_simplify": settings.ORS_SIMPLIFY_GEOMETRY,
    }
    data = _ors_request("POST", "/v2/directions/driving-car/geojson", RouteNotFound, json=body)
    try:
        feature = data["features"][0]
        summary = feature["properties"]["summary"]
        coordinates = [(c[0], c[1]) for c in feature["geometry"]["coordinates"]]
        route = RouteResult(
            distance_miles=summary["distance"] / METERS_PER_MILE,
            duration_hours=summary["duration"] / 3600,
            coordinates=coordinates,
        )
    except (KeyError, IndexError, TypeError):
        raise RoutingUnavailable("The routing service returned an unexpected response.")
    if len(coordinates) < 2:
        raise RouteNotFound()

    cache.set(cache_key, route, settings.ROUTE_CACHE_SECONDS)
    return route
