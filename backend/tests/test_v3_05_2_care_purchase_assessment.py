"""V3-05.2 deterministic Care purchase assessment coverage."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.domains.care.decisions import evaluate_care_context
from app.domains.care.routine_plan import plan_care_routine
from app.domains.care.schemas import CARE_CONTEXT_VERSION, CareContext, CareEnvironment
from app.domains.purchase.candidate_truth import CarePurchaseCandidateTruth
from app.domains.purchase.care_assessment import assess_care_purchase
from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    resolve_purchase_strategy,
)
from app.domains.recommendation.context import OwnedItem
from app.domains.recommendation.models import ShoppingCandidate
from app.domains.routines import rules
from app.domains.routines.ontology import COMPATIBILITY_RULES, INGREDIENT_BY_KEY

from tests.conftest import auth

ASSESSMENT_DATE = date(2026, 8, 19)


def _truth(
    *,
    category: str = "beauty",
    slot: str | None = "cleanser",
    recognised: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    candidate_id: uuid.UUID | None = None,
) -> CarePurchaseCandidateTruth:
    candidate_id = candidate_id or uuid.uuid4()
    families = tuple(sorted({INGREDIENT_BY_KEY[key].family for key in recognised}))
    return CarePurchaseCandidateTruth(
        truth_version=PURCHASE_CANDIDATE_TRUTH_VERSION,
        candidate_id=candidate_id,
        category=category,
        customer_category_label="Skin Care" if category == "beauty" else "Hair Care",
        display_name="Prospective Care product",
        brand="Brand",
        product_type=slot,
        care_slot=slot,
        verification_state="user_declared",
        source="manual",
        facts_trusted=True,
        review_required=False,
        recognised_ingredient_keys=tuple(sorted(recognised)),
        recognised_ingredient_families=families,
        missing_information=missing,
    )


def _owned(
    *,
    category: str = "beauty",
    product_type: str = "cleanser",
    display_name: str = "Owned cleanser",
    details: dict | None = None,
) -> OwnedItem:
    return OwnedItem(
        id=uuid.uuid4(), category=category, subcategory=None,
        display_name=display_name, brand="Owned Brand",
        details=details or {"product_type": product_type}, condition="good",
        usage_count=1, last_used_at=None, purchase_price=None, currency="INR",
    )


def _context(
    products: tuple[OwnedItem, ...] = (),
    *,
    allergies: tuple[str, ...] = (),
    paused: frozenset[uuid.UUID] = frozenset(),
) -> CareContext:
    built_skin = tuple(rules.build_products(products, "beauty"))
    built_hair = tuple(rules.build_products(products, "hair"))
    return CareContext(
        context_version=CARE_CONTEXT_VERSION,
        account_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        plan_date=ASSESSMENT_DATE,
        skin_facts={}, hair_facts={}, preferences={},
        environment=CareEnvironment(
            weather_snapshot_id=None, air_quality_snapshot_id=None, condition=None,
            temp_min_c=None, temp_max_c=None, humidity=None, precipitation_chance=None,
            uv_index=None, aqi=None, aqi_index_system=None, aqi_category=None,
            climate_region=None, calendar_prior=None, season=None,
            temperature_band=None, moisture_regime=None, daily_regime=None,
            climate_confidence=None, climate_reason=None,
        ),
        primary_event=None, allergies=allergies,
        skin_products=built_skin, hair_products=built_hair,
        draft_product_count=0, missing_information=(),
        paused_product_ids=paused, preferred_product_ids=frozenset(),
    )


def _assess(truth: CarePurchaseCandidateTruth, context: CareContext):
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    return assess_care_purchase(truth, context, decisions, plan)


def test_versions_and_frozen_strategy():
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION == "v3-05.2"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert resolve_purchase_strategy("beauty").state == "inactive"


def test_required_gap_covered_optional_and_redundancy_statuses():
    empty = _context()
    gap = _assess(_truth(slot="cleanser"), empty)
    assert gap.role_utility["status"] == "addresses_required_gap"
    assert gap.role_utility["required"] is True
    assert gap.redundancy["status"] == "none_eligible_owned_same_slot"
    assert gap.redundancy["eligible_owned_same_slot_count"] == 0

    owned = _owned(display_name="Current cleanser")
    covered = _assess(_truth(slot="cleanser"), _context((owned,)))
    assert covered.role_utility["status"] == "required_role_already_covered"
    assert covered.redundancy["status"] == "one_eligible_owned_same_slot"
    assert covered.redundancy["selected_owned_item_id"] == str(owned.id)

    multiple = _assess(
        _truth(slot="cleanser"),
        _context((owned, _owned(display_name="Second cleanser"))),
    )
    assert multiple.redundancy["status"] == "multiple_eligible_owned_same_slot"
    assert [row["owned_item_id"] for row in multiple.redundancy["eligible_owned_same_slot"]] == sorted(
        row["owned_item_id"] for row in multiple.redundancy["eligible_owned_same_slot"]
    )

    optional = _assess(_truth(slot="face_oil"), _context())
    assert optional.role_utility["status"] == "optional_role_not_required"
    assert optional.role_utility["required"] is False
    assert "gap" not in optional.role_utility["status"]

    blocked = _assess(_truth(slot="cleanser"), _context((owned,), paused=frozenset({owned.id})))
    assert blocked.redundancy["status"] == "none_eligible_owned_same_slot"
    assert blocked.redundancy["blocked_owned_same_slot"][0]["owned_item_id"] == str(owned.id)
    assert "user_paused_for_routine" in blocked.redundancy["blocked_owned_same_slot"][0]["reason_codes"]

    hair_gap = _assess(_truth(category="hair", slot="shampoo"), _context())
    assert hair_gap.role_utility["status"] == "addresses_required_gap"
    assert hair_gap.role_utility["care_slot"] == "shampoo"


def test_user_constraints_overlap_and_unknown_ingredient_boundary():
    owned = _owned(details={"product_type": "cleanser", "ingredients_text": "Niacinamide"})
    assessed = _assess(
        _truth(slot="cleanser", recognised=("niacinamide",)),
        _context((owned,), allergies=("niacinamide",)),
    )
    assert assessed.user_constraints["status"] == "confirmed_user_constraint_match"
    assert assessed.user_constraints["matched_ingredient_keys"] == ["niacinamide"]
    overlap = _assess(
        _truth(candidate_id=assessed.candidate_id, slot="cleanser", recognised=("niacinamide",)),
        _context((owned,)),
    )
    assert overlap.same_slot_ingredient_overlap[0]["ingredient_key"] == "niacinamide"
    assert overlap.same_slot_ingredient_overlap[0]["owned_item_ids"] == [str(owned.id)]

    missing = _assess(
        _truth(slot="cleanser", missing=("ingredients",)), _context()
    )
    assert missing.compatibility["status"] == "insufficient_ingredient_information"
    assert missing.user_constraints["status"] == "insufficient_ingredient_information"
    assert "safe" not in str(missing.as_dict()).lower()

    partial = _assess(
        _truth(
            slot="cleanser", recognised=("niacinamide",),
            missing=("unrecognised_ingredient:mystery-label-term",),
        ),
        _context((owned,)),
    )
    assert partial.identity_confidence["status"] == "trusted_with_missing_information"
    assert partial.compatibility["coverage"] == "partial"
    assert "mystery-label-term" in partial.identity_confidence["missing_information"][0]


def test_compatibility_reuses_canonical_rules_and_selected_plan_only():
    retinoid = _owned(
        display_name="Retinoid cleanser",
        details={"product_type": "cleanser", "ingredients_text": "Retinol"},
    )
    assessment = _assess(
        _truth(slot="treatment", recognised=("glycolic_acid",)),
        _context((retinoid,)),
    )
    caution = next(row for row in COMPATIBILITY_RULES if row.rule_id == "rule.retinoid_aha")
    assert assessment.compatibility["status"] == "reviewed_rule_matches"
    assert assessment.compatibility["findings"][0]["rule_id"] == caution.rule_id
    assert assessment.compatibility["findings"][0]["severity"] == caution.severity

    niacinamide = _owned(
        display_name="Niacinamide cleanser",
        details={"product_type": "cleanser", "ingredients_text": "Niacinamide"},
    )
    info = _assess(
        _truth(slot="treatment", recognised=("ascorbic_acid",)),
        _context((niacinamide,)),
    )
    assert info.compatibility["findings"][0]["rule_id"] == "rule.vitamin_c_niacinamide"
    assert info.compatibility["findings"][0]["severity"] == "info"

    selected = _owned(
        display_name="Selected cleanser",
        details={"product_type": "cleanser", "ingredients_text": "Glycerin"},
    )
    paused_conflict = _owned(
        display_name="Paused retinoid",
        details={"product_type": "cleanser", "ingredients_text": "Retinol"},
    )
    selected_only = _assess(
        _truth(slot="treatment", recognised=("glycolic_acid",)),
        _context((selected, paused_conflict), paused=frozenset({paused_conflict.id})),
    )
    assert selected_only.compatibility["findings"] == []
    assert str(paused_conflict.id) not in selected_only.compatibility["compared_owned_item_ids"]


def test_fingerprint_is_material_only_and_payload_has_no_verdict_or_score():
    context = _context()
    first = _assess(_truth(slot="cleanser", recognised=("niacinamide",)), context)
    second = _assess(_truth(candidate_id=first.candidate_id, slot="cleanser", recognised=("niacinamide",)), context)
    assert first.assessment_fingerprint == second.assessment_fingerprint
    payload = first.as_dict()
    flattened = str(payload).lower()
    for forbidden in (
        "verdict", "buy", "wait", "skip", "should_buy", "purchase_score",
        "quality_score", "compatibility_score", "redundancy_score",
    ):
        assert forbidden not in flattened
    assert payload["dimensions"]["evidence_support"]["status"] == "not_assessed"
    assert payload["dimensions"]["value_context"]["status"] == "not_assessed"

    changed_ingredient = _assess(_truth(candidate_id=first.candidate_id, slot="cleanser", recognised=("retinol",)), context)
    assert changed_ingredient.assessment_fingerprint != first.assessment_fingerprint

    # Price, currency and marketing purpose are deliberately outside candidate
    # truth and therefore cannot change the assessment fingerprint/material.
    same_facts = _truth(candidate_id=first.candidate_id, slot="cleanser", recognised=("niacinamide",))
    assert _assess(same_facts, context).assessment_fingerprint == first.assessment_fingerprint


def test_generic_rule_helpers_preserve_canonical_outputs():
    assert rules.declared_allergy_ingredient_keys(("niacinamide",)) == frozenset({"niacinamide"})
    for rule in COMPATIBILITY_RULES:
        assert rules.compatibility_rule_applies_to_families(
            rule, {rule.family_a}, {rule.family_b}
        ) is True


@pytest.mark.asyncio
async def test_draft_candidate_rejected_before_care_assembly(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    factory = __import__("app.shared.database.sql", fromlist=["get_sessionmaker"]).get_sessionmaker()
    candidate_id = uuid.uuid4()
    async with factory() as session:
        session.add(ShoppingCandidate(
            id=candidate_id, account_id=account_id, source="photo_extracted",
            category="beauty", display_name="Unconfirmed serum", details={"product_type": "serum"},
            verification_state="draft", extraction_confidence=0.99,
        ))
        await session.commit()
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("planning context must not be gathered for a draft")
    monkeypatch.setattr("app.domains.purchase.service.planning_context.gather", fail_if_called)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["field"] == "verification_state"


@pytest.mark.asyncio
async def test_real_assessment_is_read_only_and_account_scoped(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    created = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={
            "source": "manual",
            "item": {
                "category": "beauty", "display_name": "Assessment cleanser",
                "details": {"product_type": "cleanser", "purpose": "metadata"},
            },
            "client_mutation_id": "v3-05-2-assessment",
        },
    )
    assert created.status_code == 200, created.text
    candidate_id = created.json()["candidate"]["id"]
    before = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}", headers=auth(token)
    )
    first = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    second = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    assert first.json()["dimensions"]["role_utility"]["status"] == "addresses_required_gap"
    assert first.json()["dimensions"]["redundancy"]["eligible_owned_same_slot_count"] == 0
    assert "boundary" in first.json()
    assert "verdict" not in first.json()
    after = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}", headers=auth(token)
    )
    assert before.json()["candidate"] == after.json()["candidate"]
    assert (await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment",
        headers=auth(intruder_token),
    )).status_code == 404
    assert account_id
