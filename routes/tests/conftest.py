import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    """LocMemCache survives across tests in-process; isolate every test."""
    cache.clear()
    yield
    cache.clear()
