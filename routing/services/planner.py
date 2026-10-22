"""Turn two city names into a route, fuel stops, and a cost."""

import logging
from dataclasses import dataclass

from django.conf import settings

from routing.services.errors import LocationError, Unreachable
from routing.services.geo import geocode_location, haversine, samples_along
from routing.services.optimize import plan_stops
from routing.services.osrm import fetch_driving_route
from routing.services.stations import get_index

logger = logging.getLogger(__name__)

# Try a tight corridor first so we do not recommend a stop far off the highway.
# A wider pass is only used when the narrow one cannot cover the tank range.
# Both passes are local. The router is not called again.
CORRIDORS_MILES = (20.0, 45.0)
SAMPLE_STEP_MILES = 2.0
DISPLAY_POINTS = 700
# Ignore price gaps under a nickel, and don't stop for a few gallons,
# unless that stop is what keeps the truck inside its range.
MIN_SAVINGS_PER_GALLON = 0.05
MIN_PURCHASE_GALLONS = 5.0


@dataclass
class AlongRoute:
    station: object
    mile: float
    miles_off_route: float

    @property
    def price(self):
        return self.station.price


def plan_route(start_text: str, end_text: str) -> dict:
    start = geocode_location(start_text)
    end = geocode_location(end_text)
    if start.state == end.state and start.city.lower() == end.city.lower():
        raise LocationError("Start and end resolve to the same city.")
    if haversine(start.lat, start.lon, end.lat, end.lon) < 1:
        raise LocationError("Start and end are essentially the same place.")

    driving = fetch_driving_route(start.lat, start.lon, end.lat, end.lon)
    tank = float(settings.TANK_RANGE_MILES)
    mpg = float(settings.MILES_PER_GALLON)
    samples, _scale = samples_along(
        driving.coordinates, driving.distance_miles, SAMPLE_STEP_MILES
    )
    index = get_index()

    last_error = None
    chosen = None
    radius_used = CORRIDORS_MILES[-1]
    for radius in CORRIDORS_MILES:
        candidates = _candidates(index, samples, radius, driving.distance_miles)
        try:
            purchases = plan_stops(
                candidates,
                driving.distance_miles,
                tank_miles=tank,
                mpg=mpg,
                min_savings=MIN_SAVINGS_PER_GALLON,
                min_gallons=MIN_PURCHASE_GALLONS,
            )
        except Unreachable as exc:
            last_error = exc
            logger.info("no fuel plan within %.0f miles of the route", radius)
            continue
        chosen = (candidates, purchases, radius)
        radius_used = radius
        break

    if chosen is None:
        raise Unreachable(
            f"{last_error} Searched within {CORRIDORS_MILES[-1]:.0f} miles of the route."
        )

    _candidates_used, purchases, radius_used = chosen
    stops, total_gallons, total_cost, remaining = _price_stops(
        purchases, driving.distance_miles, tank, mpg
    )
    display = samples_along(
        driving.coordinates,
        driving.distance_miles,
        max(driving.distance_miles / (DISPLAY_POINTS - 1), 0.5),
    )[0]
    line = [[round(lon, 5), round(lat, 5)] for _mile, lat, lon in display]

    logger.info(
        "planned %s -> %s, %.0f miles, radius %.0f, %d stops, osrm_calls=%d",
        start.city,
        end.city,
        driving.distance_miles,
        radius_used,
        len(stops),
        0 if driving.from_cache else 1,
    )

    return {
        "start": _place_dict(start),
        "end": _place_dict(end),
        "distance_miles": round(driving.distance_miles, 1),
        "duration_minutes": int(round(driving.duration_seconds / 60.0)),
        "assumptions": {
            "mpg": mpg,
            "tank_range_miles": tank,
            "tank_gallons": tank / mpg,
            "started_with_full_tank": True,
            "initial_fuel_included_in_cost": False,
            "station_coordinates": "city_centroid",
            "search_radius_miles": radius_used,
            "min_savings_per_gallon": MIN_SAVINGS_PER_GALLON,
            "min_purchase_gallons": MIN_PURCHASE_GALLONS,
        },
        "stops": stops,
        "fuel": {
            "total_gallons": total_gallons,
            "total_cost": total_cost,
            "gallons_burned": round(driving.distance_miles / mpg, 2),
            "fuel_remaining_gallons": remaining,
        },
        "route": {
            "type": "LineString",
            "coordinates": line,
        },
        "routing_api_calls": 0 if driving.from_cache else 1,
    }


def _candidates(index, samples, radius, dest_miles):
    best = {}
    for mile, lat, lon in samples:
        for station in index.near(lat, lon, radius):
            off = haversine(lat, lon, station.lat, station.lon)
            if off > radius:
                continue
            current = best.get(station.opis_id)
            if current is None or off < current[0]:
                best[station.opis_id] = (off, mile, station)

    # Same city shares a centroid, so keep the cheapest stop there.
    grouped = {}
    for off, mile, station in best.values():
        if mile <= 1.0 or mile >= dest_miles - 0.5:
            continue
        key = (round(station.lat, 3), round(station.lon, 3))
        current = grouped.get(key)
        if current is None or station.price < current.station.price:
            grouped[key] = AlongRoute(station, mile, off)
    return list(grouped.values())


def _price_stops(purchases, dest_miles, tank, mpg):
    stops = []
    fuel_miles = tank
    pos = 0.0
    for along, gallons_exact in purchases:
        fuel_miles -= along.mile - pos
        fuel_miles += gallons_exact * mpg
        pos = along.mile
        gallons = round(gallons_exact + 1e-9, 2)
        if gallons <= 0:
            continue
        station = along.station
        stops.append(
            {
                "opis_id": station.opis_id,
                "name": station.name,
                "address": station.address,
                "city": station.city,
                "state": station.state,
                "price_per_gallon": station.price,
                "gallons": gallons,
                "cost": round(gallons * station.price + 1e-9, 2),
                "mile_marker": round(along.mile, 1),
                "lat": round(station.lat, 6),
                "lon": round(station.lon, 6),
                "miles_off_route": round(along.miles_off_route, 1),
            }
        )
    fuel_miles -= dest_miles - pos
    if abs(fuel_miles) < 1e-6:
        fuel_miles = 0.0
    total_gallons = round(sum(stop["gallons"] for stop in stops) + 1e-9, 2)
    total_cost = round(sum(stop["cost"] for stop in stops) + 1e-9, 2)
    remaining = round(fuel_miles / mpg + 1e-9, 2)
    return stops, total_gallons, total_cost, remaining


def _place_dict(place):
    return {
        "query": place.query,
        "city": place.city,
        "state": place.state,
        "lat": round(place.lat, 6),
        "lon": round(place.lon, 6),
    }
