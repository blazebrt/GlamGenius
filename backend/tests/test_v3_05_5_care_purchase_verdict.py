"""V3-05.5 deterministic Care purchase verdict policy tests."""
from __future__ import annotations

import copy
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.bootstrap import seed_inventory_categories
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.inventory.models import InventoryAttribute, InventoryItem, InventoryValueEvent, ItemUsageEvent
from app.domains.purchase.care_verdict import EXPLANATIONS, HEADLINES, project_care_purchase_verdict
from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_SCHEMA_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    CARE_PURCHASE_VERDICT_SCHEMA_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
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
    RecommendationInput,
    RecommendationRun,
    ShoppingCandidate,
)
from app.domains.routines.models import Routine, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth


def _assessment(
    *,
    category: str = "beauty",
    role_status: str = "addresses_required_gap",
    required: bool = True,
    is_gap: bool = True,
    redundancy_status: str = "none_eligible_owned_same_slot",
    count: int = 0,
    selected: str | None = None,
    user_status: str = "no_match_on_recognised_ingredients",
    compatibility_status: str = "no_reviewed_rule_match_on_recognised_ingredients",
    compatibility_findings: list[dict] | None = None,
    missing: tuple[str, ...] = (),
    fingerprint: str = "assessment-fingerprint",
    account_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    candidate_id: str = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
) -> dict:
    slot = "shampoo" if category == "hair" else "cleanser"
    return {
        "care_purchase_assessment_version": CARE_PURCHASE_ASSESSMENT_VERSION,
        "care_purchase_assessment_schema_version": CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
        "account_id": account_id,
        "candidate_id": candidate_id,
        "category": category,
        "plan_date": "2026-08-19",
        "candidate_truth_version": PURCHASE_CANDIDATE_TRUTH_VERSION,
        "assessment_fingerprint": fingerprint,
        "dimensions": {
            "identity_confidence": {"missing_information": list(missing)},
            "role_utility": {"status": role_status, "care_slot": slot, "required": required, "is_gap": is_gap},
            "redundancy": {
                "status": redundancy_status,
                "eligible_owned_same_slot_count": count,
                "selected_owned_item_id": selected,
            },
            "compatibility": {
                "status": compatibility_status,
                "findings": compatibility_findings or [],
            },
        },
        "user_constraints": {"status": user_status, "matched_ingredient_keys": []},
    }


def _evidence(
    assessment: dict,
    *,
    status: str = "no_applicable_reviewed_support",
    utility_status: str = "not_established_from_existing_evidence",
    findings: list[dict] | None = None,
    utility_findings: list[dict] | None = None,
    fingerprint: str = "evidence-fingerprint",
) -> dict:
    return {
        "care_purchase_evidence_version": CARE_PURCHASE_EVIDENCE_VERSION,
        "care_purchase_evidence_schema_version": CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
        "account_id": assessment["account_id"],
        "candidate_id": assessment["candidate_id"],
        "category": assessment["category"],
        "plan_date": assessment["plan_date"],
        "assessment_fingerprint": assessment["assessment_fingerprint"],
        "projection_fingerprint": fingerprint,
        "evidence_support": {"status": status, "findings": findings or []},
        "ingredient_utility": {"status": utility_status, "findings": utility_findings or []},
    }


def _value(
    assessment: dict,
    *,
    price_status: str = "recorded",
    recovery_status: str = "no_low_use_eligible_owned_same_slot",
    currency_status: str = "no_quantified_recovery",
    financial_status: str = "financial_context_available",
    amount: str = "1299.00",
    fingerprint: str = "value-fingerprint",
) -> dict:
    return {
        "care_purchase_value_version": CARE_PURCHASE_VALUE_VERSION,
        "care_purchase_value_schema_version": CARE_PURCHASE_VALUE_SCHEMA_VERSION,
        "account_id": assessment["account_id"],
        "candidate_id": assessment["candidate_id"],
        "category": assessment["category"],
        "plan_date": assessment["plan_date"],
        "assessment_fingerprint": assessment["assessment_fingerprint"],
        "value_fingerprint": fingerprint,
        "value_context": {
            "status": financial_status,
            "candidate_spend": {"status": price_status, "amount": amount if price_status == "recorded" else None, "currency": "INR" if price_status == "recorded" else None},
            "owned_value_recovery": {"status": recovery_status, "items": []},
            "currency_context": {"status": currency_status, "comparison_available": currency_status == "same_currency_context"},
        },
    }


def _project(assessment: dict, evidence: dict | None = None, value: dict | None = None):
    return project_care_purchase_verdict(
        assessment,
        evidence or _evidence(assessment),
        value or _value(assessment),
    ).as_dict()


def test_versions_registry_and_boundary_are_frozen():
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert CARE_PURCHASE_VERDICT_SCHEMA_VERSION == "v3-05.5"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert resolve_purchase_strategy("beauty").state == "inactive"
    assert resolve_purchase_strategy("hair").state == "inactive"


def test_required_skin_gap_buys_without_efficacy_or_score():
    body = _project(_assessment())
    assert body["verdict"] == "buy"
    assert body["primary_reason_code"] == "required_gap_no_owned_alternative"
    assert body["headline"] == "This fills a real gap."
    assert "score" not in str(body).lower()
    assert "effective" not in body["explanation"].lower()


def test_required_hair_gap_buys():
    body = _project(_assessment(category="hair"))
    assert body["verdict"] == "buy"
    assert body["category_label"] == "Hair Care"


def test_canonical_top_level_user_constraints_are_required():
    assessment = _assessment()
    assert _project(assessment)["verdict"] == "buy"

    nested_only = copy.deepcopy(assessment)
    nested_only["dimensions"]["user_constraints"] = nested_only.pop("user_constraints")
    with pytest.raises(ValueError):
        _project(nested_only)


def test_user_constraint_always_skips():
    assessment = _assessment(user_status="confirmed_user_constraint_match")
    evidence = _evidence(assessment, utility_status="reviewed_utility_available")
    assert _project(assessment, evidence)["primary_reason_code"] == "user_declared_constraint_match"
    assert _project(assessment, evidence)["verdict"] == "skip"
    assert "unsafe" not in _project(assessment, evidence)["explanation"].lower()


@pytest.mark.parametrize("missing", (("ingredients",), ("unrecognised_ingredient:mystery-label",)))
def test_incomplete_or_unknown_ingredients_wait(missing):
    body = _project(_assessment(missing=missing))
    assert body["verdict"] == "wait"
    assert body["primary_reason_code"] == "candidate_ingredient_information_incomplete"


def test_compatibility_caution_waits_but_info_only_does_not():
    caution = _assessment(
        compatibility_status="reviewed_rule_matches",
        compatibility_findings=[{"severity": "caution", "rule_id": "rule.caution"}],
    )
    body = _project(caution)
    assert body["verdict"] == "wait"
    assert body["primary_reason_code"] == "reviewed_compatibility_caution"
    info = _assessment(
        compatibility_status="reviewed_rule_matches",
        compatibility_findings=[{"severity": "info", "rule_id": "rule.info"}],
    )
    body = _project(info)
    assert body["verdict"] == "buy"
    assert "reviewed_compatibility_info" in body["supporting_reason_codes"]


def test_conflicting_evidence_waits_unsupported_and_qualified_do_not():
    assessment = _assessment()
    conflicting = _evidence(
        assessment,
        status="reviewed_support_partial",
        findings=[{"claim_status": "conflicting", "claim_key": "claim.conflict"}],
    )
    assert _project(assessment, conflicting)["primary_reason_code"] == "reviewed_evidence_conflict"
    for claim_status in ("unsupported", "qualified"):
        evidence = _evidence(
            assessment,
            status="reviewed_support_partial",
            findings=[{"claim_status": claim_status, "claim_key": f"claim.{claim_status}"}],
        )
        body = _project(assessment, evidence)
        assert body["verdict"] == "buy"


def test_known_ingredient_without_utility_mapping_does_not_block_buy():
    body = _project(_assessment(), _evidence(_assessment()))
    assert body["verdict"] == "buy"
    assert body["decision_context"]["ingredient_utility_status"] == "not_established_from_existing_evidence"


def test_utility_evidence_cannot_create_buy_for_optional_role():
    assessment = _assessment(
        role_status="optional_role_not_required",
        required=False,
        is_gap=False,
    )
    evidence = _evidence(assessment, utility_status="reviewed_utility_available")
    body = _project(assessment, evidence)
    assert body["verdict"] == "wait"
    assert body["primary_reason_code"] == "optional_role_not_required"


def test_price_recovery_and_currency_gates():
    assessment = _assessment()
    body = _project(assessment, value=_value(assessment, price_status="missing"))
    assert body["primary_reason_code"] == "candidate_price_missing"
    zero = _project(assessment, value=_value(assessment, amount="0.00"))
    assert zero["verdict"] == "buy"
    for recovery in ("low_use_recovery_estimated", "low_use_recovery_partially_estimated", "low_use_recovery_unquantified"):
        body = _project(assessment, value=_value(assessment, recovery_status=recovery))
        assert body["primary_reason_code"] == "owned_value_to_recover_first"
    mixed = _project(assessment, value=_value(assessment, currency_status="mixed_currency_no_conversion"))
    assert mixed["primary_reason_code"] == "mixed_currency_no_conversion"


def test_price_magnitude_does_not_create_affordability_judgment():
    assessment = _assessment()
    low = _project(assessment, value=_value(assessment, amount="100.00", fingerprint="value-low"))
    high = _project(assessment, value=_value(assessment, amount="100000.00", fingerprint="value-high"))
    assert low["verdict"] == high["verdict"] == "buy"
    assert low["decision_fingerprint"] != high["decision_fingerprint"]


def test_redundancy_policy_states():
    covered = _assessment(
        role_status="required_role_already_covered", required=True, is_gap=False,
        redundancy_status="one_eligible_owned_same_slot", count=1, selected="item-a",
    )
    assert _project(covered)["primary_reason_code"] == "required_role_already_covered"
    optional_owned = _assessment(
        role_status="optional_role_not_required", required=False, is_gap=False,
        redundancy_status="one_eligible_owned_same_slot", count=1, selected="item-a",
    )
    assert _project(optional_owned)["primary_reason_code"] == "optional_role_already_owned"
    optional_unowned = _assessment(role_status="optional_role_not_required", required=False, is_gap=False)
    assert _project(optional_unowned)["primary_reason_code"] == "optional_role_not_required"
    blocked_gap = _assessment()
    assert _project(blocked_gap)["verdict"] == "buy"


@pytest.mark.parametrize(
    "field",
    ("account_id", "candidate_id", "category", "plan_date", "assessment_fingerprint"),
)
def test_projection_identity_mismatch_fails_closed(field):
    assessment = _assessment()
    evidence = _evidence(assessment)
    value = _value(assessment)
    if field == "assessment_fingerprint":
        evidence[field] = "different"
    else:
        evidence[field] = "different" if field != "plan_date" else "2026-08-20"
    with pytest.raises(ValueError):
        _project(assessment, evidence, value)


def test_version_and_impossible_state_fail_closed():
    assessment = _assessment()
    evidence = _evidence(assessment)
    evidence["care_purchase_evidence_version"] = "future"
    with pytest.raises(ValueError):
        _project(assessment, evidence)
    impossible = _assessment(count=1, redundancy_status="one_eligible_owned_same_slot")
    with pytest.raises(ValueError):
        _project(impossible)


@pytest.mark.parametrize(
    ("target", "field"),
    (
        ("assessment", "care_purchase_assessment_schema_version"),
        ("evidence", "care_purchase_evidence_schema_version"),
        ("value", "care_purchase_value_schema_version"),
    ),
)
def test_future_authority_schema_fails_closed(target, field):
    assessment = _assessment()
    evidence = _evidence(assessment)
    value = _value(assessment)
    {"assessment": assessment, "evidence": evidence, "value": value}[target][field] = "future"
    with pytest.raises(ValueError):
        _project(assessment, evidence, value)


@pytest.mark.parametrize(
    ("target", "path"),
    (
        ("evidence", ("evidence_support", "status")),
        ("evidence", ("ingredient_utility", "status")),
        ("value", ("value_context", "status")),
        ("value", ("value_context", "candidate_spend", "status")),
        ("value", ("value_context", "owned_value_recovery", "status")),
        ("value", ("value_context", "currency_context", "status")),
    ),
)
def test_unknown_authority_status_fails_closed(target, path):
    assessment = _assessment()
    evidence = _evidence(assessment)
    value = _value(assessment)
    current = {"evidence": evidence, "value": value}[target]
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = "future"
    with pytest.raises(ValueError):
        _project(assessment, evidence, value)


@pytest.mark.parametrize("section", ("evidence_support", "ingredient_utility"))
def test_malformed_evidence_finding_fails_closed(section):
    assessment = _assessment()
    evidence = _evidence(assessment)
    evidence[section]["findings"] = ["not-a-mapping"]
    with pytest.raises(ValueError):
        _project(assessment, evidence)


def test_fingerprints_are_deterministic_material_and_copy_invariant(monkeypatch):
    assessment = _assessment()
    evidence = _evidence(assessment)
    value = _value(assessment)
    first = _project(assessment, evidence, value)
    second = _project(copy.deepcopy(assessment), copy.deepcopy(evidence), copy.deepcopy(value))
    assert first["decision_fingerprint"] == second["decision_fingerprint"]
    changed_assessment = copy.deepcopy(assessment)
    changed_assessment["assessment_fingerprint"] = "changed-assessment"
    changed_evidence = _evidence(changed_assessment)
    changed_value = _value(changed_assessment)
    assert _project(changed_assessment, changed_evidence, changed_value)["decision_fingerprint"] != first["decision_fingerprint"]
    changed_evidence = _evidence(assessment, fingerprint="changed-evidence")
    assert _project(assessment, changed_evidence, value)["decision_fingerprint"] != first["decision_fingerprint"]
    changed_value = _value(assessment, fingerprint="changed-value")
    assert _project(assessment, evidence, changed_value)["decision_fingerprint"] != first["decision_fingerprint"]
    monkeypatch.setitem(HEADLINES, "buy", "Copy-only alternate headline.")
    monkeypatch.setitem(
        EXPLANATIONS,
        "required_gap_no_owned_alternative",
        "Copy-only alternate explanation.",
    )
    copy_only = _project(assessment, evidence, value)
    for key in (
        "verdict",
        "primary_reason_code",
        "reason_codes",
        "supporting_reason_codes",
        "decision_context",
        "decision_fingerprint",
    ):
        assert copy_only[key] == first[key]
    assert copy_only["headline"] != first["headline"]
    assert copy_only["explanation"] != first["explanation"]


def test_policy_module_has_no_runtime_boundary_imports():
    source = (Path(__file__).parents[1] / "app" / "domains" / "purchase" / "care_verdict.py").read_text(encoding="utf-8").lower()
    for forbidden in ("sqlalchemy", "fastapi", "recommendation.roi", "payment", "billing", "checkout", "affiliate", "httpx", "requests"):
        assert forbidden not in source
    assert "score" not in source


async def _seed_candidate(account_id: uuid.UUID, *, verification_state: str = "confirmed") -> uuid.UUID:
    candidate_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await seed_inventory_categories(session)
        session.add(
            ShoppingCandidate(
                id=candidate_id,
                account_id=account_id,
                source="manual",
                category="beauty",
                display_name="Verdict cleanser",
                details={"product_type": "cleanser", "purpose": "cleanse", "active_ingredients": ["Niacinamide"]},
                verification_state=verification_state,
                extraction_confidence=0.99,
                price=Decimal("1299.00"),
                currency="INR",
            )
        )
        await session.commit()
    return candidate_id


async def _counts(account_id: uuid.UUID) -> dict[str, Any]:
    factory = get_sessionmaker()
    async with factory() as session:
        entitlement = await session.scalar(select(RecommendationEntitlement).where(RecommendationEntitlement.account_id == account_id, RecommendationEntitlement.feature == "shopping_evaluation"))
        return {
            "candidates": await session.scalar(select(func.count(ShoppingCandidate.id)).where(ShoppingCandidate.account_id == account_id)),
            "runs": await session.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == account_id)),
            "inputs": await session.scalar(
                select(func.count(RecommendationInput.id))
                .join(RecommendationRun, RecommendationRun.id == RecommendationInput.run_id)
                .where(RecommendationRun.account_id == account_id)
            ),
            "evaluations": await session.scalar(select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == account_id)),
            "factors": await session.scalar(select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id)),
            "decisions": await session.scalar(select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == account_id)),
            "inventory": await session.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id)),
            "attributes": await session.scalar(select(func.count(InventoryAttribute.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)),
            "value_events": await session.scalar(select(func.count(InventoryValueEvent.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)),
            "usage_events": await session.scalar(select(func.count(ItemUsageEvent.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)),
            "routines": await session.scalar(select(func.count(Routine.id)).where(Routine.account_id == account_id)),
            "steps": await session.scalar(select(func.count(RoutineStep.id)).join(Routine).where(Routine.account_id == account_id)),
            "entitlement": None if entitlement is None else (entitlement.included, entitlement.used, entitlement.period_key),
            "evidence_sources": await session.scalar(select(func.count(EvidenceSource.id))),
            "evidence_claims": await session.scalar(select(func.count(EvidenceClaim.id))),
            "evidence_claim_sources": await session.scalar(select(func.count(EvidenceClaimSource.id))),
            "rule_evidence_links": await session.scalar(select(func.count(RuleEvidenceLink.id))),
        }


async def _updated_at(candidate_id: uuid.UUID):
    factory = get_sessionmaker()
    async with factory() as session:
        return await session.scalar(select(ShoppingCandidate.updated_at).where(ShoppingCandidate.id == candidate_id))


@pytest.mark.asyncio
async def test_runtime_draft_fails_before_verdict_work(app_client, db_clean, registered_supabase_user, monkeypatch):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_candidate(account_id, verification_state="draft")
    calls = 0

    async def fail_if_called(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("draft must fail before Care verdict authorities")

    monkeypatch.setattr("app.domains.purchase.service.care_purchase_assessment", fail_if_called)
    response = await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/care-verdict?on=2026-08-19", headers=auth(token))
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "verification_state"
    assert calls == 0


@pytest.mark.asyncio
async def test_runtime_verdict_is_account_scoped_read_only_and_fixed_date(app_client, db_clean, registered_supabase_user, monkeypatch):
    token, account_id = await registered_supabase_user()
    intruder, _ = await registered_supabase_user()
    candidate_id = await _seed_candidate(account_id)
    before = await _counts(account_id)
    before_updated = await _updated_at(candidate_id)
    ai_calls = 0

    async def unexpected_ai(*args, **kwargs):
        nonlocal ai_calls
        ai_calls += 1
        raise AssertionError("Care verdict must not call AI")

    monkeypatch.setattr("app.domains.purchase.extraction.extract_purchase_candidate", unexpected_ai)
    monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", unexpected_ai)
    path = f"/api/v2/shopping/candidates/{candidate_id}/care-verdict?on=2026-08-19"
    first = await app_client.get(path, headers=auth(token))
    second = await app_client.get(path, headers=auth(token))
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    assert first.json()["decision_fingerprint"] == second.json()["decision_fingerprint"]
    assert await _counts(account_id) == before
    assert await _updated_at(candidate_id) == before_updated
    assert ai_calls == 0
    assert (await app_client.get(path, headers=auth(intruder))).status_code == 404


@pytest.mark.asyncio
async def test_runtime_generic_care_evaluate_remains_inactive(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_candidate(account_id)
    verdict = await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/care-verdict?on=2026-08-19", headers=auth(token))
    assert verdict.status_code == 200, verdict.text
    before = await _counts(account_id)
    for category in ("beauty", "hair"):
        response = await app_client.post(
            "/api/v2/shopping/evaluate",
            headers=auth(token),
            json={"source": "manual", "item": {"category": category, "display_name": "Care item"}, "client_mutation_id": f"v3-05-5-{category}"},
        )
        assert response.status_code == 422
        assert "verdict" not in response.json()
    assert await _counts(account_id) == before
