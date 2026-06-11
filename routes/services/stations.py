"""In-memory station index and route-corridor search.

All 6.6k stations are held in parallel numpy arrays (loaded once per process),
so finding the stations within a few miles of a 2,000-mile route is a couple of
vectorized haversine passes - no spatial database needed.
"""

import threading
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_MILES = 3958.8
MILES_PER_DEG_LAT = EARTH_RADIUS_MILES * np.pi / 180.0  # ~69.1

# Route points are downsampled to this spacing before the station-distance pass;
# coarser than the corridor width matters, finer just burns cycles.
SAMPLE_SPACING_MILES = 3.0


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles; accepts scalars or broadcastable arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def cumulative_miles(lats, lons):
    """Distance along the polyline at each vertex, starting at 0."""
    seg = haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    return np.concatenate(([0.0], np.cumsum(seg)))


@dataclass
class Candidate:
    """A fuel station near the route, projected onto it."""

    station: dict
    route_position_miles: float
    detour_miles: float

    @property
    def price(self):
        return self.station['price_per_gallon']


class StationIndex:
    def __init__(self, stations):
        """stations: iterable of dicts with at least lat, lon, price_per_gallon."""
        self.meta = list(stations)
        self.lats = np.array([s['lat'] for s in self.meta])
        self.lons = np.array([s['lon'] for s in self.meta])

    def __len__(self):
        return len(self.meta)


_index = None
_index_lock = threading.Lock()


def get_station_index():
    """Process-wide lazy singleton over the FuelStation table."""
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                from routes.models import FuelStation

                _index = StationIndex(
                    FuelStation.objects.values(
                        'opis_id', 'name', 'address', 'city', 'state',
                        'lat', 'lon', 'price_per_gallon', 'geocode_source',
                    )
                )
    return _index


def find_corridor_stations(index, route_lats, route_lons, max_detour_miles=10.0):
    """Stations within max_detour_miles of the route, sorted by position along it.

    A station's position is taken from its nearest sampled route point; with
    ~3-mile sampling that is accurate to a couple of miles, which is plenty for
    a 500-mile tank. If the route passes the same spot twice, the station is
    assigned the single nearest pass (a known simplification).
    """
    if len(index) == 0 or len(route_lats) < 2:
        return []

    cum = cumulative_miles(route_lats, route_lons)
    sample_idx = np.unique(np.searchsorted(cum, np.arange(0.0, cum[-1], SAMPLE_SPACING_MILES)))
    sample_idx = np.unique(np.append(sample_idx, len(cum) - 1))
    s_lats, s_lons, s_cum = route_lats[sample_idx], route_lons[sample_idx], cum[sample_idx]

    pad_lat = max_detour_miles / MILES_PER_DEG_LAT
    pad_lon = max_detour_miles / (MILES_PER_DEG_LAT * np.cos(np.radians(np.mean(s_lats))))
    in_bbox = (
        (index.lats >= s_lats.min() - pad_lat) & (index.lats <= s_lats.max() + pad_lat)
        & (index.lons >= s_lons.min() - pad_lon) & (index.lons <= s_lons.max() + pad_lon)
    )
    candidate_ids = np.flatnonzero(in_bbox)
    if candidate_ids.size == 0:
        return []

    # candidates x samples distance matrix (a few hundred x a few hundred: sub-ms)
    dists = haversine_miles(
        index.lats[candidate_ids][:, None], index.lons[candidate_ids][:, None],
        s_lats[None, :], s_lons[None, :],
    )
    nearest = dists.argmin(axis=1)
    detours = dists[np.arange(len(candidate_ids)), nearest]

    candidates = [
        Candidate(
            station=index.meta[i],
            route_position_miles=float(s_cum[nearest[k]]),
            detour_miles=float(detours[k]),
        )
        for k, i in enumerate(candidate_ids)
        if detours[k] <= max_detour_miles
    ]
    candidates.sort(key=lambda c: c.route_position_miles)
    return candidates
