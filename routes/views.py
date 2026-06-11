import polyline as polyline_codec
from django.http import JsonResponse
from django.shortcuts import render

from routes.services.fuel_optimizer import CoverageGapError
from routes.services.geocoding import GeocodingUnavailable, LocationNotFound
from routes.services.planner import build_route_plan
from routes.services.routing import RouteNotFound, RoutingUnavailable

USAGE = (
    'Provide ?start=<place>&finish=<place> (free text within the USA), '
    'or coordinates via start_lat/start_lon/finish_lat/finish_lon.'
)


def _parse_inputs(request):
    """Returns kwargs for build_route_plan, or an error string."""
    kwargs = {}
    for which in ('start', 'finish'):
        text = request.GET.get(which, '').strip()
        lat, lon = request.GET.get(f'{which}_lat'), request.GET.get(f'{which}_lon')
        if lat is not None and lon is not None:
            try:
                lat, lon = float(lat), float(lon)
            except ValueError:
                return None, f'{which}_lat/{which}_lon must be numbers'
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None, f'{which} coordinates out of range'
            kwargs[f'{which}_coords'] = (lat, lon)
        elif text:
            kwargs[f'{which}_text'] = text
        else:
            return None, f'missing "{which}" parameter. {USAGE}'
    return kwargs, None


def _build_plan_response(request):
    """Shared by the JSON and map views: (plan, None) or (None, error response)."""
    kwargs, problem = _parse_inputs(request)
    if problem:
        return None, JsonResponse({'error': 'invalid_request', 'detail': problem}, status=400)
    try:
        return build_route_plan(**kwargs), None
    except LocationNotFound as exc:
        return None, JsonResponse(
            {'error': 'location_not_found',
             'detail': f'Could not find "{exc}" within the USA.'}, status=400)
    except RouteNotFound as exc:
        return None, JsonResponse(
            {'error': 'no_route', 'detail': f'No drivable route: {exc}'}, status=400)
    except CoverageGapError as exc:
        return None, JsonResponse(
            {'error': 'no_reachable_fuel_station', 'detail': str(exc)}, status=422)
    except (GeocodingUnavailable, RoutingUnavailable) as exc:
        return None, JsonResponse(
            {'error': 'routing_service_unavailable',
             'detail': f'Upstream map service failed: {exc}'}, status=502)


def route_view(request):
    plan, error = _build_plan_response(request)
    return error or JsonResponse(plan)


def map_view(request):
    plan, error = _build_plan_response(request)
    if error:
        return error
    coords = polyline_codec.decode(plan['route']['geometry'])
    context = {
        'route_coords': [[round(lat, 5), round(lon, 5)] for lat, lon in coords],
        'plan': plan,
    }
    return render(request, 'routes/map.html', context)
