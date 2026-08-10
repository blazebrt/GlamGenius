"""Database-backed V3-03.6 locked-day Care freshness coverage."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from app.bootstrap import run as run_seed
from app.domains.inventory.models import InventoryItem
from app.domains.planning import clock
from app.domains.planning import compiler as planning_compiler
from app.domains.planning import context as context_stage
from app.domains.planning.models import (
    DailyPlan,
    DailyPlanAction,
    DailyPlanInput,
    OutfitSchedule,
    PlanRecalculationEvent,
)
from app.domains.recommendation.models import Look, RecommendationRun
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 2, 16)


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
        "/api/v2/inventory/items", headers=auth(token),
        json={
            "category": category, "display_name": name,
            "subcategory": subcategory, "details": details or {},
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _stock_wardrobe(client, token: str) -> None:
    for category, name, subcategory, details in (
        ("wardrobe", "Charcoal Blazer", "blazer", {"colour": "charcoal", "fabric": "wool", "formality": "smart_casual", "season": ["all"]}),
        ("wardrobe", "White Cotton Shirt", "shirt", {"colour": "white", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]}),
        ("wardrobe", "Navy Chinos", "trousers", {"colour": "navy", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]}),
        ("shoes", "Brown Leather Derbies", "derby", {"colour": "brown", "shoe_type": "derby", "occasion": ["work"]}),
    ):
        await _inventory(client, token, category=category, name=name, subcategory=subcategory, details=details)


async def _care_product(client, token: str) -> str:
    return await _inventory(
        client, token, category="beauty", name="Fragranced Moisturiser",
        subcategory="moisturiser", details={"product_type": "moisturiser", "active_ingredients": ["fragrance"]},
    )


async def _allergy(client, token: str, values: list[str]) -> None:
    response = await client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "allergies", "value": values}]},
    )
    assert response.status_code == 200, response.text


async def _effort(client, token: str, value: str) -> None:
    response = await client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "care_routine_effort", "value": value}]},
    )
    assert response.status_code == 200, response.text


async def _weather(client, token: str, condition: str = "rainy") -> None:
    response = await client.post(
        "/api/v2/today/weather", headers=auth(token),
        json={"for_date": TODAY.isoformat(), "condition": condition},
    )
    assert response.status_code == 200, response.text


async def _today(client, token: str) -> dict:
    response = await client.get(f"/api/v2/today?plan_date={TODAY.isoformat()}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _lock(client, token: str) -> None:
    generated = await client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )
    assert generated.status_code == 200, generated.text
    locked = await client.post(
        f"/api/v2/planner/day/{TODAY.isoformat()}/lock", headers=auth(token),
        json={"locked": True},
    )
    assert locked.status_code == 200, locked.text


async def _snapshot(account_id: uuid.UUID) -> dict:
    factory = get_sessionmaker()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == TODAY)
        )).scalar_one()
        actions = (await session.execute(
            select(DailyPlanAction).where(DailyPlanAction.plan_id == plan.id)
        )).scalars().all()
        inputs = (await session.execute(
            select(DailyPlanInput).where(DailyPlanInput.plan_id == plan.id, DailyPlanInput.input_type == "care")
        )).scalars().all()
        schedule = (await session.execute(
            select(OutfitSchedule).where(OutfitSchedule.account_id == account_id, OutfitSchedule.plan_date == TODAY)
        )).scalar_one_or_none()
        events = await session.scalar(
            select(func.count()).select_from(PlanRecalculationEvent).where(
                PlanRecalculationEvent.account_id == account_id, PlanRecalculationEvent.plan_date == TODAY,
            )
        )
        runs = await session.scalar(
            select(func.count()).select_from(RecommendationRun).where(RecommendationRun.account_id == account_id)
        )
        looks = await session.scalar(
            select(func.count()).select_from(Look).where(Look.account_id == account_id)
        )
        return {
            "plan_id": plan.id, "version": plan.version, "cache_key": plan.cache_key,
            "look_id": plan.look_id, "schedule": (schedule.look_id, schedule.item_ids) if schedule else None,
            "care_input_ids": {row.input_key: row.id for row in inputs},
            "care_values": {row.input_key: row.value for row in inputs},
            "actions": {(row.module, row.action_type, row.title): row.id for row in actions},
            "events": events, "runs": runs, "looks": looks,
        }


async def _canonical_key(account_id: uuid.UUID) -> str:
    factory = get_sessionmaker()
    async with factory() as session:
        context = await context_stage.gather(
            session, account_id=account_id, plan_date=TODAY,
        )
        material = await planning_compiler.build_day_care_material(session, context)
        return planning_compiler.material_cache_key(context, material)


async def test_locked_allergy_refresh_preserves_outfit_and_is_stable(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    item_id = await _care_product(app_client, token)
    await _today(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)

    await _allergy(app_client, token, ["fragrance"])
    refreshed = await _today(app_client, token)
    after = await _snapshot(account_id)
    actions = refreshed["primary"] + refreshed["optional_modules"]

    assert refreshed["locked"] is True
    assert after["version"] == before["version"] + 1
    assert after["cache_key"] == before["cache_key"]
    assert after["look_id"] == before["look_id"]
    assert after["schedule"] == before["schedule"]
    assert not any(row["action_type"] == "routine" and row.get("inventory_item_id") == item_id for row in actions)
    assert any(row["action_type"] == "care_safety" and row.get("inventory_item_id") == item_id for row in actions)
    assert after["runs"] == before["runs"]
    assert after["looks"] == before["looks"]
    assert after["events"] == before["events"] + 1
    assert after["care_input_ids"] != before["care_input_ids"]

    second = await _today(app_client, token)
    stable = await _snapshot(account_id)
    assert second["version"] == refreshed["version"]
    assert stable["events"] == after["events"]
    assert stable["care_input_ids"] == after["care_input_ids"]
    assert stable["actions"] == after["actions"]


async def test_locked_care_reversion_refreshes_even_when_full_key_returns_to_a(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    item_id = await _care_product(app_client, token)
    await _today(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)

    await _allergy(app_client, token, ["fragrance"])
    blocked = await _today(app_client, token)
    assert any(row.get("inventory_item_id") == item_id and row["action_type"] == "care_safety" for row in blocked["primary"] + blocked["optional_modules"])
    await _allergy(app_client, token, [])
    assert await _canonical_key(account_id) == before["cache_key"]
    reverted = await _today(app_client, token)
    after = await _snapshot(account_id)

    assert after["cache_key"] == before["cache_key"]
    assert after["version"] == before["version"] + 2
    assert any(row.get("inventory_item_id") == item_id and row["action_type"] == "routine" for row in reverted["primary"] + reverted["optional_modules"])
    assert after["events"] == before["events"] + 2


async def test_weekly_locked_day_refreshes_care_without_replacing_outfit(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    item_id = await _care_product(app_client, token)
    await _today(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)

    await _allergy(app_client, token, ["fragrance"])
    regenerated = await app_client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )
    assert regenerated.status_code == 200, regenerated.text
    after = await _snapshot(account_id)
    weekly_day = regenerated.json()["days"][0]
    today = await _today(app_client, token)
    actions = today["primary"] + today["optional_modules"]

    assert weekly_day["locked"] is True
    assert after["version"] == before["version"] + 1
    assert after["look_id"] == before["look_id"]
    assert after["schedule"] == before["schedule"]
    assert any(row.get("inventory_item_id") == item_id and row["action_type"] == "care_safety" for row in actions)


async def test_unlocked_appearance_keeps_drafts_ahead_of_expiry_advisory(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    expiring_id = await _inventory(
        app_client, token, category="beauty", name="Soon Expiring Moisturiser",
        subcategory="moisturiser", details={"product_type": "moisturiser", "expiry_date": "2026-02-20"},
    )
    draft_id = await _inventory(
        app_client, token, category="wardrobe", name="Draft Shirt", subcategory="shirt",
        details={"colour": "white", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]},
    )
    factory = get_sessionmaker()
    async with factory() as session:
        draft = await session.get(InventoryItem, uuid.UUID(draft_id))
        draft.verification_state = "draft"
        await session.commit()

    first = await _today(app_client, token)
    first_actions = first["primary"] + first["optional_modules"]
    assert any(row["action_type"] == "confirm_drafts" for row in first_actions)
    assert not any(row["action_type"] == "care_expiring_soon" for row in first_actions)

    async with factory() as session:
        draft = await session.get(InventoryItem, uuid.UUID(draft_id))
        draft.verification_state = "confirmed"
        await session.commit()
    second = await _today(app_client, token)
    second_actions = second["primary"] + second["optional_modules"]
    assert any(row["action_type"] == "care_expiring_soon" and row.get("inventory_item_id") == expiring_id for row in second_actions)


async def test_locked_effort_change_removes_optional_care_without_outfit_churn(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _effort(app_client, token, "detailed")
    optional_id = await _inventory(
        app_client, token, category="beauty", name="Optional Toner", subcategory="toner",
        details={"product_type": "toner"},
    )
    first = await _today(app_client, token)
    assert any(row.get("inventory_item_id") == optional_id for row in first["primary"] + first["optional_modules"])
    await _lock(app_client, token)
    before = await _snapshot(account_id)
    await _effort(app_client, token, "minimal")
    changed = await _today(app_client, token)
    after = await _snapshot(account_id)
    assert not any(row.get("inventory_item_id") == optional_id and row["action_type"] == "routine" for row in changed["primary"] + changed["optional_modules"])
    assert after["care_values"]["care_routine_plan_fingerprint"] != before["care_values"]["care_routine_plan_fingerprint"]
    assert after["cache_key"] == before["cache_key"]
    assert after["look_id"] == before["look_id"]
    assert after["version"] == before["version"] + 1
    assert after["runs"] == before["runs"] and after["looks"] == before["looks"]
    stable = await _snapshot(account_id)
    assert stable["version"] == after["version"] and stable["actions"] == after["actions"]


async def test_locked_continuity_change_switches_routine_without_safety_change(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    first_id = await _inventory(app_client, token, category="beauty", name="Cleanser A", subcategory="cleanser", details={"product_type": "cleanser"})
    second_id = await _inventory(app_client, token, category="beauty", name="Cleanser B", subcategory="cleanser", details={"product_type": "cleanser"})
    factory = get_sessionmaker()
    async with factory() as session:
        first_item = await session.get(InventoryItem, uuid.UUID(first_id))
        second_item = await session.get(InventoryItem, uuid.UUID(second_id))
        first_item.last_used_at = datetime(2026, 2, 15, tzinfo=UTC)
        second_item.last_used_at = datetime(2026, 2, 1, tzinfo=UTC)
        await session.commit()
    initial = await _today(app_client, token)
    assert any(row.get("inventory_item_id") == first_id for row in initial["primary"] + initial["optional_modules"])
    await _lock(app_client, token)
    before = await _snapshot(account_id)
    async with factory() as session:
        first_item = await session.get(InventoryItem, uuid.UUID(first_id))
        second_item = await session.get(InventoryItem, uuid.UUID(second_id))
        first_item.last_used_at = datetime(2026, 2, 15, tzinfo=UTC)
        second_item.last_used_at = utcnow()
        await session.commit()
    changed = await _today(app_client, token)
    after = await _snapshot(account_id)
    assert any(row.get("inventory_item_id") == second_id for row in changed["primary"] + changed["optional_modules"])
    assert after["care_values"]["care_decision_fingerprint"] == before["care_values"]["care_decision_fingerprint"]
    assert after["care_values"]["care_routine_plan_fingerprint"] != before["care_values"]["care_routine_plan_fingerprint"]
    assert after["cache_key"] == before["cache_key"] and after["look_id"] == before["look_id"]
    assert after["version"] == before["version"] + 1
    stable = await _snapshot(account_id)
    assert stable["version"] == after["version"] and stable["actions"] == after["actions"]


async def test_locked_care_and_weather_refresh_only_care(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    item_id = await _care_product(app_client, token)
    await _today(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)
    await _weather(app_client, token)
    await _allergy(app_client, token, ["fragrance"])
    changed = await _today(app_client, token)
    after = await _snapshot(account_id)
    actions = changed["primary"] + changed["optional_modules"]
    assert any(row["action_type"] == "care_safety" and row.get("inventory_item_id") == item_id for row in actions)
    assert after["cache_key"] == before["cache_key"]
    assert after["look_id"] == before["look_id"] and after["schedule"] == before["schedule"]
    version = after["version"]
    stable = await _today(app_client, token)
    assert stable["version"] == version
    assert (await _snapshot(account_id))["events"] == after["events"]


async def test_locked_plan_missing_care_fingerprints_is_upgraded_in_place(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    await _care_product(app_client, token)
    await _today(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(DailyPlanInput).where(
                DailyPlanInput.plan_id == before["plan_id"],
                DailyPlanInput.input_type == "care",
                DailyPlanInput.input_key.in_(("care_decision_fingerprint", "care_routine_plan_fingerprint")),
            )
        )).scalars().all()
        for row in rows:
            await session.delete(row)
        await session.commit()
    current = await _today(app_client, token)
    after = await _snapshot(account_id)
    assert current["locked"] is True
    assert after["version"] == before["version"] + 1
    assert set(after["care_values"]) >= {"care_decision_fingerprint", "care_routine_plan_fingerprint"}
    assert after["cache_key"] == before["cache_key"] and after["look_id"] == before["look_id"]


async def test_unlock_after_care_refresh_allows_full_recompute(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    await _care_product(app_client, token)
    await _today(app_client, token)
    await _lock(app_client, token)
    await _allergy(app_client, token, ["fragrance"])
    await _today(app_client, token)
    partial = await _snapshot(account_id)
    await _weather(app_client, token)
    unlocked = await app_client.post(
        f"/api/v2/planner/day/{TODAY.isoformat()}/lock", headers=auth(token),
        json={"locked": False},
    )
    assert unlocked.status_code == 200, unlocked.text
    rebuilt = await _today(app_client, token)
    assert rebuilt["locked"] is False
    assert rebuilt["generated_from"] == "fresh"
    assert rebuilt["version"] > partial["version"]
    assert (await _snapshot(account_id))["cache_key"] != partial["cache_key"]


async def test_weekly_locked_unchanged_care_has_no_churn(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    await _care_product(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)
    regenerated = await app_client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat(), "regenerate_locked": False},
    )
    assert regenerated.status_code == 200, regenerated.text
    after = await _snapshot(account_id)
    assert after["version"] == before["version"]
    assert after["care_input_ids"] == before["care_input_ids"]
    assert after["actions"] == before["actions"]
    assert after["look_id"] == before["look_id"] and after["schedule"] == before["schedule"]


async def test_weekly_regenerate_locked_true_keeps_explicit_override(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed()
    await _stock_wardrobe(app_client, token)
    await _care_product(app_client, token)
    await _lock(app_client, token)
    before = await _snapshot(account_id)
    regenerated = await app_client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat(), "regenerate_locked": True},
    )
    assert regenerated.status_code == 200, regenerated.text
    after = await _snapshot(account_id)
    assert after["version"] == before["version"] + 1


async def test_locked_care_refresh_is_account_scoped(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seed()
    await _care_product(app_client, token_a)
    await _care_product(app_client, token_b)
    await _today(app_client, token_a)
    await _today(app_client, token_b)
    await _lock(app_client, token_a)
    await _lock(app_client, token_b)
    before_b = await _snapshot(account_b)
    await _allergy(app_client, token_a, ["fragrance"])
    await _today(app_client, token_a)
    after_b = await _snapshot(account_b)
    assert after_b == before_b
