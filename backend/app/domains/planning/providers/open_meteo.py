"""Small, deterministic Open-Meteo adapter for live environment context.

Only fixed provider hosts are used.  The transport is injectable so unit tests
never need the public network, and the adapter returns the existing planning
value objects rather than introducing another context model.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import (
    OPEN_METEO_API_KEY,
    OPEN_METEO_MODE,
    OPEN_METEO_TIMEOUT_SECONDS,
)
from app.domains.planning.providers.base import (
    AirQualityReading,
    ProviderUnavailable,
    WeatherReading,
)

PUBLIC_HOSTS = {
    "weather": "https://api.open-meteo.com/v1/forecast",
    "air": "https://air-quality-api.open-meteo.com/v1/air-quality",
    "geocode": "https://geocoding-api.open-meteo.com/v1/search",
}
# Commercial endpoints are fixed constants; callers cannot supply a URL.
COMMERCIAL_HOSTS = {
    "weather": "https://customer-api.open-meteo.com/v1/forecast",
    "air": "https://customer-air-quality-api.open-meteo.com/v1/air-quality",
    # Geocoding remains the documented public endpoint; customer billing
    # applies to the forecast and air-quality APIs, not this lookup service.
    "geocode": "https://geocoding-api.open-meteo.com/v1/search",
}

_WMO = {
    0: "clear", 1: "clear", 2: "partly_cloudy", 3: "cloudy",
    45: "foggy", 48: "foggy", 51: "rainy", 53: "rainy", 55: "rainy",
    56: "rainy", 57: "rainy", 61: "rainy", 63: "rainy", 65: "rainy",
    66: "rainy", 67: "rainy", 71: "cold", 73: "cold", 75: "cold",
    77: "cold", 80: "rainy", 81: "rainy", 82: "rainy", 85: "cold",
    86: "cold", 95: "stormy", 96: "stormy", 99: "stormy",
}


def _hosts() -> dict[str, str]:
    return COMMERCIAL_HOSTS if OPEN_METEO_MODE == "commercial" else PUBLIC_HOSTS


def location_identity(location: str | None) -> str | None:
    """Stable, private identity for the requested environmental target."""
    if not location or not location.strip():
        return None
    normalized = " ".join(location.casefold().split())
    return "loc:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResolvedLocation:
    identity: str
    latitude: float
    longitude: float
    display_name: str


def _number(values: Any, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return float(value) if isinstance(value, (int, float)) else None


def _weather_condition(code: int | None, low: float | None, high: float | None) -> str:
    if code is not None and code in _WMO:
        return _WMO[code]
    if high is not None and high >= 35:
        return "hot"
    if low is not None and low <= 15:
        return "cold"
    if high is not None and high >= 25:
        return "warm"
    if high is not None and high < 20:
        return "cool"
    return "mild"


def _aqi_category(value: int) -> str:
    if value <= 20:
        return "Good"
    if value <= 40:
        return "Fair"
    if value <= 60:
        return "Moderate"
    if value <= 80:
        return "Poor"
    if value <= 100:
        return "Very Poor"
    return "Extremely Poor"


class OpenMeteoProvider:
    name = "open_meteo"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float | None = None) -> None:
        self._transport = transport
        self._timeout = timeout if timeout is not None else OPEN_METEO_TIMEOUT_SECONDS

    def is_configured(self) -> bool:
        return OPEN_METEO_MODE in {"evaluation", "commercial"} and (
            OPEN_METEO_MODE != "commercial" or bool(OPEN_METEO_API_KEY)
        )

    async def _get(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured():
            raise ProviderUnavailable("Live environment data is not enabled.", provider=self.name, reason="not_configured")
        headers = {"Accept": "application/json"}
        request_params = dict(params)
        if OPEN_METEO_MODE == "commercial" and kind in {"weather", "air"}:
            # Open-Meteo customer endpoints authenticate with the documented
            # query parameter. Never include the key in an exception or raw
            # snapshot payload.
            request_params["apikey"] = OPEN_METEO_API_KEY
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    response = await client.get(_hosts()[kind], params=request_params, headers=headers)
                if (response.status_code in {408, 425, 429} or response.status_code >= 500) and attempt == 0:
                    await asyncio.sleep(0)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("response is not an object")
                return payload
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0)
                    continue
                raise ProviderUnavailable("Live environment data is temporarily unavailable.", provider=self.name, reason="provider_error") from exc
        raise ProviderUnavailable("Live environment data is temporarily unavailable.", provider=self.name, reason="provider_error")

    async def resolve_location(self, location: str) -> ResolvedLocation:
        query = location.strip()
        if not query:
            raise ProviderUnavailable("A city is needed for live environment data.", provider=self.name, reason="location_unresolved")
        payload = await self._get("geocode", {"name": query, "count": 5, "language": "en", "countryCode": "IN"})
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise ProviderUnavailable("That location could not be resolved.", provider=self.name, reason="location_unresolved")
        indian = [r for r in results if isinstance(r, dict) and r.get("country_code") == "IN"]
        if len(indian) != 1:
            raise ProviderUnavailable("That location could not be resolved unambiguously.", provider=self.name, reason="location_unresolved")
        row = indian[0]
        try:
            return ResolvedLocation(
                identity=location_identity(query) or "",
                latitude=float(row["latitude"]), longitude=float(row["longitude"]),
                display_name=str(row.get("name") or query),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("That location could not be resolved.", provider=self.name, reason="location_unresolved") from exc

    @staticmethod
    def _within_horizon(dates: list[date], days: int) -> list[date]:
        today = date.today()
        return sorted({day for day in dates if today <= day <= today + timedelta(days=days - 1)})

    async def forecast_resolved(self, *, target: ResolvedLocation, dates: list[date], timezone_name: str) -> list[WeatherReading]:
        if not dates:
            return []
        dates = self._within_horizon(dates, 16)
        if not dates:
            return []
        payload = await self._get("weather", {
            "latitude": target.latitude, "longitude": target.longitude, "timezone": timezone_name,
            "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max,relative_humidity_2m_mean,uv_index_max,weather_code",
            "forecast_days": min(16, max(1, (max(dates) - date.today()).days + 1)),
        })
        daily = payload.get("daily")
        if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
            raise ProviderUnavailable("Live weather data was malformed.", provider=self.name, reason="invalid_provider_response")
        output: list[WeatherReading] = []
        for index, raw_day in enumerate(daily["time"]):
            try:
                day = date.fromisoformat(str(raw_day))
            except ValueError:
                continue
            if day not in dates:
                continue
            low, high = _number(daily.get("temperature_2m_min"), index), _number(daily.get("temperature_2m_max"), index)
            precip, humidity = _number(daily.get("precipitation_probability_max"), index), _number(daily.get("relative_humidity_2m_mean"), index)
            code = _number(daily.get("weather_code"), index)
            output.append(WeatherReading(
                for_date=day, condition=_weather_condition(int(code) if code is not None else None, low, high),
                temp_min_c=low, temp_max_c=high,
                precipitation_chance=int(precip) if precip is not None else None,
                humidity=int(humidity) if humidity is not None else None,
                uv_index=_number(daily.get("uv_index_max"), index), location=target.display_name,
                provider=self.name, source="external_provider", attribution="Weather data · Open-Meteo",
            ))
        return output

    async def forecast(self, *, location: str | None, dates: list[date], timezone_name: str) -> list[WeatherReading]:
        target = await self.resolve_location(location or "")
        return await self.forecast_resolved(target=target, dates=dates, timezone_name=timezone_name)

    async def air_quality_resolved(self, *, target: ResolvedLocation, dates: list[date], timezone_name: str) -> list[AirQualityReading]:
        if not dates:
            return []
        dates = self._within_horizon(dates, 7)
        if not dates:
            return []
        payload = await self._get("air", {
            "latitude": target.latitude, "longitude": target.longitude, "timezone": timezone_name,
            "hourly": "european_aqi,european_aqi_pm2_5,european_aqi_pm10,pm2_5,pm10",
            "index_system": "european_aqi",
            "forecast_days": min(7, max(1, (max(dates) - date.today()).days + 1)),
        })
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise ProviderUnavailable("Live air-quality data was malformed.", provider=self.name, reason="invalid_provider_response")
        buckets: dict[date, list[int]] = {d: [] for d in dates}
        pollutant_aqi: dict[date, dict[str, list[int]]] = {d: {"pm2_5": [], "pm10": []} for d in dates}
        pm25: dict[date, list[float]] = {d: [] for d in dates}
        pm10: dict[date, list[float]] = {d: [] for d in dates}
        for index, raw_time in enumerate(hourly["time"]):
            try:
                day = date.fromisoformat(str(raw_time)[:10])
            except ValueError:
                continue
            aqi = _number(hourly.get("european_aqi"), index)
            if day in buckets and aqi is not None:
                buckets[day].append(int(aqi))
                for key in ("pm2_5", "pm10"):
                    pollutant_value = _number(hourly.get(f"european_aqi_{key}"), index)
                    if pollutant_value is not None:
                        pollutant_aqi[day][key].append(int(pollutant_value))
                for key, dest in (("pm2_5", pm25), ("pm10", pm10)):
                    value = _number(hourly.get(key), index)
                    if value is not None:
                        dest[day].append(value)
        output: list[AirQualityReading] = []
        for day in dates:
            if not buckets[day]:
                continue
            value = max(buckets[day])
            prominent = max(
                ((max(values), key) for key, values in pollutant_aqi[day].items() if values),
                default=(None, None),
            )[1]
            output.append(AirQualityReading(
                for_date=day, aqi=value, index_system="european_aqi", category=_aqi_category(value),
                location=target.display_name, prominent_pollutant=prominent,
                pm2_5=max(pm25[day]) if pm25[day] else None,
                pm10=max(pm10[day]) if pm10[day] else None, provider=self.name,
                source="external_provider", attribution="Air quality · Open-Meteo / CAMS",
            ))
        return output

    async def air_quality(self, *, location: str | None, dates: list[date], timezone_name: str) -> list[AirQualityReading]:
        target = await self.resolve_location(location or "")
        return await self.air_quality_resolved(target=target, dates=dates, timezone_name=timezone_name)
