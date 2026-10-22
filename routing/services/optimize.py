"""Pick fuel stops along an already measured route.

The truck starts at mile 0 with a full tank. Fuel already in the tank is
not billed. Stations must sit on the route's mile markers (we snap nearby
stops onto the line before calling this).

Rule, walking forward:
  Drive to the cheapest station still inside the current tank.
  If a station ahead is cheaper by at least `min_savings` and a full tank
  can reach it, buy only enough to get there.
  If the destination is inside a full tank, buy only enough to finish.
  Otherwise fill the tank.

A purchase under `min_gallons` is skipped when another station (or the
destination) is still reachable. That avoids a stop for a few cents.
Set both knobs to 0 for the pure cheapest plan. Equal prices: go to the
farther station. The pure plan matches an exhaustive search on small
instances (see routing/tests/test_optimize.py).
"""

from routing.services.errors import Unreachable


def plan_stops(
    stations,
    dest_miles,
    tank_miles=500.0,
    mpg=10.0,
    min_savings=0.0,
    min_gallons=0.0,
):
    """Return (station, gallons) pairs. Gallons are exact, not rounded."""
    if dest_miles < 0:
        raise ValueError("distance must be positive")
    if tank_miles <= 0 or mpg <= 0:
        raise ValueError("tank range and mpg must be positive")
    if min_savings < 0 or min_gallons < 0:
        raise ValueError("thresholds must be non-negative")

    ordered = sorted(stations, key=lambda station: (station.mile, station.price))
    if dest_miles <= tank_miles + 1e-6:
        return []

    pos = 0.0
    fuel = float(tank_miles)
    purchases = []
    guard = 0
    limit = len(ordered) + 2

    while pos < dest_miles - 1e-4:
        guard += 1
        if guard > limit:
            raise Unreachable(_stuck_message(pos, pos + fuel, dest_miles, tank_miles))
        if pos + fuel >= dest_miles - 1e-4:
            break

        reach = pos + fuel
        reachable = [
            station
            for station in ordered
            if pos + 1e-4 < station.mile <= reach + 1e-6
        ]
        if not reachable:
            raise Unreachable(_stuck_message(pos, reach, dest_miles, tank_miles))

        chosen = min(reachable, key=lambda station: (station.price, -station.mile))
        fuel -= chosen.mile - pos
        pos = chosen.mile

        horizon = pos + tank_miles
        cheaper = [
            station.mile
            for station in ordered
            if pos + 1e-4 < station.mile <= horizon + 1e-6
            and _meaningfully_cheaper(station.price, chosen.price, min_savings)
        ]
        if cheaper:
            target = min(cheaper)
            buy_miles = max(0.0, target - pos - fuel)
        elif dest_miles <= horizon + 1e-6:
            buy_miles = max(0.0, dest_miles - pos - fuel)
        else:
            buy_miles = tank_miles - fuel

        buy_miles = max(0.0, min(buy_miles, tank_miles - fuel))
        # Skip a splash-and-dash when we can still reach another stop.
        if min_gallons and (buy_miles / mpg) < min_gallons - 1e-9:
            farther = any(
                pos + 1e-4 < station.mile <= pos + fuel + 1e-6 for station in ordered
            )
            can_finish = pos + fuel >= dest_miles - 1e-4
            if farther or can_finish:
                buy_miles = 0.0
        fuel += buy_miles
        if buy_miles > 1e-6:
            purchases.append((chosen, buy_miles / mpg))

    return purchases


def _meaningfully_cheaper(price, other, min_savings):
    return price < other - min_savings - 1e-9


def _stuck_message(mile, reach, dest, tank_miles):
    return (
        f"Can't finish this trip on a {tank_miles:.0f} mile tank. "
        f"At mile {mile:.0f} the truck can reach mile {reach:.0f}, "
        f"and there is no truck stop in range before the destination at mile {dest:.0f}."
    )
