"""One-time preprocessing: geocode the OPIS fuel-price CSV into data/stations_geocoded.csv.

Two tiers:
  1. US Census Batch Geocoder (street-level). Highway-exit addresses like
     "I-44, EXIT 283 & US-69" rarely match a street database, so misses are expected.
  2. City-centroid fallback from data/uscities.csv (GeoNames-derived). Truck stops sit
     at their town's highway exits, so a centroid lands well inside the 10-mile
     route corridor used at query time.

Stations that miss both tiers are dropped and logged - a fabricated coordinate
would silently corrupt fuel-stop selection.

Runtime never geocodes: the output CSV is committed to the repo.
"""

import csv
import io
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

RAW_CSV = settings.BASE_DIR / 'fuel-prices-for-be-assessment.csv'
CITIES_CSV = settings.BASE_DIR / 'data' / 'uscities.csv'
OUTPUT_CSV = settings.STATIONS_CSV

CENSUS_BATCH_URL = 'https://geocoding.geo.census.gov/geocoder/locations/addressbatch'
CENSUS_BATCH_SIZE = 2000
CENSUS_RETRIES = 3

CANADIAN_PROVINCES = {'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'}

CITY_ABBREVIATIONS = {'st': 'saint', 'st.': 'saint', 'ft': 'fort', 'ft.': 'fort', 'mt': 'mount', 'mt.': 'mount'}


def normalize_city(city):
    words = city.lower().replace('.', '. ').replace('-', ' ').split()
    return ' '.join(CITY_ABBREVIATIONS.get(w, w.rstrip('.')) for w in words)


def squash_city(city):
    """Spacing-insensitive form: 'De Forest' and 'DeForest' both -> 'deforest'."""
    return normalize_city(city).replace(' ', '')


def load_clean_stations():
    """Read the raw CSV, drop non-US rows, dedup by OPIS ID keeping the lowest price.

    Duplicate OPIS IDs are the same physical truck stop listed under different
    rack IDs / name variants; a cost-optimizing driver pays the lowest price there.
    """
    stations = {}
    dropped_foreign = 0
    with open(RAW_CSV, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            state = row['State'].strip()
            if state in CANADIAN_PROVINCES:
                dropped_foreign += 1
                continue
            opis_id = int(row['OPIS Truckstop ID'])
            price = float(row['Retail Price'])
            existing = stations.get(opis_id)
            if existing is None:
                stations[opis_id] = {
                    'opis_id': opis_id,
                    'name': row['Truckstop Name'].strip(),
                    'address': row['Address'].strip(),
                    'city': row['City'].strip(),
                    'state': state,
                    'price_per_gallon': price,
                }
            else:
                existing['price_per_gallon'] = min(existing['price_per_gallon'], price)
    return list(stations.values()), dropped_foreign


def load_city_centroids():
    exact, squashed = {}, {}
    with open(CITIES_CSV, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            coords = (float(row['lat']), float(row['lng']))
            exact[(normalize_city(row['city']), row['state'])] = coords
            squashed[(squash_city(row['city']), row['state'])] = coords
    return exact, squashed


class Command(BaseCommand):
    help = 'Geocode the raw fuel-price CSV into data/stations_geocoded.csv (run once).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-census', action='store_true',
            help='Skip the Census batch geocoder and use city centroids only (faster).',
        )

    def handle(self, *args, **options):
        if not CITIES_CSV.exists():
            raise CommandError(f'{CITIES_CSV} missing - see README for how it is produced.')

        stations, dropped_foreign = load_clean_stations()
        self.stdout.write(f'{len(stations)} unique US stations ({dropped_foreign} non-US rows dropped)')

        census_coords = {} if options['skip_census'] else self.census_geocode(stations)
        centroids, centroids_squashed = load_city_centroids()

        rows, dropped = [], []
        for s in stations:
            if s['opis_id'] in census_coords:
                s['lat'], s['lon'] = census_coords[s['opis_id']]
                s['geocode_source'] = 'census'
            else:
                centroid = centroids.get(
                    (normalize_city(s['city']), s['state'])
                ) or centroids_squashed.get((squash_city(s['city']), s['state']))
                if centroid is None:
                    dropped.append(s)
                    continue
                s['lat'], s['lon'] = centroid
                s['geocode_source'] = 'city_centroid'
            rows.append(s)

        OUTPUT_CSV.parent.mkdir(exist_ok=True)
        fields = ['opis_id', 'name', 'address', 'city', 'state', 'lat', 'lon', 'price_per_gallon', 'geocode_source']
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: s[k] for k in fields} for s in rows)

        n_census = sum(1 for s in rows if s['geocode_source'] == 'census')
        self.stdout.write(self.style.SUCCESS(
            f'Wrote {len(rows)} stations to {OUTPUT_CSV} '
            f'(census: {n_census}, city_centroid: {len(rows) - n_census}, dropped: {len(dropped)})'
        ))
        for s in dropped:
            self.stdout.write(f'  dropped (no centroid): {s["opis_id"]} {s["name"]} - {s["city"]}, {s["state"]}')

    def census_geocode(self, stations):
        """Batch-geocode via the Census API; returns {opis_id: (lat, lon)} for matches."""
        coords = {}
        chunks = [stations[i:i + CENSUS_BATCH_SIZE] for i in range(0, len(stations), CENSUS_BATCH_SIZE)]
        for n, chunk in enumerate(chunks, 1):
            buf = io.StringIO()
            writer = csv.writer(buf)
            for s in chunk:
                writer.writerow([s['opis_id'], s['address'], s['city'], s['state'], ''])
            for attempt in range(1, CENSUS_RETRIES + 1):
                try:
                    resp = requests.post(
                        CENSUS_BATCH_URL,
                        data={'benchmark': 'Public_AR_Current'},
                        files={'addressFile': ('addresses.csv', buf.getvalue())},
                        timeout=300,
                    )
                    resp.raise_for_status()
                    break
                except requests.RequestException as exc:
                    self.stdout.write(self.style.WARNING(f'batch {n} attempt {attempt} failed: {exc}'))
                    if attempt == CENSUS_RETRIES:
                        self.stdout.write(self.style.WARNING(f'batch {n} skipped; fallback will cover it'))
                        resp = None
                    else:
                        time.sleep(5 * attempt)
            if resp is None:
                continue
            matched = 0
            # Response rows: id, input address, Match/No_Match/Tie, Exact/Non_Exact,
            # matched address, "lon,lat", tigerline id, side
            for row in csv.reader(io.StringIO(resp.text)):
                if len(row) >= 6 and row[2] == 'Match' and row[5]:
                    lon, lat = map(float, row[5].split(','))
                    coords[int(row[0])] = (lat, lon)
                    matched += 1
            self.stdout.write(f'census batch {n}/{len(chunks)}: {matched}/{len(chunk)} matched')
        return coords
