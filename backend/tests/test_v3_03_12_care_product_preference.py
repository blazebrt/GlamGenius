"""V3-03.12 explicit Care product selection preference coverage."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest
from app.domains.care.decisions import decision_fingerprint, evaluate_care_context
from app.domains.care.product_preferences import (
    CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
    is_effective_user_preference,
)
from app.domains.care.routine_plan import CareSelectionBasis, plan_care_routine, routine_plan_fingerprint
from app.domains.inventory.models import InventoryAttribute, InventoryEvent, InventoryItem
from app.domains.routines import service as routines_service
from app.domains.routines.models import Routine, RoutineRecommendationRun, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_care_decisions import _context
from tests.test_care_decisions import _product as _pure_product
from tests.test_v3_03_3_integration import _generate, _seed
from tests.test_v3_03_3_integration import _product as _db_product


def test_preference_predicate_requires_exact_confirmed_user_boolean():
    assert is_effective_user_preference(value=True, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_preference(value=False, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_preference(value=1, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_preference(value=True, source="photo_extracted", verification_state="confirmed")
    assert not is_effective_user_preference(value=True, source="user_declared", verification_state="draft")


def test_preferred_eligible_candidate_wins_without_changing_decisions():
    first = _pure_product("beauty", "cleanser", "A Cleanser")
    second = _pure_product("beauty", "cleanser", "B Cleanser")
    context = _context(first, second)
    before = evaluate_care_context(context)
    preferred = replace(context, preferred_product_ids=frozenset({second.item.id}))
    after = evaluate_care_context(preferred)
    before_plan = plan_care_routine(context, before)
    after_plan = plan_care_routine(preferred, after)
    slot = next(row for row in after_plan.skin_slots if row.slot == "cleanser")
    assert decision_fingerprint(before) == decision_fingerprint(after)
    assert routine_plan_fingerprint(before_plan) != routine_plan_fingerprint(after_plan)
    assert slot.selected_item_id == second.item.id
    assert slot.selection_basis is CareSelectionBasis.USER_PREFERRED
    assert first.item.id in slot.alternative_item_ids


def test_multiple_preferred_candidates_use_existing_continuity_within_subset():
    first = _pure_product("beauty", "cleanser", "A Cleanser", usage_count=1)
    second = _pure_product("beauty", "cleanser", "B Cleanser", usage_count=4)
    third = _pure_product("beauty", "cleanser", "C Cleanser", usage_count=2)
    context = replace(
        _context(first, second, third),
        preferred_product_ids=frozenset({first.item.id, second.item.id}),
    )
    slot = next(row for row in plan_care_routine(context, evaluate_care_context(context)).skin_slots if row.slot == "cleanser")
    assert slot.selected_item_id == second.item.id
    assert slot.selection_basis is CareSelectionBasis.USER_PREFERRED
    assert third.item.id in slot.alternative_item_ids


def test_optional_preference_does_not_activate_minimal_effort():
    toner = _pure_product("beauty", "toner", "Preferred Toner", usage_count=5)
    context = replace(
        _context(toner),
        preferences={"care_routine_effort": "minimal"},
        preferred_product_ids=frozenset({toner.item.id}),
    )
    slot = next(row for row in plan_care_routine(context, evaluate_care_context(context)).skin_slots if row.slot == "toner")
    assert slot.active is False
    assert slot.selected_item_id is None


@pytest.mark.asyncio
async def test_db_context_trust_and_account_scope_for_preferred_ids(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _db_product(app_client, token_a, name="A Cleanser", product_type="cleanser")
    item_b = await _db_product(app_client, token_b, name="B Cleanser", product_type="cleanser")
    factory = get_sessionmaker()
    async with factory() as session:
        session.add_all([
            InventoryAttribute(
                item_id=uuid.UUID(item_a), key=CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY, value=True,
                source="user_declared", confidence=1.0, verification_state="confirmed",
            ),
            InventoryAttribute(
                item_id=uuid.UUID(item_b), key=CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY, value=True,
                source="user_declared", confidence=1.0, verification_state="confirmed",
            ),
        ])
        await session.commit()
        _, context, _ = await routines_service._current_care_decisions(session, account_a, date(2026, 8, 12))
        assert context.preferred_product_ids == frozenset({uuid.UUID(item_a)})
        row = (await session.execute(select(InventoryAttribute).where(
            InventoryAttribute.item_id == uuid.UUID(item_a),
            InventoryAttribute.key == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
        ))).scalar_one()
        row.source = "photo_extracted"
        await session.commit()
        _, context, _ = await routines_service._current_care_decisions(session, account_a, date(2026, 8, 12))
        assert not context.preferred_product_ids


async def _current_step(session, account_id: uuid.UUID, slot: str) -> RoutineStep:
    return (await session.execute(
        select(RoutineStep).join(Routine, Routine.id == RoutineStep.routine_id).where(
            Routine.account_id == account_id, Routine.kind == "morning", Routine.status == "active",
            RoutineStep.slot == slot,
        )
    )).scalar_one()


async def _latest_run(session, account_id: uuid.UUID) -> RoutineRecommendationRun:
    return (await session.execute(
        select(RoutineRecommendationRun).where(RoutineRecommendationRun.account_id == account_id)
        .order_by(RoutineRecommendationRun.created_at.desc()).limit(1)
    )).scalar_one()


@pytest.mark.asyncio
async def test_prefer_api_replaces_selected_product_and_preserves_identity(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _db_product(app_client, token, name="A Cleanser", product_type="cleanser")
    item_b = await _db_product(app_client, token, name="B Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    factory = get_sessionmaker()
    async with factory() as session:
        before = await _current_step(session, account_id, "cleanser")
        before_id = before.id
        assert str(before.inventory_item_id) == item_a

    response = await app_client.post(f"/api/v2/routines/products/{item_b}/prefer", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "preferred"
    assert response.json()["selection_applied"] is True
    async with factory() as session:
        current = await _current_step(session, account_id, "cleanser")
        assert current.id == before_id
        assert str(current.inventory_item_id) == item_b
        run = await _latest_run(session, account_id)
        assert run.inputs["care_adjustment"]["kind"] == "explicit_product_preference"
        snapshot = run.inputs["care_snapshot"]
        assert snapshot["snapshot_version"] == "v3-03.12"
        assert snapshot["product_preferences"]["preferred_product_ids"] == [item_b]
        assert snapshot["decisions"]["decision_fingerprint"] == response.json()["new_decision_fingerprint"]


@pytest.mark.asyncio
async def test_prefer_exclusivity_idempotency_and_unprefer(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _db_product(app_client, token, name="A Cleanser", product_type="cleanser")
    item_b = await _db_product(app_client, token, name="B Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    first = await app_client.post(f"/api/v2/routines/products/{item_a}/prefer", headers=auth(token))
    assert first.status_code == 200, first.text
    factory = get_sessionmaker()
    async with factory() as session:
        a = await session.get(InventoryItem, uuid.UUID(item_a))
        b = await session.get(InventoryItem, uuid.UUID(item_b))
        before_versions = (a.version, b.version)
        before_runs = await session.scalar(select(func.count(RoutineRecommendationRun.id)).where(RoutineRecommendationRun.account_id == account_id))
    second = await app_client.post(f"/api/v2/routines/products/{item_a}/prefer", headers=auth(token))
    assert second.json()["changed"] is False
    replacement = await app_client.post(f"/api/v2/routines/products/{item_b}/prefer", headers=auth(token))
    assert replacement.json()["changed"] is True
    async with factory() as session:
        attrs = (await session.execute(select(InventoryAttribute).where(
            InventoryAttribute.item_id.in_([uuid.UUID(item_a), uuid.UUID(item_b)]),
            InventoryAttribute.key == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
        ))).scalars().all()
        assert {str(row.item_id) for row in attrs if row.value is True} == {item_b}
        events = (await session.execute(select(InventoryEvent).where(InventoryEvent.account_id == account_id))).scalars().all()
        assert {row.event_type for row in events} >= {"care_routine_preferred", "care_routine_preference_cleared"}
        a = await session.get(InventoryItem, uuid.UUID(item_a))
        b = await session.get(InventoryItem, uuid.UUID(item_b))
        assert a.version == before_versions[0] + 1
        assert b.version == before_versions[1] + 1
    clear = await app_client.post(f"/api/v2/routines/products/{item_b}/unprefer", headers=auth(token))
    assert clear.status_code == 200, clear.text
    assert clear.json()["status"] == "standard"
    again = await app_client.post(f"/api/v2/routines/products/{item_b}/unprefer", headers=auth(token))
    assert again.json()["changed"] is False
    async with factory() as session:
        assert await session.scalar(select(func.count(RoutineRecommendationRun.id)).where(RoutineRecommendationRun.account_id == account_id)) >= before_runs + 3


@pytest.mark.asyncio
async def test_prefer_rejects_pause_safety_and_generic_patch_without_writes(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Only Cleanser", product_type="cleanser")
    paused = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    rejected = await app_client.post(f"/api/v2/routines/products/{item_id}/prefer", headers=auth(token))
    assert rejected.status_code == 422
    assert "again before making it preferred" in str(rejected.json())
    bypass = await app_client.patch(
        f"/api/v2/inventory/items/{item_id}", headers=auth(token),
        json={"attributes": [{"key": CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY, "value": True}]},
    )
    assert bypass.status_code == 422


@pytest.mark.asyncio
async def test_pause_preserves_preference_then_resume_restores_selection(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _db_product(app_client, token, name="A Cleanser", product_type="cleanser")
    item_b = await _db_product(app_client, token, name="B Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    assert (await app_client.post(f"/api/v2/routines/products/{item_b}/prefer", headers=auth(token))).status_code == 200
    assert (await app_client.post(f"/api/v2/routines/products/{item_b}/pause", headers=auth(token))).status_code == 200
    factory = get_sessionmaker()
    async with factory() as session:
        current = await _current_step(session, account_id, "cleanser")
        assert str(current.inventory_item_id) == item_a
        attr = (await session.execute(select(InventoryAttribute).where(
            InventoryAttribute.item_id == uuid.UUID(item_b),
            InventoryAttribute.key == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
        ))).scalar_one()
        assert attr.value is True
    assert (await app_client.post(f"/api/v2/routines/products/{item_b}/resume", headers=auth(token))).status_code == 200
    async with factory() as session:
        current = await _current_step(session, account_id, "cleanser")
        assert str(current.inventory_item_id) == item_b


@pytest.mark.asyncio
async def test_preference_privacy_export_and_no_usage_side_effects(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _db_product(app_client, token, name="Export Cleanser", product_type="cleanser")
    await _generate(app_client, token)
    factory = get_sessionmaker()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        before_usage = (item.usage_count, item.last_used_at)
    preferred = await app_client.post(f"/api/v2/routines/products/{item_id}/prefer", headers=auth(token))
    assert preferred.status_code == 200, preferred.text
    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    assert any(row["key"] == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY for row in export["domains"]["inventory"]["attributes"])
    assert any(row["event_type"] == "care_routine_preferred" for row in export["domains"]["inventory"]["events"])
    assert any(run["inputs"].get("care_adjustment", {}).get("kind") == "explicit_product_preference" for run in export["domains"]["routines"]["recommendation_runs"])
    await app_client.post(f"/api/v2/routines/products/{item_id}/unprefer", headers=auth(token))
    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    assert not any(row["key"] == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY for row in export["domains"]["inventory"]["attributes"])
    assert {row["event_type"] for row in export["domains"]["inventory"]["events"]} >= {"care_routine_preferred", "care_routine_preference_cleared"}
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        assert (item.usage_count, item.last_used_at) == before_usage
        assert item.account_id == account_id
