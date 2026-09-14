"""V3-03.5.1 canonical Today material-cache-key integration coverage."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.bootstrap import run as run_seed
from app.domains.inventory.models import InventoryItem
from app.domains.planning import clock
from app.domains.planning import compiler as planning_compiler
from app.domains.planning import context as context_stage
from app.domains.planning.models import DailyPlan
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 2, 16)
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture(autouse=True)
def _force_morning(monkeypatch):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")


async def _seed() -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()


async def _inventory(client, token: str, *, category: str, name: str, subcategory: str, details: dict | None = None) -> str:
    response = await client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": category,
            "display_name": name,
            "subcategory": subcategory,
            "details": details or {},
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _stock_wardrobe(client, token: str) -> list[str]:
    rows = [
        ("beauty", "Gentle Cleanser", "cleanser", {"product_type": "cleanser"}),
        ("beauty", "Daily Moisturiser", "moisturiser", {"product_type": "moisturiser"}),
        ("hair", "Gentle Shampoo", "shampoo", {"product_type": "shampoo"}),
        ("hair", "Hair Conditioner", "conditioner", {"product_type": "conditioner"}),
    ]
    return [
        await _inventory(client, token, category=category, name=name, subcategory=subcategory, details=details)
        for category, name, subcategory, details in rows
    ]


async def _care_product(client, token: str, *, name: str, product_type: str = "cleanser", active_ingredients: list[str] | None = None) -> str:
    details: dict[str, object] = {"product_type": product_type}
    if active_ingredients is not None:
        details["active_ingredients"] = active_ingredients
    return await _inventory(client, token, category="beauty", name=name, subcategory=product_type, details=details)


async def _today(client, token: str, day: date = TODAY) -> dict:
    response = await client.get(
        f"/api/v2/today?plan_date={day.isoformat()}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _canonical_key(account_id: uuid.UUID, day: date = TODAY) -> str:
    factory = get_sessionmaker()
    async with factory() as session:
        context = await context_stage.gather(session, account_id=account_id, plan_date=day)
        material = await planning_compiler.build_day_care_material(session, context)
        return planning_compiler.material_cache_key(context, material)


async def _plan(account_id: uuid.UUID, day: date = TODAY) -> DailyPlan:
    factory = get_sessionmaker()
    async with factory() as session:
        return (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == day)
        )).scalar_one()


async def _patch_profile(client, token: str, key: str, value) -> None:
    response = await client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": key, "value": value}]},
    )
    assert response.status_code == 200, response.text


async def test_today_reread_serves_cache_and_stores_canonical_key(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)

    first = await _today(app_client, token)
    reread = await _today(app_client, token)
    stored = await _plan(account_id)

    assert reread["generated_from"] == "cache"
    assert reread["version"] == first["version"]
    assert stored.cache_key == await _canonical_key(account_id)


async def test_weekly_today_reads_keep_each_cache_key_canonical(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    generated = await app_client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["days"]
    first_today = await _today(app_client, token, TODAY)
    second_today = await _today(app_client, token, TOMORROW)
    first_plan = await _plan(account_id, TODAY)
    second_plan = await _plan(account_id, TOMORROW)

    assert first_today["generated_from"] == "cache"
    assert second_today["generated_from"] == "cache"
    assert first_plan.cache_key == await _canonical_key(account_id, TODAY)
    assert second_plan.cache_key == await _canonical_key(account_id, TOMORROW)


async def test_pinned_today_still_invalidates_for_effort_change(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    await _patch_profile(app_client, token, "care_routine_effort", "detailed")
    await _care_product(app_client, token, name="Owned Toner", product_type="toner")
    first = await _today(app_client, token)
    pinned = await _plan(account_id)
    await _patch_profile(app_client, token, "care_routine_effort", "minimal")
    reread = await _today(app_client, token)

    assert reread["generated_from"] == "fresh"
    assert reread["version"] > first["version"]
    assert (await _plan(account_id)).cache_key != pinned.cache_key


async def test_pinned_today_still_invalidates_for_care_safety_change(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    item_id = await _care_product(
        app_client, token, name="Possible Fragrance", active_ingredients=["fragrance"]
    )
    first = await _today(app_client, token)
    pinned = await _plan(account_id)
    await _patch_profile(app_client, token, "allergies", ["fragrance"])
    reread = await _today(app_client, token)
    actions = reread["primary"] + reread["optional_modules"]

    assert reread["generated_from"] == "fresh"
    assert reread["version"] > first["version"]
    assert not any(
        row["action_type"] == "routine" and row.get("inventory_item_id") == item_id
        for row in actions
    )
    assert (await _plan(account_id)).cache_key != pinned.cache_key


async def test_pinned_today_still_invalidates_for_care_selection_change(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    first_id = await _care_product(app_client, token, name="Continuity A")
    second_id = await _care_product(app_client, token, name="Continuity B")
    factory = get_sessionmaker()
    async with factory() as session:
        first_item = await session.get(InventoryItem, uuid.UUID(first_id))
        first_item.last_used_at = TODAY - timedelta(days=1)
        await session.commit()

    first = await _today(app_client, token)
    first_actions = first["primary"] + first["optional_modules"]
    assert any(row.get("inventory_item_id") == first_id for row in first_actions)
    pinned = await _plan(account_id)
    async with factory() as session:
        second_item = await session.get(InventoryItem, uuid.UUID(second_id))
        second_item.last_used_at = TODAY
        await session.commit()
    reread = await _today(app_client, token)
    actions = reread["primary"] + reread["optional_modules"]

    assert reread["generated_from"] == "fresh"
    assert reread["version"] > first["version"]
    assert any(row.get("inventory_item_id") == second_id for row in actions)
    assert (await _plan(account_id)).cache_key != pinned.cache_key


async def test_pinned_today_still_invalidates_for_normal_weather_change(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    first = await _today(app_client, token)
    pinned = await _plan(account_id)
    weather = await app_client.post(
        "/api/v2/today/weather", headers=auth(token),
        json={"for_date": TODAY.isoformat(), "condition": "humid", "temp_max_c": 34.0, "humidity": 80},
    )
    assert weather.status_code == 200, weather.text
    reread = await _today(app_client, token)

    assert reread["generated_from"] == "cache"
    assert weather.json()["plan"]["version"] > first["version"]
    assert (await _plan(account_id)).cache_key == await _canonical_key(account_id)
    assert pinned.cache_key != (await _plan(account_id)).cache_key
