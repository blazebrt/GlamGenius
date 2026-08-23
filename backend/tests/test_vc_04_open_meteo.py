"""VC-04 provider contract tests; all HTTP is supplied by MockTransport."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.domains.planning.context import _resolve_air_quality_for_day, _resolve_weather_for_day
from app.domains.planning.providers import open_meteo
from app.domains.planning.providers.base import WeatherReading


@pytest.mark.asyncio
async def test_open_meteo_normalises_weather_and_european_aqi(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")

    day = date.today()

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}]})
        if "air-quality" in str(request.url):
            return httpx.Response(200, json={"hourly": {
                "time": [f"{day.isoformat()}T00:00"], "european_aqi": [35],
                "european_aqi_pm2_5": [35], "european_aqi_pm10": [20],
                "pm2_5": [12.0], "pm10": [20.0],
            }})
        return httpx.Response(200, json={"daily": {
            "time": [day.isoformat()], "temperature_2m_min": [20], "temperature_2m_max": [30],
            "precipitation_probability_max": [10], "relative_humidity_2m_mean": [60],
            "uv_index_max": [7], "weather_code": [1],
        }})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    weather = await provider.forecast(location="Bengaluru", dates=[day], timezone_name="Asia/Kolkata")
    air = await provider.air_quality(location="Bengaluru", dates=[day], timezone_name="Asia/Kolkata")

    assert weather[0].condition == "clear"
    assert weather[0].provider == "open_meteo"
    assert weather[0].source == "external_provider"
    assert weather[0].attribution == "Weather data · Open-Meteo"
    assert air[0].index_system == "european_aqi"
    assert air[0].category == "Fair"
    assert air[0].prominent_pollutant == "pm2_5"
    assert air[0].attribution == "Air quality · Open-Meteo / CAMS"


@pytest.mark.asyncio
async def test_open_meteo_rejects_ambiguous_location(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))
    provider = open_meteo.OpenMeteoProvider(transport=transport)
    with pytest.raises(open_meteo.ProviderUnavailable) as error:
        await provider.resolve_location("unknown venue")
    assert error.value.reason == "location_unresolved"


@pytest.mark.asyncio
async def test_open_meteo_enforces_weather_16_day_and_aqi_7_day_horizons(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    def handler(request: httpx.Request) -> httpx.Response:
        if "air-quality" in str(request.url):
            return httpx.Response(200, json={"hourly": {"time": [date.today().isoformat() + "T00:00"], "european_aqi": [20]}})
        return httpx.Response(200, json={"daily": {"time": [date.today().isoformat()], "temperature_2m_min": [20], "temperature_2m_max": [25], "weather_code": [1]}})

    transport = httpx.MockTransport(handler)
    provider = open_meteo.OpenMeteoProvider(transport=transport)
    target = open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru")
    assert date.today() + timedelta(days=15) in provider._within_horizon([date.today() + timedelta(days=15)], 16)
    assert date.today() + timedelta(days=16) not in provider._within_horizon([date.today() + timedelta(days=16)], 16)
    assert date.today() + timedelta(days=6) in provider._within_horizon([date.today() + timedelta(days=6)], 7)
    assert date.today() + timedelta(days=7) not in provider._within_horizon([date.today() + timedelta(days=7)], 7)


@pytest.mark.asyncio
async def test_open_meteo_commercial_uses_apikey_query_parameter(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_API_KEY", "test-key-never-persisted")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}]})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    await provider._get("weather", {})
    assert seen[0].url.params.get("apikey") == "test-key-never-persisted"
    assert "customer-api.open-meteo.com" in str(seen[0].url)
    assert "authorization" not in {key.lower() for key in seen[0].headers}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "server", "malformed"])
async def test_open_meteo_failures_are_sanitized(monkeypatch, failure):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_API_KEY", "secret-not-for-errors")

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream timeout", request=request)
        if failure == "server":
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"unexpected": []}, request=request)

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(open_meteo.ProviderUnavailable) as error:
        await provider.resolve_location("Bengaluru")
    assert "secret-not-for-errors" not in str(error.value)
    assert error.value.reason in {"provider_error", "location_unresolved", "invalid_provider_response"}


@pytest.mark.asyncio
async def test_weather_and_aqi_precedence_helpers_are_independent():
    now = datetime.now(UTC)
    day = date.today()
    manual_weather = WeatherReading(for_date=day, condition="rainy")
    cached_air = SimpleNamespace(
        id=uuid4(), for_date=day, created_at=now, aqi=22, index_system="european_aqi",
        category="Fair", location="Bengaluru", prominent_pollutant=None, pm2_5=None, pm10=None,
    )
    weather, _, _ = await _resolve_weather_for_day(None, uuid4(), day, manual_weather, None, None, None, "Asia/Kolkata", None, now)
    air, _, _ = await _resolve_air_quality_for_day(None, uuid4(), day, None, cached_air, None, None, "Asia/Kolkata", "loc:test", now)
    assert weather is manual_weather
    assert air is not None and air.aqi == 22
