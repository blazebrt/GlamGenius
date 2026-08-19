"""Pure V3-05.3 projection of reviewed Care Evidence.

The database resolver supplies only already-reviewed first-class Evidence
paths.  This module deliberately has no persistence, HTTP, AI, or ontology
authority and therefore cannot manufacture a scientific claim.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
)

REVIEWED_SUPPORT_AVAILABLE = "reviewed_support_available"
REVIEWED_SUPPORT_PARTIAL = "reviewed_support_partial"
NO_APPLICABLE_REVIEWED_SUPPORT = "no_applicable_reviewed_support"
INSUFFICIENT_CANDIDATE_INFORMATION = "insufficient_candidate_information"

REVIEWED_UTILITY_AVAILABLE = "reviewed_utility_available"
REVIEWED_UTILITY_PARTIAL = "reviewed_utility_partial"
NOT_ESTABLISHED_FROM_EXISTING_EVIDENCE = "not_established_from_existing_evidence"

_PUBLIC_SOURCE_KEYS = (
    "source_id", "source_key", "title", "publisher", "source_type",
    "publication_date", "canonical_url", "relationship", "locator",
)


@dataclass(frozen=True, slots=True)
class ReviewedEvidencePath:
    """A reviewed rule/claim/source path supplied by the DB resolver."""

    rule_id: str | None
    rule_kind: str | None
    rule_version: str | None
    relationship: str
    claim_id: uuid.UUID | str
    claim_key: str
    claim_version: int
    claim_summary: str
    claim_scope: str
    claim_type: str
    evidence_strength: str | None
    claim_status: str | None
    applicability: Mapping[str, Any] | None
    sources: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class IngredientUtilityPath:
    """An explicit ingredient/family-to-claim mapping, if one exists."""

    ingredient_key: str | None
    ingredient_family: str | None
    claim_id: uuid.UUID | str
    claim_key: str
    claim_version: int
    claim_summary: str
    claim_scope: str
    claim_type: str
    evidence_strength: str | None
    claim_status: str | None
    applicability: Mapping[str, Any] | None
    sources: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CarePurchaseEvidenceProjection:
    evidence_version: str
    schema_version: str
    account_id: uuid.UUID | str
    candidate_id: uuid.UUID | str
    category: str
    plan_date: date
    candidate_truth_version: str
    care_purchase_assessment_version: str
    assessment_fingerprint: str
    evidence_support: Mapping[str, Any]
    ingredient_utility: Mapping[str, Any]
    value_context: Mapping[str, Any]
    projection_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "care_purchase_evidence_version": self.evidence_version,
            "care_purchase_evidence_schema_version": self.schema_version,
            "account_id": str(self.account_id),
            "candidate_id": str(self.candidate_id),
            "category": self.category,
            "plan_date": self.plan_date.isoformat(),
            "candidate_truth_version": self.candidate_truth_version,
            "care_purchase_assessment_version": self.care_purchase_assessment_version,
            "assessment_fingerprint": self.assessment_fingerprint,
            "evidence_support": dict(self.evidence_support),
            "ingredient_utility": dict(self.ingredient_utility),
            "value_context": dict(self.value_context),
            "projection_fingerprint": self.projection_fingerprint,
            "boundary": (
                "This projection reports reviewed Evidence already held by GlamGenius. "
                "It does not judge whether a product should be purchased."
            ),
        }


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _assessment_value(assessment: Any, key: str, default: Any = None) -> Any:
    if isinstance(assessment, Mapping):
        if key in assessment:
            return assessment[key]
        dimensions = assessment.get("dimensions", {})
        if isinstance(dimensions, Mapping) and key in dimensions:
            return dimensions[key]
        return default
    value = _value(assessment, key, default)
    if value is not default:
        return value
    serialized = assessment.as_dict() if hasattr(assessment, "as_dict") else {}
    if key in serialized:
        return serialized[key]
    dimensions = serialized.get("dimensions", {})
    return dimensions.get(key, default)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        values = [_json_value(item) for item in value]
        return sorted(values, key=_canonical)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _id(value: Any) -> str:
    return str(value)


def _source(source: Any, *, relationship: str | None = None, locator: str | None = None) -> dict[str, Any]:
    if isinstance(source, Mapping):
        row = {key: source[key] for key in _PUBLIC_SOURCE_KEYS if key in source}
    else:
        row = {key: getattr(source, key) for key in _PUBLIC_SOURCE_KEYS if hasattr(source, key)}
    if relationship is not None:
        row["relationship"] = relationship
    if locator is not None:
        row["locator"] = locator
    if "publication_date" in row and row["publication_date"] is not None:
        row["publication_date"] = str(row["publication_date"])
    if "source_id" in row:
        row["source_id"] = _id(row["source_id"])
    return row


def _source_sort_key(source: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(
        str(source.get(key) or "")
        for key in ("source_key", "source_id", "relationship", "locator")
    )


def _sources(path: Any) -> list[dict[str, Any]]:
    sources = [
        _source(source)
        for source in (_path_value(path, "sources", ()) or ())
    ]
    return sorted(sources, key=_source_sort_key)


def _path_value(path: Any, key: str, default: Any = None) -> Any:
    return _value(path, key, default)


def _finding(path: Any) -> dict[str, Any]:
    return {
        "finding_type": "compatibility_rule_evidence",
        "rule_id": _path_value(path, "rule_id"),
        "rule_kind": _path_value(path, "rule_kind"),
        "rule_version": _path_value(path, "rule_version"),
        "relationship": _path_value(path, "relationship"),
        "claim_id": _id(_path_value(path, "claim_id")),
        "claim_key": _path_value(path, "claim_key"),
        "claim_version": _path_value(path, "claim_version"),
        "claim_summary": _path_value(path, "claim_summary"),
        "claim_scope": _path_value(path, "claim_scope"),
        "claim_type": _path_value(path, "claim_type"),
        "evidence_strength": _path_value(path, "evidence_strength"),
        "claim_status": _path_value(path, "claim_status"),
        "applicability": _json_value(_path_value(path, "applicability")),
        "substantive_support": (
            _path_value(path, "relationship") == "supports"
            and _path_value(path, "claim_status") == "supported"
        ),
        "sources": _sources(path),
    }


def _utility_finding(path: Any) -> dict[str, Any]:
    return {
        "finding_type": "ingredient_utility_evidence",
        "ingredient_key": _path_value(path, "ingredient_key"),
        "ingredient_family": _path_value(path, "ingredient_family"),
        "claim_id": _id(_path_value(path, "claim_id")),
        "claim_key": _path_value(path, "claim_key"),
        "claim_version": _path_value(path, "claim_version"),
        "claim_summary": _path_value(path, "claim_summary"),
        "claim_scope": _path_value(path, "claim_scope"),
        "claim_type": _path_value(path, "claim_type"),
        "evidence_strength": _path_value(path, "evidence_strength"),
        "claim_status": _path_value(path, "claim_status"),
        "applicability": _json_value(_path_value(path, "applicability")),
        "substantive_support": _path_value(path, "claim_status") == "supported",
        "sources": _sources(path),
    }


def _compatibility_findings(assessment: Any) -> tuple[Mapping[str, Any], ...]:
    compatibility = _assessment_value(assessment, "compatibility", {}) or {}
    if not isinstance(compatibility, Mapping):
        return ()
    return tuple(row for row in compatibility.get("findings", ()) if isinstance(row, Mapping))


def _identity_missing(assessment: Any) -> tuple[str, ...]:
    identity = _assessment_value(assessment, "identity_confidence", {}) or {}
    if not isinstance(identity, Mapping):
        return ()
    return tuple(sorted(str(value) for value in identity.get("missing_information", ()) or ()))


def _structural_authorities(assessment: Any) -> dict[str, Any]:
    role = _assessment_value(assessment, "role_utility", {}) or {}
    redundancy = _assessment_value(assessment, "redundancy", {}) or {}
    constraints = _assessment_value(assessment, "user_constraints", {}) or {}
    return {
        "role_utility": {
            "authority": "account_state",
            "status": role.get("status") if isinstance(role, Mapping) else None,
            "required": role.get("required") if isinstance(role, Mapping) else None,
        },
        "redundancy": {
            "authority": "deterministic_inventory_fact",
            "status": redundancy.get("status") if isinstance(redundancy, Mapping) else None,
            "eligible_owned_same_slot_count": (
                redundancy.get("eligible_owned_same_slot_count")
                if isinstance(redundancy, Mapping) else None
            ),
        },
        "user_constraints": {
            "authority": "user_declared_constraint",
            "status": constraints.get("status") if isinstance(constraints, Mapping) else None,
            "matched_ingredient_keys": sorted(
                constraints.get("matched_ingredient_keys", ())
                if isinstance(constraints, Mapping) else ()
            ),
        },
    }


def project_care_purchase_evidence(
    assessment: Any,
    *,
    rule_evidence: Iterable[Any] = (),
    ingredient_evidence: Iterable[Any] = (),
    candidate_truth: Any | None = None,
) -> CarePurchaseEvidenceProjection:
    """Project only explicit reviewed paths onto a V3-05.2 assessment."""
    findings = _compatibility_findings(assessment)
    missing = _identity_missing(assessment)
    paths = tuple(rule_evidence)
    utility_paths = tuple(ingredient_evidence)
    path_by_rule: dict[str, list[Any]] = {}
    for path in paths:
        rule_id = _path_value(path, "rule_id")
        if rule_id:
            path_by_rule.setdefault(str(rule_id), []).append(path)

    projected_findings: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    substantive_by_rule: dict[str, bool] = {}
    for finding in findings:
        rule_id = finding.get("rule_id")
        matches = path_by_rule.get(str(rule_id), []) if rule_id else []
        if matches:
            projected_findings.extend(_finding(path) for path in matches)
            substantive_by_rule[str(rule_id)] = any(
                _path_value(path, "relationship") == "supports"
                and _path_value(path, "claim_status") == "supported"
                for path in matches
            )
        else:
            unsupported.append({
                "reason_code": "no_reviewed_evidence_link",
                "finding_type": "compatibility_rule",
                "rule_id": rule_id,
            })

    substantive_count = sum(substantive_by_rule.values())
    coverage_complete = bool(findings) and all(
        substantive_by_rule.get(str(finding.get("rule_id")), False)
        for finding in findings
    )
    material_context = any(
        row.get("substantive_support") is False for row in projected_findings
    )
    if missing and not projected_findings:
        support_status = INSUFFICIENT_CANDIDATE_INFORMATION
    elif not findings or not substantive_count:
        support_status = NO_APPLICABLE_REVIEWED_SUPPORT
    elif not coverage_complete or unsupported or material_context:
        support_status = REVIEWED_SUPPORT_PARTIAL
    elif projected_findings:
        support_status = REVIEWED_SUPPORT_AVAILABLE
    else:
        support_status = NO_APPLICABLE_REVIEWED_SUPPORT

    evidence_support = {
        "status": support_status,
        "authority": "first_class_reviewed_evidence",
        "substantive_support": substantive_count > 0,
        "reviewed_context": bool(projected_findings),
        "findings": sorted(
            projected_findings,
            key=lambda row: (
                str(row.get("rule_id")),
                str(row.get("claim_key")),
                str(row.get("claim_id")),
                _canonical(row),
            ),
        ),
        "unsupported": sorted(
            unsupported,
            key=lambda row: (str(row.get("rule_id")), str(row.get("reason_code"))),
        ),
        "structural_facts": _structural_authorities(assessment),
    }

    recognised_source = (
        _value(candidate_truth, "recognised_ingredient_keys", ())
        if candidate_truth is not None
        else _assessment_value(assessment, "recognised_ingredient_keys", ())
    )
    recognised = tuple(sorted(str(key) for key in (recognised_source or ())))
    if not recognised:
        utility_status = INSUFFICIENT_CANDIDATE_INFORMATION
    else:
        supported_utility_paths = tuple(
            path
            for path in utility_paths
            if _path_value(path, "claim_status") == "supported"
        )
        has_context_only_paths = bool(utility_paths) and not all(
            _path_value(path, "claim_status") == "supported"
            for path in utility_paths
        )
        if supported_utility_paths:
            utility_status = (
                REVIEWED_UTILITY_PARTIAL
                if missing or has_context_only_paths
                else REVIEWED_UTILITY_AVAILABLE
            )
        else:
            utility_status = NOT_ESTABLISHED_FROM_EXISTING_EVIDENCE
    unknown = sorted(
        value.split(":", 1)[1]
        for value in missing
        if value.startswith("unrecognised_ingredient:")
    )
    ingredient_utility = {
        "status": utility_status,
        "authority": "first_class_reviewed_evidence",
        "recognised_ingredient_keys": recognised,
        "unknown_ingredient_terms": unknown,
        "findings": sorted(
            [_utility_finding(path) for path in utility_paths],
            key=lambda row: (
                str(row.get("ingredient_key")),
                str(row.get("ingredient_family")),
                str(row.get("claim_key")),
                _canonical(row),
            ),
        ),
        "unsupported": sorted(
                [
                    {
                        "reason_code": "unknown_ingredient_term",
                        "term": value,
                    }
                for value in unknown
            ],
            key=lambda row: row["term"],
        ) + ([{"reason_code": "no_explicit_ingredient_evidence_mapping"}] if not utility_paths and recognised else []),
    }

    value_context = {
        "status": "not_assessed",
        "reason_code": "care_purchase_value_not_assessed",
    }

    projection_material = {
        "evidence_version": CARE_PURCHASE_EVIDENCE_VERSION,
        "schema_version": CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
        "account_id": _id(_assessment_value(assessment, "account_id")),
        "candidate_id": _id(_assessment_value(assessment, "candidate_id")),
        "assessment_fingerprint": _assessment_value(assessment, "assessment_fingerprint"),
        "evidence_support": evidence_support,
        "ingredient_utility": ingredient_utility,
        "value_context": value_context,
    }
    projection_fingerprint = hashlib.sha256(_canonical(projection_material).encode()).hexdigest()

    plan_date_value = _assessment_value(assessment, "plan_date")
    if isinstance(plan_date_value, str):
        plan_date_value = date.fromisoformat(plan_date_value)
    return CarePurchaseEvidenceProjection(
        evidence_version=CARE_PURCHASE_EVIDENCE_VERSION,
        schema_version=CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
        account_id=_assessment_value(assessment, "account_id"),
        candidate_id=_assessment_value(assessment, "candidate_id"),
        category=_assessment_value(assessment, "category"),
        plan_date=plan_date_value,
        candidate_truth_version=_assessment_value(assessment, "candidate_truth_version"),
        care_purchase_assessment_version=(
            _assessment_value(assessment, "assessment_version", CARE_PURCHASE_ASSESSMENT_VERSION)
        ),
        assessment_fingerprint=_assessment_value(assessment, "assessment_fingerprint"),
        evidence_support=evidence_support,
        ingredient_utility=ingredient_utility,
        value_context=value_context,
        projection_fingerprint=projection_fingerprint,
    )


__all__ = [
    "CarePurchaseEvidenceProjection",
    "IngredientUtilityPath",
    "NOT_ESTABLISHED_FROM_EXISTING_EVIDENCE",
    "NO_APPLICABLE_REVIEWED_SUPPORT",
    "REVIEWED_SUPPORT_AVAILABLE",
    "REVIEWED_SUPPORT_PARTIAL",
    "REVIEWED_UTILITY_AVAILABLE",
    "REVIEWED_UTILITY_PARTIAL",
    "ReviewedEvidencePath",
    "project_care_purchase_evidence",
]
