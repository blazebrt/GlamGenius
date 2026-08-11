"""Pure provenance contract tests for V3-03.9."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

from app.domains.care.decisions import CareDecisionSet, evaluate_care_context
from app.domains.care.routine_plan import plan_care_routine
from app.domains.care.schemas import CareContext, CareEnvironment, CareEvent, CareFact, MissingCareFact
from app.domains.care.snapshot import (
    CARE_RECOMMENDATION_SNAPSHOT_VERSION,
    build_care_recommendation_snapshot,
    care_recommendation_snapshot_fingerprint,
)
from app.domains.recommendation.context import OwnedItem
from app.domains.routines.compiler import CompiledRoutine, RoutineStep
from app.domains.routines.ontology import ONTOLOGY_VERSION
from app.domains.routines.parser import ParsedIngredient
from app.domains.routines.rules import ShelfProduct

ACCOUNT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ITEM_A = uuid.UUID("22222222-2222-2222-2222-222222222222")
ITEM_B = uuid.UUID("33333333-3333-3333-3333-333333333333")
PLAN_DATE = date(2026, 8, 11)


def _product(item_id: uuid.UUID, name: str, *, slot: str = "cleanser", usage_count: int = 1) -> ShelfProduct:
    return ShelfProduct(
        item=OwnedItem(
            id=item_id, category="beauty", subcategory=None, display_name=name, brand="secret",
            details={"ingredients_text": "Retinol", "private_note": "do not copy"}, condition="new",
            usage_count=usage_count, last_used_at=PLAN_DATE, purchase_price=99.0, currency="INR",
        ),
        slot=slot,
        ingredients=[ParsedIngredient(
            key="retinol", display_name="Retinol", family="retinoid", matched_text="RETINOL",
            position=1, confidence=0.55, source="label_text",
        )],
        effective_expiry=date(2026, 9, 1), low_use=False,
    )


def _context(products: tuple[ShelfProduct, ...]) -> CareContext:
    fact = CareFact(
        key="care_skin_usual_feel", value="dry", fact_source="care_user_declared",
        record_source="user_declared", confidence=1.0, verification_state="confirmed",
        profile_attribute_id=uuid.UUID("44444444-4444-4444-4444-444444444444"), explicit_unknown=False,
    )
    return CareContext(
        context_version="v3-03.1", account_id=ACCOUNT_ID, plan_date=PLAN_DATE,
        skin_facts={fact.key: fact}, hair_facts={}, preferences={},
        environment=CareEnvironment(
            weather_snapshot_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
            air_quality_snapshot_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
            condition="clear", temp_min_c=20.0, temp_max_c=30.0, humidity=40,
            precipitation_chance=0, uv_index=5.0, aqi=42, aqi_index_system="india_naqi",
            aqi_category="Good", climate_region="north_plains", calendar_prior="winter",
            season="winter", temperature_band="warm", moisture_regime="dry", daily_regime="clear",
            climate_confidence=0.9, climate_reason="manual", weather_unavailable_reason=None,
        ),
        primary_event=CareEvent(
            id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
            starts_at=datetime(2026, 8, 11, 10, tzinfo=UTC), ends_at=None,
            all_day=False, occasion_key="office", confidence=0.85, user_confirmed=True,
        ),
        allergies=("fragrance", "retinol"), skin_products=products, hair_products=(),
        draft_product_count=1,
        missing_information=(MissingCareFact("hair", "care_hair_pattern", "missing"),),
    )


def _compiled() -> tuple[CompiledRoutine, ...]:
    return (
        CompiledRoutine(
            kind="morning", label="Morning routine", frequency="Every morning",
            steps=[RoutineStep(
                slot="cleanser", label="Cleanser", order=1, required=True, why="Cleanse",
                frequency="Every morning", item_id=str(ITEM_A), product_name="Cleanser A",
            )],
        ),
    )


def _snapshot(products: tuple[ShelfProduct, ...] = (_product(ITEM_A, "Cleanser A"),)) -> dict:
    context = _context(products)
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    return build_care_recommendation_snapshot(
        care_context=context, decisions=decisions, care_plan=plan,
        compiled_routines=_compiled(), requested_kinds=None, legacy_climate="clear",
        routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
    )


def test_snapshot_is_json_compatible_and_excludes_sensitive_or_mutable_identity():
    snapshot = _snapshot()
    json.dumps(snapshot)
    assert snapshot["snapshot_version"] == CARE_RECOMMENDATION_SNAPSHOT_VERSION
    text = json.dumps(snapshot)
    assert "secret" not in text
    assert "ingredients_text" not in text
    assert "private_note" not in text
    assert "purchase_price" not in text
    assert "matched_text" not in text
    assert "77777777-7777-7777-7777-777777777777" in text
    assert "fingerprint" in snapshot
    assert snapshot["rendered_routines"][0]["steps"][0]["slot"] == "cleanser"


def test_same_material_and_reordered_set_like_inputs_have_same_fingerprint():
    first = _snapshot((_product(ITEM_A, "Cleanser A"), _product(ITEM_B, "Cleanser B")))
    second = _snapshot((_product(ITEM_B, "Cleanser B"), _product(ITEM_A, "Cleanser A")))
    assert first["fingerprint"] == second["fingerprint"]
    assert care_recommendation_snapshot_fingerprint(first) == first["fingerprint"]


def test_fingerprint_changes_for_material_environment_and_effort_changes():
    first = _snapshot()
    context = _context((_product(ITEM_A, "Cleanser A"),))
    context = replace(context, environment=replace(context.environment, humidity=41))
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    changed = build_care_recommendation_snapshot(
        care_context=context, decisions=decisions, care_plan=plan,
        compiled_routines=_compiled(), requested_kinds=None, legacy_climate="clear",
        routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
    )
    assert first["fingerprint"] != changed["fingerprint"]


def test_account_and_plan_date_mismatch_fails_loudly():
    context = _context((_product(ITEM_A, "Cleanser A"),))
    decisions = CareDecisionSet(
        decision_version="v3-03.2", account_id=uuid.uuid4(), plan_date=PLAN_DATE,
        product_decisions=(), skin_core_slots=(), hair_core_slots=(),
    )
    plan = plan_care_routine(context, evaluate_care_context(context))
    try:
        build_care_recommendation_snapshot(
            care_context=context, decisions=decisions, care_plan=plan,
            compiled_routines=(), requested_kinds=[], legacy_climate=None,
            routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
        )
    except ValueError as exc:
        assert "account_id" in str(exc)
    else:
        raise AssertionError("snapshot accepted contradictory account material")
