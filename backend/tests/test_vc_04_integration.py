"""VC-04 Today acceptance coverage against the real API and database."""
from datetime import date, timedelta

import httpx
import pytest
from app.domains.planning import clock
from app.domains.planning import context as context_stage
from app.domains.planning.models import AirQualitySnapshot, DailyPlan, WeatherSnapshot
from app.domains.planning.providers import open_meteo
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


def _provider_transport(day: date, *, weather_status: int = 200, air_status: int = 200, seen_locations: list[str] | None = None):
    calls = {"geocode": 0, "weather": 0, "air": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "geocoding" in url:
            calls["geocode"] += 1
            if seen_locations is not None:
                seen_locations.append(request.url.params.get("name", ""))
            return httpx.Response(200, json={"results": [{"country_code": "IN", "latitude": 19.07, "longitude": 72.87, "name": "Mumbai"}]}, request=request)
        if "air-quality" in url:
            calls["air"] += 1
            if air_status != 200:
                return httpx.Response(air_status, request=request)
            return httpx.Response(200, json={"hourly": {
                "time": [f"{day.isoformat()}T00:00"], "european_aqi": [35],
                "european_aqi_pm2_5": [35], "european_aqi_pm10": [20],
                "european_aqi_nitrogen_dioxide": [15], "european_aqi_ozone": [10],
                "european_aqi_sulphur_dioxide": [5],
                "pm2_5": [12.0], "pm10": [20.0],
            }}, request=request)
        calls["weather"] += 1
        if weather_status != 200:
            return httpx.Response(weather_status, request=request)
        return httpx.Response(200, json={"daily": {
            "time": [day.isoformat()], "temperature_2m_min": [20],
            "temperature_2m_max": [30], "precipitation_probability_max": [10],
            "uv_index_max": [7], "weather_code": [1],
        }, "hourly": {
            "time": [f"{day.isoformat()}T00:00"], "relative_humidity_2m": [60],
        }}, request=request)

    return calls, httpx.MockTransport(handler)


def _use_provider(monkeypatch, provider: open_meteo.OpenMeteoProvider) -> None:
    original = context_stage.weather_provider

    def weather_provider(name, session, account_id):
        return provider if name == "open_meteo" else original(name, session, account_id)

    monkeypatch.setattr(context_stage, "weather_provider", weather_provider)
    monkeypatch.setattr(context_stage, "LIVE_ENVIRONMENT_PROVIDER", "open_meteo")
    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")


async def _set_city(client, token: str) -> None:
    response = await client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "city", "value": "Delhi"}]},
    )
    assert response.status_code == 200, response.text


async def test_today_persists_live_weather_and_aqi_and_reuses_ttl_cache(
    app_client, db_clean, registered_supabase_user, monkeypatch
):
    token, account_id = await registered_supabase_user()
    day = clock.local_today("Asia/Kolkata")
    calls, transport = _provider_transport(day)
    _use_provider(monkeypatch, open_meteo.OpenMeteoProvider(transport=transport))
    await _set_city(app_client, token)

    first = await app_client.get(f"/api/v2/today?plan_date={day.isoformat()}", headers=auth(token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["weather"]["provider"] == "open_meteo"
    assert body["air_quality"]["provider"] == "open_meteo"
    assert calls == {"geocode": 1, "weather": 1, "air": 1}

    factory = get_sessionmaker()
    async with factory() as session:
        plan = (await session.execute(select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == day))).scalar_one()
        weather = await session.get(WeatherSnapshot, plan.weather_snapshot_id)
        air = await session.get(AirQualitySnapshot, plan.air_quality_snapshot_id)
        assert weather is not None and air is not None
        assert plan.weather_snapshot_id == weather.id
        assert plan.air_quality_snapshot_id == air.id

    second = await app_client.get(f"/api/v2/today?plan_date={day.isoformat()}", headers=auth(token))
    assert second.status_code == 200, second.text
    assert calls == {"geocode": 1, "weather": 1, "air": 1}


@pytest.mark.parametrize("failed_domain", ["weather", "air"])
async def test_today_provider_failure_keeps_other_domain_and_returns_200(
    app_client, db_clean, registered_supabase_user, monkeypatch, failed_domain
):
    token, _ = await registered_supabase_user()
    day = clock.local_today("Asia/Kolkata")
    calls, transport = _provider_transport(
        day, weather_status=503 if failed_domain == "weather" else 200,
        air_status=503 if failed_domain == "air" else 200,
    )
    _use_provider(monkeypatch, open_meteo.OpenMeteoProvider(transport=transport))
    await _set_city(app_client, token)

    response = await app_client.get(f"/api/v2/today?plan_date={day.isoformat()}", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["weather"] is None if failed_domain == "weather" else body["weather"] is not None
    assert body["air_quality"] is None if failed_domain == "air" else body["air_quality"] is not None


async def test_event_ready_uses_explicit_location_for_both_live_domains(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, _ = await registered_supabase_user()
    await _set_city(app_client, token)
    target = clock.local_today("Asia/Kolkata") + timedelta(days=1)
    seen_locations: list[str] = []
    calls, transport = _provider_transport(target, seen_locations=seen_locations)
    _use_provider(monkeypatch, open_meteo.OpenMeteoProvider(transport=transport))

    created = await app_client.post(
        "/api/v2/today/events", headers=auth(token),
        json={
            "title": "Mumbai wedding", "starts_at": f"{target.isoformat()}T12:00:00+05:30",
            "location": "Mumbai", "occasion_key": "wedding",
        },
    )
    assert created.status_code in (200, 201), created.text
    event_id = created.json()["event"]["id"]
    seen_locations.clear()
    generated = await app_client.post(
        f"/api/v2/planner/events/{event_id}/ready/generate", headers=auth(token),
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert seen_locations == ["Mumbai"]
    assert body["context"]["weather"]["location"] == "Mumbai"
    assert body["context"]["air_quality"]["location"] == "Mumbai"
    assert calls == {"geocode": 1, "weather": 1, "air": 1}


@pytest.mark.parametrize(("location", "expected_geocodes"), [("Unknown Hall", ["Unknown Hall"]), ("Hall", [])])
async def test_event_ready_never_falls_back_after_explicit_location_failure(
    app_client, db_clean, registered_supabase_user, monkeypatch, location, expected_geocodes,
):
    token, _ = await registered_supabase_user()
    await _set_city(app_client, token)
    target = clock.local_today("Asia/Kolkata") + timedelta(days=1)
    seen_locations: list[str] = []
    calls, transport = _provider_transport(target, seen_locations=seen_locations)

    def unresolved(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            seen_locations.append(request.url.params.get("name", ""))
            return httpx.Response(200, json={"results": []}, request=request)
        return httpx.Response(500, request=request)

    _use_provider(monkeypatch, open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(unresolved)))
    created = await app_client.post(
        "/api/v2/today/events", headers=auth(token),
        json={
            "title": "Unresolved venue", "starts_at": f"{target.isoformat()}T12:00:00+05:30",
            "location": location, "occasion_key": "wedding",
        },
    )
    assert created.status_code in (200, 201), created.text
    event_id = created.json()["event"]["id"]
    seen_locations.clear()
    generated = await app_client.post(
        f"/api/v2/planner/events/{event_id}/ready/generate", headers=auth(token),
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["context"]["weather"] is None
    assert body["context"]["air_quality"] is None
    assert seen_locations == expected_geocodes
    assert calls == {"geocode": 0, "weather": 0, "air": 0}
