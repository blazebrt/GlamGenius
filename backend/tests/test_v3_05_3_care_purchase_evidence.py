"""V3-05.3 Care Purchase Evidence projection and runtime boundaries."""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.domains.inventory.models import InventoryAttribute, InventoryItem
from app.domains.purchase.care_evidence import (
    IngredientUtilityPath,
    ReviewedEvidencePath,
    project_care_purchase_evidence,
)
from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    resolve_purchase_strategy,
)
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseEvaluation,
    PurchaseEvaluationFactor,
    RecommendationEntitlement,
    RecommendationRun,
    ShoppingCandidate,
)
from app.domains.routines.models import Routine, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth


def _assessment(*, fingerprint: str = "assessment-a", missing: tuple[str, ...] = ()) -> dict:
    return {
        "care_purchase_assessment_version": "v3-05.2",
        "care_purchase_assessment_schema_version": "v3-05.2",
        "account_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "candidate_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "category": "beauty",
        "plan_date": "2026-08-19",
        "candidate_truth_version": "v3-05.1",
        "assessment_fingerprint": fingerprint,
        "dimensions": {
            "identity_confidence": {
                "status": "trusted_with_missing_information" if missing else "trusted",
                "missing_information": list(missing),
            },
            "role_utility": {"status": "addresses_required_gap", "required": True},
            "redundancy": {
                "status": "none_eligible_owned_same_slot",
                "eligible_owned_same_slot_count": 0,
            },
            "compatibility": {
                "status": "reviewed_rule_matches",
                "coverage": "recognised_ingredients_only",
                "findings": [{
                    "rule_id": "rule.retinoid_bha",
                    "severity": "caution",
                    "owned_item_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                }],
            },
        },
        "user_constraints": {
            "status": "no_match_on_recognised_ingredients",
            "matched_ingredient_keys": [],
        },
    }


def _reviewed_path(*, claim_key: str = "skin.reviewed.compatibility") -> ReviewedEvidencePath:
    return ReviewedEvidencePath(
        rule_id="rule.retinoid_bha",
        rule_kind="ingredient_compatibility",
        rule_version="phase6-v1",
        relationship="supports",
        claim_id=uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        claim_key=claim_key,
        claim_version=1,
        claim_summary="Reviewed compatibility context for the exact rule.",
        claim_scope="General context only; not a product efficacy claim.",
        claim_type="compatibility_context",
        evidence_strength="moderate",
        claim_status="supported",
        applicability={"behavior_applicability": {"schema_version": "v3-03.15"}},
        sources=(
            {
                "source_id": uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
                "source_key": "reviewed.source",
                "title": "Reviewed source",
                "publisher": "Reviewed authority",
                "source_type": "professional_consensus",
                "publication_date": date(2024, 1, 1),
                "canonical_url": "https://example.test/reviewed-source",
                "relationship": "supports",
                "locator": "exact reviewed locator",
            },
        ),
    )


def test_versions_strategy_and_boundary_are_frozen():
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION == "v3-05.3"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION == "v3-05.2"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert resolve_purchase_strategy("beauty").state == "inactive"
    assert resolve_purchase_strategy("hair").state == "inactive"


def test_reviewed_rule_evidence_is_projected_without_product_claims():
    result = project_care_purchase_evidence(
        _assessment(), rule_evidence=(_reviewed_path(),), candidate_truth=SimpleNamespace(
            recognised_ingredient_keys=("retinol",),
        ),
    )
    body = result.as_dict()
    assert body["evidence_support"]["status"] == "reviewed_support_available"
    finding = body["evidence_support"]["findings"][0]
    assert finding["rule_id"] == "rule.retinoid_bha"
    assert finding["claim_key"] == "skin.reviewed.compatibility"
    assert finding["sources"][0]["source_key"] == "reviewed.source"
    flattened = str(body).lower()
    for forbidden in ("this product works", "this product is effective", "clinically proven product", "best product", "guaranteed result", "score"):
        assert forbidden not in flattened
    assert body["value_context"] == {
        "status": "not_assessed",
        "reason_code": "care_purchase_value_not_assessed",
    }


def test_rule_without_link_is_visible_and_structural_facts_keep_their_authority():
    result = project_care_purchase_evidence(
        _assessment(), candidate_truth=SimpleNamespace(recognised_ingredient_keys=("retinol",))
    ).as_dict()
    assert result["evidence_support"]["status"] == "no_applicable_reviewed_support"
    assert result["evidence_support"]["unsupported"] == [{
        "reason_code": "no_reviewed_evidence_link",
        "finding_type": "compatibility_rule",
        "rule_id": "rule.retinoid_bha",
    }]
    facts = result["evidence_support"]["structural_facts"]
    assert facts["role_utility"]["authority"] == "account_state"
    assert facts["redundancy"]["authority"] == "deterministic_inventory_fact"
    assert facts["user_constraints"]["authority"] == "user_declared_constraint"

    constrained = _assessment()
    constrained["user_constraints"] = {
        "status": "confirmed_user_constraint_match",
        "matched_ingredient_keys": ["niacinamide"],
    }
    constrained_body = project_care_purchase_evidence(
        constrained, candidate_truth=SimpleNamespace(recognised_ingredient_keys=("niacinamide",))
    ).as_dict()
    assert constrained_body["evidence_support"]["structural_facts"]["user_constraints"] == {
        "authority": "user_declared_constraint",
        "status": "confirmed_user_constraint_match",
        "matched_ingredient_keys": ["niacinamide"],
    }


def test_known_ingredient_is_not_evidence_and_ontology_note_is_not_authority():
    result = project_care_purchase_evidence(
        _assessment(), candidate_truth=SimpleNamespace(recognised_ingredient_keys=("niacinamide",))
    ).as_dict()
    assert result["ingredient_utility"]["status"] == "not_established_from_existing_evidence"
    assert {row["reason_code"] for row in result["ingredient_utility"]["unsupported"]} == {
        "no_explicit_ingredient_evidence_mapping"
    }


def test_explicit_ingredient_path_and_unknown_label_boundaries():
    utility_path = IngredientUtilityPath(
        ingredient_key="niacinamide", ingredient_family="niacinamide",
        claim_id=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        claim_key="skin.reviewed.niacinamide.utility", claim_version=1,
        claim_summary="Reviewed general ingredient context.",
        claim_scope="Ingredient-level context only; not product efficacy.",
        claim_type="usage_context", evidence_strength="moderate", claim_status="supported",
        applicability={"use": "skin_care"}, sources=(_reviewed_path().sources[0],),
    )
    supported = project_care_purchase_evidence(
        _assessment(), ingredient_evidence=(utility_path,), candidate_truth=SimpleNamespace(
            recognised_ingredient_keys=("niacinamide",),
        )
    ).as_dict()
    assert supported["ingredient_utility"]["status"] == "reviewed_utility_available"
    assert supported["ingredient_utility"]["findings"][0]["claim_key"] == "skin.reviewed.niacinamide.utility"

    unknown = project_care_purchase_evidence(
        _assessment(missing=("unrecognised_ingredient:mystery-label",)),
        candidate_truth=SimpleNamespace(recognised_ingredient_keys=("niacinamide",)),
    ).as_dict()
    assert unknown["ingredient_utility"]["unknown_ingredient_terms"] == ["unrecognised_ingredient:mystery-label"]
    assert unknown["ingredient_utility"]["status"] == "not_established_from_existing_evidence"
    assert unknown["ingredient_utility"]["unsupported"] == [
        {"reason_code": "unknown_ingredient_term", "term": "mystery-label"},
        {"reason_code": "no_explicit_ingredient_evidence_mapping"},
    ]


def test_projection_fingerprint_is_material_and_metadata_invariant():
    truth = SimpleNamespace(recognised_ingredient_keys=("niacinamide",))
    first = project_care_purchase_evidence(_assessment(), candidate_truth=truth)
    metadata_only = {**_assessment(), "price": "999.00", "currency": "USD", "product_url": "https://example.test", "brand": "Changed", "purpose": "marketing"}
    second = project_care_purchase_evidence(metadata_only, candidate_truth=truth)
    assert first.projection_fingerprint == second.projection_fingerprint
    assessment_changed = project_care_purchase_evidence(_assessment(fingerprint="assessment-b"), candidate_truth=truth)
    assert first.projection_fingerprint != assessment_changed.projection_fingerprint
    evidence_changed = project_care_purchase_evidence(
        _assessment(), rule_evidence=(_reviewed_path(claim_key="skin.other.claim"),), candidate_truth=truth
    )
    assert first.projection_fingerprint != evidence_changed.projection_fingerprint


def test_pure_import_boundary_is_explicit():
    source = Path(__file__).parents[1] / "app" / "domains" / "purchase" / "care_evidence.py"
    text = source.read_text(encoding="utf-8").lower()
    for forbidden in ("sqlalchemy", "fastapi", "ai_gateway", "payments", "billing", "checkout", "affiliate", "merchant", "amazon", "flipkart", "nykaa"):
        assert forbidden not in text


async def _runtime_counts(account_id: uuid.UUID) -> dict[str, int | bool | None]:
    factory = get_sessionmaker()
    async with factory() as session:
        return {
            "candidates": await session.scalar(select(func.count(ShoppingCandidate.id)).where(ShoppingCandidate.account_id == account_id)),
            "inventory": await session.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id)),
            "attributes": await session.scalar(select(func.count(InventoryAttribute.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)),
            "runs": await session.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == account_id)),
            "evaluations": await session.scalar(select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == account_id)),
            "factors": await session.scalar(select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id)),
            "decisions": await session.scalar(select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == account_id)),
            "routines": await session.scalar(select(func.count(Routine.id)).where(Routine.account_id == account_id)),
            "steps": await session.scalar(select(func.count(RoutineStep.id)).join(Routine).where(Routine.account_id == account_id)),
            "entitlement_used": await session.scalar(select(RecommendationEntitlement.used).where(RecommendationEntitlement.account_id == account_id, RecommendationEntitlement.feature == "shopping_evaluation")),
        }


async def _evidence_counts() -> dict[str, int]:
    from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink

    factory = get_sessionmaker()
    async with factory() as session:
        return {
            "sources": await session.scalar(select(func.count(EvidenceSource.id))),
            "claims": await session.scalar(select(func.count(EvidenceClaim.id))),
            "claim_sources": await session.scalar(select(func.count(EvidenceClaimSource.id))),
            "rule_links": await session.scalar(select(func.count(RuleEvidenceLink.id))),
        }


async def _updated_at(candidate_id: uuid.UUID):
    factory = get_sessionmaker()
    async with factory() as session:
        return await session.scalar(select(ShoppingCandidate.updated_at).where(ShoppingCandidate.id == candidate_id))


@pytest.mark.asyncio
async def test_draft_fails_before_evidence_queries(app_client, db_clean, registered_supabase_user, monkeypatch):
    token, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    candidate_id = uuid.uuid4()
    async with factory() as session:
        session.add(ShoppingCandidate(
            id=candidate_id, account_id=account_id, source="photo_extracted", category="beauty",
            display_name="Draft", details={"product_type": "cleanser"}, verification_state="draft",
            extraction_confidence=0.99,
        ))
        await session.commit()

    async def fail_if_gathered(*args, **kwargs):
        raise AssertionError("draft evidence projection must fail before Care assembly")

    monkeypatch.setattr("app.domains.planning.context.gather", fail_if_gathered)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-evidence?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "verification_state"


@pytest.mark.asyncio
async def test_real_projection_is_read_only_account_scoped_and_deterministic(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    created = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={
            "source": "manual",
            "item": {
                "category": "beauty", "display_name": "Evidence cleanser",
                "details": {"product_type": "cleanser", "active_ingredients": ["Niacinamide"]},
            },
            "client_mutation_id": "v3-05-3-evidence-read-only",
        },
    )
    assert created.status_code == 200, created.text
    candidate_id = uuid.UUID(created.json()["candidate"]["id"])
    before_runtime = await _runtime_counts(account_id)
    before_evidence = await _evidence_counts()
    before_updated = await _updated_at(candidate_id)
    ai_calls = 0

    async def unexpected_ai(*args, **kwargs):
        nonlocal ai_calls
        ai_calls += 1
        raise AssertionError("Care Evidence projection must not call AI")

    monkeypatch.setattr("app.domains.purchase.extraction.extract_purchase_candidate", unexpected_ai)
    path = f"/api/v2/shopping/candidates/{candidate_id}/care-evidence?on=2026-08-19"
    frozen_assessment = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    assert frozen_assessment.status_code == 200, frozen_assessment.text
    assert frozen_assessment.json()["care_purchase_assessment_version"] == "v3-05.2"
    first = await app_client.get(path, headers=auth(token))
    second = await app_client.get(path, headers=auth(token))
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    body = first.json()
    assert body["care_purchase_evidence_version"] == "v3-05.3"
    assert body["ingredient_utility"]["status"] == "not_established_from_existing_evidence"
    assert body["value_context"]["status"] == "not_assessed"
    assert await _runtime_counts(account_id) == before_runtime
    assert await _evidence_counts() == before_evidence
    assert await _updated_at(candidate_id) == before_updated
    assert ai_calls == 0

    baseline = {
        "projection_fingerprint": body["projection_fingerprint"],
        "evidence_support": body["evidence_support"],
        "ingredient_utility": body["ingredient_utility"],
    }
    for correction in (
        {"price": "999.00"},
        {"currency": "USD"},
        {"product_url": "https://example.test/product"},
        {"details": {"product_type": "cleanser", "active_ingredients": ["Niacinamide"], "purpose": "barrier support"}},
    ):
        corrected = await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/confirm",
            headers=auth(token), json=correction,
        )
        assert corrected.status_code == 200, corrected.text
        current = await app_client.get(path, headers=auth(token))
        assert current.status_code == 200, current.text
        current_body = current.json()
        assert {key: current_body[key] for key in baseline} == baseline

    assert (await app_client.get(path, headers=auth(intruder_token))).status_code == 404

    for category in ("beauty", "hair"):
        inactive = await app_client.post(
            "/api/v2/shopping/evaluate", headers=auth(token),
            json={"source": "manual", "item": {"category": category, "display_name": "Inactive"}, "client_mutation_id": f"v3-05-3-inactive-{category}"},
        )
        assert inactive.status_code == 422, inactive.text
        assert "verdict" not in inactive.json()
