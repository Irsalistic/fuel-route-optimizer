from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from routing.services.geo import Place
from routing.services.osrm import DrivingRoute
from routing.services.stations import Station, StationIndex


def _line(lat, lon1, lon2, points=40):
    coords = []
    for index in range(points):
        fraction = index / (points - 1)
        coords.append([lon1 + fraction * (lon2 - lon1), lat])
    return coords


def _station(opis_id, name, price, lon):
    return Station(
        opis_id=opis_id,
        name=name,
        address="I-70",
        city="Testville",
        state="KS",
        price=price,
        lat=39.0,
        lon=lon,
    )


class RouteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_missing_end_is_a_400(self):
        response = self.client.get("/api/route/", {"start": "Denver, CO"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_same_place_is_a_400(self):
        response = self.client.post(
            "/api/route/",
            {"start": "Denver, CO", "end": "denver, co"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_city_does_not_call_the_router(self):
        with patch("routing.services.planner.fetch_driving_route") as fetch:
            response = self.client.get(
                "/api/route/",
                {"start": "NotARealTownXYZ, KS", "end": "Denver, CO"},
            )
        self.assertEqual(response.status_code, 400)
        fetch.assert_not_called()

    def test_plan_buys_at_the_cheap_stop_and_reports_one_router_call(self):
        # 700 miles east to west at latitude 39. One degree of longitude is ~54 miles.
        span = 700 / (69.0 * 0.7771)
        lon1 = -100.0
        lon2 = lon1 - span

        def lon_at(mile):
            return lon1 + (lon2 - lon1) * (mile / 700.0)

        index = StationIndex(
            [
                _station("1", "Pricey Travel", 5.0, lon_at(200)),
                _station("2", "Cheap Travel", 3.0, lon_at(400)),
                _station("3", "Late Travel", 4.0, lon_at(600)),
            ]
        )
        route = DrivingRoute(
            distance_miles=700.0,
            duration_seconds=700 * 60,
            coordinates=_line(39.0, lon1, lon2),
            from_cache=False,
        )

        def fake_geocode(text):
            if text.startswith("Start"):
                return Place(text, "Startville", "KS", 39.0, lon1)
            return Place(text, "Endville", "UT", 39.0, lon2)

        with patch("routing.services.planner.geocode_location", side_effect=fake_geocode), patch(
            "routing.services.planner.fetch_driving_route", return_value=route
        ) as fetch, patch("routing.services.planner.get_index", return_value=index):
            response = self.client.get(
                "/api/route/",
                {"start": "Startville, KS", "end": "Endville, UT"},
            )

        self.assertEqual(response.status_code, 200, response.content)
        fetch.assert_called_once()
        body = response.json()
        self.assertEqual(body["routing_api_calls"], 1)
        self.assertEqual(body["distance_miles"], 700.0)
        self.assertEqual(len(body["stops"]), 1)
        stop = body["stops"][0]
        self.assertEqual(stop["name"], "Cheap Travel")
        self.assertEqual(stop["price_per_gallon"], 3.0)
        self.assertEqual(stop["gallons"], 20.0)
        self.assertEqual(stop["cost"], 60.0)
        self.assertEqual(body["fuel"]["total_cost"], 60.0)
        self.assertEqual(body["fuel"]["total_gallons"], 20.0)
        self.assertEqual(body["fuel"]["gallons_burned"], 70.0)
        self.assertGreaterEqual(body["fuel"]["fuel_remaining_gallons"], 0)
        self.assertEqual(body["route"]["type"], "LineString")
        self.assertGreater(len(body["route"]["coordinates"]), 2)
        self.assertIn("/map/?", body["map_url"])
        # GeoJSON is longitude, latitude.
        self.assertLess(body["route"]["coordinates"][0][0], 0)

    def test_gap_in_coverage_is_422(self):
        route = DrivingRoute(
            distance_miles=900.0,
            duration_seconds=1000,
            coordinates=_line(39.0, -100.0, -116.0),
            from_cache=True,
        )
        index = StationIndex([_station("9", "Lonely Stop", 3.5, -101.0)])

        def fake_geocode(text):
            if "Start" in text:
                return Place(text, "Startville", "KS", 39.0, -100.0)
            return Place(text, "Endville", "UT", 39.0, -116.0)

        with patch("routing.services.planner.geocode_location", side_effect=fake_geocode), patch(
            "routing.services.planner.fetch_driving_route", return_value=route
        ), patch("routing.services.planner.get_index", return_value=index):
            response = self.client.get(
                "/api/route/",
                {"start": "Startville, KS", "end": "Endville, UT"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("500", response.json()["error"])

    def test_map_page_renders(self):
        response = self.client.get("/map/", {"start": "Austin, TX", "end": "Dallas, TX"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("leaflet", html)
        self.assertIn("Austin, TX", html)
        self.assertIn("Dallas, TX", html)
