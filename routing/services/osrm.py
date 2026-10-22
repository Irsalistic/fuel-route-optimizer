"""One driving-route call per uncached request.

Uses the public OSRM demo (no API key). Geometry comes back as GeoJSON.
The same start and end are cached for an hour so the map refresh is free.
"""

import json
import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from routing.services.errors import RouteNotFound, RoutingServiceError

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344
CACHE_SECONDS = 60 * 60


@dataclass(frozen=True)
class DrivingRoute:
    distance_miles: float
    duration_seconds: float
    coordinates: list
    from_cache: bool


def fetch_driving_route(lat1, lon1, lat2, lon2) -> DrivingRoute:
    key = f"osrm:v1:{lat1:.4f}:{lon1:.4f}:{lat2:.4f}:{lon2:.4f}"
    cached = cache.get(key)
    if cached is not None:
        logger.info("osrm cache hit %s", key)
        return DrivingRoute(
            distance_miles=cached["distance_miles"],
            duration_seconds=cached["duration_seconds"],
            coordinates=cached["coordinates"],
            from_cache=True,
        )

    query = urlencode(
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }
    )
    url = (
        f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?{query}"
    )
    request = Request(url, headers={"User-Agent": "fuel-route-optimizer/1.0"})
    logger.info("osrm request %s", url)
    try:
        with urlopen(request, timeout=settings.OSRM_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise RoutingServiceError(
                "The routing service rate limited this request. "
                "Wait a minute and try again. Repeat lookups are cached."
            ) from exc
        raise RoutingServiceError(
            f"The routing service returned HTTP {exc.code}."
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RoutingServiceError(
            "The routing service did not respond. Try again in a minute."
        ) from exc

    code = payload.get("code")
    if code == "NoRoute":
        raise RouteNotFound(
            "No driving route between those cities. "
            "They may not be connected by road (Hawaii to the mainland, for example)."
        )
    routes = payload.get("routes") or []
    if code != "Ok" or not routes:
        raise RoutingServiceError("The routing service did not return a route.")

    route = routes[0]
    geometry = route.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        raise RoutingServiceError("The routing service returned an empty route.")

    stored = {
        "distance_miles": route["distance"] / METERS_PER_MILE,
        "duration_seconds": float(route["duration"]),
        "coordinates": coordinates,
    }
    cache.set(key, stored, CACHE_SECONDS)
    return DrivingRoute(
        distance_miles=stored["distance_miles"],
        duration_seconds=stored["duration_seconds"],
        coordinates=stored["coordinates"],
        from_cache=False,
    )
