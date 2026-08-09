"""Pure CareContext projections and account boundary tests."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from app.domains.care.context_adapter import project_environment, project_primary_event
from app.domains.care.service import build_care_context
from app.domains.planning.context import DayContext, DayEvent
from app.domains.planning.providers.base import AirQualityReading, WeatherReading


def _day_context(account_id: uuid.UUID) -> DayContext:
    event = DayEvent(
        id=uuid.uuid4(),
        title="Client dinner",
        starts_at=datetime(2026, 8, 12, 18, tzinfo=UTC),
        ends_at=datetime(2026, 8, 12, 20, tzinfo=UTC),
        all_day=False,
        location=None,
        occasion_key="date",
        dress_code_hint=None,
        confidence=0.95,
        user_confirmed=True,
    )
    return DayContext(
        account_id=account_id,
        plan_date=date(2026, 8, 12),
        timezone_name="UTC",
        now_local=datetime(2026, 8, 12, 9, tzinfo=UTC),
        weather=WeatherReading(
            for_date=date(2026, 8, 12),
            condition="rainy",
            temp_min_c=22,
            temp_max_c=29,
            humidity=81,
            precipitation_chance=70,
            uv_index=6,
            location="Mumbai",
        ),
        weather_snapshot_id=uuid.uuid4(),
        air_quality=AirQualityReading(
            for_date=date(2026, 8, 12),
            aqi=92,
            index_system="india_naqi",
            category="Satisfactory",
        ),
        air_quality_snapshot_id=uuid.uuid4(),
        events=[event],
        profile={"city": "Mumbai"},
    )


def test_context_adapter_projects_supplied_values_without_reinterpretation():
    day = _day_context(uuid.uuid4())
    environment = project_environment(day)

    assert environment.weather_snapshot_id == day.weather_snapshot_id
    assert environment.air_quality_snapshot_id == day.air_quality_snapshot_id
    assert environment.condition == "rainy"
    assert environment.temp_min_c == 22
    assert environment.temp_max_c == 29
    assert environment.humidity == 81
    assert environment.precipitation_chance == 70
    assert environment.uv_index == 6
    assert environment.aqi == 92
    assert environment.aqi_index_system == "india_naqi"
    assert environment.aqi_category == "Satisfactory"
    assert environment.daily_regime == day.climate.daily_regime
    assert project_primary_event(day).id == day.primary_event.id


def test_context_adapter_keeps_missing_weather_missing():
    day = _day_context(uuid.uuid4())
    day.weather = None
    day.weather_snapshot_id = None
    day.weather_unavailable_reason = "not_configured"
    environment = project_environment(day)

    assert environment.condition is None
    assert environment.temp_max_c is None
    assert environment.weather_snapshot_id is None
    assert environment.weather_unavailable_reason == "not_configured"


@pytest.mark.asyncio
async def test_context_account_mismatch_fails_before_db_work():
    owner = uuid.uuid4()
    day = _day_context(uuid.uuid4())
    with pytest.raises(ValueError, match="does not match"):
        await build_care_context(object(), owner, day_context=day)
