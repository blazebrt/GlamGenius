"""V3-05.9 Fragrance Purchase strategy contract and deterministic policy."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.domains.purchase.contract import (
    CARE_PURCHASE_CHECK_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
    FRAGRANCE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    FRAGRANCE_PURCHASE_CHECK_VERSION,
    FRAGRANCE_PURCHASE_VERDICT_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    is_active_care_category,
    is_active_fragrance_category,
    resolve_purchase_strategy,
)
from app.domains.purchase.fragrance_check import OwnedFragrance, evaluate_fragrance_purchase
from app.domains.purchase.fragrance_truth import (
    FRAGRANCE_CANDIDATE_DETAIL_KEYS,
    build_fragrance_candidate_truth,
    validate_fragrance_candidate_details,
)


def _candidate(*, price=Decimal("1200"), **details):
    return SimpleNamespace(
        id=uuid4(), category="perfumes", display_name="Rain Garden", brand="House",
        details=details, price=price, currency="INR", verification_state="confirmed",
        source="manual", subcategory=None, product_url=None, media_asset_id=None,
        uncertain_fields=[], extraction_confidence=None, ai_run_id=None,
        model_version=None, prompt_version=None, schema_version=None,
    )


def _owned(*, name="Other", brand="House", family="woody", occasion=None, season=None, remaining=50):
    item = SimpleNamespace(
        id=uuid4(), display_name=name, brand=brand, usage_count=0, last_used_at=None,
    )
    detail = SimpleNamespace(
        fragrance_family=family, concentration="EDP", occasion=occasion or [], season=season or [],
        remaining_percent=remaining,
    )
    return OwnedFragrance(item=item, detail=detail)


def test_versions_activation_and_frozen_prior_authorities():
    assert FRAGRANCE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.9"
    assert FRAGRANCE_PURCHASE_VERDICT_VERSION == "v3-05.9"
    assert FRAGRANCE_PURCHASE_CHECK_VERSION == "v3-05.9"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.9"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert CARE_PURCHASE_CHECK_VERSION == "v3-05.7"
    assert resolve_purchase_strategy("perfumes").state == "active"
    assert is_active_fragrance_category("perfumes")
    assert is_active_care_category("beauty")
    assert resolve_purchase_strategy("supplements").state == "prohibited"


def test_candidate_truth_reuses_family_ontology_and_rejects_owned_only_fields():
    assert {"fragrance_family", "concentration", "season", "occasion"} <= FRAGRANCE_CANDIDATE_DETAIL_KEYS
    truth = build_fragrance_candidate_truth(_candidate(fragrance_family="woody", occasion=["office"]))
    assert truth.normalized_fragrance_family == "woody"
    assert truth.facts_trusted is True
    assert truth.review_required is False
    try:
        validate_fragrance_candidate_details({"remaining_percent": 10})
    except ValueError as exc:
        assert "remaining_percent" in str(exc)
    else:
        raise AssertionError("owned-only remaining_percent must not be accepted on a candidate")


def test_exact_and_price_precedence():
    candidate = _candidate(fragrance_family="woody")
    exact = _owned(name="Rain Garden", brand="House", remaining=40)
    result = evaluate_fragrance_purchase(candidate=candidate, owned=[exact])
    assert result["verdict"] == "wait"
    assert result["primary_reason_code"] == "exact_bottle_available"
    result = evaluate_fragrance_purchase(candidate=_candidate(fragrance_family="woody", price=None), owned=[])
    assert result["primary_reason_code"] == "candidate_price_missing"
    result = evaluate_fragrance_purchase(candidate=_candidate(fragrance_family="woody"), owned=[_owned(name="Rain Garden", brand="House", remaining=10)])
    assert result["verdict"] == "buy"
    assert result["primary_reason_code"] == "exact_replacement_ready"
    result = evaluate_fragrance_purchase(candidate=candidate, owned=[exact, _owned(name="Rain Garden", brand="House", remaining=80)])
    assert result["verdict"] == "skip"
    assert result["primary_reason_code"] == "multiple_exact_bottles_owned"


def test_first_fragrance_and_context_coverage_are_owned_first():
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["office"], season=["summer"]), owned=[])
    assert result["verdict"] == "buy"
    assert result["primary_reason_code"] == "first_fragrance_gap"
    owner = _owned(occasion=["office"], season=["summer"])
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["office"], season=["summer"]), owned=[owner])
    assert result["primary_reason_code"] == "declared_use_already_covered"
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["festive"]), owned=[_owned(occasion=[])])
    assert result["primary_reason_code"] == "owned_context_incomplete"
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["festive"]), owned=[_owned(occasion=["office"])])
    assert result["primary_reason_code"] == "declared_use_gap"


def test_family_overlap_is_supporting_only_and_fingerprint_is_order_stable():
    candidate = _candidate(fragrance_family="woody")
    first = _owned(name="Other A", family="woody", occasion=["office"], season=["summer"])
    second = _owned(name="Other B", family="woody", occasion=["office"], season=["summer"])
    one = evaluate_fragrance_purchase(candidate=candidate, owned=[first, second])
    two = evaluate_fragrance_purchase(candidate=candidate, owned=[second, first])
    assert one["verdict"] == two["verdict"] == "wait"
    assert one["primary_reason_code"] == "intended_use_missing"
    assert one["decision_fingerprint"] == two["decision_fingerprint"]
    assert one["supporting_reason_codes"] == ["same_family_owned"]
