# Fuel-Optimal Route API

Django API that plans a driving route between two US locations and picks the
**cost-optimal fuel stops** along it for a truck with a 500-mile tank at
10 MPG, using the provided OPIS fuel-price list (6,626 US truck stops).

Built with **Django 6.0.6** (latest stable).

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py import_stations        # loads data/stations_geocoded.csv (committed)
python manage.py runserver
```

```bash
curl "http://localhost:8000/api/route/?start=Los+Angeles,+CA&finish=New+York,+NY"
```

Then open the `map_url` from the response in a browser, e.g.
`http://localhost:8000/map/?start=Los+Angeles,+CA&finish=New+York,+NY`

## API

### `GET /api/route/`

| Param | Description |
|---|---|
| `start`, `finish` | Free-text US locations (`"Denver, CO"`, `"1600 Pennsylvania Ave"`) |
| `start_lat`/`start_lon`, `finish_lat`/`finish_lon` | Alternative coordinate input (skips geocoding → 1 external call) |

Response (abridged — LA → NY):

```json
{
  "start":  {"query": "Los Angeles, CA", "display_name": "Los Angeles, …", "lat": 34.05, "lon": -118.24},
  "finish": {"query": "New York, NY", "display_name": "New York, …", "lat": 40.71, "lon": -74.0},
  "route":  {"distance_miles": 2793.6, "duration_hours": 49.9,
             "geometry": "<encoded polyline>", "geometry_format": "polyline5"},
  "fuel_plan": {
    "mpg": 10.0, "tank_range_miles": 500.0,
    "stops": [
      {"name": "Maverik #674", "city": "Las Vegas", "state": "NV",
       "price_per_gallon": 3.2823, "route_position_miles": 273.1, "detour_miles": 2.4,
       "gallons_purchased": 27.31, "cost": 89.63, "lat": 36.19, "lon": -115.11, "...": "..."}
    ],
    "total_gallons_purchased": 229.36,
    "total_fuel_cost": 693.13,
    "assumptions": ["Vehicle departs with a full 500-mile tank; …"]
  },
  "map_url": "/map/?start=Los+Angeles%2C+CA&finish=New+York%2C+NY",
  "external_api_calls": 3
}
```

Errors: `400` (missing/invalid params, unknown location, no drivable route),
`422` (a stretch of the route has no station within tank range),
`502` (upstream map service down).

### `GET /map/`

Same parameters; renders an interactive Leaflet map of the route with the
start/finish, every fuel stop (colored cheap→pricey, popups with price/gallons/
cost), and a total-cost banner. It reuses the cached plan from the API call,
so it makes **no additional** routing calls.

## External API budget

The assessment asks for as few map/routing calls as possible. Per request:

| Scenario | Calls |
|---|---|
| Fresh request, free-text locations | **3** (2 Nominatim geocodes + 1 OSRM route) |
| Coordinates given, or geocodes cached (24 h) | **1** (OSRM route only) |
| Identical query within 10 min (plan cache) | **0** |

The response reports its own `external_api_calls`. Station coordinates never
cost a runtime call — see below.

## How it works

1. **Offline geocoding (one-time, output committed).** The OPIS CSV has no
   coordinates, and addresses like `"I-44, EXIT 283 & US-69"` aren't street
   addresses. `manage.py geocode_stations` dedups the list (8,151 rows → 6,626
   unique US stations; duplicate OPIS IDs keep their lowest price; non-US rows
   dropped), then geocodes in two tiers: the free **US Census batch geocoder**
   (526 street-level matches) and a **city-centroid fallback** built from the
   GeoNames US postal-code dataset (6,100 stations). Truck stops sit at their
   town's highway exits, so a centroid lands well inside the corridor
   tolerance. Each row records its `geocode_source`. Zero stations dropped.
2. **Routing.** One OSRM call returns the route geometry (polyline5) and
   distance. Nominatim resolves free-text inputs (`countrycodes=us` enforces
   the USA-only requirement).
3. **Corridor search** (`routes/services/stations.py`). All stations live in
   numpy arrays in memory (loaded once per process). The route is downsampled
   to ~3-mile spacing; a padded bounding box prefilters stations; a vectorized
   haversine matrix finds every station within **10 miles** of the route and
   its position along it. Sub-millisecond for coast-to-coast routes.
4. **Fuel optimization** (`routes/services/fuel_optimizer.py`). The classic
   provably-optimal greedy for the fixed-MPG refueling problem:
   *at each station, if a cheaper station (or the finish) is reachable on a
   full tank, buy just enough fuel to get there; otherwise fill up and drive
   to the cheapest station in reach.* You can see it behave on the map -
   small top-ups bridge into cheap-fuel regions, full fill-ups happen at the
   cheapest stations.

## Assumptions

- The vehicle departs with a **full** 500-mile tank; the reported cost is fuel
  purchased en route (a trip ≤ 500 miles needs no stops and costs $0).
- Fixed 10 MPG; detour mileage to stations (all ≤ 10 miles off-route) is not
  added to consumption.
- Duplicate OPIS listings are one physical stop; the lowest listed price wins.
- These are restated in the response's `assumptions` field.

## Design notes

- **No DRF** - deliberately. One read-only GET endpoint with two query params
  doesn't justify serializers/viewsets; plain `JsonResponse` keeps the
  dependency list at six packages.
- **SQLite + in-memory index.** Stations are a Django model (imported via
  management command), but queries never scan the DB: a process-wide numpy
  index serves the corridor math.
- `OSRM_BASE_URL` / `NOMINATIM_BASE_URL` are env-overridable in case the
  public demo servers are unavailable; upstream failures return a clean 502.
- `DEBUG=True` and `ALLOWED_HOSTS=['*']` are assessment conveniences, not
  production settings.

## Testing

```bash
pytest        # 27 tests, no network needed
```

- `test_optimizer.py` - the greedy on synthetic routes: buy-just-enough
  behavior, fill-ups, boundary cases, coverage gaps, gallons invariant.
- `test_corridor.py` - corridor inclusion/exclusion, projection, bbox edges.
- `test_api.py` - views with mocked Nominatim/OSRM: schema, call budget,
  cache hits, every error status, the import command.

## Data attribution

- Fuel prices: assessment-provided OPIS truckstop list (`fuel-prices-for-be-assessment.csv`).
- City centroids: derived from the [GeoNames](https://www.geonames.org/)
  US postal-code dataset (CC BY 4.0), aggregated per city into `data/uscities.csv`.
- Routing/geocoding: [OSRM](http://project-osrm.org/) demo server and
  [Nominatim](https://nominatim.org/) (© OpenStreetMap contributors).
