"""Pure V3-05.5 deterministic Care purchase verdict policy.

This module consumes the already-resolved V3-05.2, V3-05.3 and V3-05.4
authority projections.  It deliberately contains no persistence, scoring,
network, AI, entitlement or generic shopping-evaluation behavior.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_SCHEMA_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    CARE_PURCHASE_VERDICT_SCHEMA_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
    PURCHASE_CATEGORY_LABELS,
)

VERDICT_BUY = "buy"
VERDICT_WAIT = "wait"
VERDICT_SKIP = "skip"

HEADLINES = {
    VERDICT_BUY: "This fills a real gap.",
    VERDICT_WAIT: "Hold this one for now.",
    VERDICT_SKIP: "You can pass on this one.",
}

EXPLANATIONS = {
    "user_declared_constraint_match": "This contains an ingredient you told GlamGenius to avoid.",
    "care_role_unresolved": "GlamGenius cannot yet resolve the Care role this candidate would fill.",
    "candidate_ingredient_information_incomplete": "There is not enough confirmed ingredient information to make this decision yet.",
    "reviewed_evidence_conflict": "Reviewed Evidence indicates that the relevant scientific picture is unsettled.",
    "reviewed_compatibility_caution": "The candidate raises a reviewed routine-combination caution with something already selected in your Care routine.",
    "candidate_price_missing": "The role may be useful, but GlamGenius does not yet have the candidate's spend context.",
    "mixed_currency_no_conversion": "The candidate and owned-value amounts use different currencies, so they cannot be compared here.",
    "financial_context_incomplete": "Some owned-value financial information is incomplete, so GlamGenius will not guess the missing number.",
    "owned_value_to_recover_first": "You already have an eligible same-role product that is currently low-use. Use the value you already own first, then reconsider.",
    "optional_role_already_owned": "This optional role is already represented by an eligible product you own.",
    "required_role_already_covered": "You already have this required role covered. Reconsider when the product doing that job is running low or no longer part of your routine.",
    "optional_role_not_required": "This role is not required in the minimum-effective Care plan right now.",
    "required_gap_no_owned_alternative": "This fills a required Care role you do not currently have covered.",
}

_ROLE_STATES = {
    "addresses_required_gap",
    "required_role_already_covered",
    "optional_role_not_required",
    "role_unresolved",
}
_REDUNDANCY_STATES = {
    "role_unresolved",
    "none_eligible_owned_same_slot",
    "one_eligible_owned_same_slot",
    "multiple_eligible_owned_same_slot",
}
_CONSTRAINT_STATES = {
    "confirmed_user_constraint_match",
    "no_match_on_recognised_ingredients",
    "insufficient_ingredient_information",
}
_COMPATIBILITY_STATES = {
    "insufficient_ingredient_information",
    "no_selected_owned_products_to_compare",
    "reviewed_rule_matches",
    "no_reviewed_rule_match_on_recognised_ingredients",
}
_RECOVERY_STATES = {
    "low_use_recovery_estimated",
    "low_use_recovery_partially_estimated",
    "low_use_recovery_unquantified",
}
_EVIDENCE_SUPPORT_STATES = {
    "reviewed_support_available",
    "reviewed_support_partial",
    "no_applicable_reviewed_support",
    "insufficient_candidate_information",
}
_INGREDIENT_UTILITY_STATES = {
    "reviewed_utility_available",
    "reviewed_utility_partial",
    "not_established_from_existing_evidence",
    "insufficient_candidate_information",
}
_FINANCIAL_CONTEXT_STATES = {
    "financial_context_available",
    "financial_context_partial",
    "financial_context_unavailable",
}
_CANDIDATE_SPEND_STATES = {"recorded", "missing"}
_CURRENCY_STATES = {
    "same_currency_context",
    "candidate_price_missing",
    "no_quantified_recovery",
    "mixed_currency_no_conversion",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=_canonical)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "as_dict"):
        result = value.as_dict()
        if isinstance(result, Mapping):
            return dict(result)
    raise ValueError(f"{label} authority must be a mapping projection.")


def _date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{label} plan_date is invalid.") from exc
    raise ValueError(f"{label} plan_date is required.")


def _required_text(value: Any, label: str) -> str:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is required for Care verdict identity.")
    return str(value)


def _nested(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key, default)
    return current


def _authority_identity(
    assessment: Mapping[str, Any], evidence: Mapping[str, Any], value: Mapping[str, Any]
) -> tuple[str, str, str, date, str, str, str]:
    if assessment.get("care_purchase_assessment_version") != CARE_PURCHASE_ASSESSMENT_VERSION:
        raise ValueError("Unsupported Care assessment authority version.")
    if assessment.get("care_purchase_assessment_schema_version") != CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION:
        raise ValueError("Unsupported Care assessment authority schema version.")
    if evidence.get("care_purchase_evidence_version") != CARE_PURCHASE_EVIDENCE_VERSION:
        raise ValueError("Unsupported Care Evidence authority version.")
    if evidence.get("care_purchase_evidence_schema_version") != CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("Unsupported Care Evidence authority schema version.")
    if value.get("care_purchase_value_version") != CARE_PURCHASE_VALUE_VERSION:
        raise ValueError("Unsupported Care value authority version.")
    if value.get("care_purchase_value_schema_version") != CARE_PURCHASE_VALUE_SCHEMA_VERSION:
        raise ValueError("Unsupported Care value authority schema version.")

    account_id = _required_text(assessment.get("account_id"), "assessment.account_id")
    candidate_id = _required_text(assessment.get("candidate_id"), "assessment.candidate_id")
    category = assessment.get("category")
    if category not in {"beauty", "hair"}:
        raise ValueError("Care verdict category must be beauty or hair.")
    plan_date = _date(assessment.get("plan_date"), "assessment")
    assessment_fingerprint = _required_text(
        assessment.get("assessment_fingerprint"), "assessment.assessment_fingerprint"
    )
    evidence_fingerprint = _required_text(
        evidence.get("projection_fingerprint"), "evidence.projection_fingerprint"
    )
    value_fingerprint = _required_text(value.get("value_fingerprint"), "value.value_fingerprint")

    for label, projection in (("Evidence", evidence), ("Value", value)):
        if _required_text(projection.get("account_id"), f"{label}.account_id") != account_id:
            raise ValueError("Care purchase authorities have mismatched account identity.")
        if _required_text(projection.get("candidate_id"), f"{label}.candidate_id") != candidate_id:
            raise ValueError("Care purchase authorities have mismatched candidate identity.")
        if projection.get("category") != category:
            raise ValueError("Care purchase authorities have mismatched category identity.")
        if _date(projection.get("plan_date"), label) != plan_date:
            raise ValueError("Care purchase authorities have mismatched plan date.")
        if _required_text(projection.get("assessment_fingerprint"), f"{label}.assessment_fingerprint") != assessment_fingerprint:
            raise ValueError("Care purchase authorities have mismatched assessment fingerprint.")
    return account_id, candidate_id, category, plan_date, assessment_fingerprint, evidence_fingerprint, value_fingerprint


def _validate_assessment(assessment: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    dimensions = assessment.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise ValueError("Care assessment dimensions are required.")
    role = dimensions.get("role_utility")
    redundancy = dimensions.get("redundancy")
    compatibility = dimensions.get("compatibility")
    constraints = assessment.get("user_constraints")
    if not all(isinstance(item, Mapping) for item in (role, redundancy, constraints, compatibility)):
        raise ValueError("Care assessment dimensions are malformed.")
    role = dict(role)
    redundancy = dict(redundancy)
    constraints = dict(constraints)
    compatibility = dict(compatibility)
    role_status = role.get("status")
    redundancy_status = redundancy.get("status")
    if role_status not in _ROLE_STATES:
        raise ValueError("Unexpected Care role state.")
    if redundancy_status not in _REDUNDANCY_STATES:
        raise ValueError("Unexpected Care redundancy state.")
    if constraints.get("status") not in _CONSTRAINT_STATES:
        raise ValueError("Unexpected Care user-constraint state.")
    if compatibility.get("status") not in _COMPATIBILITY_STATES:
        raise ValueError("Unexpected Care compatibility state.")
    try:
        count = int(redundancy.get("eligible_owned_same_slot_count", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Care redundancy count is malformed.") from exc
    if count < 0:
        raise ValueError("Care redundancy count cannot be negative.")
    expected_counts = {
        "none_eligible_owned_same_slot": lambda value: value == 0,
        "one_eligible_owned_same_slot": lambda value: value == 1,
        "multiple_eligible_owned_same_slot": lambda value: value >= 2,
        "role_unresolved": lambda value: value == 0,
    }
    if not expected_counts[redundancy_status](count):
        raise ValueError("Care redundancy state and count are inconsistent.")
    selected = redundancy.get("selected_owned_item_id")
    if role_status == "addresses_required_gap" and (
        role.get("required") is not True or role.get("is_gap") is not True or count != 0 or selected is not None
    ):
        raise ValueError("Required-gap role state is inconsistent with redundancy.")
    if role_status == "required_role_already_covered" and (
        role.get("required") is not True or role.get("is_gap") is not False or count < 1 or selected in (None, "")
    ):
        raise ValueError("Covered required role state is inconsistent with redundancy.")
    if role_status == "optional_role_not_required" and role.get("required") is not False:
        raise ValueError("Optional role state is inconsistent with required flag.")
    findings = compatibility.get("findings", ())
    if not isinstance(findings, (list, tuple)):
        raise ValueError("Care compatibility findings are malformed.")
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise ValueError("Care compatibility finding is malformed.")
        if finding.get("severity") not in {"info", "caution"}:
            raise ValueError("Unexpected Care compatibility severity.")
    return role, redundancy, constraints, compatibility


def _evidence_findings(evidence: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for path in (
        _nested(evidence, "evidence_support", "findings", default=()),
        _nested(evidence, "ingredient_utility", "findings", default=()),
    ):
        if not isinstance(path, (list, tuple)):
            raise ValueError("Care Evidence findings are malformed.")
        for row in path:
            if not isinstance(row, Mapping):
                raise ValueError("Care Evidence finding is malformed.")
            rows.append(row)
    return tuple(rows)


def _known_status(value: Any, *, states: set[str], label: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is malformed.")
    status = value.get("status")
    if status not in states:
        raise ValueError(f"Unexpected {label}.")
    return str(status)


def _decision_fingerprint(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(material).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CarePurchaseVerdict:
    verdict_version: str
    schema_version: str
    account_id: uuid.UUID | str
    candidate_id: uuid.UUID | str
    category: str
    category_label: str
    plan_date: date
    assessment_fingerprint: str
    evidence_projection_fingerprint: str
    value_fingerprint: str
    verdict: str
    headline: str
    primary_reason_code: str
    reason_codes: tuple[str, ...]
    supporting_reason_codes: tuple[str, ...]
    explanation: str
    decision_context: Mapping[str, Any]
    decision_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "care_purchase_verdict_version": self.verdict_version,
            "care_purchase_verdict_schema_version": self.schema_version,
            "strategy": "care_purchase",
            "account_id": str(self.account_id),
            "candidate_id": str(self.candidate_id),
            "category": self.category,
            "category_label": self.category_label,
            "plan_date": self.plan_date.isoformat(),
            "assessment_fingerprint": self.assessment_fingerprint,
            "evidence_projection_fingerprint": self.evidence_projection_fingerprint,
            "value_fingerprint": self.value_fingerprint,
            "decision_fingerprint": self.decision_fingerprint,
            "verdict": self.verdict,
            "headline": self.headline,
            "primary_reason_code": self.primary_reason_code,
            "reason_codes": list(self.reason_codes),
            "supporting_reason_codes": list(self.supporting_reason_codes),
            "decision_context": dict(self.decision_context),
            "explanation": self.explanation,
            "boundary": (
                "This is a current-context Care purchase decision from confirmed facts, owned inventory, "
                "reviewed Care rules, existing Evidence and recorded value context. It is not medical advice "
                "or a claim that this product is objectively effective."
            ),
        }


def project_care_purchase_verdict(
    assessment: Any,
    evidence: Any,
    value: Any,
) -> CarePurchaseVerdict:
    """Apply the ordered V3-05.5 policy to three matching authority projections."""
    assessment_data = _mapping(assessment, "Assessment")
    evidence_data = _mapping(evidence, "Evidence")
    value_data = _mapping(value, "Value")
    (
        account_id,
        candidate_id,
        category,
        plan_date,
        assessment_fingerprint,
        evidence_fingerprint,
        value_fingerprint,
    ) = _authority_identity(assessment_data, evidence_data, value_data)
    role, redundancy, constraints, compatibility = _validate_assessment(assessment_data)
    evidence_findings = _evidence_findings(evidence_data)
    identity = _nested(assessment_data, "dimensions", "identity_confidence", default={})
    if not isinstance(identity, Mapping):
        raise ValueError("Care assessment identity coverage is malformed.")
    missing = tuple(str(item) for item in identity.get("missing_information", ()) or ())
    role_status = role["status"]
    count = int(redundancy.get("eligible_owned_same_slot_count", 0))
    user_status = constraints["status"]
    compatibility_status = compatibility["status"]
    caution_count = sum(1 for finding in compatibility.get("findings", ()) if finding.get("severity") == "caution")
    info_count = sum(1 for finding in compatibility.get("findings", ()) if finding.get("severity") == "info")
    evidence_support_status = _known_status(
        evidence_data.get("evidence_support"),
        states=_EVIDENCE_SUPPORT_STATES,
        label="Care Evidence support status",
    )
    utility_status = _known_status(
        evidence_data.get("ingredient_utility"),
        states=_INGREDIENT_UTILITY_STATES,
        label="Care ingredient-utility status",
    )
    value_context = value_data.get("value_context")
    value_status = _known_status(
        value_context,
        states=_FINANCIAL_CONTEXT_STATES,
        label="Care financial-context status",
    )
    candidate_spend_status = _known_status(
        _nested(value_context, "candidate_spend"),
        states=_CANDIDATE_SPEND_STATES,
        label="Care candidate-spend status",
    )
    recovery_status = _known_status(
        _nested(value_context, "owned_value_recovery"),
        states={"no_low_use_eligible_owned_same_slot", *_RECOVERY_STATES},
        label="Care owned-value-recovery status",
    )
    currency_status = _known_status(
        _nested(value_context, "currency_context"),
        states=_CURRENCY_STATES,
        label="Care currency-context status",
    )
    conflict = any(finding.get("claim_status") == "conflicting" for finding in evidence_findings)
    supporting: list[str] = []
    if evidence_support_status not in {None, "no_applicable_reviewed_support", "not_established_from_existing_evidence"}:
        supporting.append("reviewed_evidence_context_available")
    if utility_status in {"reviewed_utility_available", "reviewed_utility_partial"}:
        supporting.append("reviewed_utility_context_available")
    if info_count:
        supporting.append("reviewed_compatibility_info")
    supporting = sorted(set(supporting))
    decision_context = {
        "role_status": role_status,
        "care_slot": role.get("care_slot"),
        "eligible_owned_same_slot_count": count,
        "selected_owned_item_id": redundancy.get("selected_owned_item_id"),
        "user_constraint_status": user_status,
        "compatibility_status": compatibility_status,
        "compatibility_caution_count": caution_count,
        "compatibility_info_count": info_count,
        "evidence_support_status": evidence_support_status,
        "ingredient_utility_status": utility_status,
        "candidate_spend_status": candidate_spend_status,
        "owned_value_recovery_status": recovery_status,
        "currency_context_status": currency_status,
    }

    # The order below is the V3-05.5 policy.  Do not reorder these gates.
    if user_status == "confirmed_user_constraint_match":
        verdict, primary = VERDICT_SKIP, "user_declared_constraint_match"
    elif role_status == "role_unresolved":
        verdict, primary = VERDICT_WAIT, "care_role_unresolved"
    elif (
        "ingredients" in missing
        or any(item.startswith("unrecognised_ingredient:") for item in missing)
        or compatibility_status == "insufficient_ingredient_information"
        or user_status == "insufficient_ingredient_information"
    ):
        verdict, primary = VERDICT_WAIT, "candidate_ingredient_information_incomplete"
    elif conflict:
        verdict, primary = VERDICT_WAIT, "reviewed_evidence_conflict"
    elif caution_count:
        verdict, primary = VERDICT_WAIT, "reviewed_compatibility_caution"
    elif candidate_spend_status == "missing":
        verdict, primary = VERDICT_WAIT, "candidate_price_missing"
    elif currency_status == "mixed_currency_no_conversion":
        verdict, primary = VERDICT_WAIT, "mixed_currency_no_conversion"
    elif value_status == "financial_context_partial":
        verdict, primary = VERDICT_WAIT, "financial_context_incomplete"
    elif recovery_status in _RECOVERY_STATES:
        verdict, primary = VERDICT_WAIT, "owned_value_to_recover_first"
    elif role_status == "optional_role_not_required" and count >= 1:
        verdict, primary = VERDICT_SKIP, "optional_role_already_owned"
    elif role_status == "required_role_already_covered":
        verdict, primary = VERDICT_WAIT, "required_role_already_covered"
    elif role_status == "optional_role_not_required" and count == 0:
        verdict, primary = VERDICT_WAIT, "optional_role_not_required"
    elif (
        role_status == "addresses_required_gap"
        and redundancy["status"] == "none_eligible_owned_same_slot"
        and count == 0
        and user_status == "no_match_on_recognised_ingredients"
        and candidate_spend_status == "recorded"
        and recovery_status == "no_low_use_eligible_owned_same_slot"
        and currency_status != "mixed_currency_no_conversion"
        and value_status != "financial_context_partial"
    ):
        verdict, primary = VERDICT_BUY, "required_gap_no_owned_alternative"
    else:
        raise ValueError("Unexpected Care role, redundancy or financial policy combination.")

    reason_codes = (primary,)
    material = {
        "verdict_version": CARE_PURCHASE_VERDICT_VERSION,
        "schema_version": CARE_PURCHASE_VERDICT_SCHEMA_VERSION,
        "account_id": account_id,
        "candidate_id": candidate_id,
        "category": category,
        "plan_date": plan_date,
        "assessment_fingerprint": assessment_fingerprint,
        "evidence_projection_fingerprint": evidence_fingerprint,
        "value_fingerprint": value_fingerprint,
        "verdict": verdict,
        "primary_reason_code": primary,
        "reason_codes": reason_codes,
        "supporting_reason_codes": tuple(supporting),
        "decision_context": decision_context,
    }
    return CarePurchaseVerdict(
        verdict_version=CARE_PURCHASE_VERDICT_VERSION,
        schema_version=CARE_PURCHASE_VERDICT_SCHEMA_VERSION,
        account_id=account_id,
        candidate_id=candidate_id,
        category=category,
        category_label=PURCHASE_CATEGORY_LABELS[category],
        plan_date=plan_date,
        assessment_fingerprint=assessment_fingerprint,
        evidence_projection_fingerprint=evidence_fingerprint,
        value_fingerprint=value_fingerprint,
        verdict=verdict,
        headline=HEADLINES[verdict],
        primary_reason_code=primary,
        reason_codes=reason_codes,
        supporting_reason_codes=tuple(supporting),
        explanation=EXPLANATIONS[primary],
        decision_context=decision_context,
        decision_fingerprint=_decision_fingerprint(material),
    )


evaluate_care_purchase_verdict = project_care_purchase_verdict

__all__ = [
    "EXPLANATIONS",
    "HEADLINES",
    "CarePurchaseVerdict",
    "VERDICT_BUY",
    "VERDICT_SKIP",
    "VERDICT_WAIT",
    "evaluate_care_purchase_verdict",
    "project_care_purchase_verdict",
]
