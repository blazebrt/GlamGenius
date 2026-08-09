"""Pure CareContext projections and account boundary tests."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from app.domains.care.context_adapter import project_environment, project_primary_event
from app.domains.care.service import build_care_context
from app.domains.planning.context import DayContext, DayEvent
from app.domains.planning.providers.base import AirQualityReading, WeatherReading
from app.domains.profile.models import AppearanceProfile, ProfileAttribute, ProfileChangeEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth


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


@pytest.mark.asyncio
async def test_no_event_is_valid_context(monkeypatch):
    owner = uuid.uuid4()
    day = _day_context(owner)
    day.events = []

    class EmptyShelf:
        allergies = []
        draft_count = 0

    async def gather(*args, **kwargs):
        return EmptyShelf()

    async def get_profile(*args, **kwargs):
        return None

    monkeypatch.setattr("app.domains.care.service.profile_service.get_profile", get_profile)
    monkeypatch.setattr("app.domains.care.service.shelf.gather", gather)
    monkeypatch.setattr("app.domains.care.service.shelf.build", lambda *_: [])
    context = await build_care_context(object(), owner, day_context=day)
    assert context.primary_event is None
    assert not any(row.area == "event" and row.key == "primary_event" for row in context.missing_information)


@pytest.mark.asyncio
async def test_no_profile_assembly_is_read_only(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        context = await build_care_context(
            session, account_id, day_context=_day_context(account_id)
        )
        assert context.account_id == account_id
        assert context.skin_facts == {}
        assert context.hair_facts == {}
        assert context.preferences == {}

        for model in (AppearanceProfile, ProfileAttribute, ProfileChangeEvent):
            count = await session.scalar(
                select(func.count()).select_from(model)
            )
            assert count == 0, model.__tablename__


@pytest.mark.asyncio
async def test_successful_assembly_is_account_scoped_and_projects_shelf(
    app_client, db_clean, registered_supabase_user
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()

    response = await app_client.patch(
        "/api/v2/profile",
        headers=auth(token_a),
        json={"attributes": [
            {"key": "care_skin_usual_feel", "value": "often_dry_or_tight"},
            {"key": "care_hair_pattern", "value": "curly"},
            {"key": "care_hair_processing", "value": ["coloured", "relaxed"]},
            {"key": "care_routine_effort", "value": "balanced"},
            {"key": "allergies", "value": ["fragrance"]},
        ]},
    )
    assert response.status_code == 200, response.text
    await app_client.patch(
        "/api/v2/profile",
        headers=auth(token_b),
        json={"attributes": [
            {"key": "care_skin_usual_feel", "value": "often_oily"},
            {"key": "allergies", "value": ["latex"]},
        ]},
    )

    await app_client.post(
        "/api/v2/inventory/items", headers=auth(token_a), json={
            "category": "beauty", "display_name": "A Confirmed Skin Serum",
            "subcategory": "serum", "details": {
                "product_type": "serum", "active_ingredients": ["glycerin"],
            },
        }
    )
    await app_client.post(
        "/api/v2/inventory/items", headers=auth(token_a), json={
            "category": "hair", "display_name": "A Confirmed Hair Mask",
            "subcategory": "mask", "details": {"product_type": "mask"},
        }
    )
    draft = (await app_client.post(
        "/api/v2/inventory/items", headers=auth(token_a), json={
            "category": "beauty", "display_name": "A Draft Skin Product",
            "subcategory": "serum", "details": {"product_type": "serum"},
        }
    )).json()
    # Move the last item back to the draft state for the shelf boundary test.
    factory = get_sessionmaker()
    async with factory() as session:
        from app.domains.inventory.models import InventoryItem

        row = await session.get(InventoryItem, draft["id"])
        row.verification_state = "draft"
        await session.commit()

    await app_client.post(
        "/api/v2/inventory/items", headers=auth(token_b), json={
            "category": "beauty", "display_name": "B Private Skin Product",
            "subcategory": "serum", "details": {"product_type": "serum"},
        }
    )

    async with factory() as session:
        context = await build_care_context(
            session, account_a, day_context=_day_context(account_a)
        )

    assert context.context_version == "v3-03.1"
    assert context.plan_date == _day_context(account_a).plan_date
    assert context.skin_facts["care_skin_usual_feel"].value == "often_dry_or_tight"
    assert context.hair_facts["care_hair_pattern"].value == "curly"
    assert context.hair_facts["care_hair_processing"].value == ("coloured", "relaxed")
    assert context.preferences["care_routine_effort"].value == "balanced"
    assert context.allergies == ("fragrance",)
    assert {row.item.display_name for row in context.skin_products} == {"A Confirmed Skin Serum"}
    assert {row.item.display_name for row in context.hair_products} == {"A Confirmed Hair Mask"}
    assert context.draft_product_count == 1
    serum = context.skin_products[0]
    assert any(row.key == "glycerin" and not row.needs_confirmation for row in serum.ingredients)
    assert context.environment.weather_snapshot_id is not None
    assert context.environment.air_quality_snapshot_id is not None
    assert context.environment.humidity == 81
    assert context.environment.uv_index == 6
    assert context.environment.aqi == 92
    assert context.environment.daily_regime == _day_context(account_a).climate.daily_regime
    assert context.primary_event is not None
    assert all("B Private" not in row.item.display_name for row in context.skin_products + context.hair_products)
    assert all("latex" not in str(row.value) for row in context.skin_facts.values())
