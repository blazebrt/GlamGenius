"""Pure V3-03.11 Care product pause contracts."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest
from app.domains.care.decisions import (
    CareDecisionAuthority,
    CareDecisionReasonCode,
    evaluate_care_context,
)
from app.domains.care.product_preferences import is_effective_user_pause
from app.domains.care.routine_plan import plan_care_routine
from app.domains.inventory.models import InventoryAttribute, InventoryEvent, InventoryItem
from app.domains.routines import service as routines_service
from app.domains.routines.models import (
    ProductIngredient,
    Routine,
    RoutineAdherence,
    RoutineRecommendationRun,
    RoutineStep,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_care_decisions import _context
from tests.test_care_decisions import _product as _pure_product
from tests.test_v3_03_3_integration import _allergy, _generate, _seed, _stored_ingredient
from tests.test_v3_03_3_integration import _product as _db_product


async def _latest_run(session, account_id):
    return (await session.execute(
        select(RoutineRecommendationRun)
        .where(RoutineRecommendationRun.account_id == account_id)
        .order_by(RoutineRecommendationRun.created_at.desc())
        .limit(1)
    )).scalar_one()


async def _current_step(session, account_id, *, kind: str, slot: str) -> RoutineStep:
    return (await session.execute(
        select(RoutineStep)
        .join(Routine, Routine.id == RoutineStep.routine_id)
        .where(
            Routine.account_id == account_id,
            Routine.kind == kind,
            Routine.status == "active",
            RoutineStep.slot == slot,
        )
    )).scalar_one()


async def _count(session, model, account_id):
    return await session.scalar(select(func.count(model.id)).where(model.account_id == account_id))


@pytest.mark.asyncio
async def test_db_care_context_reads_only_trusted_persisted_pause(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Persisted Cleanser", product_type="cleanser")
    factory = get_sessionmaker()

    async with factory() as session:
        session.add(InventoryAttribute(
            item_id=uuid.UUID(item_id), key="care_routine_paused", value=True,
            source="user_declared", confidence=1.0, verification_state="confirmed",
        ))
        await session.commit()
        _, context, _ = await routines_service._current_care_decisions(
            session, account_id, date(2026, 8, 12),
        )
        assert uuid.UUID(item_id) in context.paused_product_ids

        row = (await session.execute(
            select(InventoryAttribute).where(
                InventoryAttribute.item_id == uuid.UUID(item_id),
                InventoryAttribute.key == "care_routine_paused",
            )
        )).scalar_one()
        row.source = "photo_extracted"
        await session.commit()
        _, context, _ = await routines_service._current_care_decisions(
            session, account_id, date(2026, 8, 12),
        )
        assert uuid.UUID(item_id) not in context.paused_product_ids

        row.source = "user_declared"
        row.verification_state = "draft"
        await session.commit()
        _, context, _ = await routines_service._current_care_decisions(
            session, account_id, date(2026, 8, 12),
        )
        assert uuid.UUID(item_id) not in context.paused_product_ids


@pytest.mark.asyncio
async def test_db_care_context_pause_ids_are_account_scoped(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _db_product(app_client, token_a, name="Account A Cleanser", product_type="cleanser")
    item_b = await _db_product(app_client, token_b, name="Account B Cleanser", product_type="cleanser")
    factory = get_sessionmaker()
    async with factory() as session:
        session.add_all([
            InventoryAttribute(item_id=uuid.UUID(item_a), key="care_routine_paused", value=True, source="user_declared", confidence=1.0, verification_state="confirmed"),
            InventoryAttribute(item_id=uuid.UUID(item_b), key="care_routine_paused", value=True, source="user_declared", confidence=1.0, verification_state="confirmed"),
        ])
        await session.commit()
        _, context_a, _ = await routines_service._current_care_decisions(session, account_a, date(2026, 8, 12))
        assert context_a.paused_product_ids == frozenset({uuid.UUID(item_a)})
        assert uuid.UUID(item_b) not in context_a.paused_product_ids


@pytest.mark.asyncio
async def test_api_pause_replaces_selected_product_and_preserves_step_identity(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _db_product(app_client, token, name="A Cleanser", product_type="cleanser")
    item_b = await _db_product(app_client, token, name="B Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    factory = get_sessionmaker()
    async with factory() as session:
        before_step = await _current_step(session, account_id, kind="morning", slot="cleanser")
        assert str(before_step.inventory_item_id) == item_a
        before_step_id = before_step.id

    response = await app_client.post(f"/api/v2/routines/products/{item_a}/pause", headers=auth(token))
    assert response.status_code == 200, response.text
    async with factory() as session:
        step = await _current_step(session, account_id, kind="morning", slot="cleanser")
        run = await _latest_run(session, account_id)
        decisions = run.inputs["care_snapshot"]["decisions"]["product_decisions"]
        by_id = {row["item_id"]: row for row in decisions}
        assert by_id[item_a]["eligible"] is False
        assert {reason["code"] for reason in by_id[item_a]["blocking_reasons"]} == {"user_paused_for_routine"}
        assert by_id[item_b]["eligible"] is True
        assert str(step.inventory_item_id) == item_b
        assert step.id == before_step_id


@pytest.mark.asyncio
async def test_api_pause_required_product_persists_canonical_gap(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Only Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    factory = get_sessionmaker()
    async with factory() as session:
        before_step = await _current_step(session, account_id, kind="morning", slot="cleanser")
        before_step_id = before_step.id
    response = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert response.status_code == 200, response.text
    async with factory() as session:
        step = await _current_step(session, account_id, kind="morning", slot="cleanser")
        run = await _latest_run(session, account_id)
        slots = run.inputs["care_snapshot"]["routine_plan"]["slots"]
        cleanser = next(row for row in slots if row["slot"] == "cleanser" and row["category"] == "beauty")
        assert (cleanser["required"], cleanser["active"], cleanser["selected_item_id"], cleanser["is_gap"]) == (True, True, None, True)
        assert step.id == before_step_id
        assert step.inventory_item_id is None
        assert step.is_gap is True


@pytest.mark.asyncio
async def test_optional_removal_detaches_but_does_not_duplicate_adherence(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Established Toner", product_type="toner")
    usage = await app_client.post(
        f"/api/v2/inventory/items/{item_id}/usage",
        headers=auth(token),
        json={"used_on": "2026-08-11", "quantity": 1},
    )
    assert usage.status_code == 200, usage.text
    await _generate(app_client, token)
    factory = get_sessionmaker()
    async with factory() as session:
        toner = await _current_step(session, account_id, kind="morning", slot="toner")
        toner_step_id = toner.id
    completed = await app_client.post(
        f"/api/v2/routines/steps/{toner_step_id}/complete",
        headers=auth(token),
        json={"done_on": "2026-08-11", "completed": True},
    )
    assert completed.status_code == 200, completed.text
    async with factory() as session:
        adherence = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
                RoutineAdherence.slot == "toner",
            )
        )).scalars().all()
        assert len(adherence) == 1
        assert adherence[0].step_id == toner_step_id

    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    async with factory() as session:
        current = (await session.execute(
            select(RoutineStep).join(Routine, Routine.id == RoutineStep.routine_id).where(
                Routine.account_id == account_id, Routine.kind == "morning", RoutineStep.slot == "toner",
            )
        )).scalar_one_or_none()
        adherence = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id, RoutineAdherence.slot == "toner",
            )
        )).scalars().all()
        assert current is None
        assert len(adherence) == 1
        assert adherence[0].step_id is None

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    async with factory() as session:
        current = await _current_step(session, account_id, kind="morning", slot="toner")
        adherence = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id, RoutineAdherence.slot == "toner",
            )
        )).scalars().all()
        assert current.inventory_item_id == uuid.UUID(item_id)
        assert len(adherence) == 1


@pytest.mark.asyncio
async def test_pause_validation_generic_patch_and_cross_account_ownership(
    app_client, db_clean, registered_supabase_user,
):
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token_a, name="Validation Cleanser", product_type="cleanser")
    factory = get_sessionmaker()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        item.verification_state = "draft"
        await session.commit()
        version = item.version
    draft = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token_a))
    assert draft.status_code == 422
    assert "confirm" in str(draft.json()).casefold()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        assert item.version == version
        assert not (await session.execute(select(InventoryAttribute).where(InventoryAttribute.item_id == uuid.UUID(item_id), InventoryAttribute.key == "care_routine_paused"))).scalars().first()

    bypass = await app_client.patch(
        f"/api/v2/inventory/items/{item_id}", headers=auth(token_a),
        json={"attributes": [{"key": "care_routine_paused", "value": True}]},
    )
    assert bypass.status_code == 422
    cross_pause = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token_b))
    cross_resume = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token_b))
    assert cross_pause.status_code == 404
    assert cross_resume.status_code == 404

    wardrobe = await app_client.post(
        "/api/v2/inventory/items", headers=auth(token_a),
        json={"category": "wardrobe", "display_name": "Blue Shirt"},
    )
    assert wardrobe.status_code in (200, 201), wardrobe.text
    unsupported = await app_client.post(
        f"/api/v2/routines/products/{wardrobe.json()['id']}/pause", headers=auth(token_a),
    )
    assert unsupported.status_code == 422
    assert "Only Skin Care and Hair Care products can be paused from Care routines." in str(unsupported.json())


@pytest.mark.asyncio
async def test_snapshot_history_adjustment_separation_and_resume_determinism(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    monkeypatch.setattr(
        "app.domains.routines.service.clock.local_today",
        lambda *_args, **_kwargs: date(2026, 8, 12),
    )
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Snapshot Cleanser", product_type="cleanser")
    initial = await app_client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"as_of": "2026-08-12", "explain": False},
    )
    assert initial.status_code == 200, initial.text
    factory = get_sessionmaker()
    async with factory() as session:
        run_a = await _latest_run(session, account_id)
        snapshot_a = run_a.inputs["care_snapshot"]

    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    async with factory() as session:
        run_b = await _latest_run(session, account_id)
        snapshot_b = run_b.inputs["care_snapshot"]
        assert run_b.inputs["care_adjustment"] == {
            "version": "v3-03.11", "kind": "explicit_product_pause", "item_id": item_id,
            "from_state": "active", "to_state": "paused",
        }
        decision = next(row for row in snapshot_b["decisions"]["product_decisions"] if row["item_id"] == item_id)
        assert decision["eligible"] is False
        assert {reason["code"] for reason in decision["blocking_reasons"]} == {"user_paused_for_routine"}
        assert {reason["authority"] for reason in decision["blocking_reasons"]} == {"user_constraint"}

    normal = await app_client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"as_of": "2026-08-12", "explain": False},
    )
    assert normal.status_code == 200, normal.text
    assert normal
    async with factory() as session:
        run_normal = await _latest_run(session, account_id)
        assert "care_adjustment" not in run_normal.inputs
        assert run_normal.inputs["care_snapshot"]["fingerprint"] == snapshot_b["fingerprint"]
        preserved = await session.get(RoutineRecommendationRun, run_a.id)
        assert preserved.inputs["care_snapshot"] == snapshot_a

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    async with factory() as session:
        run_c = await _latest_run(session, account_id)
        assert run_c.inputs["care_adjustment"] == {
            "version": "v3-03.11", "kind": "explicit_product_resume", "item_id": item_id,
            "from_state": "paused", "to_state": "active",
        }
        assert run_c.inputs["care_snapshot"]["snapshot_version"] == "v3-03.12"
        assert run_c.inputs["care_snapshot"]["fingerprint"] == snapshot_a["fingerprint"]


@pytest.mark.asyncio
async def test_routines_today_is_fresh_and_pause_only_is_not_safety(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Today Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    today = await app_client.get(
        "/api/v2/routines/today?on=2026-08-12", headers=auth(token),
    )
    assert today.status_code == 200, today.text
    body = today.json()
    assert body["refresh_required"] is False
    assert item_id not in str(body["routines"])
    assert item_id not in str(body["care_safety"]["blocked_products"])

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    today_after = await app_client.get(
        "/api/v2/routines/today?on=2026-08-12", headers=auth(token),
    )
    assert today_after.status_code == 200, today_after.text
    assert today_after.json()["refresh_required"] is False


@pytest.mark.asyncio
async def test_pause_and_resume_preserve_expired_safety_reason(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(
        app_client, token, name="Expired Cleanser", product_type="cleanser", expiry=date(2026, 8, 1),
    )
    await _generate(app_client, token)
    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    factory = get_sessionmaker()
    async with factory() as session:
        run = await _latest_run(session, account_id)
        decision = next(row for row in run.inputs["care_snapshot"]["decisions"]["product_decisions"] if row["item_id"] == item_id)
        assert {reason["code"] for reason in decision["blocking_reasons"]} == {"product_expired", "user_paused_for_routine"}

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    async with factory() as session:
        run = await _latest_run(session, account_id)
        decision = next(row for row in run.inputs["care_snapshot"]["decisions"]["product_decisions"] if row["item_id"] == item_id)
        assert {reason["code"] for reason in decision["blocking_reasons"]} == {"product_expired"}
        assert decision["eligible"] is False


@pytest.mark.asyncio
async def test_pause_preserves_confirmed_allergy_and_unconfirmed_ingredient_advisory(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(
        app_client, token, name="Allergic Cleanser", product_type="cleanser",
        active_ingredients=["fragrance"],
    )
    await _stored_ingredient(account_id, item_id, confirmed=False)
    await _allergy(app_client, token)
    await _generate(app_client, token)

    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    today = await app_client.get("/api/v2/routines/today?on=2026-08-12", headers=auth(token))
    assert today.status_code == 200, today.text
    blocked = next(
        row for row in today.json()["care_safety"]["blocked_products"]
        if row["inventory_item_id"] == item_id
    )
    assert set(blocked["reasons"]) >= {"confirmed_allergy_match"}

    factory = get_sessionmaker()
    async with factory() as session:
        ingredient = (await session.execute(
            select(ProductIngredient).where(
                ProductIngredient.account_id == account_id,
                ProductIngredient.item_id == uuid.UUID(item_id),
            )
        )).scalar_one()
        before = (ingredient.confidence, ingredient.source, ingredient.needs_confirmation, ingredient.confirmed_at)

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    async with factory() as session:
        ingredient = (await session.execute(
            select(ProductIngredient).where(
                ProductIngredient.account_id == account_id,
                ProductIngredient.item_id == uuid.UUID(item_id),
            )
        )).scalar_one()
        assert (ingredient.confidence, ingredient.source, ingredient.needs_confirmation, ingredient.confirmed_at) == before
        run = await _latest_run(session, account_id)
        row = next(
            row for row in run.inputs["care_snapshot"]["decisions"]["product_decisions"]
            if row["item_id"] == item_id
        )
        assert {reason["code"] for reason in row["blocking_reasons"]} == {"confirmed_allergy_match"}


@pytest.mark.asyncio
async def test_pause_resume_privacy_export_retains_preference_history(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Export Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    inventory = export["domains"]["inventory"]
    routines = export["domains"]["routines"]
    assert any(
        row["key"] == "care_routine_paused" and row["value"] is True
        for row in inventory["attributes"]
    )
    assert any(row["event_type"] == "care_routine_paused" for row in inventory["events"])
    assert any(
        run["inputs"].get("care_adjustment", {}).get("kind") == "explicit_product_pause"
        for run in routines["recommendation_runs"]
    )

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    inventory = export["domains"]["inventory"]
    routines = export["domains"]["routines"]
    assert not any(row["key"] == "care_routine_paused" for row in inventory["attributes"])
    assert {row["event_type"] for row in inventory["events"]} >= {"care_routine_paused", "care_routine_resumed"}
    assert any(
        run["inputs"].get("care_adjustment", {}).get("kind") == "explicit_product_resume"
        for run in routines["recommendation_runs"]
    )
    assert all(run["account_id"] == str(account_id) for run in routines["recommendation_runs"])


def test_only_exact_confirmed_user_boolean_is_an_effective_pause():
    assert is_effective_user_pause(value=True, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_pause(value=False, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_pause(value=1, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_pause(value=True, source="photo_extracted", verification_state="confirmed")
    assert not is_effective_user_pause(value=True, source="user_declared", verification_state="draft")


def test_pause_is_a_user_constraint_and_creates_required_gap():
    cleanser = _pure_product("beauty", "cleanser")
    context = replace(_context(cleanser), paused_product_ids=frozenset({cleanser.item.id}))
    decision = evaluate_care_context(context)
    row = decision.product_decisions[0]
    assert row.eligible is False
    assert row.blocking_reasons == (
        row.blocking_reasons[0],
    )
    assert row.blocking_reasons[0].code is CareDecisionReasonCode.USER_PAUSED_FOR_ROUTINE
    assert row.blocking_reasons[0].authority is CareDecisionAuthority.USER_CONSTRAINT
    cleanser_slot = next(slot for slot in decision.skin_core_slots if slot.slot == "cleanser")
    assert cleanser_slot.filled is False
    plan = plan_care_routine(context, decision)
    planned = next(slot for slot in plan.skin_slots if slot.slot == "cleanser")
    assert (planned.required, planned.active, planned.selected_item_id, planned.is_gap) == (True, True, None, True)


def test_pause_does_not_erase_expiry_safety_reason():
    product = _pure_product("beauty", "cleanser", expiry=date(2026, 8, 1))
    context = replace(_context(product), paused_product_ids=frozenset({product.item.id}))
    row = evaluate_care_context(context).product_decisions[0]
    assert {reason.code for reason in row.blocking_reasons} == {
        CareDecisionReasonCode.PRODUCT_EXPIRED,
        CareDecisionReasonCode.USER_PAUSED_FOR_ROUTINE,
    }


def test_untrusted_pause_context_is_not_constructed_by_preference_predicate():
    product = _pure_product("beauty", "cleanser")
    assert not is_effective_user_pause(value=True, source="photo_extracted", verification_state="confirmed")
    context = _context(product)
    decision = evaluate_care_context(context).product_decisions[0]
    assert decision.eligible is True


def test_safety_payload_excludes_pause_only_products():
    product = _pure_product("beauty", "cleanser")
    context = replace(_context(product), paused_product_ids=frozenset({product.item.id}))
    decisions = evaluate_care_context(context)
    payload = routines_service._care_safety_payload(context, decisions)
    assert payload["blocked_products"] == []


def test_pause_changes_plan_selection_without_a_second_selector():
    first = _pure_product("beauty", "cleanser")
    second = _pure_product("beauty", "cleanser")
    active_context = _context(first, second)
    active_decisions = evaluate_care_context(active_context)
    active_plan = plan_care_routine(active_context, active_decisions)
    paused_context = replace(active_context, paused_product_ids=frozenset({first.item.id}))
    paused_decisions = evaluate_care_context(paused_context)
    paused_plan = plan_care_routine(paused_context, paused_decisions)
    active_slot = next(slot for slot in active_plan.skin_slots if slot.slot == "cleanser")
    paused_slot = next(slot for slot in paused_plan.skin_slots if slot.slot == "cleanser")
    assert active_slot.selected_item_id in {first.item.id, second.item.id}
    assert paused_slot.selected_item_id == second.item.id
    assert first.item.id not in paused_slot.candidate_item_ids


@pytest.mark.asyncio
async def test_pause_resume_api_is_audited_and_idempotent(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Pauseable Cleanser", product_type="cleanser")
    await _generate(app_client, token)

    factory = get_sessionmaker()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        initial_version = item.version
        initial_runs = await session.scalar(
            select(func.count(RoutineRecommendationRun.id)).where(
                RoutineRecommendationRun.account_id == account_id,
            )
        )

    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    assert paused.json()["changed"] is True
    assert paused.json()["status"] == "paused"

    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        attribute = (await session.execute(
            select(InventoryAttribute).where(
                InventoryAttribute.item_id == uuid.UUID(item_id),
            )
        )).scalars().all()
        events = (await session.execute(
            select(InventoryEvent).where(
                InventoryEvent.account_id == account_id,
                InventoryEvent.item_id == uuid.UUID(item_id),
            )
        )).scalars().all()
        runs = await session.scalar(
            select(func.count(RoutineRecommendationRun.id)).where(
                RoutineRecommendationRun.account_id == account_id,
            )
        )
        assert item.status == "active"
        assert item.version == initial_version + 1
        pause_attr = next(row for row in attribute if row.key == "care_routine_paused")
        assert pause_attr.value is True
        assert pause_attr.source == "user_declared"
        assert pause_attr.verification_state == "confirmed"
        assert pause_attr.confidence == 1.0
        assert [row.event_type for row in events].count("care_routine_paused") == 1
        assert runs == initial_runs + 1
        pause_snapshot = {
            "version": item.version,
            "events": len(events),
            "runs": runs,
            "routines": tuple(sorted(
                (row.id, row.version) for row in (
                    await session.execute(select(Routine).where(Routine.account_id == account_id))
                ).scalars().all()
            )),
            "adherence": await _count(session, RoutineAdherence, account_id),
            "attribute": (pause_attr.id, pause_attr.value),
        }

    no_op = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert no_op.status_code == 200
    assert no_op.json()["changed"] is False
    assert no_op.json()["status"] == "already_paused"
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        events = (await session.execute(select(InventoryEvent).where(InventoryEvent.item_id == uuid.UUID(item_id)))).scalars().all()
        runs = await _count(session, RoutineRecommendationRun, account_id)
        routines = tuple(sorted((row.id, row.version) for row in (await session.execute(select(Routine).where(Routine.account_id == account_id))).scalars().all()))
        adherence = await _count(session, RoutineAdherence, account_id)
        attr = (await session.execute(select(InventoryAttribute).where(InventoryAttribute.item_id == uuid.UUID(item_id), InventoryAttribute.key == "care_routine_paused"))).scalar_one()
        assert {"version": item.version, "events": len(events), "runs": runs, "routines": routines, "adherence": adherence, "attribute": (attr.id, attr.value)} == pause_snapshot

    resumed = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["changed"] is True
    assert resumed.json()["status"] == "active"

    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        attribute = (await session.execute(
            select(InventoryAttribute).where(
                InventoryAttribute.item_id == uuid.UUID(item_id),
            )
        )).scalars().all()
        events = (await session.execute(
            select(InventoryEvent).where(
                InventoryEvent.account_id == account_id,
                InventoryEvent.item_id == uuid.UUID(item_id),
            )
        )).scalars().all()
        assert item.status == "active"
        assert item.version == initial_version + 2
        assert not any(row.key == "care_routine_paused" for row in attribute)
        assert [row.event_type for row in events].count("care_routine_paused") == 1
        assert [row.event_type for row in events].count("care_routine_resumed") == 1
        resume_snapshot = {
            "version": item.version,
            "events": len(events),
            "runs": await _count(session, RoutineRecommendationRun, account_id),
            "routines": tuple(sorted((row.id, row.version) for row in (await session.execute(select(Routine).where(Routine.account_id == account_id))).scalars().all())),
            "adherence": await _count(session, RoutineAdherence, account_id),
        }

    second_resume = await app_client.post(f"/api/v2/routines/products/{item_id}/resume", headers=auth(token))
    assert second_resume.status_code == 200
    assert second_resume.json()["changed"] is False
    assert second_resume.json()["status"] == "already_active"
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        events = (await session.execute(select(InventoryEvent).where(InventoryEvent.item_id == uuid.UUID(item_id)))).scalars().all()
        routines = tuple(sorted((row.id, row.version) for row in (await session.execute(select(Routine).where(Routine.account_id == account_id))).scalars().all()))
        assert {"version": item.version, "events": len(events), "runs": await _count(session, RoutineRecommendationRun, account_id), "routines": routines, "adherence": await _count(session, RoutineAdherence, account_id)} == resume_snapshot
