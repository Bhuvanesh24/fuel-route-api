"""Load data/stations_geocoded.csv into the FuelStation table. Idempotent."""

import csv

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction

from routes.models import FuelStation


class Command(BaseCommand):
    help = 'Import geocoded stations from data/stations_geocoded.csv (replaces existing rows).'

    def handle(self, *args, **options):
        path = settings.STATIONS_CSV
        if not path.exists():
            raise CommandError(f'{path} missing - run `manage.py geocode_stations` first.')

        with open(path, newline='', encoding='utf-8') as f:
            stations = [
                FuelStation(
                    opis_id=int(row['opis_id']),
                    name=row['name'],
                    address=row['address'],
                    city=row['city'],
                    state=row['state'],
                    lat=float(row['lat']),
                    lon=float(row['lon']),
                    price_per_gallon=float(row['price_per_gallon']),
                    geocode_source=row['geocode_source'],
                )
                for row in csv.DictReader(f)
            ]

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(f'Imported {len(stations)} fuel stations'))
