"""Load fuel stations from the supplied CSV.

The CSV has no coordinates, only City and State. Each station is placed at the mean
centroid of the ZIP codes belonging to its city (offline data from the `zipcodes`
package). That is a city-level approximation, not the exact truck-stop location.
"""
import csv
import logging
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import zipcodes
from django.conf import settings

from .exceptions import FuelDataUnavailable

logger = logging.getLogger(__name__)

US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
REQUIRED_COLUMNS = ["OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Retail Price"]
AGGREGATIONS = {"mean": statistics.fmean, "min": min, "max": max}


@dataclass(frozen=True)
class Station:
    station_id: int
    name: str
    address: str
    city: str
    state: str
    price: float  # USD per gallon
    lat: float
    lon: float


@dataclass
class LoadReport:
    rows_read: int = 0
    non_us_rows: int = 0
    invalid_price_rows: int = 0
    invalid_id_rows: int = 0
    duplicate_rows_merged: int = 0
    stations_without_location: int = 0
    stations_loaded: int = 0
    non_us_states: set = field(default_factory=set)


def _clean(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalize_city(city):
    city = city.upper().replace(".", "")
    city = re.sub(r"\bSAINT\b", "ST", city)
    city = re.sub(r"\bMOUNT\b", "MT", city)
    city = re.sub(r"\bFORT\b", "FT", city)
    return re.sub(r"[^A-Z0-9]+", " ", city).strip()


@lru_cache(maxsize=1)
def _city_index():
    """Map (state, normalized city) to the mean ZIP centroid. Primary city names win over alternates."""
    primary, alternate = defaultdict(list), defaultdict(list)
    for z in zipcodes.list_all():
        if z["state"] not in US_STATE_CODES:
            continue
        try:
            point = (float(z["lat"]), float(z["long"]))
        except (TypeError, ValueError):
            continue
        # The ZIP data contains a few "0" / "0.0000" placeholders; ignore anything outside the US.
        if not (18 <= point[0] <= 72 and -180 <= point[1] <= -65):
            continue
        primary[(z["state"], _normalize_city(z["city"]))].append(point)
        for name in z.get("acceptable_cities") or []:
            alternate[(z["state"], _normalize_city(name))].append(point)

    def mean_point(points):
        return (statistics.fmean(p[0] for p in points), statistics.fmean(p[1] for p in points))

    index = {key: mean_point(points) for key, points in alternate.items()}
    index.update({key: mean_point(points) for key, points in primary.items()})
    # "De Forest" vs "DeForest": also index each name without spaces.
    for (state, name), point in list(index.items()):
        index.setdefault((state, name.replace(" ", "")), point)
    return index


def locate_city(city, state):
    """Return (lat, lon) for a US city, or None when the city is unknown."""
    key = _normalize_city(city)
    index = _city_index()
    return index.get((state, key)) or index.get((state, key.replace(" ", "")))


def _parse_price(raw, min_price, max_price):
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or not (min_price <= price <= max_price):
        return None
    return price


def load_stations(path, aggregation="mean", min_price=1.0, max_price=10.0):
    """Return (stations, LoadReport). One Station per station ID."""
    path = Path(path)
    if not path.is_file():
        raise FuelDataUnavailable(f"Fuel price file not found: {path.name}. Set FUEL_CSV_PATH.")
    aggregate = AGGREGATIONS[aggregation]
    report = LoadReport()
    groups = {}  # station id -> {"info": (...), "prices": [...]}

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise FuelDataUnavailable(f"Fuel price file is missing columns: {', '.join(missing)}.")
        for row in reader:
            report.rows_read += 1
            state = _clean(row["State"]).upper()
            if state not in US_STATE_CODES:
                report.non_us_rows += 1
                report.non_us_states.add(state)
                continue
            price = _parse_price(row["Retail Price"], min_price, max_price)
            if price is None:
                report.invalid_price_rows += 1
                continue
            try:
                station_id = int(row["OPIS Truckstop ID"])
            except (TypeError, ValueError):
                report.invalid_id_rows += 1
                continue
            group = groups.setdefault(
                station_id,
                {"info": (_clean(row["Truckstop Name"]), _clean(row["Address"]), _clean(row["City"]), state), "prices": []},
            )
            group["prices"].append(price)

    stations = []
    for station_id, group in groups.items():
        name, address, city, state = group["info"]
        report.duplicate_rows_merged += len(group["prices"]) - 1
        point = locate_city(city, state)
        if point is None:
            report.stations_without_location += 1
            continue
        stations.append(Station(station_id, name, address, city, state, round(aggregate(group["prices"]), 4), *point))

    report.stations_loaded = len(stations)
    if not stations:
        raise FuelDataUnavailable("The fuel price file contains no usable station records.")
    logger.info("loaded %d stations from %s (%s)", len(stations), path.name, report)
    return stations, report


@lru_cache(maxsize=4)
def _load_cached(path, aggregation, min_price, max_price):
    return load_stations(path, aggregation, min_price, max_price)


def get_stations():
    """Stations from the configured CSV, parsed once per process."""
    stations, _ = _load_cached(
        settings.FUEL_CSV_PATH, settings.PRICE_AGGREGATION, settings.MIN_VALID_PRICE, settings.MAX_VALID_PRICE
    )
    return stations
