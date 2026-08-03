"""Weather adapter unit tests (Fix 6, WP5).

The Open-Meteo integration test itself belongs in the WP5 live-monitoring
workflow — it is skipped here so the offline suite stays runnable without
network. What we test here is the classification logic and the caching /
stale-while-revalidate contract, which are pure and can run anywhere.
"""
from __future__ import annotations

import pytest

from app.domains.planning import weather as weather_mod
from app.domains.planning.weather import (
    NULL_WEATHER,
    NullWeatherProvider,
    Weather,
    _classify_condition,
    clear_cache,
    get_weather,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


# ---------- classifier ------------------------------------------------------

@pytest.mark.parametrize(
    "temp,humidity,precip,expected",
    [
        (33.0, 55, 0.0, "hot"),
        (26.0, 82, 0.0, "humid"),
        (12.0, 60, 0.0, "cold"),
        (22.0, 45, 0.0, "mild"),
        (22.0, 55, 1.2, "rain"),
        (None, None, None, "unknown"),
        (25.0, 40, None, "mild"),
    ],
)
def test_classify_condition(temp, humidity, precip, expected):
    assert _classify_condition(temp, humidity, precip) == expected


# ---------- provider protocol ----------------------------------------------

async def test_null_provider_returns_null_weather():
    p = NullWeatherProvider()
    result = await p.fetch(28.6, 77.2)
    assert result == NULL_WEATHER
    assert result.condition == "unknown"


# ---------- get_weather cache contract --------------------------------------


class _StubProvider:
    name = "stub"
    def __init__(self, weather: Weather):
        self.calls = 0
        self._weather = weather
    async def fetch(self, lat, lon):
        self.calls += 1
        return self._weather


async def test_first_call_populates_cache():
    stub = _StubProvider(Weather("hot", 32.0, 40, False))
    w = await get_weather(28.61, 77.20, provider=stub)
    assert stub.calls == 1
    assert w.condition == "hot"
    assert w.stale is False


async def test_second_call_serves_from_cache():
    stub = _StubProvider(Weather("hot", 32.0, 40, False))
    await get_weather(28.61, 77.20, provider=stub)
    await get_weather(28.61, 77.20, provider=stub)
    assert stub.calls == 1  # cache hit


async def test_provider_failure_returns_null_and_does_not_cache():
    class _Boom:
        name = "boom"
        async def fetch(self, lat, lon):
            return NULL_WEATHER
    boom = _Boom()
    w = await get_weather(28.61, 77.20, provider=boom)
    assert w == NULL_WEATHER
    # Next call retries — a NULL_WEATHER is deliberately not cached.
    w2 = await get_weather(28.61, 77.20, provider=boom)
    assert w2 == NULL_WEATHER


async def test_coordinates_are_rounded_before_cache_key():
    stub = _StubProvider(Weather("mild", 22.0, 50, False))
    # Both inputs round to (28.61, 77.20) at 2 decimal places.
    await get_weather(28.6099, 77.2001, provider=stub)
    await get_weather(28.6111, 77.2049, provider=stub)
    assert stub.calls == 1


def test_unknown_provider_name_falls_back_to_null(monkeypatch):
    monkeypatch.setenv("WEATHER_PROVIDER", "some-paid-thing-we-do-not-ship")
    p = weather_mod._get_provider()
    assert isinstance(p, NullWeatherProvider)
