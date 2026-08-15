"""Pure V3-03.4 minimum-effective Care routine planning tests."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.domains.care.decisions import evaluate_care_context
from app.domains.care.routine_plan import (
    CARE_ROUTINE_PLAN_VERSION,
    CareEffortSource,
    CareInclusionReason,
    CareRoutineEffort,
    CareSelectionBasis,
    plan_care_routine,
    routine_plan_fingerprint,
)
from app.domains.care.schemas import CareContext, CareEnvironment, CareEvent, CareFact
from app.domains.recommendation.context import OwnedItem
from app.domains.routines import compiler
from app.domains.routines.ontology import HAIR_SLOTS, SKIN_SLOTS, slot_for_product_type
from app.domains.routines.parser import ParsedIngredient
from app.domains.routines.rules import ShelfProduct
from app.domains.routines.selection import RoutineSelectionPlan, RoutineSlotDirective

ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-000000000301")
PLAN_DATE = date(2026, 8, 12)


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


def _fact(key: str, value: str) -> CareFact:
    return CareFact(
        key=key, value=value, fact_source="care_user_declared",
        record_source="user_declared", confidence=1.0,
        verification_state="confirmed", profile_attribute_id=None,
        explicit_unknown=value == "not_sure",
    )


def _product(
    category: str,
    product_type: str,
    name: str,
    *,
    usage_count: int = 0,
    last_used_at: date | None = None,
    expiry: date | None = None,
    brand: str | None = None,
    price: float | None = None,
    remaining_percent: int | None = None,
    ingredient_confidence: float | None = None,
) -> ShelfProduct:
    item_id = uuid.uuid5(ACCOUNT_ID, f"{category}:{product_type}:{name}")
    item = OwnedItem(
        id=item_id, category=category, subcategory=product_type,
        display_name=name, brand=brand, details={
            "remaining_percent": remaining_percent,
        }, condition="good", usage_count=usage_count,
        last_used_at=last_used_at, purchase_price=price, currency="INR",
    )
    ingredients = []
    if ingredient_confidence is not None:
        ingredients = [ParsedIngredient(
            key="fragrance", display_name="Fragrance", family="fragrance",
            matched_text="fragrance", position=None,
            confidence=ingredient_confidence,
            source="photo_extracted" if ingredient_confidence < 0.6 else "user_declared",
        )]
    return ShelfProduct(
        item=item, slot=slot_for_product_type(product_type, category),
        ingredients=ingredients, effective_expiry=expiry,
    )


def _context(*products: ShelfProduct, effort: str | None = None, **changes) -> CareContext:
    skin = tuple(row for row in products if row.item.category == "beauty")
    hair = tuple(row for row in products if row.item.category == "hair")
    preferences = {"care_routine_effort": _fact("care_routine_effort", effort)} if effort else {}
    return CareContext(
        context_version="v3-03.1", account_id=ACCOUNT_ID, plan_date=PLAN_DATE,
        skin_facts={}, hair_facts={}, preferences=preferences,
        environment=changes.pop("environment", _environment()),
        primary_event=changes.pop("primary_event", None), allergies=changes.pop("allergies", ()),
        skin_products=skin, hair_products=hair, draft_product_count=0,
        missing_information=(), **changes,
    )


def _plan(*products: ShelfProduct, effort: str | None = None, **changes):
    context = _context(*products, effort=effort, **changes)
    return context, plan_care_routine(context, evaluate_care_context(context))


def _slot(plan, key: str):
    return next(row for row in (*plan.skin_slots, *plan.hair_slots) if row.slot == key)


def _selection(plan) -> RoutineSelectionPlan:
    return RoutineSelectionPlan(
        plan_version=plan.plan_version,
        plan_fingerprint=routine_plan_fingerprint(plan),
        effort=plan.resolved_effort.value,
        effort_source=plan.effort_source.value,
        directives=tuple(
            RoutineSlotDirective(
                slot=row.slot, category=row.category, required=row.required,
                active=row.active,
                selected_item_id=str(row.selected_item_id) if row.selected_item_id else None,
                is_gap=row.is_gap,
            )
            for row in (*plan.skin_slots, *plan.hair_slots)
        ),
    )


def test_contract_version_and_ontology_required_slots_are_canonical():
    _, plan = _plan(effort="minimal")
    assert plan.plan_version == CARE_ROUTINE_PLAN_VERSION == "v3-03.12"
    assert tuple(row.slot for row in plan.skin_slots if row.required) == tuple(row.key for row in SKIN_SLOTS if row.required)
    assert tuple(row.slot for row in plan.hair_slots if row.required) == tuple(row.key for row in HAIR_SLOTS if row.required)
    assert _slot(plan, "toner").is_gap is False
    assert _slot(plan, "hair_mask").is_gap is False


def test_v3_03_5_compiler_renders_exact_plan_selection_without_ranker(monkeypatch):
    older = _product("beauty", "moisturiser", "A recently used moisturiser", last_used_at=PLAN_DATE)
    recovery_favourite = _product(
        "beauty", "moisturiser", "B expiring moisturiser", expiry=PLAN_DATE,
        remaining_percent=90, price=999,
    )
    context, plan = _plan(older, recovery_favourite, effort="minimal")
    selected = _slot(plan, "moisturiser")
    assert selected.selected_item_id == older.item.id
    projection = _selection(plan)
    monkeypatch.setattr(
        compiler.rules_engine, "rank_for_slot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy ranker called")),
    )

    routine = compiler.compile_routine(
        compiler.ROUTINE_MORNING, [older, recovery_favourite],
        today=context.plan_date,
        eligibility=compiler.RoutineEligibility(
            eligible_item_ids=frozenset({older.id, recovery_favourite.id}),
        ),
        selection_plan=projection,
    )
    moisturiser = next(row for row in routine.steps if row.slot == "moisturiser")
    assert moisturiser.item_id == str(older.item.id)


def test_v3_03_5_minimal_projection_omits_owned_optional_slots():
    products = (
        _product("beauty", "cleanser", "Cleanser"),
        _product("beauty", "moisturiser", "Moisturiser"),
        _product("beauty", "sunscreen", "Sunscreen"),
        _product("beauty", "toner", "Owned toner"),
        _product("hair", "shampoo", "Shampoo"),
        _product("hair", "conditioner", "Conditioner"),
        _product("hair", "leave-in", "Owned leave-in"),
    )
    context, plan = _plan(*products, effort="minimal")
    compiled = compiler.compile_all(
        list(context.skin_products), list(context.hair_products),
        today=context.plan_date,
        eligibility=compiler.RoutineEligibility(
            eligible_item_ids=frozenset(product.id for product in products),
        ),
        selection_plan=_selection(plan),
    )
    slots = {step.slot for routine in compiled for step in routine.steps}
    assert {"cleanser", "moisturiser", "sunscreen", "shampoo", "conditioner"} <= slots
    assert "toner" not in slots
    assert "leave_in" not in slots


def test_v3_03_5_active_optional_without_selection_fails_loudly():
    with pytest.raises(ValueError, match="Active optional"):
        RoutineSelectionPlan(
            plan_version="v3-03.12", plan_fingerprint="x", effort="balanced",
            effort_source="user_declared",
            directives=(RoutineSlotDirective(
                slot="toner", category="beauty", required=False,
                active=True, selected_item_id=None,
            ),),
        )


def test_minimal_keeps_required_skin_and_hair_only():
    products = (
        _product("beauty", "cleanser", "Cleanser"),
        _product("beauty", "moisturiser", "Moisturiser"),
        _product("beauty", "sunscreen", "Sunscreen"),
        _product("beauty", "toner", "Toner", usage_count=4),
        _product("beauty", "treatment", "Serum"),
        _product("hair", "shampoo", "Shampoo"),
        _product("hair", "conditioner", "Conditioner"),
        _product("hair", "leave-in", "Leave-in", usage_count=4),
        _product("hair", "hair mask", "Mask"),
        _product("hair", "styling cream", "Styling"),
    )
    _, plan = _plan(*products, effort="minimal")
    assert {row.slot for row in (*plan.skin_slots, *plan.hair_slots) if row.active} == {
        "cleanser", "moisturiser", "sunscreen", "shampoo", "conditioner",
    }
    assert all(not row.active for row in plan.skin_slots if not row.required)
    assert all(not row.active for row in plan.hair_slots if not row.required)


def test_balanced_activates_established_optional_but_not_unused_optional():
    used = _product("beauty", "treatment", "Used Serum", usage_count=1)
    unused = _product("beauty", "toner", "Unused Toner")
    _, plan = _plan(used, unused, effort="balanced")
    assert _slot(plan, "treatment").active is True
    assert _slot(plan, "treatment").inclusion_reason == CareInclusionReason.BALANCED_ESTABLISHED_USE
    assert _slot(plan, "toner").active is False
    assert _slot(plan, "toner").inclusion_reason == CareInclusionReason.BALANCED_NO_ESTABLISHED_USE


def test_detailed_activates_every_owned_optional_without_optional_gaps():
    products = (_product("beauty", "toner", "Toner"), _product("hair", "hair mask", "Mask"))
    _, plan = _plan(*products, effort="detailed")
    assert _slot(plan, "toner").active is True
    assert _slot(plan, "hair_mask").active is True
    assert all(not row.is_gap for row in (*plan.skin_slots, *plan.hair_slots) if not row.required)
    assert _slot(plan, "eye").inclusion_reason == CareInclusionReason.NO_ELIGIBLE_OWNED_PRODUCT


def test_missing_and_not_sure_effort_resolve_to_distinct_balanced_defaults():
    missing_context, missing = _plan(_product("beauty", "toner", "Toner"))
    sure_context, sure = _plan(_product("beauty", "toner", "Toner"), effort="not_sure")
    assert missing.resolved_effort == sure.resolved_effort == CareRoutineEffort.BALANCED
    assert missing.effort_source == CareEffortSource.SYSTEM_DEFAULT_MISSING
    assert sure.effort_source == CareEffortSource.SYSTEM_DEFAULT_NOT_SURE
    assert "care_routine_effort" not in missing_context.preferences
    assert sure_context.preferences["care_routine_effort"].value == "not_sure"


def test_explicit_effort_values_are_used_without_reinterpretation():
    for value, expected in (("minimal", CareRoutineEffort.MINIMAL), ("balanced", CareRoutineEffort.BALANCED), ("detailed", CareRoutineEffort.DETAILED)):
        _, plan = _plan(effort=value)
        assert plan.resolved_effort == expected
        assert plan.effort_source == CareEffortSource.USER_DECLARED


def test_blocked_candidates_are_absent_and_required_block_becomes_gap():
    expired = _product("beauty", "moisturiser", "Expired", expiry=date(2026, 8, 1))
    valid = _product("beauty", "moisturiser", "Valid", expiry=date(2026, 9, 1))
    _, plan = _plan(expired, valid, effort="detailed")
    slot = _slot(plan, "moisturiser")
    assert slot.selected_item_id == valid.item.id
    assert expired.item.id not in slot.candidate_item_ids
    only_context, only_plan = _plan(expired, effort="detailed")
    only_slot = _slot(only_plan, "moisturiser")
    assert only_slot.active is True and only_slot.is_gap is True
    assert only_slot.candidate_item_ids == ()
    assert only_context.plan_date == PLAN_DATE


def test_confirmed_allergy_is_blocked_but_unconfirmed_allergen_remains_eligible():
    confirmed = _product("beauty", "moisturiser", "Confirmed", ingredient_confidence=1.0)
    possible = _product("hair", "conditioner", "Possible", ingredient_confidence=0.55)
    context = _context(confirmed, possible, effort="detailed", allergies=("fragrance",))
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    assert confirmed.item.id not in _slot(plan, "moisturiser").candidate_item_ids
    assert possible.item.id in _slot(plan, "conditioner").candidate_item_ids


def test_recent_use_wins_and_usage_count_is_the_next_basis():
    recent = _product("beauty", "moisturiser", "Recent", last_used_at=date(2026, 8, 10), usage_count=1)
    older = _product("beauty", "moisturiser", "Older", last_used_at=date(2026, 8, 1), usage_count=9)
    _, plan = _plan(recent, older, effort="detailed")
    slot = _slot(plan, "moisturiser")
    assert slot.selected_item_id == recent.item.id
    assert slot.selection_basis == CareSelectionBasis.RECENT_USE

    high = _product("beauty", "cleanser", "High", usage_count=8)
    low = _product("beauty", "cleanser", "Low", usage_count=2)
    _, count_plan = _plan(high, low, effort="detailed")
    count_slot = _slot(count_plan, "cleanser")
    assert count_slot.selected_item_id == high.item.id
    assert count_slot.selection_basis == CareSelectionBasis.USAGE_COUNT


def test_stable_fallback_and_duplicate_consolidation_are_deterministic():
    first = _product("beauty", "toner", "Alpha")
    second = _product("beauty", "toner", "Beta")
    third = _product("beauty", "toner", "Gamma")
    context = _context(first, second, third, effort="detailed")
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    shuffled = plan_care_routine(
        replace(context, skin_products=(third, first, second)),
        replace(decisions, product_decisions=tuple(reversed(decisions.product_decisions))),
    )
    slot = _slot(plan, "toner")
    assert slot.selected_item_id == first.item.id
    assert slot.selection_basis == CareSelectionBasis.STABLE_FALLBACK
    assert len(slot.candidate_item_ids) == 3
    assert len(slot.alternative_item_ids) == 2
    assert plan == shuffled
    assert routine_plan_fingerprint(plan) == routine_plan_fingerprint(shuffled)


def test_continuity_beats_low_use_expiry_and_ignores_brand_price_remaining():
    continuing = _product("beauty", "treatment", "Continuing", last_used_at=date(2026, 8, 11), brand="A", price=1)
    low_use_expiring = _product("beauty", "treatment", "Alternative", expiry=date(2026, 8, 20), brand="Z", price=999, remaining_percent=1)
    _, plan = _plan(continuing, low_use_expiring, effort="detailed")
    slot = _slot(plan, "treatment")
    assert slot.selected_item_id == continuing.item.id
    assert slot.selection_basis == CareSelectionBasis.RECENT_USE

    changed = _product("beauty", "treatment", "Continuing", last_used_at=date(2026, 8, 11), brand="Z", price=999, remaining_percent=1)
    changed_alt = _product("beauty", "treatment", "Alternative", expiry=date(2026, 8, 20), brand="A", price=1, remaining_percent=99)
    _, changed_plan = _plan(changed, changed_alt, effort="detailed")
    assert changed_plan == plan


def test_environment_profile_and_event_do_not_change_plan_or_fingerprint():
    product = _product("beauty", "toner", "Toner", usage_count=2)
    base_context, base_plan = _plan(product, effort="balanced")
    changed_context = replace(
        base_context,
        environment=_environment(humidity=99, uv_index=12, aqi=300, season="monsoon"),
        primary_event=CareEvent(
            id=uuid.uuid4(), starts_at=datetime(2026, 8, 12, 18, tzinfo=UTC),
            ends_at=None, all_day=False, occasion_key="date", confidence=1.0,
            user_confirmed=True,
        ),
        skin_facts={"care_skin_sensitivity": _fact("care_skin_sensitivity", "high")},
        hair_facts={"care_hair_pattern": _fact("care_hair_pattern", "curly")},
        preferences={
            "care_routine_effort": _fact("care_routine_effort", "balanced"),
            "care_skin_usual_feel": _fact("care_skin_usual_feel", "dry"),
            "care_hair_processing": _fact("care_hair_processing", "processed"),
        },
    )
    changed_plan = plan_care_routine(changed_context, evaluate_care_context(changed_context))
    assert changed_plan == base_plan
    assert routine_plan_fingerprint(changed_plan) == routine_plan_fingerprint(base_plan)


def test_only_routine_effort_changes_optional_activation():
    optional = _product("beauty", "treatment", "Serum", usage_count=0)
    _, minimal = _plan(optional, effort="minimal")
    _, balanced = _plan(optional, effort="balanced")
    _, detailed = _plan(optional, effort="detailed")
    assert _slot(minimal, "treatment").active is False
    assert _slot(balanced, "treatment").active is False
    assert _slot(detailed, "treatment").active is True


def test_contracts_are_immutable_and_collections_are_tuples():
    _, plan = _plan(_product("beauty", "cleanser", "Cleanser"), effort="minimal")
    assert isinstance(plan.skin_slots, tuple)
    assert isinstance(plan.hair_slots, tuple)
    with pytest.raises((AttributeError, TypeError)):
        plan.skin_slots.append(None)  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        plan.resolved_effort = CareRoutineEffort.DETAILED  # type: ignore[misc]


def test_account_and_date_mismatches_fail_loudly():
    context = _context(_product("beauty", "cleanser", "Cleanser"), effort="minimal")
    decisions = evaluate_care_context(context)
    with pytest.raises(ValueError, match="account_id"):
        plan_care_routine(context, replace(decisions, account_id=uuid.uuid4()))
    with pytest.raises(ValueError, match="plan_date"):
        plan_care_routine(context, replace(decisions, plan_date=date(2026, 8, 13)))


def test_fingerprint_changes_for_plan_fields_but_not_unused_context():
    product = _product("beauty", "treatment", "Serum", usage_count=2)
    _, balanced = _plan(product, effort="balanced")
    _, detailed = _plan(product, effort="detailed")
    assert routine_plan_fingerprint(balanced) != routine_plan_fingerprint(detailed)
    assert len(routine_plan_fingerprint(balanced)) == 64
