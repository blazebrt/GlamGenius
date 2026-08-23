"""VC-04 provider contract tests; all HTTP is supplied by MockTransport."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.domains.planning.context import DayContext, _note_gaps, _resolve_air_quality_for_day, _resolve_weather_for_day
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
async def test_open_meteo_commercial_geocoding_uses_customer_endpoint_and_apikey(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_API_KEY", "geocode-key-never-persisted")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}]})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    await provider.resolve_location("Bengaluru")
    assert "customer-geocoding-api.open-meteo.com" in str(seen[0].url)
    assert seen[0].url.params.get("apikey") == "geocode-key-never-persisted"
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
    assert "secret-not-for-errors" not in repr(error.value)
    chain = error.value
    while chain is not None:
        assert "secret-not-for-errors" not in repr(chain)
        chain = chain.__cause__
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert error.value.reason in {"provider_error", "location_unresolved", "invalid_provider_response"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"daily": {"time": ["2026-08-23"]}},
        {"daily": {"time": ["2026-08-23"], "temperature_2m_min": [20], "temperature_2m_max": [25], "precipitation_probability_max": [10], "relative_humidity_2m_mean": [60], "uv_index_max": [7], "weather_code": []}},
        {"daily": {"time": ["2026-08-23"], "temperature_2m_min": ["20"], "temperature_2m_max": [25], "precipitation_probability_max": [10], "relative_humidity_2m_mean": [60], "uv_index_max": [7], "weather_code": [1]}},
        {"daily": {"time": ["not-a-date"], "temperature_2m_min": [20], "temperature_2m_max": [25], "precipitation_probability_max": [10], "relative_humidity_2m_mean": [60], "uv_index_max": [7], "weather_code": [1]}},
    ],
)
async def test_open_meteo_rejects_malformed_weather_payloads(monkeypatch, payload):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: date(2026, 8, 23))
    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    target = open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru")
    with pytest.raises(open_meteo.ProviderUnavailable) as error:
        await provider.forecast_resolved(target=target, dates=[date(2026, 8, 23)], timezone_name="Asia/Kolkata")
    assert error.value.reason == "invalid_provider_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"hourly": {"time": ["2026-08-23T00:00"]}},
        {"hourly": {"time": ["2026-08-23T00:00"], "european_aqi": [20], "european_aqi_pm2_5": [20], "european_aqi_pm10": [20], "pm2_5": [12], "pm10": []}},
        {"hourly": {"time": ["2026-08-23T00:00"], "european_aqi": ["20"], "european_aqi_pm2_5": [20], "european_aqi_pm10": [20], "pm2_5": [12], "pm10": [20]}},
        {"hourly": {"time": ["not-a-date"], "european_aqi": [20], "european_aqi_pm2_5": [20], "european_aqi_pm10": [20], "pm2_5": [12], "pm10": [20]}},
    ],
)
async def test_open_meteo_rejects_malformed_aqi_payloads(monkeypatch, payload):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: date(2026, 8, 23))
    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    target = open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru")
    with pytest.raises(open_meteo.ProviderUnavailable) as error:
        await provider.air_quality_resolved(target=target, dates=[date(2026, 8, 23)], timezone_name="Asia/Kolkata")
    assert error.value.reason == "invalid_provider_response"


@pytest.mark.asyncio
async def test_open_meteo_rejects_genuine_ambiguous_indian_location(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [
        {"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"},
        {"country_code": "IN", "latitude": 13.08, "longitude": 80.27, "name": "Bengaluru East"},
    ]}))
    with pytest.raises(open_meteo.ProviderUnavailable) as error:
        await open_meteo.OpenMeteoProvider(transport=transport).resolve_location("Bengaluru")
    assert error.value.reason == "location_unresolved"


def test_open_meteo_horizon_uses_planning_timezone(monkeypatch):
    today_by_timezone = {"Asia/Kolkata": date(2026, 8, 24), "UTC": date(2026, 8, 23)}
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: today_by_timezone[timezone_name])
    assert open_meteo.OpenMeteoProvider._within_horizon([date(2026, 9, 8)], 16, "Asia/Kolkata") == [date(2026, 9, 8)]
    assert open_meteo.OpenMeteoProvider._within_horizon([date(2026, 9, 9)], 16, "Asia/Kolkata") == []
    assert open_meteo.OpenMeteoProvider._within_horizon([date(2026, 8, 30)], 7, "Asia/Kolkata") == [date(2026, 8, 30)]
    assert open_meteo.OpenMeteoProvider._within_horizon([date(2026, 8, 31)], 7, "Asia/Kolkata") == []


def test_environment_reason_codes_are_not_customer_copy():
    context = DayContext(
        account_id=uuid4(), plan_date=date(2026, 8, 23), timezone_name="Asia/Kolkata",
        now_local=datetime(2026, 8, 23, tzinfo=UTC),
        weather_unavailable_reason="environment_location_unresolved",
    )
    _note_gaps(context)
    assert context.missing_information
    assert "environment_location_unresolved" not in context.missing_information[0]
    assert "weather" in context.missing_information[0].lower()


@pytest.mark.asyncio
async def test_weather_stale_fallback_does_not_hide_independent_aqi_failure():
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    stale_weather = SimpleNamespace(
        id=uuid4(), for_date=date(2026, 8, 23), created_at=now - timedelta(hours=2),
        condition="rainy", temp_min_c=20, temp_max_c=25, precipitation_chance=50,
        humidity=70, uv_index=4, location="Bengaluru",
    )

    class FailedProvider:
        name = "open_meteo"

        async def forecast_resolved(self, **_):
            raise open_meteo.ProviderUnavailable("upstream", provider=self.name, reason="provider_error")

        async def air_quality_resolved(self, **_):
            raise open_meteo.ProviderUnavailable("upstream", provider=self.name, reason="provider_error")

    weather, _, reason = await _resolve_weather_for_day(
        None, uuid4(), date(2026, 8, 23), None, stale_weather, FailedProvider(),
        open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru"),
        "Asia/Kolkata", "loc:test", now,
    )
    assert weather is not None and weather.is_stale is True
    assert reason == "provider_error"


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
