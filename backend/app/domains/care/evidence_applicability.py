"""Pure matching of reviewed evidence scope to explicit Care signals."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domains.evidence.service import RuleEvidenceAssessment

CARE_EVIDENCE_APPLICABILITY_VERSION = "v3-03.16"

_DIMENSIONS = (
    ("jurisdictions", "jurisdiction_signal_missing", "jurisdiction_mismatch"),
    ("populations", "population_signal_missing", "population_mismatch"),
    ("formulations", "formulation_signal_missing", "formulation_mismatch"),
    ("usage_contexts", "usage_context_signal_missing", "usage_context_mismatch"),
)
_REASON_ORDER = (
    "evidence_not_behavior_eligible",
    "no_behavior_eligible_path",
    "jurisdiction_signal_missing",
    "population_signal_missing",
    "formulation_signal_missing",
    "usage_context_signal_missing",
    "jurisdiction_mismatch",
    "population_mismatch",
    "formulation_mismatch",
    "usage_context_mismatch",
    "no_applicable_support_path",
    "malformed_signal",
)


def _normalize_signal(value: Any) -> tuple[tuple[str, ...], bool]:
    """Normalize an explicit token collection without inference."""
    if value is None:
        return (), True
    if not isinstance(value, (list, tuple)):
        return (), False
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return (), False
        token = item.strip()
        if not token:
            return (), False
        if token not in normalized:
            normalized.append(token)
    return tuple(normalized), True


@dataclass(frozen=True, slots=True)
class CareApplicabilitySignals:
    """Explicit, normalized current-context signals supplied by Care."""

    jurisdictions: tuple[str, ...]
    populations: tuple[str, ...]
    formulations: tuple[str, ...]
    usage_contexts: tuple[str, ...]
    _malformed_dimensions: tuple[str, ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        malformed: list[str] = []
        for dimension in ("jurisdictions", "populations", "formulations", "usage_contexts"):
            normalized, valid = _normalize_signal(getattr(self, dimension))
            object.__setattr__(self, dimension, normalized)
            if not valid:
                malformed.append(dimension)
        object.__setattr__(self, "_malformed_dimensions", tuple(malformed))

    @property
    def malformed_dimensions(self) -> tuple[str, ...]:
        return self._malformed_dimensions


@dataclass(frozen=True, slots=True)
class CareRuleApplicabilityResult:
    """Immutable, auditable result of evidence/current-context matching."""

    applicability_version: str
    applicable: bool
    behavior_evidence_eligible: bool
    matching_claim_ids: tuple[UUID, ...] = ()
    reason_codes: tuple[str, ...] = ()


def _ordered_reason_codes(codes: set[str]) -> tuple[str, ...]:
    order = {code: index for index, code in enumerate(_REASON_ORDER)}
    return tuple(sorted(codes, key=lambda code: (order.get(code, len(order)), code)))


def _path_matches(path: Any, signals: CareApplicabilitySignals) -> tuple[bool, set[str]]:
    reasons: set[str] = set()
    claim_applicability = path.applicability
    for dimension, missing_code, mismatch_code in _DIMENSIONS:
        claim_values = getattr(claim_applicability, dimension, ())
        signal_values = getattr(signals, dimension)
        if not isinstance(claim_values, (list, tuple)) or not all(
            isinstance(value, str) and value for value in claim_values
        ):
            reasons.add(mismatch_code)
            continue
        if dimension == "jurisdictions" and "global" in claim_values:
            continue
        if not signal_values:
            reasons.add(missing_code)
        elif not set(claim_values).intersection(signal_values):
            reasons.add(mismatch_code)
    return not reasons, reasons


def resolve_care_evidence_applicability(
    assessment: RuleEvidenceAssessment,
    signals: CareApplicabilitySignals,
) -> CareRuleApplicabilityResult:
    """Match eligible evidence scopes to explicitly supplied Care signals."""
    if signals.malformed_dimensions:
        return CareRuleApplicabilityResult(
            applicability_version=CARE_EVIDENCE_APPLICABILITY_VERSION,
            applicable=False,
            behavior_evidence_eligible=assessment.behavior_evidence_eligible,
            reason_codes=("malformed_signal",),
        )
    if not assessment.behavior_evidence_eligible:
        return CareRuleApplicabilityResult(
            applicability_version=CARE_EVIDENCE_APPLICABILITY_VERSION,
            applicable=False,
            behavior_evidence_eligible=False,
            reason_codes=("evidence_not_behavior_eligible",),
        )
    if not assessment.behavior_eligible_paths:
        return CareRuleApplicabilityResult(
            applicability_version=CARE_EVIDENCE_APPLICABILITY_VERSION,
            applicable=False,
            behavior_evidence_eligible=True,
            reason_codes=("no_behavior_eligible_path",),
        )

    matching_claim_ids: set[UUID] = set()
    failure_reasons: set[str] = set()
    for path in assessment.behavior_eligible_paths:
        matches, reasons = _path_matches(path, signals)
        if matches:
            matching_claim_ids.add(path.claim_id)
        else:
            failure_reasons.update(reasons)
    if matching_claim_ids:
        return CareRuleApplicabilityResult(
            applicability_version=CARE_EVIDENCE_APPLICABILITY_VERSION,
            applicable=True,
            behavior_evidence_eligible=True,
            matching_claim_ids=tuple(sorted(matching_claim_ids, key=str)),
        )
    failure_reasons.add("no_applicable_support_path")
    return CareRuleApplicabilityResult(
        applicability_version=CARE_EVIDENCE_APPLICABILITY_VERSION,
        applicable=False,
        behavior_evidence_eligible=True,
        reason_codes=_ordered_reason_codes(failure_reasons),
    )


__all__ = [
    "CARE_EVIDENCE_APPLICABILITY_VERSION",
    "CareApplicabilitySignals",
    "CareRuleApplicabilityResult",
    "resolve_care_evidence_applicability",
]
