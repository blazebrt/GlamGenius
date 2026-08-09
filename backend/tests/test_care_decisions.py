"""Pure V3-03.2 Care safety and core-slot decisions."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.domains.care.decisions import (
    CARE_DECISION_VERSION,
    CareDecisionAuthority,
    CareDecisionReasonCode,
    evaluate_care_context,
)
from app.domains.care.schemas import CareContext, CareEnvironment, CareEvent, CareFact
from app.domains.recommendation.context import OwnedItem
from app.domains.routines import rules
from app.domains.routines.ontology import HAIR_SLOTS, SKIN_SLOTS
from app.domains.routines.parser import ParsedIngredient
from app.domains.routines.rules import ShelfProduct


def _environment(**changes):
    values = dict(
        weather_snapshot_id=None, air_quality_snapshot_id=None, condition=None,
        temp_min_c=None, temp_max_c=None, humidity=None,
        precipitation_chance=None, uv_index=None, aqi=None,
        aqi_index_system=None, aqi_category=None, climate_region=None,
        calendar_prior=None, season=None, temperature_band=None,
        moisture_regime=None, daily_regime=None, climate_confidence=None,
        climate_reason=None,
    )
    values.update(changes)
    return CareEnvironment(**values)


def _product(
    category: str,
    product_type: str,
    *,
    expiry: date | None = None,
    ingredient_key: str | None = None,
    confirmed: bool = True,
):
    item = OwnedItem(
        id=uuid.uuid4(), category=category, subcategory=product_type,
        display_name=f"{category}-{product_type}-{uuid.uuid4().hex[:6]}",
        brand=None, details={}, condition="good", usage_count=0,
        last_used_at=None, purchase_price=None, currency="INR",
    )
    ingredients = []
    if ingredient_key:
        ingredients.append(ParsedIngredient(
            key=ingredient_key, display_name=ingredient_key.title(),
            family="fragrance" if ingredient_key == "fragrance" else "humectant",
            matched_text=ingredient_key, position=None,
            confidence=1.0 if confirmed else 0.55,
            source="user_declared" if confirmed else "photo_extracted",
        ))
    return ShelfProduct(
        item=item,
        slot=rules.slot_for_product_type(product_type, category),
        ingredients=ingredients,
        effective_expiry=expiry,
    )


def _context(*products, allergies=(), **changes):
    skin = tuple(row for row in products if row.item.category == "beauty")
    hair = tuple(row for row in products if row.item.category == "hair")
    return CareContext(
        context_version="v3-03.1", account_id=uuid.uuid4(),
        plan_date=date(2026, 8, 12), skin_facts={}, hair_facts={},
        preferences={}, environment=_environment(**changes), primary_event=None,
        allergies=allergies, skin_products=skin, hair_products=hair,
        draft_product_count=0, missing_information=(),
    )


def _codes(rows):
    return {reason.code for reason in rows}


def test_expired_skin_and_hair_products_are_blocked_and_leave_core_gaps():
    context = _context(
        _product("beauty", "moisturiser", expiry=date(2026, 8, 11)),
        _product("hair", "shampoo", expiry=date(2026, 8, 11)),
    )
    decisions = evaluate_care_context(context)

    assert decisions.decision_version == CARE_DECISION_VERSION
    assert all(not row.eligible for row in decisions.product_decisions)
    assert all(_codes(row.blocking_reasons) == {CareDecisionReasonCode.PRODUCT_EXPIRED} for row in decisions.product_decisions)
    assert any(row.slot == "moisturiser" and not row.filled for row in decisions.skin_core_slots)
    assert any(row.slot == "shampoo" and not row.filled for row in decisions.hair_core_slots)


def test_expired_and_valid_same_slot_returns_all_candidates_without_ranking():
    expired = _product("beauty", "moisturiser", expiry=date(2026, 8, 11))
    valid = _product("beauty", "moisturiser", expiry=date(2026, 8, 20))
    decisions = evaluate_care_context(_context(expired, valid))
    slot = next(row for row in decisions.skin_core_slots if row.slot == "moisturiser")

    assert len(decisions.product_decisions) == 2
    assert slot.filled is True
    assert slot.eligible_item_ids == (valid.item.id,)
    assert slot.blocked_item_ids == (expired.item.id,)
    assert all(not hasattr(row, "selected_item_id") for row in decisions.product_decisions)


def test_expiring_soon_remains_eligible_with_advisory_only():
    product = _product("hair", "conditioner", expiry=date(2026, 8, 20))
    decision = evaluate_care_context(_context(product)).product_decisions[0]
    assert decision.eligible is True
    assert _codes(decision.blocking_reasons) == set()
    assert _codes(decision.advisory_reasons) == {CareDecisionReasonCode.PRODUCT_EXPIRING_SOON}
    assert decision.advisory_reasons[0].authority == CareDecisionAuthority.SYSTEM_POLICY


def test_confirmed_and_unconfirmed_allergen_hits_have_distinct_outcomes():
    confirmed = _product("beauty", "moisturiser", ingredient_key="fragrance")
    unconfirmed = _product("hair", "conditioner", ingredient_key="fragrance", confirmed=False)
    decisions = evaluate_care_context(_context(confirmed, unconfirmed, allergies=("fragrance",)))
    by_id = {row.item_id: row for row in decisions.product_decisions}

    assert by_id[confirmed.item.id].eligible is False
    assert _codes(by_id[confirmed.item.id].blocking_reasons) == {CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH}
    assert by_id[unconfirmed.item.id].eligible is True
    assert _codes(by_id[unconfirmed.item.id].advisory_reasons) == {CareDecisionReasonCode.INGREDIENT_CONFIRMATION_NEEDED}
    assert by_id[unconfirmed.item.id].advisory_reasons[0].authority == CareDecisionAuthority.USER_CONSTRAINT


def test_legacy_allergy_findings_and_exclusion_parity_uses_richer_matcher():
    confirmed = _product("beauty", "moisturiser", ingredient_key="fragrance")
    unconfirmed = _product("hair", "conditioner", ingredient_key="fragrance", confirmed=False)
    products = (confirmed, unconfirmed)
    findings = rules.allergy_findings(products, ("fragrance",))
    excluded = rules.excluded_by_allergy(products, ("fragrance",))

    assert [row.item_ids for row in findings] == [[confirmed.id], [unconfirmed.id]]
    assert excluded == {confirmed.id, unconfirmed.id}
    assert "low confidence" in findings[1].detail


def test_core_slots_are_derived_from_ontology_and_optional_absence_is_not_a_gap():
    decisions = evaluate_care_context(_context())
    assert tuple(row.slot for row in decisions.skin_core_slots) == tuple(
        row.key for row in SKIN_SLOTS if row.required
    )
    assert tuple(row.slot for row in decisions.hair_core_slots) == tuple(
        row.key for row in HAIR_SLOTS if row.required
    )
    assert decisions.skin_core_gap_count == len(decisions.skin_core_slots)
    assert decisions.hair_core_gap_count == len(decisions.hair_core_slots)
    assert "toner" not in {row.slot for row in decisions.skin_core_slots}
    assert "hair_mask" not in {row.slot for row in decisions.hair_core_slots}


def test_profile_environment_and_event_facts_do_not_invent_v3_03_2_rules():
    product = _product("beauty", "moisturiser", expiry=date(2026, 8, 20))
    first = _context(product, humidity=20, uv_index=1, aqi=30, daily_regime="cool_dry")
    second = replace(first, environment=_environment(humidity=95, uv_index=11, aqi=250, daily_regime="hot_wet"))
    first_result = evaluate_care_context(first)
    second_result = evaluate_care_context(second)
    assert first_result == second_result


def test_profile_facts_and_events_do_not_change_hard_safety_decisions():
    product = _product("hair", "conditioner", expiry=date(2026, 8, 20))
    first = _context(product)
    fact = CareFact(
        key="care_skin_sensitivity", value="not_sure", fact_source="care_user_declared",
        record_source="user_declared", confidence=1.0, verification_state="confirmed",
        profile_attribute_id=None, explicit_unknown=True,
    )
    event = CareEvent(
        id=uuid.uuid4(), starts_at=datetime(2026, 8, 12, 18, tzinfo=UTC),
        ends_at=None, all_day=False, occasion_key="date", confidence=1.0,
        user_confirmed=True,
    )
    second = replace(first, skin_facts={fact.key: fact}, primary_event=event)
    assert evaluate_care_context(first) == evaluate_care_context(second)


def test_decisions_are_deterministic_and_immutable():
    context = _context(
        _product("beauty", "moisturiser", expiry=date(2026, 8, 20)),
        _product("hair", "conditioner", expiry=date(2026, 8, 20)),
    )
    first = evaluate_care_context(context)
    assert first == evaluate_care_context(context)
    with pytest.raises((AttributeError, TypeError)):
        first.product_decisions.append(None)
