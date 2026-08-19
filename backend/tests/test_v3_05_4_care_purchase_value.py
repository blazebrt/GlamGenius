"""Pure V3-05.4 Care value-context contract tests."""
from __future__ import annotations

from pathlib import Path

from app.domains.purchase.care_value import (
    project_care_purchase_value,
)
from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_SCHEMA_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    resolve_purchase_strategy,
)


def _assessment(*, fingerprint: str = "assessment-a", price_role: str = "gap") -> dict:
    role_status = "addresses_required_gap" if price_role == "gap" else "required_role_already_covered"
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
            "role_utility": {"status": role_status, "care_slot": "cleanser", "required": True, "is_gap": price_role == "gap"},
            "redundancy": {"status": "none_eligible_owned_same_slot", "eligible_owned_same_slot_count": 0, "selected_owned_item_id": None},
        },
    }


def _recovery(item_id: str = "22222222-2222-2222-2222-222222222222", *, value=420, currency="INR", missing=()):
    return {
        "item_id": item_id,
        "display_name": "Owned Cleanser",
        "metric_version": "v1",
        "is_estimate": True,
        "estimated_value": value,
        "currency": currency,
        "missing_inputs": list(missing),
        "inputs": {"purchase_price": 1000 if value is not None else None, "remaining_estimate": 0.8},
        "explanation": "Estimated Value to Recover; never exact.",
    }


def test_versions_and_care_strategy_remain_frozen():
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert CARE_PURCHASE_VALUE_SCHEMA_VERSION == "v3-05.4"
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


def test_required_gap_and_recorded_price_without_recovery():
    body = project_care_purchase_value(_assessment(), candidate_price="1299.00", candidate_currency="INR").as_dict()
    context = body["value_context"]
    assert context["status"] == "financial_context_available"
    assert context["candidate_spend"] == {"status": "recorded", "amount": 1299.0, "currency": "INR"}
    assert context["role_context"]["status"] == "addresses_required_gap"
    assert context["owned_value_recovery"]["status"] == "no_low_use_eligible_owned_same_slot"
    assert context["currency_context"]["status"] == "no_quantified_recovery"
    assert "value_score" not in str(body).lower()


def test_regular_use_role_coverage_is_not_value_recovery():
    assessment = _assessment(price_role="covered")
    assessment["dimensions"]["redundancy"] = {
        "status": "one_eligible_owned_same_slot",
        "eligible_owned_same_slot_count": 1,
        "selected_owned_item_id": "22222222-2222-2222-2222-222222222222",
    }
    body = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR").as_dict()
    assert body["value_context"]["role_context"]["status"] == "required_role_already_covered"
    assert body["value_context"]["owned_value_recovery"]["status"] == "no_low_use_eligible_owned_same_slot"


def test_recovery_statuses_and_same_currency_total():
    assessment = _assessment()
    one = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(),)).as_dict()
    assert one["value_context"]["owned_value_recovery"]["status"] == "low_use_recovery_estimated"
    assert one["value_context"]["currency_context"]["status"] == "same_currency_context"
    assert one["value_context"]["estimated_recoverable_total"] == {"amount": 420.0, "currency": "INR", "is_estimate": True}

    partial = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(), _recovery("33333333-3333-3333-3333-333333333333", value=None, missing=("purchase_price",)))).as_dict()
    assert partial["value_context"]["owned_value_recovery"]["status"] == "low_use_recovery_partially_estimated"
    assert partial["value_context"]["estimated_recoverable_total"] is None

    none = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(value=None, missing=("purchase_price",)),)).as_dict()
    assert none["value_context"]["owned_value_recovery"]["status"] == "low_use_recovery_unquantified"


def test_missing_zero_and_currency_boundaries():
    missing = project_care_purchase_value(_assessment(), candidate_price=None, candidate_currency="INR", recovery_rows=(_recovery(value=None, missing=("purchase_price",)),)).as_dict()
    assert missing["value_context"]["status"] == "financial_context_unavailable"
    assert missing["value_context"]["candidate_spend"]["status"] == "missing"
    assert missing["value_context"]["currency_context"]["status"] == "candidate_price_missing"

    partial = project_care_purchase_value(_assessment(), candidate_price=None, candidate_currency="INR", recovery_rows=(_recovery(),)).as_dict()
    assert partial["value_context"]["status"] == "financial_context_partial"

    zero = project_care_purchase_value(_assessment(), candidate_price=0, candidate_currency="INR").as_dict()
    assert zero["value_context"]["candidate_spend"] == {"status": "recorded", "amount": 0.0, "currency": "INR"}

    mixed = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(currency="USD"),)).as_dict()
    assert mixed["value_context"]["currency_context"] == {"status": "mixed_currency_no_conversion", "comparison_available": False}
    assert mixed["value_context"]["estimated_recoverable_total"] is None


def test_fingerprints_are_material_and_metadata_invariant():
    base = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(),))
    price = project_care_purchase_value(_assessment(), candidate_price=1399, candidate_currency="INR", recovery_rows=(_recovery(),))
    currency = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="USD", recovery_rows=(_recovery(),))
    changed_recovery = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(value=200),))
    assert base.value_fingerprint != price.value_fingerprint
    assert base.value_fingerprint != currency.value_fingerprint
    assert base.value_fingerprint != changed_recovery.value_fingerprint
    assert base.value_context == project_care_purchase_value({**_assessment(), "brand": "changed", "product_url": "https://example.test"}, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(),)).value_context


def test_fixed_date_and_source_boundaries_are_explicit():
    source = Path(__file__).parents[1] / "app" / "domains" / "purchase"
    value_text = (source / "care_value.py").read_text(encoding="utf-8").lower()
    service_text = (source / "value_service.py").read_text(encoding="utf-8").lower()
    assert "date.today" not in value_text
    assert "date.today" not in service_text
    for forbidden in ("price_per_ml", "price_per_g", "cost_per_use", "cost_per_application", "expected_uses", "expected_duration", "monthly_cost", "recommendation.roi", "httpx", "requests"):
        assert forbidden not in value_text + service_text
    assert "value_to_recover" in service_text
    assert "is_low_use" in service_text
