"""Pure, fail-closed validation for structured evidence applicability."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

EVIDENCE_APPLICABILITY_VERSION = "v3-03.15"


@dataclass(frozen=True, slots=True)
class EvidenceApplicability:
    """Normalized, immutable applicability dimensions from a reviewed claim."""

    schema_version: str
    jurisdictions: tuple[str, ...]
    populations: tuple[str, ...]
    formulations: tuple[str, ...]
    usage_contexts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplicabilityValidationResult:
    """Deterministic parser output with machine-readable failure reasons."""

    valid: bool
    schema_version: str | None = None
    applicability: EvidenceApplicability | None = None
    invalid_reason_codes: tuple[str, ...] = ()

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self.invalid_reason_codes

    @property
    def invalid_reasons(self) -> tuple[str, ...]:
        return self.invalid_reason_codes

    @property
    def normalized_applicability(self) -> EvidenceApplicability | None:
        return self.applicability


_DIMENSION_KEYS = (
    "jurisdictions",
    "populations",
    "formulations",
    "usage_contexts",
)
_MISSING = object()


def _structured_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value
    return getattr(value, "structured_value", _MISSING)


def _normalized_dimension(value: Any, missing_code: str) -> tuple[tuple[str, ...] | None, str | None]:
    if value is _MISSING or value is None or value == [] or value == ():
        return None, missing_code
    if not isinstance(value, (list, tuple)):
        return None, "malformed_applicability"
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None, "malformed_applicability"
        item = item.strip()
        if not item:
            return None, "malformed_applicability"
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        return None, missing_code
    return tuple(normalized), None


def parse_behavior_applicability(value: Any) -> ApplicabilityValidationResult:
    """Parse an EvidenceClaim or structured_value without inference or I/O."""
    structured = _structured_value(value)
    if structured is _MISSING or structured is None:
        return ApplicabilityValidationResult(False, invalid_reason_codes=("structured_value_missing",))
    if not isinstance(structured, Mapping):
        return ApplicabilityValidationResult(False, invalid_reason_codes=("malformed_applicability",))

    block = structured.get("behavior_applicability", _MISSING)
    if block is _MISSING:
        return ApplicabilityValidationResult(False, invalid_reason_codes=("applicability_missing",))
    if not isinstance(block, Mapping):
        return ApplicabilityValidationResult(False, invalid_reason_codes=("malformed_applicability",))

    raw_schema_version = block.get("schema_version", _MISSING)
    if raw_schema_version is _MISSING:
        return ApplicabilityValidationResult(False, invalid_reason_codes=("schema_version_missing",))
    if not isinstance(raw_schema_version, str):
        return ApplicabilityValidationResult(False, invalid_reason_codes=("malformed_applicability",))
    if raw_schema_version != EVIDENCE_APPLICABILITY_VERSION:
        return ApplicabilityValidationResult(
            False,
            schema_version=raw_schema_version,
            invalid_reason_codes=("unsupported_schema_version",),
        )

    parsed: dict[str, tuple[str, ...]] = {}
    reasons: list[str] = []
    for key in _DIMENSION_KEYS:
        dimension, reason = _normalized_dimension(block.get(key, _MISSING), f"{key[:-1]}_missing")
        if reason:
            reasons.append(reason)
        elif dimension is not None:
            parsed[key] = dimension
    if reasons:
        return ApplicabilityValidationResult(
            False,
            schema_version=raw_schema_version,
            invalid_reason_codes=tuple(reasons),
        )

    return ApplicabilityValidationResult(
        True,
        schema_version=raw_schema_version,
        applicability=EvidenceApplicability(
            schema_version=raw_schema_version,
            jurisdictions=parsed["jurisdictions"],
            populations=parsed["populations"],
            formulations=parsed["formulations"],
            usage_contexts=parsed["usage_contexts"],
        ),
    )


parse_evidence_applicability = parse_behavior_applicability
validate_behavior_applicability = parse_behavior_applicability
validate_evidence_applicability = parse_behavior_applicability
