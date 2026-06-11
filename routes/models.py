from django.db import models


class FuelStation(models.Model):
    """A truck stop from the OPIS fuel price list, geocoded offline."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    lat = models.FloatField()
    lon = models.FloatField()
    price_per_gallon = models.FloatField()
    # 'census' (street-level match) or 'city_centroid' (fallback)
    geocode_source = models.CharField(max_length=20)

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price_per_gallon:.3f}/gal"
