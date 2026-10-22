# Fuel route optimizer

Django API that plans a drive between two US cities and picks where to buy fuel.

Send a start and an end. The response has the road geometry, the stops, gallons and dollars at each stop, and the total bill. `/map/` draws the same trip.

## Setup

Python 3.12 or newer. Django 6.1.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 manage.py migrate
python3 manage.py runserver
```

Open http://127.0.0.1:8000/map/ or call the API below.

Geocoded stops already live in `data/stations.json`. To rebuild that file:

```bash
python3 manage.py geocode_stations
```

Copy `.env.example` to `.env` only if you want to change the secret, debug flag, or OSRM host. A local demo runs with the defaults. No API key.

## Example

New York to Los Angeles. First call is about a second. The same pair is cached for an hour, and `routing_api_calls` drops to 0.

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&end=Los%20Angeles,%20CA"
```

Abridged response. The real `route.coordinates` array has about 700 `[lon, lat]` points.

```json
{
  "start": {"query": "New York, NY", "city": "New York", "state": "NY", "lat": 40.74838, "lon": -73.996705},
  "end": {"query": "Los Angeles, CA", "city": "Los Angeles", "state": "CA", "lat": 33.973093, "lon": -118.247896},
  "distance_miles": 2800.6,
  "duration_minutes": 2993,
  "assumptions": {
    "mpg": 10.0,
    "tank_range_miles": 500.0,
    "tank_gallons": 50.0,
    "started_with_full_tank": true,
    "initial_fuel_included_in_cost": false,
    "station_coordinates": "city_centroid",
    "search_radius_miles": 20.0
  },
  "stops": [
    {
      "opis_id": "72445",
      "name": "SHEETZ #639",
      "address": "I-80 Exit 223",
      "city": "Youngstown",
      "state": "OH",
      "price_per_gallon": 3.059,
      "gallons": 35.4,
      "cost": 108.29,
      "mile_marker": 392.0,
      "lat": 41.0986,
      "lon": -80.6474,
      "miles_off_route": 3.8
    }
  ],
  "fuel": {
    "total_gallons": 230.06,
    "total_cost": 696.2,
    "gallons_burned": 280.06,
    "fuel_remaining_gallons": 0.0
  },
  "route": {"type": "LineString", "coordinates": [[-73.9965, 40.7486]]},
  "routing_api_calls": 1,
  "map_url": "http://127.0.0.1:8000/map/?start=New%20York,%20NY&end=Los%20Angeles,%20CA"
}
```

That run buys 230.06 gallons for $696.20. The other seven stops are Utica IL, Waco NE, Overton NE, Ogallala NE, Longmont CO, Green River UT, and North Las Vegas NV. The cheapest fill is a full 50 gallons at $2.799 in Waco.

Map: http://127.0.0.1:8000/map/?start=New%20York,%20NY&end=Los%20Angeles,%20CA

A short trip does not stop. Boston to Providence is about 50 miles, so the bill is $0. The truck still burns the fuel it started with.

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H 'Content-Type: application/json' \
  -d '{"start": "Boston, MA", "end": "Providence, RI"}'
```

Bad input returns 400 with an `error` string. A route the tank cannot cover returns 422. If OSRM is down, the status is 503.

## Assumptions

- The truck starts full. Fuel already in the tank is not billed.
- Range is 500 miles and economy is 10 mpg, so the tank holds 50 gallons.
- Prices come from `data/fuel-prices.csv`. Some OPIS ids show up more than once. The cheapest row wins.
- The price file has no lat/lon. A management command joins each stop to a US city centroid (`data/us_cities.csv`, license in `data/US_CITIES_LICENSE.txt`). Stops in the same city share that point, and the planner keeps the cheapest one.
- Only stops within 20 miles of the road are considered. If that leaves a gap longer than the tank, the search tries 45 miles. Neither pass calls the router again.
- `miles_off_route` is how far the city centroid is from the highway. The out-and-back detour is not added to the trip mileage. Treat a large value as a weak match.
- Canadian rows in the price file are skipped. Start and end have to be in the US, as `City, ST` or `City, State`.
- A station has to be at least 5 cents a gallon cheaper before the planner aims a purchase at it, and a buy under 5 gallons is skipped when another stop is still in range. Without that, the route picked up a stop every time the price moved by a tenth of a cent. Set both thresholds to 0 in `plan_stops` for the pure rule. The tests check that version against an exhaustive search.

## Routing

City lookup is local. An unknown city never calls out.

The external call is the public OSRM demo, which needs no key:

https://router.project-osrm.org

Distance, drive time, and the line all come from that one response. Set `OSRM_BASE_URL` to point at another OSRM server. The public demo is for light use. If it rate limits you, wait a minute and retry. Repeating the same trip does not call it again.

The browser map loads Leaflet and OpenStreetMap tiles on its own. Those requests are not included in `routing_api_calls`.

## How a stop is chosen

Stops near the route get a mile marker. From the current spot the planner drives to the cheapest station the tank can still reach. If a cheaper station is inside a full tank from there, it buys only enough to arrive. If the destination is inside a full tank, it buys only enough to finish. Otherwise it fills up. It will not leave a gap longer than 500 miles.

## Tests

```bash
python3 manage.py test
```

API tests stub the OSRM response so the suite stays offline.

## Postman

Import `postman/fuel_route.postman_collection.json`. The `baseUrl` variable is `http://127.0.0.1:8000`.

## Where things are

- `routing/services/geo.py` city lookup and mile markers
- `routing/services/stations.py` price file, dedupe, in-memory grid
- `routing/services/osrm.py` the routing call and its cache
- `routing/services/optimize.py` which stops to buy at
- `routing/services/planner.py` corridor search and the response
- `routing/views.py` `/api/route/` and `/map/`
