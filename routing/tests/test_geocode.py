from django.test import SimpleTestCase

from routing.services.errors import LocationError
from routing.services.geo import geocode_location, haversine, parse_location
from routing.services.stations import dedupe_stops, geocode_stops, read_fuel_rows


class GeocodeTests(SimpleTestCase):
    def test_parses_state_code_and_full_name(self):
        self.assertEqual(parse_location("  Austin, tx "), ("Austin", "TX"))
        self.assertEqual(parse_location("Des Moines, Iowa"), ("Des Moines", "IA"))

    def test_rejects_missing_comma_and_non_us_state(self):
        with self.assertRaises(LocationError):
            parse_location("Boston")
        with self.assertRaises(LocationError):
            parse_location("Toronto, ON")

    def test_known_cities_land_in_the_right_region(self):
        nyc = geocode_location("New York, NY")
        la = geocode_location("Los Angeles, California")
        self.assertEqual(nyc.state, "NY")
        self.assertAlmostEqual(nyc.lat, 40.7, delta=0.5)
        self.assertAlmostEqual(la.lon, -118.2, delta=0.5)
        # Straight line is well under the driving distance, but not a short hop.
        self.assertGreater(haversine(nyc.lat, nyc.lon, la.lat, la.lon), 2000)

    def test_unknown_city(self):
        with self.assertRaises(LocationError):
            geocode_location("NotARealTownXYZ, KS")


class FuelFileTests(SimpleTestCase):
    def test_duplicate_opis_id_keeps_the_cheapest_price(self):
        rows = [
            {
                "opis_id": "105",
                "name": "TA SAGINAW",
                "address": "I-75",
                "city": "Bridgeport",
                "state": "MI",
                "price": 3.429,
            },
            {
                "opis_id": "105",
                "name": "TA SAGINAW",
                "address": "I-75",
                "city": "Bridgeport",
                "state": "MI",
                "price": 3.269,
            },
        ]
        kept = dedupe_stops(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["price"], 3.269)

    def test_real_file_geocodes_us_stops(self):
        rows = dedupe_stops(read_fuel_rows())
        stations, skipped, unmatched = geocode_stops(rows)
        self.assertGreater(skipped, 0)
        self.assertEqual(unmatched, [])
        self.assertGreater(len(stations), 6000)
        cabin = next(
            station
            for station in stations
            if station.city == "Big Cabin" and station.state == "OK"
        )
        self.assertAlmostEqual(cabin.lat, 36.5, delta=1.0)
        self.assertLess(cabin.lon, -94)
        self.assertGreater(cabin.lon, -100)
