"""Fuel-stop optimizer tests on synthetic 1-D routes (no DB, no network).

Model under test: tank holds 500 miles of fuel, starts full; 10 MPG; buy fuel
only at corridor stations; minimize dollars spent.
"""

import pytest

from routes.services.stations import Candidate
from routes.services.fuel_optimizer import CoverageGapError, plan_fuel_stops


def cand(position, price, opis_id=None):
    return Candidate(
        station={'opis_id': opis_id or int(position), 'price_per_gallon': price},
        route_position_miles=float(position),
        detour_miles=1.0,
    )


def test_short_route_needs_no_fuel():
    plan = plan_fuel_stops([cand(100, 3.0)], total_miles=400.0)
    assert plan.stops == []
    assert plan.total_cost == 0.0
    assert plan.total_gallons == 0.0


def test_single_station_buys_exactly_what_remains():
    # 900 miles, station at 450: arrive with 50 miles left, need 450 more.
    plan = plan_fuel_stops([cand(450, 3.0)], total_miles=900.0)
    assert len(plan.stops) == 1
    stop = plan.stops[0]
    assert stop.gallons == pytest.approx(40.0)   # (450 - 50) / 10
    assert stop.cost == pytest.approx(120.0)
    assert plan.total_cost == pytest.approx(120.0)


def test_buys_just_enough_to_reach_cheaper_station():
    # The signature optimal-greedy behavior: at the $3.50 station, top up only
    # enough to bridge to the $3.00 station, then buy the rest there.
    plan = plan_fuel_stops([cand(400, 3.50), cand(700, 3.00)], total_miles=900.0)
    assert [s.candidate.route_position_miles for s in plan.stops] == [400, 700]
    first, second = plan.stops
    assert first.gallons == pytest.approx(20.0)   # bridge 300 miles with 100 in tank
    assert second.gallons == pytest.approx(20.0)  # final 200 miles
    assert plan.total_cost == pytest.approx(20 * 3.50 + 20 * 3.00)


def test_fills_tank_when_everything_ahead_is_pricier():
    # Station B is more expensive and the finish is out of range, so fill at A.
    plan = plan_fuel_stops([cand(400, 3.00), cand(800, 3.50)], total_miles=1200.0)
    first, second = plan.stops
    assert first.gallons == pytest.approx(40.0)   # fill: 500 - 100 in tank
    assert second.gallons == pytest.approx(30.0)  # arrive with 100, need 400
    assert plan.total_cost == pytest.approx(40 * 3.00 + 30 * 3.50)


def test_gap_between_stations_raises():
    with pytest.raises(CoverageGapError) as exc:
        plan_fuel_stops([cand(100, 3.0), cand(800, 3.0)], total_miles=1200.0)
    assert exc.value.mile_marker == pytest.approx(100.0)


def test_no_station_in_first_tank_raises():
    with pytest.raises(CoverageGapError) as exc:
        plan_fuel_stops([cand(600, 3.0)], total_miles=1200.0)
    assert exc.value.mile_marker == 0.0


def test_no_stations_at_all_raises():
    with pytest.raises(CoverageGapError):
        plan_fuel_stops([], total_miles=900.0)


def test_destination_exactly_at_range_boundary():
    plan = plan_fuel_stops([cand(500, 3.0)], total_miles=1000.0)
    assert len(plan.stops) == 1
    assert plan.stops[0].gallons == pytest.approx(50.0)  # arrive empty, buy 500 miles


def test_cheap_station_is_not_a_stop_when_tank_suffices():
    # Station at mile 100 is cheap, but the trip only needs the starting tank
    # plus one purchase later; a zero-gallon visit must not appear as a stop.
    plan = plan_fuel_stops([cand(100, 2.50), cand(450, 3.00)], total_miles=900.0)
    assert all(s.gallons > 0 for s in plan.stops)


def test_total_gallons_invariant():
    # Optimal plans never strand fuel: purchases cover exactly the miles beyond
    # the starting tank.
    candidates = [cand(p, price) for p, price in
                  [(200, 3.2), (390, 2.9), (640, 3.6), (900, 3.1), (1150, 2.8), (1400, 3.4)]]
    total = 1700.0
    plan = plan_fuel_stops(candidates, total_miles=total)
    assert plan.total_gallons == pytest.approx((total - 500.0) / 10.0)
    assert plan.total_cost == pytest.approx(sum(s.cost for s in plan.stops))
    positions = [s.candidate.route_position_miles for s in plan.stops]
    assert positions == sorted(positions)
