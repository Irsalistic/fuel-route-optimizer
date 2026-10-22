"""Load truck stops and a coarse grid so corridor searches stay cheap."""

import csv
import json
import logging
import math
import threading
from dataclasses import dataclass

from django.conf import settings

from routing.services.geo import US_STATES, clean_text, get_cities

logger = logging.getLogger(__name__)

FUEL_NAME = "fuel-prices.csv"
STATIONS_NAME = "stations.json"
# Half a degree is about 35 miles. Corridor queries touch a handful of cells.
CELL_DEGREES = 0.5

_lock = threading.Lock()
_index = None


@dataclass(frozen=True)
class Station:
    opis_id: str
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lon: float

    def as_dict(self):
        return {
            "opis_id": self.opis_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "price": self.price,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
        }


class StationIndex:
    def __init__(self, stations):
        self.stations = list(stations)
        self._cells = {}
        for station in self.stations:
            key = _cell(station.lat, station.lon)
            self._cells.setdefault(key, []).append(station)

    def near(self, lat, lon, miles):
        dlat = miles / 69.0
        cos_lat = max(0.25, abs(math.cos(math.radians(lat))))
        dlon = miles / (69.0 * cos_lat)
        lat0 = math.floor((lat - dlat) / CELL_DEGREES)
        lat1 = math.floor((lat + dlat) / CELL_DEGREES)
        lon0 = math.floor((lon - dlon) / CELL_DEGREES)
        lon1 = math.floor((lon + dlon) / CELL_DEGREES)
        found = []
        for lat_bucket in range(lat0, lat1 + 1):
            for lon_bucket in range(lon0, lon1 + 1):
                found.extend(self._cells.get((lat_bucket, lon_bucket), ()))
        return found


def data_path(name):
    return settings.BASE_DIR / "data" / name


def read_fuel_rows(path=None):
    path = path or data_path(FUEL_NAME)
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            try:
                price = round(float(raw["Retail Price"]), 3)
            except (TypeError, ValueError, KeyError):
                continue
            if price <= 0:
                continue
            rows.append(
                {
                    "opis_id": clean_text(raw["OPIS Truckstop ID"]),
                    "name": clean_text(raw["Truckstop Name"]),
                    "address": clean_text(raw["Address"]),
                    "city": clean_text(raw["City"]),
                    "state": clean_text(raw["State"]).upper(),
                    "price": price,
                }
            )
    return rows


def dedupe_stops(rows):
    """The file repeats OPIS ids with different prices. Keep the cheapest."""
    best = {}
    order = []
    for row in rows:
        opis_id = row["opis_id"]
        current = best.get(opis_id)
        if current is None:
            best[opis_id] = row
            order.append(opis_id)
        elif row["price"] < current["price"]:
            best[opis_id] = row
    return [best[opis_id] for opis_id in order]


def geocode_stops(rows, cities=None):
    cities = cities or get_cities()
    stations = []
    skipped_non_us = 0
    unmatched = []
    for row in rows:
        if row["state"] not in US_STATES:
            skipped_non_us += 1
            continue
        found = cities.lookup(row["city"], row["state"])
        if found is None:
            unmatched.append(f"{row['city']}, {row['state']}")
            continue
        lat, lon, _display = found
        stations.append(
            Station(
                opis_id=row["opis_id"],
                name=row["name"],
                address=row["address"],
                city=row["city"],
                state=row["state"],
                price=row["price"],
                lat=lat,
                lon=lon,
            )
        )
    stations.sort(key=lambda station: (station.state, station.city, station.opis_id))
    return stations, skipped_non_us, sorted(set(unmatched))


def build_stations():
    rows = dedupe_stops(read_fuel_rows())
    return geocode_stops(rows)


def rebuild_station_file():
    stations, skipped_non_us, unmatched = build_stations()
    path = data_path(STATIONS_NAME)
    payload = {
        "station_count": len(stations),
        "skipped_non_us": skipped_non_us,
        "unmatched_cities": unmatched,
        "stations": [station.as_dict() for station in stations],
    }
    path.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    clear_index_cache()
    return {
        "path": str(path),
        "stations": len(stations),
        "skipped_non_us": skipped_non_us,
        "unmatched": len(unmatched),
        "unmatched_names": unmatched,
    }


def load_stations():
    path = data_path(STATIONS_NAME)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            Station(
                opis_id=item["opis_id"],
                name=item["name"],
                address=item["address"],
                city=item["city"],
                state=item["state"],
                price=float(item["price"]),
                lat=float(item["lat"]),
                lon=float(item["lon"]),
            )
            for item in payload["stations"]
        ]
    stations, _skipped, _unmatched = build_stations()
    return stations


def get_index() -> StationIndex:
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is None:
            stations = load_stations()
            _index = StationIndex(stations)
            logger.info("loaded %s truck stops", len(stations))
    return _index


def clear_index_cache():
    global _index
    with _lock:
        _index = None


def _cell(lat, lon):
    return (math.floor(lat / CELL_DEGREES), math.floor(lon / CELL_DEGREES))
