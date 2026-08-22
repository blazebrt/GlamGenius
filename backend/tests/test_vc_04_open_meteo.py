"""VC-04 provider contract tests; all HTTP is supplied by MockTransport."""
from datetime import date

import httpx
import pytest
from app.domains.planning.providers import open_meteo


@pytest.mark.asyncio
async def test_open_meteo_normalises_weather_and_european_aqi(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}]})
        if "air-quality" in str(request.url):
            return httpx.Response(200, json={"hourly": {
                "time": ["2026-08-23T00:00"], "european_aqi": [35],
                "european_aqi_pm2_5": [35], "european_aqi_pm10": [20],
                "pm2_5": [12.0], "pm10": [20.0],
            }})
        return httpx.Response(200, json={"daily": {
            "time": ["2026-08-23"], "temperature_2m_min": [20], "temperature_2m_max": [30],
            "precipitation_probability_max": [10], "relative_humidity_2m_mean": [60],
            "uv_index_max": [7], "weather_code": [1],
        }})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    weather = await provider.forecast(location="Bengaluru", dates=[date(2026, 8, 23)], timezone_name="Asia/Kolkata")
    air = await provider.air_quality(location="Bengaluru", dates=[date(2026, 8, 23)], timezone_name="Asia/Kolkata")

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
