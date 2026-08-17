"""Nutrition-owned, fail-closed matching of reviewed evidence scope."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domains.evidence.service import RuleEvidenceAssessment

NUTRITION_EVIDENCE_APPLICABILITY_VERSION = "v3-04.1"


def _tokens(value: Any) -> tuple[tuple[str, ...], bool]:
    if not isinstance(value, (list, tuple)):
        return (), False
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return (), False
        if item.strip() not in out:
            out.append(item.strip())
    return tuple(out), True


@dataclass(frozen=True, slots=True)
class NutritionApplicabilitySignals:
    jurisdictions: tuple[str, ...]
    populations: tuple[str, ...]
    formulations: tuple[str, ...]
    usage_contexts: tuple[str, ...]
    _malformed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        malformed = False
        for name in ("jurisdictions", "populations", "formulations", "usage_contexts"):
            normalized, valid = _tokens(getattr(self, name))
            object.__setattr__(self, name, normalized)
            malformed = malformed or not valid
        object.__setattr__(self, "_malformed", malformed)


@dataclass(frozen=True, slots=True)
class NutritionApplicabilityResult:
    applicability_version: str
    applicable: bool
    behavior_evidence_eligible: bool
    matching_claim_ids: tuple[UUID, ...] = ()
    reason_codes: tuple[str, ...] = ()


def resolve_nutrition_evidence_applicability(
    assessment: RuleEvidenceAssessment,
    signals: NutritionApplicabilitySignals,
) -> NutritionApplicabilityResult:
    if signals._malformed:
        return NutritionApplicabilityResult(NUTRITION_EVIDENCE_APPLICABILITY_VERSION, False, assessment.behavior_evidence_eligible, reason_codes=("malformed_signal",))
    if not assessment.behavior_evidence_eligible:
        return NutritionApplicabilityResult(NUTRITION_EVIDENCE_APPLICABILITY_VERSION, False, False, reason_codes=("evidence_not_behavior_eligible",))
    matches: list[UUID] = []
    for path in assessment.behavior_eligible_paths:
        applicability = path.applicability
        ok = True
        for name in ("jurisdictions", "populations", "formulations", "usage_contexts"):
            expected = getattr(applicability, name, ())
            actual = getattr(signals, name)
            if not isinstance(expected, (list, tuple)) or not actual or not set(expected).intersection(actual):
                ok = False
                break
        if ok:
            matches.append(path.claim_id)
    ordered = tuple(sorted(set(matches), key=str))
    return NutritionApplicabilityResult(
        NUTRITION_EVIDENCE_APPLICABILITY_VERSION, bool(ordered), True, ordered,
        () if ordered else ("no_applicable_support_path",),
    )


__all__ = ["NUTRITION_EVIDENCE_APPLICABILITY_VERSION", "NutritionApplicabilitySignals", "NutritionApplicabilityResult", "resolve_nutrition_evidence_applicability"]
