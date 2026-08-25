"""Database-backed V3-03.3 safety activation closure coverage."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.bootstrap import run as run_seed
from app.domains.inventory.models import BeautyProductDetail, InventoryItem
from app.domains.planning import clock
from app.domains.planning.models import DailyPlan, DailyPlanInput
from app.domains.routines import service as routines_service
from app.domains.routines.models import (
    ProductIngredient,
    Routine,
    RoutineRecommendationRun,
    RoutineStep,
)
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

GENERATION_DATE = date(2026, 8, 12)


async def _seed(client) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()


async def _product(
    client,
    token: str,
    *,
    name: str,
    product_type: str = "moisturiser",
    expiry: date | None = None,
    active_ingredients: list[str] | None = None,
) -> str:
    details: dict[str, object] = {"product_type": product_type}
    if expiry is not None:
        details["expiry_date"] = expiry.isoformat()
    if active_ingredients is not None:
        details["active_ingredients"] = active_ingredients
    response = await client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": "beauty", "display_name": name,
            "subcategory": product_type, "details": details,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _allergy(client, token: str, value: str = "fragrance") -> None:
    response = await client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "allergies", "value": [value]}]},
    )
    assert response.status_code == 200, response.text


async def _effort(client, token: str, value: str) -> None:
    response = await client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "care_routine_effort", "value": value}]},
    )
    assert response.status_code == 200, response.text


async def _stored_ingredient(
    account_id: uuid.UUID,
    item_id: str,
    *,
    confidence: float = 0.55,
    confirmed: bool = False,
) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(ProductIngredient(
            account_id=account_id,
            item_id=uuid.UUID(item_id),
            ingredient_key="fragrance",
            matched_text="fragrance",
            confidence=1.0 if confirmed else confidence,
            source="user_declared" if confirmed else "photo_extracted",
            needs_confirmation=not confirmed,
            confirmed_at=utcnow() if confirmed else None,
        ))
        await session.commit()


async def _generate(client, token: str, *, kinds: list[str] | None = None) -> dict:
    response = await client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={
            "kinds": kinds or ["morning", "evening"],
            "as_of": GENERATION_DATE.isoformat(),
            "explain": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _force_morning(monkeypatch) -> None:
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")


def _routine(body: dict, kind: str = "morning") -> dict:
    return next(row for row in body["routines"] if row["kind"] == kind)


def _shelf_ingredients(body: dict, item_id: str) -> list[dict]:
    for report in body["reports"].values():
        for product in report["products"]:
            if product["inventory_item_id"] == item_id:
                return product["ingredients"]
    return []


def _shelf_warnings(body: dict, item_id: str | None = None) -> list[dict]:
    warnings = [
        warning
        for report in body["reports"].values()
        for warning in report["warnings"]
    ]
    if item_id is None:
        return warnings
    return [warning for warning in warnings if item_id in warning["item_ids"]]


async def _analyse_shelf(client, token: str) -> dict:
    response = await client.post(
        "/api/v2/shelf/analyse",
        headers=auth(token),
        json={"categories": ["beauty"], "as_of": GENERATION_DATE.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_generation_blocks_expired_required_product_and_persists_audit(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _product(
        app_client, token, name="Expired Moisturiser", expiry=date(2026, 8, 1)
    )

    body = await _generate(app_client, token, kinds=["morning"])
    routine = _routine(body)
    step = next(row for row in routine["steps"] if row["slot"] == "moisturiser")
    assert step["is_gap"] is True
    assert "product is recorded" in step["why"]
    assert "not eligible" in step["why"]
    assert all(row["inventory_item_id"] != item_id for row in routine["steps"])
    blocked = next(row for row in body["care_safety"]["blocked_products"] if row["inventory_item_id"] == item_id)
    assert blocked["reasons"] == ["product_expired"]

    factory = get_sessionmaker()
    async with factory() as session:
        stored = (await session.execute(
            select(Routine).where(Routine.account_id == account_id, Routine.kind == "morning")
        )).scalar_one()
        run = (await session.execute(
            select(RoutineRecommendationRun).where(RoutineRecommendationRun.account_id == account_id)
        )).scalar_one()
    assert stored.engine_version == "care-v3-03.5"
    assert run.engine_version == "care-v3-03.5"
    for key in (
        "care_context_version", "care_decision_version", "blocked_product_count",
        "expired_product_count", "confirmed_allergy_block_count",
        "ingredient_confirmation_advisory_count", "weather_snapshot_id",
        "air_quality_snapshot_id",
        "care_routine_plan_version", "care_routine_plan_fingerprint",
        "care_routine_effort", "care_routine_effort_source",
        "care_active_skin_slot_count", "care_active_hair_slot_count",
        "care_skin_gap_count", "care_hair_gap_count",
    ):
        assert key in run.inputs
    assert run.inputs["blocked_product_count"] == 1
    assert run.inputs["expired_product_count"] == 1
    assert run.inputs["confirmed_allergy_block_count"] == 0
    assert run.inputs["ingredient_confirmation_advisory_count"] == 0
    assert set(body["care_safety"]) == {
        "context_version", "decision_version", "blocked_products", "ingredient_confirmation_needed",
    }


async def test_generation_expired_and_valid_candidates_keep_valid_product(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    expired_id = await _product(
        app_client, token, name="Expired Moisturiser", expiry=date(2026, 8, 1)
    )
    valid_id = await _product(
        app_client, token, name="Valid Moisturiser", expiry=date(2026, 9, 1)
    )

    routine = _routine(await _generate(app_client, token, kinds=["morning"]))
    step = next(row for row in routine["steps"] if row["slot"] == "moisturiser")
    assert step["inventory_item_id"] == valid_id
    assert step["inventory_item_id"] != expired_id
    assert step["is_gap"] is False


async def test_generation_confirmed_allergy_blocks_product_and_reports_reason(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    await _allergy(app_client, token)
    item_id = await _product(
        app_client, token, name="Fragranced Moisturiser", active_ingredients=["fragrance"]
    )

    body = await _generate(app_client, token, kinds=["morning"])
    routine = _routine(body)
    step = next(row for row in routine["steps"] if row["slot"] == "moisturiser")
    assert step["is_gap"] is True
    assert "Fragranced Moisturiser" in routine["skipped_for_allergy"]
    assert any(row["rule_id"] == "rule.user_allergy" for row in routine["warnings"])
    blocked = next(row for row in body["care_safety"]["blocked_products"] if row["inventory_item_id"] == item_id)
    assert blocked["reasons"] == ["confirmed_allergy_match"]


async def test_generation_unconfirmed_allergen_remains_eligible_and_advisory(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _allergy(app_client, token)
    item_id = await _product(app_client, token, name="Possible Fragrance", product_type="cleanser")
    await _stored_ingredient(account_id, item_id)

    body = await _generate(app_client, token, kinds=["morning"])
    routine = _routine(body)
    cleanser = next(row for row in routine["steps"] if row["slot"] == "cleanser")
    assert cleanser["inventory_item_id"] == item_id
    assert item_id not in {row["inventory_item_id"] for row in body["care_safety"]["blocked_products"]}
    assert routine["skipped_for_allergy"] == []
    assert not any(row["rule_id"] == "rule.user_allergy" for row in routine["warnings"])
    assert any(row["rule_id"] == "rule.unconfirmed_ingredient" for row in routine["warnings"])
    assert body["care_safety"]["ingredient_confirmation_needed"] == [{
        "inventory_item_id": item_id, "display_name": "Possible Fragrance",
    }]
    shelf = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()
    shelf_warnings = [
        warning for report in shelf["reports"].values() for warning in report["warnings"]
    ]
    assert any(row["rule_id"] == "rule.unconfirmed_ingredient" for row in shelf_warnings)
    assert not any(row["rule_id"] == "rule.user_allergy" for row in shelf_warnings)


async def test_current_product_fact_beats_stale_unconfirmed_ingredient(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _allergy(app_client, token)
    item_id = await _product(
        app_client, token, name="Current Fragrance", active_ingredients=["fragrance"]
    )
    await _stored_ingredient(account_id, item_id, confidence=0.55)

    shelf = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()
    fragrance = next(row for row in _shelf_ingredients(shelf, item_id) if row["ingredient_key"] == "fragrance")
    assert fragrance["confidence"] == 1.0
    assert fragrance["source"] == "user_declared"
    assert fragrance["needs_confirmation"] is False
    assert any(row["rule_id"] == "rule.user_allergy" for row in _shelf_warnings(shelf, item_id))

    body = await _generate(app_client, token, kinds=["morning"])
    blocked = [row for row in body["care_safety"]["blocked_products"] if row["inventory_item_id"] == item_id]
    assert blocked and blocked[0]["reasons"] == ["confirmed_allergy_match"]


async def test_stale_unconfirmed_candidate_is_removed_by_fresh_shelf_analysis(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _product(app_client, token, name="Stale Candidate")
    await _stored_ingredient(account_id, item_id, confidence=0.55)

    before = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()
    assert any(row["ingredient_key"] == "fragrance" for row in _shelf_ingredients(before, item_id))

    analysed = await _analyse_shelf(app_client, token)
    assert not any(row["ingredient_key"] == "fragrance" for row in _shelf_ingredients(analysed, item_id))

    factory = get_sessionmaker()
    async with factory() as session:
        assert await session.scalar(
            select(ProductIngredient).where(
                ProductIngredient.account_id == account_id,
                ProductIngredient.item_id == uuid.UUID(item_id),
                ProductIngredient.ingredient_key == "fragrance",
            )
        ) is None
    after = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()
    assert not any(row["ingredient_key"] == "fragrance" for row in _shelf_ingredients(after, item_id))


async def test_stored_only_confirmation_survives_reanalysis_and_blocks_allergy(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _allergy(app_client, token)
    item_id = await _product(app_client, token, name="Confirmed Stored Candidate")
    await _stored_ingredient(account_id, item_id, confidence=0.55)

    confirmed = await app_client.post(
        "/api/v2/ingredients/confirm", headers=auth(token),
        json={"item_id": item_id, "ingredient_keys": ["fragrance"], "confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    analysed = await _analyse_shelf(app_client, token)
    fragrance = next(row for row in _shelf_ingredients(analysed, item_id) if row["ingredient_key"] == "fragrance")
    assert fragrance["source"] == "user_declared"
    assert fragrance["confidence"] == 1.0
    assert fragrance["needs_confirmation"] is False
    assert any(row["rule_id"] == "rule.user_allergy" for row in _shelf_warnings(analysed, item_id))

    factory = get_sessionmaker()
    async with factory() as session:
        row = await session.scalar(
            select(ProductIngredient).where(
                ProductIngredient.account_id == account_id,
                ProductIngredient.item_id == uuid.UUID(item_id),
                ProductIngredient.ingredient_key == "fragrance",
            )
        )
        assert row is not None
        assert row.confirmed_at is not None
        assert row.source == "user_declared"

    body = await _generate(app_client, token, kinds=["morning"])
    blocked = [row for row in body["care_safety"]["blocked_products"] if row["inventory_item_id"] == item_id]
    assert blocked and blocked[0]["reasons"] == ["confirmed_allergy_match"]


async def test_routines_today_hides_stale_expiry_without_writing(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _product(app_client, token, name="Soon Expired", expiry=date(2026, 8, 20))
    await _generate(app_client, token)

    factory = get_sessionmaker()
    async with factory() as session:
        detail = (await session.execute(
            select(BeautyProductDetail).join(InventoryItem).where(InventoryItem.id == uuid.UUID(item_id))
        )).scalar_one()
        detail.expiry_date = date(2026, 8, 1)
        await session.commit()

    async with factory() as session:
        routine = (await session.execute(
            select(Routine).where(Routine.account_id == account_id, Routine.kind == "morning")
        )).scalar_one()
        before_version = routine.version
        before_steps = {row.id for row in (await session.execute(
            select(RoutineStep).where(RoutineStep.routine_id == routine.id)
        )).scalars().all()}
        before_runs = await session.scalar(
            select(func.count()).select_from(RoutineRecommendationRun).where(
                RoutineRecommendationRun.account_id == account_id
            )
        )
        body = await routines_service.routines_today(
            session, account_id=account_id, on=GENERATION_DATE
        )
        after = await session.get(Routine, routine.id)
        after_steps = {row.id for row in (await session.execute(
            select(RoutineStep).where(RoutineStep.routine_id == routine.id)
        )).scalars().all()}
        after_runs = await session.scalar(
            select(func.count()).select_from(RoutineRecommendationRun).where(
                RoutineRecommendationRun.account_id == account_id
            )
        )
    assert body["refresh_required"] is True
    assert body["refresh_required_kinds"]
    assert all(item_id not in {step["inventory_item_id"] for step in row["steps"]} for row in body["routines"])
    assert after.version == before_version
    assert after_steps == before_steps
    assert after_runs == before_runs


async def test_routines_today_safe_control_returns_saved_routine(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _product(app_client, token, name="Safe Moisturiser", expiry=date(2026, 9, 1))
    await _generate(app_client, token)

    factory = get_sessionmaker()
    async with factory() as session:
        body = await routines_service.routines_today(
            session, account_id=account_id, on=GENERATION_DATE
        )
    assert body["refresh_required"] is False
    assert body["refresh_required_kinds"] == []
    assert body["routines"]


async def test_routines_today_hides_old_engine_and_effort_drift_without_writing(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _effort(app_client, token, "detailed")
    await _product(app_client, token, name="Owned Toner", product_type="toner")
    await _generate(app_client, token, kinds=["morning"])

    factory = get_sessionmaker()
    async with factory() as session:
        routine = (await session.execute(
            select(Routine).where(Routine.account_id == account_id, Routine.kind == "morning")
        )).scalar_one()
        before_version = routine.version
        before_steps = {row.id for row in (await session.execute(
            select(RoutineStep).where(RoutineStep.routine_id == routine.id)
        )).scalars().all()}
        routine.engine_version = "care-v3-03.3"
        await session.commit()

    async with factory() as session:
        old_body = await routines_service.routines_today(
            session, account_id=account_id, on=GENERATION_DATE
        )
        after = await session.get(Routine, routine.id)
        after_steps = {row.id for row in (await session.execute(
            select(RoutineStep).where(RoutineStep.routine_id == routine.id)
        )).scalars().all()}
    assert old_body["refresh_required"] is True
    assert "morning" in old_body["refresh_required_kinds"]
    assert after.version == before_version
    assert after_steps == before_steps

    async with factory() as session:
        current = await session.get(Routine, routine.id)
        current.engine_version = routines_service.ROUTINE_ENGINE_VERSION
        await session.commit()

    await _effort(app_client, token, "minimal")
    async with factory() as session:
        drifted = await routines_service.routines_today(
            session, account_id=account_id, on=GENERATION_DATE
        )
    assert drifted["refresh_required"] is True
    assert "morning" in drifted["refresh_required_kinds"]


async def test_routines_today_hides_continuity_plan_drift_without_writing(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    first_id = await _product(app_client, token, name="First Cleanser", product_type="cleanser")
    second_id = await _product(app_client, token, name="Second Cleanser", product_type="cleanser")
    factory = get_sessionmaker()
    async with factory() as session:
        first = await session.get(InventoryItem, uuid.UUID(first_id))
        first.last_used_at = date(2026, 8, 11)
        await session.commit()
    await _generate(app_client, token, kinds=["morning"])

    async with factory() as session:
        routine = (await session.execute(
            select(Routine).where(Routine.account_id == account_id, Routine.kind == "morning")
        )).scalar_one()
        before_version = routine.version
        second = await session.get(InventoryItem, uuid.UUID(second_id))
        second.last_used_at = date(2026, 8, 12)
        await session.commit()

    async with factory() as session:
        body = await routines_service.routines_today(
            session, account_id=account_id, on=GENERATION_DATE
        )
        after = await session.get(Routine, routine.id)
    assert body["refresh_required"] is True
    assert "morning" in body["refresh_required_kinds"]
    assert after.version == before_version


async def test_routines_today_hides_saved_routine_after_allergy_changes(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _product(
        app_client, token, name="New Fragrance", active_ingredients=["fragrance"]
    )
    await _generate(app_client, token)
    await _allergy(app_client, token)

    factory = get_sessionmaker()
    async with factory() as session:
        body = await routines_service.routines_today(
            session, account_id=account_id, on=GENERATION_DATE
        )
    assert body["refresh_required"] is True
    assert body["refresh_required_kinds"]
    assert all(item_id not in str(row) for row in body["routines"])


async def test_today_expired_and_confirmed_allergy_actions_use_care_safety_copy(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    expired_id = await _product(app_client, token, name="Old Moisturiser", expiry=date(2026, 8, 1))
    body = (await app_client.get(
        f"/api/v2/today?plan_date={GENERATION_DATE.isoformat()}", headers=auth(token)
    )).json()
    actions = body["primary"] + body["optional_modules"]
    safety = next(row for row in actions if row.get("inventory_item_id") == expired_id)
    assert safety["title"] == "Set aside Old Moisturiser"
    assert "date recorded" in safety["body"]
    rendered = str(actions).lower()
    assert "use " + "or replace" not in rendered
    assert "using it now is " + "better" not in rendered
    assert not any(row["action_type"] == "routine" and row.get("inventory_item_id") == expired_id for row in actions)


async def test_today_filters_blocked_first_product_and_keeps_eligible_second_product(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    blocked_id = await _product(
        app_client, token, name="Blocked First", product_type="cleanser", expiry=date(2026, 8, 1)
    )
    eligible_id = await _product(
        app_client, token, name="Eligible Second", product_type="cleanser", expiry=date(2026, 9, 1)
    )
    body = (await app_client.get(
        f"/api/v2/today?plan_date={GENERATION_DATE.isoformat()}", headers=auth(token)
    )).json()
    actions = body["primary"] + body["optional_modules"]
    routine_actions = [row for row in actions if row["action_type"] == "routine"]
    assert blocked_id not in {row.get("inventory_item_id") for row in routine_actions}
    assert eligible_id in {row.get("inventory_item_id") for row in routine_actions}


async def test_today_uses_the_care_plan_continuity_selection(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    continuity_id = await _product(
        app_client, token, name="Continuity Cleanser", product_type="cleanser",
    )
    legacy_value_id = await _product(
        app_client, token, name="Expiring Cleanser", product_type="cleanser",
        expiry=date(2026, 8, 20),
    )
    factory = get_sessionmaker()
    async with factory() as session:
        continuity = await session.get(InventoryItem, uuid.UUID(continuity_id))
        continuity.last_used_at = date(2026, 8, 11)
        await session.commit()

    body = (await app_client.get(
        f"/api/v2/today?plan_date={GENERATION_DATE.isoformat()}", headers=auth(token)
    )).json()
    actions = body["primary"] + body["optional_modules"]
    routine_actions = [row for row in actions if row["action_type"] == "routine"]
    assert continuity_id in {row.get("inventory_item_id") for row in routine_actions}
    assert legacy_value_id not in {row.get("inventory_item_id") for row in routine_actions}


async def test_today_cache_recomputes_after_ingredient_confirmation(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _allergy(app_client, token)
    item_id = await _product(app_client, token, name="Possible Fragrance", product_type="cleanser")
    await _stored_ingredient(account_id, item_id)
    url = f"/api/v2/today?plan_date={GENERATION_DATE.isoformat()}"

    first = (await app_client.get(url, headers=auth(token))).json()
    first_actions = first["primary"] + first["optional_modules"]
    assert any(row["action_type"] == "routine" and row.get("inventory_item_id") == item_id for row in first_actions)
    assert not any(row["title"] == "Keep Possible Fragrance out of your routine" for row in first_actions)
    factory = get_sessionmaker()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == GENERATION_DATE)
        )).scalar_one()
        first_key, first_version = plan.cache_key, plan.version

    confirmed = await app_client.post(
        "/api/v2/ingredients/confirm", headers=auth(token),
        json={"item_id": item_id, "ingredient_keys": ["fragrance"], "confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    second = (await app_client.get(url, headers=auth(token))).json()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == GENERATION_DATE)
        )).scalar_one()
        second_key, second_version = plan.cache_key, plan.version
        inputs = (await session.execute(
            select(DailyPlanInput).where(DailyPlanInput.plan_id == plan.id)
        )).scalars().all()
    assert first_key != second_key
    assert second_version > first_version
    actions = second["primary"] + second["optional_modules"]
    assert any(row["title"] == "Keep Possible Fragrance out of your routine" for row in actions)
    care_inputs = {row.input_key: row.value for row in inputs if row.input_type == "care"}
    assert set(care_inputs) == {
        "care_context_version", "care_decision_version", "care_decision_fingerprint",
        "care_blocked_product_count", "care_confirmation_advisory_count",
        "care_routine_plan_fingerprint", "care_routine_plan_version",
        "care_routine_effort", "care_routine_effort_source",
        "care_cadence_version", "care_hair_wash_cadence_fingerprint",
        "care_hair_wash_status", "care_hair_wash_reason", "care_hair_wash_frequency",
        "care_hair_last_wash_on", "care_hair_next_due_on",
        "care_maintenance_version", "care_maintenance_fingerprint",
        "care_maintenance_due_count", "care_maintenance_tracked_count",
    }
    assert care_inputs["care_context_version"] == "v3-03.12"
    assert care_inputs["care_decision_version"] == "v3-03.11"
    assert care_inputs["care_blocked_product_count"] == 1
    assert care_inputs["care_confirmation_advisory_count"] == 0

    assert second["generated_from"] == "fresh"


async def test_today_cache_recomputes_after_care_effort_change(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _effort(app_client, token, "detailed")
    toner_id = await _product(app_client, token, name="Owned Toner", product_type="toner")
    url = f"/api/v2/today?plan_date={GENERATION_DATE.isoformat()}"

    first = (await app_client.get(url, headers=auth(token))).json()
    first_actions = first["primary"] + first["optional_modules"]
    assert any(row["action_type"] == "routine" and row.get("inventory_item_id") == toner_id for row in first_actions)
    factory = get_sessionmaker()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == GENERATION_DATE)
        )).scalar_one()
        first_key, first_version = plan.cache_key, plan.version

    await _effort(app_client, token, "minimal")
    second = (await app_client.get(url, headers=auth(token))).json()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == GENERATION_DATE)
        )).scalar_one()
        second_key, second_version = plan.cache_key, plan.version
    actions = second["primary"] + second["optional_modules"]
    assert first_key != second_key
    assert second_version > first_version
    assert not any(row["action_type"] == "routine" and row.get("inventory_item_id") == toner_id for row in actions)


async def test_today_cache_recomputes_after_care_selection_change(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    _force_morning(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    first_id = await _product(app_client, token, name="Continuity A", product_type="cleanser")
    second_id = await _product(app_client, token, name="Continuity B", product_type="cleanser")
    factory = get_sessionmaker()
    async with factory() as session:
        first = await session.get(InventoryItem, uuid.UUID(first_id))
        first.last_used_at = date(2026, 8, 11)
        await session.commit()
    url = f"/api/v2/today?plan_date={GENERATION_DATE.isoformat()}"
    first_body = (await app_client.get(url, headers=auth(token))).json()
    first_actions = first_body["primary"] + first_body["optional_modules"]
    assert any(row["action_type"] == "routine" and row.get("inventory_item_id") == first_id for row in first_actions)
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == GENERATION_DATE)
        )).scalar_one()
        first_key, first_version = plan.cache_key, plan.version
        second = await session.get(InventoryItem, uuid.UUID(second_id))
        second.last_used_at = date(2026, 8, 12)
        await session.commit()

    second_body = (await app_client.get(url, headers=auth(token))).json()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == GENERATION_DATE)
        )).scalar_one()
        second_key, second_version = plan.cache_key, plan.version
    actions = second_body["primary"] + second_body["optional_modules"]
    routine_ids = {row.get("inventory_item_id") for row in actions if row["action_type"] == "routine"}
    assert first_key != second_key
    assert second_version > first_version
    assert second_id in routine_ids
    assert first_id not in routine_ids


async def test_account_scoping_keeps_other_account_care_safety_out_of_generation(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token_a, _ = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seed(app_client)
    await _allergy(app_client, token_b)
    private_id = await _product(
        app_client, token_b, name="Private Expired Fragrance", expiry=date(2026, 8, 1),
        active_ingredients=["fragrance"],
    )
    await _stored_ingredient(account_b, private_id)
    await _product(app_client, token_a, name="Account A Moisturiser", expiry=date(2026, 9, 1))
    body = await _generate(app_client, token_a, kinds=["morning"])
    assert private_id not in str(body)
    assert body["care_safety"]["blocked_products"] == []
    shelf_a = (await app_client.get("/api/v2/shelf/summary", headers=auth(token_a))).json()
    assert private_id not in str(shelf_a)

    factory = get_sessionmaker()
    async with factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(InventoryItem).where(InventoryItem.account_id == account_b)
        ) >= 1
