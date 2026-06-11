"""Cost-optimal fuel-stop selection along a route.

Model: the tank holds `tank_range` miles of fuel and starts full; fuel can be
bought only at corridor stations; consumption is fixed at `mpg`. The classic
greedy is provably optimal here:

  at each station, if a cheaper station (or the finish) is reachable on a full
  tank, buy just enough fuel to get there; otherwise fill up and drive to the
  cheapest station in reach.
"""

from dataclasses import dataclass

from routes.services.stations import Candidate


class CoverageGapError(Exception):
    """No station is reachable within the tank range from `mile_marker`."""

    def __init__(self, mile_marker):
        self.mile_marker = mile_marker
        super().__init__(
            f'No fuel station within range after mile {mile_marker:.0f} of the route.'
        )


@dataclass
class FuelStop:
    candidate: Candidate
    gallons: float
    cost: float


@dataclass
class FuelPlan:
    stops: list[FuelStop]
    total_gallons: float
    total_cost: float


def plan_fuel_stops(candidates, total_miles, tank_range=500.0, mpg=10.0):
    """Choose stops among `candidates` (sorted by route position) for a trip of
    `total_miles`. Raises CoverageGapError when the route cannot be covered.
    """
    if total_miles <= tank_range:
        return FuelPlan(stops=[], total_gallons=0.0, total_cost=0.0)

    stations = sorted(candidates, key=lambda c: c.route_position_miles)
    stops = []
    fuel = tank_range  # miles of fuel in the tank
    position = 0.0
    i = _first_reachable(stations, position, fuel)

    while True:
        here = stations[i]
        fuel -= here.route_position_miles - position
        position = here.route_position_miles

        reachable = [
            j for j in range(i + 1, len(stations))
            if stations[j].route_position_miles <= position + tank_range
        ]
        finish_in_reach = total_miles <= position + tank_range
        cheaper = [j for j in reachable if stations[j].price < here.price]

        if cheaper and (not finish_in_reach
                        or stations[cheaper[0]].route_position_miles < total_miles):
            # Bridge to the nearest cheaper station and buy the rest there.
            next_i = cheaper[0]
            need = stations[next_i].route_position_miles - position
        elif finish_in_reach:
            next_i = None
            need = total_miles - position
        elif reachable:
            # Nothing ahead is cheaper: stop next at the cheapest reachable
            # station (ties: the farthest). If it costs the same as here, defer
            # the fill to it - identical cost, avoids same-price stops minutes
            # apart; otherwise fill up here.
            next_i = min(reachable,
                         key=lambda j: (stations[j].price, -stations[j].route_position_miles))
            if stations[next_i].price == here.price:
                need = stations[next_i].route_position_miles - position
            else:
                need = tank_range
        else:
            raise CoverageGapError(position)

        bought = max(0.0, need - fuel)
        if bought > 0:
            gallons = bought / mpg
            stops.append(FuelStop(candidate=here, gallons=gallons, cost=gallons * here.price))
            fuel += bought

        if next_i is None:
            break
        i = next_i

    total_gallons = sum(s.gallons for s in stops)
    return FuelPlan(
        stops=stops,
        total_gallons=total_gallons,
        total_cost=sum(s.cost for s in stops),
    )


def _first_reachable(stations, position, fuel):
    """Index of the first station ahead; error if none is within the tank."""
    for i, c in enumerate(stations):
        if c.route_position_miles >= position:
            if c.route_position_miles - position <= fuel:
                return i
            break
    raise CoverageGapError(position)
