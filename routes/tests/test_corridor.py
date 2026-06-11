"""Corridor search tests on a synthetic straight route (no DB, no network)."""

import numpy as np
import pytest

from routes.services.stations import (
    MILES_PER_DEG_LAT,
    StationIndex,
    cumulative_miles,
    find_corridor_stations,
)

BASE_LAT, BASE_LON = 35.0, -100.0
ROUTE_MILES = 400.0


def make_route(total_miles=ROUTE_MILES, points=400):
    """Due-north route starting at (BASE_LAT, BASE_LON)."""
    lats = np.linspace(BASE_LAT, BASE_LAT + total_miles / MILES_PER_DEG_LAT, points)
    lons = np.full(points, BASE_LON)
    return lats, lons


def station_at(position_miles, offset_miles=0.0, price=3.50, opis_id=1):
    """A station `position_miles` up the route, offset east by `offset_miles`."""
    lat = BASE_LAT + position_miles / MILES_PER_DEG_LAT
    lon = BASE_LON + offset_miles / (MILES_PER_DEG_LAT * np.cos(np.radians(lat)))
    return {'opis_id': opis_id, 'lat': lat, 'lon': lon, 'price_per_gallon': price}


def test_cumulative_miles_matches_route_length():
    lats, lons = make_route()
    cum = cumulative_miles(lats, lons)
    assert cum[0] == 0.0
    assert cum[-1] == pytest.approx(ROUTE_MILES, abs=1.0)


def test_detour_threshold_inclusion_and_exclusion():
    lats, lons = make_route()
    index = StationIndex([
        station_at(200, offset_miles=8, opis_id=1),   # inside 10-mile corridor
        station_at(200, offset_miles=14, opis_id=2),  # outside
    ])
    found = find_corridor_stations(index, lats, lons, max_detour_miles=10.0)
    assert [c.station['opis_id'] for c in found] == [1]
    assert found[0].detour_miles == pytest.approx(8.0, abs=0.5)


def test_route_position_and_ordering():
    lats, lons = make_route()
    index = StationIndex([
        station_at(300, opis_id=3),
        station_at(50, opis_id=1),
        station_at(150, opis_id=2),
    ])
    found = find_corridor_stations(index, lats, lons)
    assert [c.station['opis_id'] for c in found] == [1, 2, 3]
    positions = [c.route_position_miles for c in found]
    assert positions == pytest.approx([50, 150, 300], abs=5.0)


def test_bbox_padding_keeps_station_just_off_route_end():
    lats, lons = make_route()
    # 5 miles east of the route's final point: outside the unpadded bbox
    index = StationIndex([station_at(ROUTE_MILES, offset_miles=5, opis_id=1)])
    found = find_corridor_stations(index, lats, lons)
    assert len(found) == 1
    assert found[0].detour_miles == pytest.approx(5.0, abs=0.5)


def test_far_away_stations_prefiltered():
    lats, lons = make_route()
    index = StationIndex([station_at(200, offset_miles=500, opis_id=1)])
    assert find_corridor_stations(index, lats, lons) == []


def test_empty_index():
    lats, lons = make_route()
    assert find_corridor_stations(StationIndex([]), lats, lons) == []
