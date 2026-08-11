"""Pure V3-03.11 Care product pause contracts."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.domains.care.decisions import (
    CareDecisionAuthority,
    CareDecisionReasonCode,
    evaluate_care_context,
)
from app.domains.care.product_preferences import is_effective_user_pause
from app.domains.care.routine_plan import plan_care_routine
from app.domains.routines import service as routines_service

from tests.test_care_decisions import _context, _product


def test_only_exact_confirmed_user_boolean_is_an_effective_pause():
    assert is_effective_user_pause(value=True, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_pause(value=False, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_pause(value=1, source="user_declared", verification_state="confirmed")
    assert not is_effective_user_pause(value=True, source="photo_extracted", verification_state="confirmed")
    assert not is_effective_user_pause(value=True, source="user_declared", verification_state="draft")


def test_pause_is_a_user_constraint_and_creates_required_gap():
    cleanser = _product("beauty", "cleanser")
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
    product = _product("beauty", "cleanser", expiry=date(2026, 8, 1))
    context = replace(_context(product), paused_product_ids=frozenset({product.item.id}))
    row = evaluate_care_context(context).product_decisions[0]
    assert {reason.code for reason in row.blocking_reasons} == {
        CareDecisionReasonCode.PRODUCT_EXPIRED,
        CareDecisionReasonCode.USER_PAUSED_FOR_ROUTINE,
    }


def test_untrusted_pause_context_is_not_constructed_by_preference_predicate():
    product = _product("beauty", "cleanser")
    assert not is_effective_user_pause(value=True, source="photo_extracted", verification_state="confirmed")
    context = _context(product)
    decision = evaluate_care_context(context).product_decisions[0]
    assert decision.eligible is True


def test_safety_payload_excludes_pause_only_products():
    product = _product("beauty", "cleanser")
    context = replace(_context(product), paused_product_ids=frozenset({product.item.id}))
    decisions = evaluate_care_context(context)
    payload = routines_service._care_safety_payload(context, decisions)
    assert payload["blocked_products"] == []


def test_pause_changes_plan_selection_without_a_second_selector():
    first = _product("beauty", "cleanser")
    second = _product("beauty", "cleanser")
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
