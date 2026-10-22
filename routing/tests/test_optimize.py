import random
from functools import lru_cache

from django.test import SimpleTestCase

from routing.services.errors import Unreachable
from routing.services.optimize import plan_stops


class Station:
    def __init__(self, mile, price, name="stop"):
        self.mile = mile
        self.price = price
        self.name = name


def cost_of(purchases):
    return sum(gallons * station.price for station, gallons in purchases)


def brute_cost(stations, dest, tank, mpg=1):
    """Exhaustive min cost. Miles and tank are integers. Small inputs only."""
    points = [0] + [station.mile for station in stations] + [dest]
    prices = [None] + [station.price for station in stations] + [None]
    last = len(points)
    inf = 10**18

    @lru_cache(None)
    def rec(idx, fuel):
        pos = points[idx]
        best = inf
        max_leave = fuel if prices[idx] is None else tank
        price = prices[idx]
        for leave in range(fuel, max_leave + 1):
            buy = leave - fuel
            paid = 0 if price is None else (buy / mpg) * price
            if pos + leave >= dest:
                if paid < best:
                    best = paid
                continue
            for nxt in range(idx + 1, last - 1):
                dist = points[nxt] - pos
                if dist <= leave:
                    val = rec(nxt, leave - dist)
                    if paid + val < best:
                        best = paid + val
                else:
                    break
        return best

    answer = rec(0, tank)
    if answer >= inf / 2:
        return None
    return answer


class PlanStopsTests(SimpleTestCase):
    def test_short_trip_buys_nothing(self):
        stations = [Station(100, 4.0), Station(200, 3.0)]
        self.assertEqual(plan_stops(stations, dest_miles=400, tank_miles=500, mpg=10), [])

    def test_buys_only_enough_to_finish_at_the_cheaper_stop(self):
        # 700 mile trip, 500 mile tank. The $3 stop is reachable on the first tank
        # and a full tank from there reaches the end, so we ignore the $5 stop.
        stations = [Station(200, 5.0), Station(400, 3.0), Station(600, 4.0)]
        purchases = plan_stops(stations, dest_miles=700, tank_miles=500, mpg=10)
        self.assertEqual(len(purchases), 1)
        station, gallons = purchases[0]
        self.assertEqual(station.price, 3.0)
        self.assertAlmostEqual(gallons, 20.0)
        self.assertAlmostEqual(cost_of(purchases), 60.0)

    def test_tops_up_cheap_fuel_before_an_expensive_gap(self):
        # Can't reach the $1 station or the destination from the start.
        # Fill at $2, then buy the rest at $1.
        stations = [Station(200, 4.0), Station(400, 2.0), Station(800, 1.0)]
        purchases = plan_stops(stations, dest_miles=1000, tank_miles=500, mpg=10)
        prices = [station.price for station, _gallons in purchases]
        self.assertIn(2.0, prices)
        self.assertIn(1.0, prices)
        self.assertNotIn(4.0, prices)

    def test_small_price_gaps_do_not_create_splash_and_dash_stops(self):
        stations = [
            Station(120 + index * 25, round(3.20 - index * 0.004, 3)) for index in range(20)
        ]
        strict = plan_stops(stations, dest_miles=900, tank_miles=500, mpg=10)
        relaxed = plan_stops(
            stations,
            dest_miles=900,
            tank_miles=500,
            mpg=10,
            min_savings=0.05,
            min_gallons=5,
        )
        self.assertGreater(len(strict), len(relaxed))
        self.assertGreaterEqual(len(relaxed), 1)
        fuel = 500.0
        pos = 0.0
        for station, gallons in relaxed:
            self.assertGreaterEqual(gallons, 5.0)
            fuel -= station.mile - pos
            self.assertGreaterEqual(fuel, -1e-6)
            fuel += gallons * 10
            pos = station.mile
        self.assertGreaterEqual(fuel, 900 - pos - 1e-6)

    def test_unreachable_when_a_gap_exceeds_the_tank(self):
        stations = [Station(100, 3.0)]
        with self.assertRaises(Unreachable):
            plan_stops(stations, dest_miles=800, tank_miles=500, mpg=10)

    def test_matches_exhaustive_search_on_random_routes(self):
        rng = random.Random(7)
        for _ in range(40):
            tank = rng.randint(10, 24)
            dest = rng.randint(tank, tank * 4)
            count = rng.randint(0, 6)
            if count and dest > 1:
                miles = sorted(rng.sample(range(1, dest), min(count, dest - 1)))
            else:
                miles = []
            stations = [Station(mile, rng.choice([1, 2, 3, 5, 8])) for mile in miles]
            planned = None
            try:
                planned = plan_stops(stations, dest, tank_miles=tank, mpg=1)
            except Unreachable:
                planned = None
            expected = brute_cost(stations, dest, tank, mpg=1)
            if expected is None:
                self.assertIsNone(planned)
            else:
                self.assertIsNotNone(planned)
                self.assertAlmostEqual(cost_of(planned), expected, places=6)

    def test_purchase_keeps_the_tank_legal(self):
        stations = [Station(mile, 3.5) for mile in (180, 420, 690, 900, 1200)]
        dest = 1400
        tank = 500
        mpg = 10
        purchases = plan_stops(stations, dest, tank_miles=tank, mpg=mpg)
        fuel = float(tank)
        pos = 0.0
        for station, gallons in purchases:
            self.assertGreaterEqual(fuel + 1e-6, station.mile - pos)
            fuel -= station.mile - pos
            fuel += gallons * mpg
            self.assertLessEqual(fuel, tank + 1e-6)
            pos = station.mile
        self.assertGreaterEqual(fuel + 1e-6, dest - pos)
