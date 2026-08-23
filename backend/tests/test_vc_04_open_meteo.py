"""VC-04 provider contract tests; all HTTP is supplied by MockTransport."""
import logging
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.domains.planning.context import DayContext, _note_gaps, _resolve_air_quality_for_day, _resolve_weather_for_day
from app.domains.planning.providers import open_meteo
from app.domains.planning.providers.base import AirQualityReading, WeatherReading
from app.shared.observability.sentry_privacy import scrub_event


@pytest.mark.asyncio
async def test_open_meteo_normalises_weather_and_european_aqi(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    day = date(2026, 8, 23)
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: day)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "geocoding" in str(request.url):
            return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}]})
        if "air-quality" in str(request.url):
            return httpx.Response(200, json={"hourly": {
                "time": [f"{day.isoformat()}T00:00"], "european_aqi": [35],
                "european_aqi_pm2_5": [35], "european_aqi_pm10": [20],
                "european_aqi_nitrogen_dioxide": [15], "european_aqi_ozone": [10],
                "european_aqi_sulphur_dioxide": [5],
                "pm2_5": [12.0], "pm10": [20.0],
            }})
        return httpx.Response(200, json={"daily": {
            "time": [day.isoformat()], "temperature_2m_min": [20], "temperature_2m_max": [30],
            "precipitation_probability_max": [10], "uv_index_max": [7], "weather_code": [1],
        }, "hourly": {
            "time": [f"{day.isoformat()}T00:00", f"{day.isoformat()}T01:00"],
            "relative_humidity_2m": [50, 70],
        }})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    weather = await provider.forecast(location="Bengaluru", dates=[day], timezone_name="Asia/Kolkata")
    air = await provider.air_quality(location="Bengaluru", dates=[day], timezone_name="Asia/Kolkata")

    assert weather[0].condition == "clear"
    assert weather[0].humidity == 60
    assert weather[0].provider == "open_meteo"
    assert weather[0].source == "external_provider"
    assert weather[0].attribution == "Weather data · Open-Meteo"
    assert air[0].index_system == "european_aqi"
    assert air[0].category == "Fair"
    assert air[0].prominent_pollutant == "pm2_5"
    assert air[0].attribution == "Air quality · Open-Meteo / CAMS"
    weather_request = next(request for request in seen if "forecast" in str(request.url))
    assert set(weather_request.url.params.keys()) == {"latitude", "longitude", "timezone", "daily", "hourly", "forecast_days"}
    assert weather_request.url.params["daily"] == "temperature_2m_min,temperature_2m_max,precipitation_probability_max,uv_index_max,weather_code"
    assert weather_request.url.params["hourly"] == "relative_humidity_2m"
    air_request = next(request for request in seen if "air-quality" in str(request.url))
    assert set(air_request.url.params.keys()) == {"latitude", "longitude", "timezone", "hourly", "forecast_days"}
    assert air_request.url.params["hourly"] == "european_aqi,european_aqi_pm2_5,european_aqi_pm10,european_aqi_nitrogen_dioxide,european_aqi_ozone,european_aqi_sulphur_dioxide,pm2_5,pm10"
    assert "index_system" not in air_request.url.params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"pm2_5": 35, "pm10": 20, "nitrogen_dioxide": 15, "ozone": 10, "sulphur_dioxide": 5}, "pm2_5"),
        ({"pm2_5": 10, "pm10": 20, "nitrogen_dioxide": 15, "ozone": 25, "sulphur_dioxide": 5}, "ozone"),
        ({"pm2_5": 10, "pm10": 20, "nitrogen_dioxide": 30, "ozone": 25, "sulphur_dioxide": 5}, "nitrogen_dioxide"),
        ({"pm2_5": 10, "pm10": 20, "nitrogen_dioxide": 15, "ozone": 25, "sulphur_dioxide": 35}, "sulphur_dioxide"),
        ({"pm2_5": 10, "pm10": 40, "nitrogen_dioxide": 15, "ozone": 25, "sulphur_dioxide": 5}, "pm10"),
        ({"pm2_5": 30, "pm10": 30, "nitrogen_dioxide": 15, "ozone": 25, "sulphur_dioxide": 5}, "pm2_5"),
    ],
)
async def test_open_meteo_prominent_pollutant_uses_all_european_aqi_constituents(monkeypatch, values, expected):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    day = date(2026, 8, 23)
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: day)

    def handler(request: httpx.Request) -> httpx.Response:
        if "air-quality" not in str(request.url):
            return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}]})
        return httpx.Response(200, json={"hourly": {
            "time": [f"{day.isoformat()}T00:00"], "european_aqi": [max(values.values())],
            "european_aqi_pm2_5": [values["pm2_5"]], "european_aqi_pm10": [values["pm10"]],
            "european_aqi_nitrogen_dioxide": [values["nitrogen_dioxide"]],
            "european_aqi_ozone": [values["ozone"]],
            "european_aqi_sulphur_dioxide": [values["sulphur_dioxide"]],
            "pm2_5": [12.0], "pm10": [20.0],
        }})

    reading = await open_meteo.OpenMeteoProvider(
        transport=httpx.MockTransport(handler),
    ).air_quality(location="Bengaluru", dates=[day], timezone_name="Asia/Kolkata")
    assert reading[0].prominent_pollutant == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [999, -1, 4])
async def test_open_meteo_rejects_unknown_weather_codes(monkeypatch, code):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    day = date(2026, 8, 23)
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: day)
    payload = {"daily": {
        "time": [day.isoformat()], "temperature_2m_min": [20], "temperature_2m_max": [35],
        "precipitation_probability_max": [10], "uv_index_max": [7], "weather_code": [code],
    }, "hourly": {"time": [f"{day.isoformat()}T00:00"], "relative_humidity_2m": [50]}}
    provider = open_meteo.OpenMeteoProvider(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    with pytest.raises(open_meteo.ProviderUnavailable) as error:
        await provider.forecast_resolved(
            target=open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru"),
            dates=[day], timezone_name="Asia/Kolkata",
        )
    assert error.value.reason == "invalid_provider_response"


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
    today = date(2026, 8, 23)
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: today)
    def handler(request: httpx.Request) -> httpx.Response:
        if "air-quality" in str(request.url):
            return httpx.Response(200, json={"hourly": {"time": [today.isoformat() + "T00:00"], "european_aqi": [20], "european_aqi_pm2_5": [20], "european_aqi_pm10": [20], "european_aqi_nitrogen_dioxide": [20], "european_aqi_ozone": [20], "european_aqi_sulphur_dioxide": [20], "pm2_5": [12], "pm10": [20]}})
        return httpx.Response(200, json={"daily": {"time": [today.isoformat()], "temperature_2m_min": [20], "temperature_2m_max": [25], "precipitation_probability_max": [10], "uv_index_max": [7], "weather_code": [1]}, "hourly": {"time": [today.isoformat() + "T00:00"], "relative_humidity_2m": [50]}})

    transport = httpx.MockTransport(handler)
    provider = open_meteo.OpenMeteoProvider(transport=transport)
    target = open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru")
    assert today + timedelta(days=15) in provider._within_horizon([today + timedelta(days=15)], 16, "Asia/Kolkata")
    assert today + timedelta(days=16) not in provider._within_horizon([today + timedelta(days=16)], 16, "Asia/Kolkata")
    assert today + timedelta(days=6) in provider._within_horizon([today + timedelta(days=6)], 7, "Asia/Kolkata")
    assert today + timedelta(days=7) not in provider._within_horizon([today + timedelta(days=7)], 7, "Asia/Kolkata")


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
async def test_open_meteo_commercial_air_uses_customer_endpoint_and_apikey(monkeypatch):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_API_KEY", "air-key-never-persisted")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    await provider._get("air", {})
    assert "customer-air-quality-api.open-meteo.com" in str(seen[0].url)
    assert seen[0].url.params.get("apikey") == "air-key-never-persisted"
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
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_open_meteo_does_not_retry_terminal_http_errors(monkeypatch, status):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_API_KEY", "terminal-key")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, request=request)

    with pytest.raises(open_meteo.ProviderUnavailable):
        await open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))._get("weather", {})
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "server"])
async def test_open_meteo_retries_transient_failures_once(monkeypatch, failure):
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(503, request=request)

    with pytest.raises(open_meteo.ProviderUnavailable):
        await open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))._get("weather", {})
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["success", "failure"])
async def test_commercial_api_key_is_redacted_from_logs_and_sentry(monkeypatch, caplog, failure):
    marker = "vc04-redaction-marker"
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_API_KEY", marker)

    def handler(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpx").info("HTTP Request: %s", request.url)
        if failure == "failure":
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    caplog.set_level(logging.INFO)
    url = "https://customer-api.open-meteo.com/v1/forecast?apikey=" + marker
    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    if failure == "failure":
        with pytest.raises(open_meteo.ProviderUnavailable):
            await provider._get("weather", {})
    else:
        await provider._get("weather", {})
    event = scrub_event({"message": url, "request": {"url": url}, "breadcrumbs": [{"message": url}]})
    assert marker not in caplog.text
    assert marker not in str(event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"daily": {"time": ["2026-08-23"]}},
        {"daily": {"time": ["2026-08-23"], "temperature_2m_min": [20], "temperature_2m_max": [25], "precipitation_probability_max": [10], "uv_index_max": [7], "weather_code": []}},
        {"daily": {"time": ["2026-08-23"], "temperature_2m_min": ["20"], "temperature_2m_max": [25], "precipitation_probability_max": [10], "uv_index_max": [7], "weather_code": [1]}},
        {"daily": {"time": ["not-a-date"], "temperature_2m_min": [20], "temperature_2m_max": [25], "precipitation_probability_max": [10], "uv_index_max": [7], "weather_code": [1]}},
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
async def test_environment_cache_fresh_expired_and_beyond_stale_are_independent():
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    day = date(2026, 8, 23)
    session = SimpleNamespace(added=[])
    session.add = session.added.append
    async def flush():
        return None
    session.flush = flush

    class Provider:
        name = "open_meteo"
        weather_calls = 0
        air_calls = 0

        async def forecast_resolved(self, **_):
            type(self).weather_calls += 1
            return [WeatherReading(for_date=day, condition="clear", temp_max_c=30)]

        async def air_quality_resolved(self, **_):
            type(self).air_calls += 1
            return [AirQualityReading(for_date=day, aqi=25, index_system="european_aqi", category="Fair")]

    provider = Provider()
    target = open_meteo.ResolvedLocation("loc:test", 12.97, 77.59, "Bengaluru")
    fresh_weather = SimpleNamespace(
        id=uuid4(), for_date=day, created_at=now, condition="rainy", temp_min_c=20,
        temp_max_c=25, precipitation_chance=50, humidity=70, uv_index=4, location="Bengaluru",
    )
    fresh_air = SimpleNamespace(
        id=uuid4(), for_date=day, created_at=now, aqi=22, index_system="european_aqi",
        category="Fair", location="Bengaluru", prominent_pollutant=None, pm2_5=None, pm10=None,
    )
    weather, _, _ = await _resolve_weather_for_day(
        session, uuid4(), day, None, fresh_weather, provider, target,
        "Asia/Kolkata", "loc:test", now,
    )
    air, _, _ = await _resolve_air_quality_for_day(
        session, uuid4(), day, None, fresh_air, provider, target,
        "Asia/Kolkata", "loc:test", now,
    )
    assert weather.condition == "rainy" and air.aqi == 22
    assert Provider.weather_calls == Provider.air_calls == 0

    expired_weather = SimpleNamespace(**{**vars(fresh_weather), "created_at": now - timedelta(hours=2)})
    expired_air = SimpleNamespace(**{**vars(fresh_air), "created_at": now - timedelta(hours=2)})
    await _resolve_weather_for_day(session, uuid4(), day, None, expired_weather, provider, target, "Asia/Kolkata", "loc:test", now)
    await _resolve_air_quality_for_day(session, uuid4(), day, None, expired_air, provider, target, "Asia/Kolkata", "loc:test", now)
    assert Provider.weather_calls == Provider.air_calls == 1

    class Down(Provider):
        async def forecast_resolved(self, **_):
            raise open_meteo.ProviderUnavailable("down", provider=self.name, reason="provider_error")

        async def air_quality_resolved(self, **_):
            raise open_meteo.ProviderUnavailable("down", provider=self.name, reason="provider_error")

    too_old_weather = SimpleNamespace(**{**vars(fresh_weather), "created_at": now - timedelta(hours=8)})
    too_old_air = SimpleNamespace(**{**vars(fresh_air), "created_at": now - timedelta(hours=8)})
    missing_weather, _, _ = await _resolve_weather_for_day(session, uuid4(), day, None, too_old_weather, Down(), target, "Asia/Kolkata", "loc:test", now)
    missing_air, _, _ = await _resolve_air_quality_for_day(session, uuid4(), day, None, too_old_air, Down(), target, "Asia/Kolkata", "loc:test", now)
    assert missing_weather is None and missing_air is None


@pytest.mark.asyncio
async def test_location_resolution_reason_is_preserved_when_provider_is_configured():
    weather, _, reason = await _resolve_weather_for_day(
        None, uuid4(), date(2026, 8, 23), None, None, SimpleNamespace(name="open_meteo"), None,
        "Asia/Kolkata", "loc:unknown", datetime(2026, 8, 23, tzinfo=UTC), "environment_location_unresolved",
    )
    assert weather is None
    assert reason == "environment_location_unresolved"


@pytest.mark.asyncio
async def test_weather_and_aqi_precedence_helpers_are_independent():
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    day = date(2026, 8, 23)
    manual_weather = WeatherReading(for_date=day, condition="rainy")
    cached_air = SimpleNamespace(
        id=uuid4(), for_date=day, created_at=now, aqi=22, index_system="european_aqi",
        category="Fair", location="Bengaluru", prominent_pollutant=None, pm2_5=None, pm10=None,
    )
    weather, _, _ = await _resolve_weather_for_day(None, uuid4(), day, manual_weather, None, None, None, "Asia/Kolkata", None, now)
    air, _, _ = await _resolve_air_quality_for_day(None, uuid4(), day, None, cached_air, None, None, "Asia/Kolkata", "loc:test", now)
    assert weather is manual_weather
    assert air is not None and air.aqi == 22
