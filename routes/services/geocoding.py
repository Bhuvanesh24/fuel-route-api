"""Nominatim geocoding for the start/finish inputs (the station list is
geocoded offline; this is never used for stations)."""

from dataclasses import dataclass

import requests
from django.conf import settings

_session = requests.Session()


class GeocodingUnavailable(Exception):
    pass


class LocationNotFound(Exception):
    pass


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    display_name: str


def geocode(query):
    """Resolve a free-text US location to coordinates.

    `countrycodes=us` enforces the assessment's USA-only constraint at the
    source. Nominatim's usage policy requires the identifying User-Agent.
    """
    try:
        resp = _session.get(
            f'{settings.NOMINATIM_BASE_URL}/search',
            params={'q': query, 'format': 'jsonv2', 'countrycodes': 'us', 'limit': 1},
            headers={'User-Agent': settings.NOMINATIM_USER_AGENT},
            timeout=(3, 10),
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodingUnavailable(str(exc)) from exc

    if not results:
        raise LocationNotFound(query)
    top = results[0]
    return GeocodeResult(lat=float(top['lat']), lon=float(top['lon']), display_name=top['display_name'])
