"""View tests with the external services mocked - no network, no station DB."""

from unittest import mock

import numpy as np
import pytest

from routes.services.geocoding import GeocodeResult, LocationNotFound
from routes.services.routing import Route, RoutingUnavailable
from routes.services.stations import MILES_PER_DEG_LAT, StationIndex

BASE_LAT, BASE_LON = 35.0, -100.0
ROUTE_MILES = 900.0


def fake_route(total_miles=ROUTE_MILES):
    lats = np.linspace(BASE_LAT, BASE_LAT + total_miles / MILES_PER_DEG_LAT, 300)
    lons = np.full(300, BASE_LON)
    return Route(distance_miles=total_miles, duration_hours=total_miles / 60.0,
                 polyline5='fake_polyline', lats=lats, lons=lons)


def fake_index():
    def station(position, price, opis_id):
        return {
            'opis_id': opis_id, 'name': f'STATION {opis_id}', 'address': 'I-40, EXIT 1',
            'city': 'Somewhere', 'state': 'TX', 'price_per_gallon': price,
            'lat': BASE_LAT + position / MILES_PER_DEG_LAT, 'lon': BASE_LON,
            'geocode_source': 'city_centroid',
        }
    return StationIndex([station(300, 3.50, 1), station(600, 3.00, 2)])


@pytest.fixture
def mocked_services():
    def geocode_side_effect(query):
        return GeocodeResult(lat=BASE_LAT, lon=BASE_LON, display_name=f'{query}, USA')

    with mock.patch('routes.services.geocoding.geocode', side_effect=geocode_side_effect) as geocode, \
         mock.patch('routes.services.routing.get_route', return_value=fake_route()) as get_route, \
         mock.patch('routes.services.planner.get_station_index', return_value=fake_index()):
        yield geocode, get_route


def test_route_response_schema_and_call_budget(client, mocked_services):
    geocode, get_route = mocked_services
    resp = client.get('/api/route/', {'start': 'Amarillo, TX', 'finish': 'Wichita, KS'})
    assert resp.status_code == 200
    body = resp.json()

    assert body['external_api_calls'] == 3  # 2 geocodes + 1 route
    assert geocode.call_count == 2
    assert get_route.call_count == 1

    assert body['route']['distance_miles'] == ROUTE_MILES
    assert body['route']['geometry'] == 'fake_polyline'
    assert body['route']['geometry_format'] == 'polyline5'
    assert body['start']['display_name'] == 'Amarillo, TX, USA'
    assert body['map_url'].startswith('/map/?')

    plan = body['fuel_plan']
    assert plan['mpg'] == 10.0 and plan['tank_range_miles'] == 500.0
    assert plan['total_gallons_purchased'] == pytest.approx((ROUTE_MILES - 500) / 10)
    assert plan['total_fuel_cost'] == pytest.approx(sum(s['cost'] for s in plan['stops']), abs=0.05)
    assert plan['assumptions']
    for stop in plan['stops']:
        assert {'opis_id', 'name', 'price_per_gallon', 'route_position_miles',
                'gallons_purchased', 'cost', 'lat', 'lon'} <= stop.keys()


def test_repeat_request_hits_cache(client, mocked_services):
    geocode, get_route = mocked_services
    first = client.get('/api/route/', {'start': 'A', 'finish': 'B'}).json()
    second = client.get('/api/route/', {'start': 'a ', 'finish': ' b'}).json()  # normalized key
    assert first['external_api_calls'] == 3
    assert second['external_api_calls'] == 0
    assert geocode.call_count == 2 and get_route.call_count == 1
    assert second['fuel_plan'] == first['fuel_plan']


def test_coordinate_input_skips_geocoding(client, mocked_services):
    geocode, get_route = mocked_services
    resp = client.get('/api/route/', {
        'start_lat': BASE_LAT, 'start_lon': BASE_LON,
        'finish_lat': BASE_LAT + 10, 'finish_lon': BASE_LON,
    })
    assert resp.status_code == 200
    assert resp.json()['external_api_calls'] == 1
    assert geocode.call_count == 0


def test_missing_parameter_is_400(client):
    resp = client.get('/api/route/', {'start': 'Dallas, TX'})
    assert resp.status_code == 400
    assert resp.json()['error'] == 'invalid_request'


def test_bad_coordinates_are_400(client):
    resp = client.get('/api/route/', {'start_lat': '91', 'start_lon': '0',
                                      'finish': 'Dallas, TX'})
    assert resp.status_code == 400


def test_unknown_location_is_400(client, mocked_services):
    geocode, _ = mocked_services
    geocode.side_effect = LocationNotFound('xyzzy')
    resp = client.get('/api/route/', {'start': 'xyzzy', 'finish': 'Dallas, TX'})
    assert resp.status_code == 400
    assert resp.json()['error'] == 'location_not_found'


def test_routing_outage_is_502(client, mocked_services):
    _, get_route = mocked_services
    get_route.side_effect = RoutingUnavailable('timeout')
    resp = client.get('/api/route/', {'start': 'A', 'finish': 'B'})
    assert resp.status_code == 502
    assert resp.json()['error'] == 'routing_service_unavailable'


def test_coverage_gap_is_422(client, mocked_services):
    with mock.patch('routes.services.planner.get_station_index',
                    return_value=StationIndex([])):
        resp = client.get('/api/route/', {'start': 'A', 'finish': 'B'})
    assert resp.status_code == 422
    assert resp.json()['error'] == 'no_reachable_fuel_station'


def test_map_view_renders(client, mocked_services):
    with mock.patch('routes.views.polyline_codec.decode',
                    return_value=[(BASE_LAT, BASE_LON), (BASE_LAT + 1, BASE_LON)]):
        resp = client.get('/map/', {'start': 'A', 'finish': 'B'})
    assert resp.status_code == 200
    assert b'leaflet' in resp.content


@pytest.mark.django_db
def test_import_stations_command(tmp_path, settings):
    import textwrap
    from django.core.management import call_command
    from routes.models import FuelStation

    csv_path = tmp_path / 'stations.csv'
    csv_path.write_text(textwrap.dedent('''\
        opis_id,name,address,city,state,lat,lon,price_per_gallon,geocode_source
        1,ALPHA,I-40 EXIT 1,Amarillo,TX,35.19,-101.84,3.10,census
        2,BETA,I-40 EXIT 2,Tucumcari,NM,35.17,-103.72,3.25,city_centroid
    '''))
    settings.STATIONS_CSV = csv_path
    call_command('import_stations')
    call_command('import_stations')  # idempotent
    assert FuelStation.objects.count() == 2
    assert FuelStation.objects.get(opis_id=1).name == 'ALPHA'
