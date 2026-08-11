"""Pure V3-03.11 Care product pause contracts."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select
from app.domains.care.decisions import (
    CareDecisionAuthority,
    CareDecisionReasonCode,
    evaluate_care_context,
)
from app.domains.care.product_preferences import is_effective_user_pause
from app.domains.care.routine_plan import plan_care_routine
from app.domains.routines import service as routines_service
from app.domains.inventory.models import InventoryAttribute, InventoryEvent, InventoryItem
from app.domains.routines.models import RoutineRecommendationRun
from app.shared.database.sql import get_sessionmaker

from tests.conftest import auth
from tests.test_care_decisions import _context, _product as _pure_product
from tests.test_v3_03_3_integration import _generate, _product as _db_product, _seed


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

    no_op = await app_client.post(f"/api/v2/routines/products/{item_id}/pause", headers=auth(token))
    assert no_op.status_code == 200
    assert no_op.json()["changed"] is False
    assert no_op.json()["status"] == "already_paused"

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
