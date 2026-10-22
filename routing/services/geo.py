"""Local geocoding and distance helpers.

City coordinates come from data/us_cities.csv. Requests never call a geocoder.
"""

import csv
import math
import re
from dataclasses import dataclass

from django.conf import settings

from routing.services.errors import LocationError

# Miles per degree of latitude. Good enough for short polyline segments.
# The route total is then scaled to the router's road distance.
MILES_PER_DEGREE = 69.0
EARTH_RADIUS_MILES = 3958.7613

STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

US_STATES = set(STATE_NAMES.values())

# Fuel-file spellings that do not match the cities file.
CITY_ALIASES = {
    ("brookpark", "OH"): "brook park",
    ("elizabethport", "NJ"): "elizabeth",
}

_PREFIXES = (
    ("st ", "saint "),
    ("ste ", "sainte "),
    ("mt ", "mount "),
    ("ft ", "fort "),
    ("pt ", "port "),
)


def normalize_city(name: str) -> str:
    text = name.strip().lower()
    text = text.replace(".", " ").replace("'", "").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    for old, new in _PREFIXES:
        if text.startswith(old):
            text = new + text[len(old) :]
            break
    return text


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class Place:
    query: str
    city: str
    state: str
    lat: float
    lon: float


class CityIndex:
    """One coordinate per city and state.

    Some city names appear once per county. Prefer the row whose county
    name matches the city, otherwise the row nearest the median point.
    """

    def __init__(self, places):
        self._places = places

    @classmethod
    def load(cls, path=None):
        path = path or settings.BASE_DIR / "data" / "us_cities.csv"
        grouped = {}
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                state = row["STATE_CODE"].strip().upper()
                if state not in US_STATES:
                    continue
                city = clean_text(row["CITY"])
                key = (normalize_city(city), state)
                try:
                    lat = float(row["LATITUDE"])
                    lon = float(row["LONGITUDE"])
                except (TypeError, ValueError):
                    continue
                grouped.setdefault(key, []).append(
                    (lat, lon, city, clean_text(row.get("COUNTY") or ""))
                )
        places = {key: _choose_point(points) for key, points in grouped.items()}
        return cls(places)

    def lookup(self, city: str, state: str):
        state = state.upper()
        key_city = CITY_ALIASES.get((normalize_city(city), state), normalize_city(city))
        return self._places.get((key_city, state))

    def __len__(self):
        return len(self._places)


_cities = None


def get_cities() -> CityIndex:
    global _cities
    if _cities is None:
        _cities = CityIndex.load()
    return _cities


def parse_location(text: str) -> tuple[str, str]:
    """Split 'City, ST' or 'City, State Name' into a city and USPS code."""
    raw = clean_text(text or "")
    if "," not in raw:
        raise LocationError(
            "Use a city and state, for example 'Denver, CO' or 'Denver, Colorado'."
        )
    city_part, state_part = raw.rsplit(",", 1)
    city_part = city_part.strip()
    state_part = state_part.strip().lower().replace(".", "")
    state_part = re.sub(r"\s+", " ", state_part)
    if not city_part or not state_part:
        raise LocationError(
            "Use a city and state, for example 'Denver, CO' or 'Denver, Colorado'."
        )
    if len(state_part) == 2:
        state = state_part.upper()
        if state not in US_STATES:
            raise LocationError(
                f"{state_part.upper()} is not a US state. "
                "Start and end both need to be in the US."
            )
    else:
        state = STATE_NAMES.get(state_part)
        if state is None:
            raise LocationError(
                f"Could not read the state in '{text}'. "
                "Use a name like 'Colorado' or a code like 'CO'."
            )
    return city_part, state


def geocode_location(text: str) -> Place:
    city, state = parse_location(text)
    found = get_cities().lookup(city, state)
    if found is None:
        raise LocationError(
            f"No US city match for '{clean_text(text)}'. "
            "Try the city name and state, like 'Oklahoma City, OK'."
        )
    lat, lon, display_city = found
    return Place(
        query=clean_text(text),
        city=display_city,
        state=state,
        lat=lat,
        lon=lon,
    )


def haversine(lat1, lon1, lat2, lon2) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


def segment_miles(lat1, lon1, lat2, lon2) -> float:
    """Fast length of a short segment. Fine for dense route geometry."""
    mid = math.radians((lat1 + lat2) / 2.0)
    dlat = lat2 - lat1
    dlon = (lon2 - lon1) * math.cos(mid)
    return MILES_PER_DEGREE * math.hypot(dlat, dlon)


def samples_along(coordinates, distance_miles, step_miles):
    """Points every `step_miles` along a [lon, lat] line.

    `distance_miles` is the router's road distance. Segment lengths are scaled
    so the mile markers add up to that figure.
    """
    if len(coordinates) < 2:
        raise ValueError("Route geometry needs at least two points.")
    raw = [0.0]
    for index in range(1, len(coordinates)):
        lon1, lat1 = coordinates[index - 1]
        lon2, lat2 = coordinates[index]
        raw.append(raw[-1] + segment_miles(lat1, lon1, lat2, lon2))
    raw_total = raw[-1]
    scale = (distance_miles / raw_total) if raw_total else 1.0

    if distance_miles <= 0:
        lon, lat = coordinates[0]
        return [(0.0, lat, lon)], scale

    step = max(step_miles, distance_miles / 5000.0)
    targets = []
    mile = 0.0
    while mile < distance_miles:
        targets.append(mile)
        mile += step
    if not targets or targets[-1] < distance_miles:
        targets.append(distance_miles)

    samples = []
    cursor = 0
    last = len(raw) - 1
    for target in targets:
        raw_target = target / scale if scale else target
        while cursor < last and raw[cursor + 1] < raw_target:
            cursor += 1
        if cursor >= last:
            lon, lat = coordinates[-1]
            samples.append((distance_miles, lat, lon))
            continue
        span = raw[cursor + 1] - raw[cursor]
        if span <= 0:
            fraction = 0.0
        else:
            fraction = (raw_target - raw[cursor]) / span
        fraction = min(1.0, max(0.0, fraction))
        lon1, lat1 = coordinates[cursor]
        lon2, lat2 = coordinates[cursor + 1]
        lon = lon1 + fraction * (lon2 - lon1)
        lat = lat1 + fraction * (lat2 - lat1)
        samples.append((target, lat, lon))
    return samples, scale


def _choose_point(points):
    if len(points) == 1:
        lat, lon, city, _county = points[0]
        return (lat, lon, city)
    city_key = normalize_city(points[0][2])
    for lat, lon, city, county in points:
        if normalize_city(county) == city_key:
            return (lat, lon, city)
    lats = sorted(point[0] for point in points)
    lons = sorted(point[1] for point in points)
    mid_lat = lats[len(lats) // 2]
    mid_lon = lons[len(lons) // 2]
    lat, lon, city, _county = min(
        points,
        key=lambda point: (point[0] - mid_lat) ** 2 + (point[1] - mid_lon) ** 2,
    )
    return (lat, lon, city)
