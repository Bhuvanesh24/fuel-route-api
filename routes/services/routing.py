"""OSRM routing client - the single map/route API call per request."""

from dataclasses import dataclass

import numpy as np
import polyline
import requests
from django.conf import settings

METERS_PER_MILE = 1609.344

_session = requests.Session()


class RoutingUnavailable(Exception):
    pass


class RouteNotFound(Exception):
    pass


@dataclass
class Route:
    distance_miles: float
    duration_hours: float
    polyline5: str       # OSRM's encoded geometry, passed through to clients
    lats: np.ndarray
    lons: np.ndarray


def get_route(start_lat, start_lon, finish_lat, finish_lon):
    """Driving route between two points. Note OSRM expects lon,lat order."""
    url = (
        f'{settings.OSRM_BASE_URL}/route/v1/driving/'
        f'{start_lon},{start_lat};{finish_lon},{finish_lat}'
    )
    try:
        resp = _session.get(url, params={'overview': 'full', 'geometries': 'polyline'}, timeout=(3, 30))
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingUnavailable(str(exc)) from exc

    if body.get('code') != 'Ok' or not body.get('routes'):
        if body.get('code') in ('NoRoute', 'NoSegment'):
            raise RouteNotFound(body.get('message', 'no drivable route'))
        raise RoutingUnavailable(body.get('message', f"OSRM returned {body.get('code')}"))

    route = body['routes'][0]
    coords = np.array(polyline.decode(route['geometry']))  # [(lat, lon), ...]
    return Route(
        distance_miles=route['distance'] / METERS_PER_MILE,
        duration_hours=route['duration'] / 3600.0,
        polyline5=route['geometry'],
        lats=coords[:, 0],
        lons=coords[:, 1],
    )
