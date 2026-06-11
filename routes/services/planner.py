"""Orchestrates geocoding, routing, corridor search and fuel optimization into
one route plan, with caching so repeat queries hit zero external APIs.

External-call budget per fresh request: 2 Nominatim geocodes + 1 OSRM route = 3.
With coordinates supplied (or geocodes cached): 1. Fully cached: 0.
"""

import hashlib
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache

from routes.services import geocoding, routing
from routes.services.fuel_optimizer import plan_fuel_stops
from routes.services.stations import find_corridor_stations, get_station_index

PLAN_CACHE_SECONDS = 600
GEOCODE_CACHE_SECONDS = 86400

ASSUMPTIONS = [
    'Vehicle departs with a full 500-mile tank; total cost covers fuel purchased en route.',
    'Routes of 500 miles or less therefore need no stops and cost $0.',
    'Fuel economy is fixed at 10 MPG; detours to stations (all within 10 miles of the route) are not added to consumption.',
]


def _normalize(text):
    return ' '.join(text.lower().split())


def _cached_geocode(query):
    """Nominatim lookup with a 24h cache; returns (result, was_api_call)."""
    key = 'geocode:' + hashlib.sha1(_normalize(query).encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        return hit, False
    result = geocoding.geocode(query)
    cache.set(key, result, GEOCODE_CACHE_SECONDS)
    return result, True


def build_route_plan(start_text=None, finish_text=None, start_coords=None, finish_coords=None):
    """Returns the full response dict. Raises LocationNotFound,
    GeocodingUnavailable, RoutingUnavailable, RouteNotFound, CoverageGapError.
    """
    start_key = start_text or '{:.5f},{:.5f}'.format(*start_coords)
    finish_key = finish_text or '{:.5f},{:.5f}'.format(*finish_coords)
    plan_key = 'plan:' + hashlib.sha1(
        (_normalize(start_key) + '|' + _normalize(finish_key)).encode()
    ).hexdigest()

    cached = cache.get(plan_key)
    if cached is not None:
        return cached | {'external_api_calls': 0}

    api_calls = 0
    points = {}
    for which, text, coords in (('start', start_text, start_coords),
                                ('finish', finish_text, finish_coords)):
        if coords is not None:
            lat, lon = coords
            points[which] = {'query': f'{lat},{lon}', 'display_name': f'{lat},{lon}', 'lat': lat, 'lon': lon}
        else:
            result, called = _cached_geocode(text)
            api_calls += called
            points[which] = {'query': text, 'display_name': result.display_name,
                             'lat': result.lat, 'lon': result.lon}

    route = routing.get_route(points['start']['lat'], points['start']['lon'],
                              points['finish']['lat'], points['finish']['lon'])
    api_calls += 1

    candidates = find_corridor_stations(
        get_station_index(), route.lats, route.lons,
        max_detour_miles=settings.MAX_DETOUR_MILES,
    )
    fuel_plan = plan_fuel_stops(
        candidates, route.distance_miles,
        tank_range=settings.VEHICLE_TANK_RANGE_MILES, mpg=settings.VEHICLE_MPG,
    )

    plan = {
        'start': points['start'],
        'finish': points['finish'],
        'route': {
            'distance_miles': round(route.distance_miles, 1),
            'duration_hours': round(route.duration_hours, 1),
            'geometry': route.polyline5,
            'geometry_format': 'polyline5',
        },
        'fuel_plan': {
            'mpg': settings.VEHICLE_MPG,
            'tank_range_miles': settings.VEHICLE_TANK_RANGE_MILES,
            'stops': [
                {
                    'opis_id': s.candidate.station['opis_id'],
                    'name': s.candidate.station['name'],
                    'address': s.candidate.station['address'],
                    'city': s.candidate.station['city'],
                    'state': s.candidate.station['state'],
                    'lat': s.candidate.station['lat'],
                    'lon': s.candidate.station['lon'],
                    'price_per_gallon': round(s.candidate.price, 4),
                    'route_position_miles': round(s.candidate.route_position_miles, 1),
                    'detour_miles': round(s.candidate.detour_miles, 1),
                    'gallons_purchased': round(s.gallons, 2),
                    'cost': round(s.cost, 2),
                }
                for s in fuel_plan.stops
            ],
            'total_gallons_purchased': round(fuel_plan.total_gallons, 2),
            'total_fuel_cost': round(fuel_plan.total_cost, 2),
            'assumptions': ASSUMPTIONS,
        },
        'map_url': '/map/?' + urlencode({'start': start_key, 'finish': finish_key}),
    }
    cache.set(plan_key, plan, PLAN_CACHE_SECONDS)
    return plan | {'external_api_calls': api_calls}
