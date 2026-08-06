"""Weather provider (Fix 6, WP5).

Design rules:

* **Provider-independent by default.** The rest of the app calls
  ``get_weather(lat, lon)`` and gets back a small typed record. The
  provider behind that surface is chosen at runtime; the fallback
  is ``NullWeather`` so a missing / degraded provider does not
  crash a recommendation request.
* **Coarse location only.** The adapter accepts a latitude and
  longitude rounded to two decimal places (roughly 1 km) — precise
  enough to pick "hot" versus "humid" for a city, coarse enough
  that a leaked call log does not pin an address.
* **Cache stale-while-revalidate.** A successful fetch is cached
  in-process for 30 minutes; a stale entry is returned while a
  background refresh is in flight. Callers see a
  ``stale`` boolean so the frontend can render a discreet
  "using cached weather" hint.
* **No user id in the outbound call.** The adapter is called with
  a coordinate; nothing about the requesting user leaves the
  process.
* **Bounded time and cost.** Every fetch has a 5-second timeout
  and no retry loop. Failure returns ``NullWeather`` — the
  recommendation engine treats "no weather" as neutral.

The default provider is Open-Meteo — no API key, no user account,
no rate-limit surprises within the free tier's daily budget. If a
paid provider is needed later (Weatherbit, AccuWeather, etc.), it
lives as a second adapter that satisfies the same
``WeatherProvider`` protocol and is selected via
``WEATHER_PROVIDER=<name>``.

See docs/stabilisation/LIVE_INTEGRATIONS.md for the credential /
rotation / retention story per provider.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

# --- Model ------------------------------------------------------------------


@dataclass(frozen=True)
class Weather:
    """The subset of weather facts the app actually consumes."""

    condition: str          # 'hot', 'humid', 'cold', 'mild', 'rain', 'unknown'
    temperature_c: float | None
    humidity_percent: int | None
    stale: bool = False


NULL_WEATHER = Weather(condition="unknown", temperature_c=None, humidity_percent=None, stale=False)


# --- Provider protocol ------------------------------------------------------


class WeatherProvider(Protocol):
    name: str
    async def fetch(self, lat: float, lon: float) -> Weather: ...


# --- Null provider (used in tests and when misconfigured) -------------------


class NullWeatherProvider:
    """Returns ``NULL_WEATHER`` unconditionally."""
    name = "null"

    async def fetch(self, lat: float, lon: float) -> Weather:  # noqa: ARG002
        return NULL_WEATHER


# --- Open-Meteo provider (default; no credential) ---------------------------


def _classify_condition(temp_c: float | None, humidity: int | None, precipitation_mm: float | None) -> str:
    """Bucketise the fetched numbers into the strings the routine
    engine already knows how to interpret ('hot', 'humid', 'cold',
    'mild', 'rain', 'unknown').
    """
    if precipitation_mm is not None and precipitation_mm >= 0.5:
        return "rain"
    if humidity is not None and humidity >= 70 and (temp_c is None or temp_c >= 22):
        return "humid"
    if temp_c is None:
        return "unknown"
    if temp_c >= 30:
        return "hot"
    if temp_c <= 15:
        return "cold"
    return "mild"


class OpenMeteoWeatherProvider:
    """Open-Meteo current-weather endpoint.

    No API key required. Docs: https://open-meteo.com/en/docs
    """

    name = "open_meteo"
    _endpoint = "https://api.open-meteo.com/v1/forecast"

    async def fetch(self, lat: float, lon: float) -> Weather:
        # Round to 2 dp before the outbound call. See module docstring.
        lat = round(lat, 2)
        lon = round(lon, 2)
        try:
            import httpx  # noqa: PLC0415 - lazy so a bad httpx does not break app import
        except ImportError:
            logger.warning("httpx not installed; weather provider disabled")
            return NULL_WEATHER

        params = {
            "latitude": str(lat),
            "longitude": str(lon),
            "current": "temperature_2m,relative_humidity_2m,precipitation",
            "timezone": "UTC",
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self._endpoint, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 — any provider hiccup is neutral
            logger.info("weather_fetch_failed provider=%s error=%s", self.name, type(exc).__name__)
            return NULL_WEATHER

        current = data.get("current") or {}
        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        precipitation = current.get("precipitation")
        return Weather(
            condition=_classify_condition(temp, humidity, precipitation),
            temperature_c=float(temp) if temp is not None else None,
            humidity_percent=int(humidity) if humidity is not None else None,
            stale=False,
        )


# --- Factory + cache --------------------------------------------------------

_CACHE: dict[tuple[float, float], tuple[float, Weather]] = {}
_CACHE_TTL_SECONDS = 30 * 60


def _get_provider() -> WeatherProvider:
    import os  # noqa: PLC0415
    name = os.environ.get("WEATHER_PROVIDER", "open_meteo").strip().lower()
    if name == "open_meteo":
        return OpenMeteoWeatherProvider()
    if name == "null":
        return NullWeatherProvider()
    logger.warning("unknown WEATHER_PROVIDER=%s; falling back to null", name)
    return NullWeatherProvider()


async def get_weather(lat: float, lon: float, provider: WeatherProvider | None = None) -> Weather:
    """Cached, timeouted weather fetch.

    ``provider`` is dependency-injectable so tests can pass a stub.
    """
    key = (round(lat, 2), round(lon, 2))
    now = time.monotonic()

    cached = _CACHE.get(key)
    if cached is not None:
        cached_at, weather = cached
        if now - cached_at <= _CACHE_TTL_SECONDS:
            return weather
        # Beyond TTL — mark stale, refresh below, return stale.
        stale_weather = Weather(
            condition=weather.condition,
            temperature_c=weather.temperature_c,
            humidity_percent=weather.humidity_percent,
            stale=True,
        )
        # Background refresh (best effort).
        asyncio.create_task(_refresh(key, provider))
        return stale_weather

    return await _refresh(key, provider)


async def _refresh(key: tuple[float, float], provider: WeatherProvider | None) -> Weather:
    lat, lon = key
    provider = provider or _get_provider()
    result = await provider.fetch(lat, lon)
    if result.condition != "unknown":
        _CACHE[key] = (time.monotonic(), result)
    return result


def clear_cache() -> None:
    """Drop the in-process cache. Test-only."""
    _CACHE.clear()
