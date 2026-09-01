"""Policy for normalized community observations, not canonical product truth.

This module evaluates already-deduplicated structured evidence.  It neither
persists submissions nor changes ingredients, nutrition, product versions,
scientific scoring, or official records.  Presentation receives semantic keys,
never customer-facing copy, from this policy layer.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

COMMUNITY_SIGNAL_POLICY_VERSION: Final = "community-signals-v3"
# Product-policy window, not a scientific constant.  Historical evidence is
# retained separately and can be re-evaluated under a future policy version.
ACTIVE_POLICY_WINDOW_DAYS: Final = 90
MAX_DOMINANT_BATCH_SHARE_FOR_PRODUCT_SCOPE: Final = 0.70
MIN_GEOGRAPHY_AGGREGATION_REPORTERS: Final = 5


class ObservationSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalKind(StrEnum):
    """Presentation and epistemic meaning of an observation."""

    DATA_VERIFICATION = "data_verification"
    CONSUMER_CONDITION = "consumer_condition"


class ScopePolicy(StrEnum):
    """How an observation can establish its narrowest current scope."""

    PRODUCT_DATA = "product_data"
    BATCH_SENSITIVE = "batch_sensitive"
    BATCH_AND_CONDITION_SENSITIVE = "batch_and_condition_sensitive"


class SignalStage(StrEnum):
    COLLECTING = "collecting"
    EMERGING = "emerging"
    ESTABLISHED = "established"


class SignalScope(StrEnum):
    PRODUCT = "product"
    BATCH = "batch"
    INSUFFICIENT_CONTEXT = "insufficient_context"


class StorageConditionCategory(StrEnum):
    AMBIENT = "ambient"
    REFRIGERATED = "refrigerated"
    FROZEN = "frozen"
    UNKNOWN = "unknown"


class ObservationTimingCategory(StrEnum):
    ON_OPENING = "on_opening"
    WITHIN_24_HOURS = "within_24_hours"
    AFTER_24_HOURS = "after_24_hours"
    UNKNOWN = "unknown"


class PreparationUseConditionCategory(StrEnum):
    AS_PACKAGED = "as_packaged"
    PREPARED_AS_DIRECTED = "prepared_as_directed"
    OTHER_REPORTED_CONDITION = "other_reported_condition"
    UNKNOWN = "unknown"


class DeduplicationOutcome(StrEnum):
    NO_REJECTIONS = "no_rejections"
    DUPLICATES_REJECTED = "duplicates_rejected"
    REPLAYS_REJECTED = "replays_rejected"
    DUPLICATES_AND_REPLAYS_REJECTED = "duplicates_and_replays_rejected"


@dataclass(frozen=True, slots=True)
class ObservationDefinition:
    code: str
    severity: ObservationSeverity
    signal_kind: SignalKind
    scope_policy: ScopePolicy
    label_key: str
    requires_photo_for_public_signal: bool = False
    requires_batch_when_available: bool = False
    requires_condition_context: bool = False


# Closed vocabulary: observations are structured facts reported by shoppers,
# never user-authored accusations or conclusions.
OBSERVATIONS: Final[dict[str, ObservationDefinition]] = {
    "barcode_mismatch": ObservationDefinition(
        "barcode_mismatch", ObservationSeverity.LOW, SignalKind.DATA_VERIFICATION, ScopePolicy.PRODUCT_DATA,
        "community.observation.barcode_mismatch",
    ),
    "ingredients_changed": ObservationDefinition(
        "ingredients_changed", ObservationSeverity.LOW, SignalKind.DATA_VERIFICATION, ScopePolicy.PRODUCT_DATA,
        "community.observation.ingredients_changed", requires_photo_for_public_signal=True,
    ),
    "nutrition_changed": ObservationDefinition(
        "nutrition_changed", ObservationSeverity.LOW, SignalKind.DATA_VERIFICATION, ScopePolicy.PRODUCT_DATA,
        "community.observation.nutrition_changed", requires_photo_for_public_signal=True,
    ),
    "pack_size_changed": ObservationDefinition(
        "pack_size_changed", ObservationSeverity.LOW, SignalKind.DATA_VERIFICATION, ScopePolicy.PRODUCT_DATA,
        "community.observation.pack_size_changed", requires_photo_for_public_signal=True,
    ),
    # Date marking concerns a specific physical pack and remains batch-aware.
    "date_marking_unreadable": ObservationDefinition(
        "date_marking_unreadable", ObservationSeverity.MEDIUM, SignalKind.CONSUMER_CONDITION,
        ScopePolicy.BATCH_SENSITIVE, "community.observation.date_marking_unreadable",
        requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "seal_broken": ObservationDefinition(
        "seal_broken", ObservationSeverity.HIGH, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.seal_broken", requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "pack_leaking": ObservationDefinition(
        "pack_leaking", ObservationSeverity.HIGH, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.pack_leaking", requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "pack_swollen": ObservationDefinition(
        "pack_swollen", ObservationSeverity.HIGH, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.pack_swollen", requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "visible_foreign_material": ObservationDefinition(
        "visible_foreign_material", ObservationSeverity.HIGH, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.visible_foreign_material", requires_photo_for_public_signal=True,
        requires_batch_when_available=True,
    ),
    "insect_observed": ObservationDefinition(
        "insect_observed", ObservationSeverity.HIGH, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.insect_observed", requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "unusual_colour": ObservationDefinition(
        "unusual_colour", ObservationSeverity.MEDIUM, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.unusual_colour", requires_batch_when_available=True,
    ),
    "unusual_smell": ObservationDefinition(
        "unusual_smell", ObservationSeverity.MEDIUM, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.unusual_smell", requires_batch_when_available=True,
    ),
    "appeared_spoiled": ObservationDefinition(
        "appeared_spoiled", ObservationSeverity.HIGH, SignalKind.CONSUMER_CONDITION, ScopePolicy.BATCH_SENSITIVE,
        "community.observation.appeared_spoiled", requires_photo_for_public_signal=True,
        requires_batch_when_available=True,
    ),
    "did_not_solidify_as_expected": ObservationDefinition(
        "did_not_solidify_as_expected", ObservationSeverity.MEDIUM, SignalKind.CONSUMER_CONDITION,
        ScopePolicy.BATCH_AND_CONDITION_SENSITIVE, "community.observation.did_not_solidify_as_expected",
        requires_batch_when_available=True, requires_condition_context=True,
    ),
    "did_not_curdle_as_expected": ObservationDefinition(
        "did_not_curdle_as_expected", ObservationSeverity.MEDIUM, SignalKind.CONSUMER_CONDITION,
        ScopePolicy.BATCH_AND_CONDITION_SENSITIVE, "community.observation.did_not_curdle_as_expected",
        requires_batch_when_available=True, requires_condition_context=True,
    ),
}


@dataclass(frozen=True, slots=True)
class DisplayThreshold:
    emerging_independent_reporters: int
    established_independent_reporters: int
    emerging_photo_reporters: int
    established_photo_reporters: int


DISPLAY_THRESHOLDS: Final[dict[ObservationSeverity, DisplayThreshold]] = {
    ObservationSeverity.LOW: DisplayThreshold(5, 15, 1, 3),
    ObservationSeverity.MEDIUM: DisplayThreshold(4, 10, 1, 3),
    ObservationSeverity.HIGH: DisplayThreshold(3, 8, 2, 4),
}


@dataclass(frozen=True, slots=True)
class ConditionContext:
    """Closed, non-sensitive context retained with a condition observation."""

    storage_condition: StorageConditionCategory
    observation_timing: ObservationTimingCategory
    preparation_or_use_condition: PreparationUseConditionCategory


@dataclass(frozen=True, slots=True)
class GeographyEvidenceSummary:
    """Privacy-safe aggregate geography; never a precise location."""

    aggregated_reporters: int
    region_count: int
    dominant_region_reporters: int

    def __post_init__(self) -> None:
        if min(self.aggregated_reporters, self.region_count, self.dominant_region_reporters) < 0:
            raise ValueError("geography evidence values cannot be negative")
        if self.dominant_region_reporters > self.aggregated_reporters:
            raise ValueError("dominant region reporters cannot exceed aggregated reporters")
        if self.region_count > self.aggregated_reporters:
            raise ValueError("region count cannot exceed aggregated reporters")

    @property
    def is_privacy_safe(self) -> bool:
        return self.aggregated_reporters >= MIN_GEOGRAPHY_AGGREGATION_REPORTERS

    @property
    def dominant_region_share(self) -> float | None:
        return self.dominant_region_reporters / self.aggregated_reporters if self.aggregated_reporters else None


@dataclass(frozen=True, slots=True)
class EvidenceWindowSummary:
    """Normalized evidence inside one reporting period."""

    independent_reporters: int = 0
    photo_reporters: int = 0
    reporters_by_batch: Mapping[str, int] = field(default_factory=dict)
    condition_context_reporters: int = 0

    def __post_init__(self) -> None:
        normalized_batches = dict(sorted(self.reporters_by_batch.items()))
        if any(not batch_id or count <= 0 for batch_id, count in normalized_batches.items()):
            raise ValueError("batch reporter counts require non-empty identifiers and positive counts")
        for value in (self.independent_reporters, self.photo_reporters, self.condition_context_reporters):
            if value < 0:
                raise ValueError("community signal counts cannot be negative")
        if self.photo_reporters > self.independent_reporters:
            raise ValueError("photo reporters cannot exceed independent reporters")
        if self.condition_context_reporters > self.independent_reporters:
            raise ValueError("condition-context reporters cannot exceed independent reporters")
        if sum(normalized_batches.values()) > self.independent_reporters:
            raise ValueError("batch reporter counts cannot exceed independent reporters")
        object.__setattr__(self, "reporters_by_batch", MappingProxyType(normalized_batches))

    @property
    def distinct_batches(self) -> int:
        return len(self.reporters_by_batch)

    @property
    def dominant_batch_reporters(self) -> int:
        return max(self.reporters_by_batch.values(), default=0)

    @property
    def dominant_batch_share(self) -> float | None:
        if not self.independent_reporters or not self.reporters_by_batch:
            return None
        return self.dominant_batch_reporters / self.independent_reporters

    @property
    def batch_information_complete(self) -> bool:
        return bool(self.reporters_by_batch) and sum(self.reporters_by_batch.values()) == self.independent_reporters


@dataclass(frozen=True, slots=True)
class SignalEvidenceSummary:
    """Separate active-window evidence from historical evidence.

    Only ``active`` evidence counts toward present maturity and present batch
    scope. ``historical`` remains available for trends, investigations, and a
    later policy version without allowing an old signal to be revived by one
    new report.
    """

    active: EvidenceWindowSummary
    historical: EvidenceWindowSummary = field(default_factory=EvidenceWindowSummary)
    active_window_days: int = ACTIVE_POLICY_WINDOW_DAYS
    duplicate_replay_rejections: int = 0
    deduplication_outcome: DeduplicationOutcome = DeduplicationOutcome.NO_REJECTIONS
    active_geography: GeographyEvidenceSummary | None = None

    def __post_init__(self) -> None:
        if self.active_window_days <= 0:
            raise ValueError("active policy window must be positive")
        if self.duplicate_replay_rejections < 0:
            raise ValueError("duplicate/replay rejections cannot be negative")
        if self.duplicate_replay_rejections and self.deduplication_outcome is DeduplicationOutcome.NO_REJECTIONS:
            raise ValueError("deduplication outcome cannot be empty when reports were rejected")
        if not self.duplicate_replay_rejections and self.deduplication_outcome is not DeduplicationOutcome.NO_REJECTIONS:
            raise ValueError("deduplication outcome requires rejected reports")
        if self.active_geography and self.active_geography.aggregated_reporters > self.active.independent_reporters:
            raise ValueError("active geography cannot exceed active independent reporters")

    @property
    def total_independent_reporters(self) -> int:
        return self.active.independent_reporters + self.historical.independent_reporters

    @property
    def total_photo_reporters(self) -> int:
        return self.active.photo_reporters + self.historical.photo_reporters


COMMUNITY_DISCLOSURE_KEYS: Final[tuple[str, ...]] = (
    "community.disclosure.heading",
    "community.disclosure.reported_by_shoppers",
    "community.disclosure.not_laboratory_testing",
    "community.disclosure.not_official_finding",
)


@dataclass(frozen=True, slots=True)
class SignalDecision:
    policy_version: str
    observation_code: str
    signal_kind: SignalKind
    stage: SignalStage
    scope: SignalScope
    public: bool
    internal_review: bool
    analysis_score_eligible: bool
    official_finding: bool
    evidence_summary: SignalEvidenceSummary
    reason_keys: tuple[str, ...]
    disclosure_keys: tuple[str, ...]


def observation_definition(code: str) -> ObservationDefinition:
    try:
        return OBSERVATIONS[code]
    except KeyError as exc:
        raise ValueError(f"unknown community observation code: {code}") from exc


def _scope_for(definition: ObservationDefinition, evidence: SignalEvidenceSummary, reasons: list[str]) -> SignalScope:
    if definition.scope_policy is ScopePolicy.PRODUCT_DATA:
        reasons.append("community.reason.product_data_verification")
        return SignalScope.PRODUCT

    active = evidence.active
    if not active.batch_information_complete:
        reasons.append("community.reason.active_batch_information_incomplete")
        return SignalScope.INSUFFICIENT_CONTEXT
    if active.distinct_batches == 1:
        reasons.append("community.reason.active_signal_scoped_to_batch")
        return SignalScope.BATCH
    if (active.dominant_batch_share or 0) >= MAX_DOMINANT_BATCH_SHARE_FOR_PRODUCT_SCOPE:
        reasons.append("community.reason.active_batch_pattern_concentrated")
        return SignalScope.BATCH
    reasons.append("community.reason.active_batch_pattern_distributed")
    return SignalScope.PRODUCT


def evaluate_signal(code: str, evidence: SignalEvidenceSummary) -> SignalDecision:
    """Evaluate current presentation maturity without mutating product truth."""

    definition = observation_definition(code)
    threshold = DISPLAY_THRESHOLDS[definition.severity]
    active = evidence.active
    reasons: list[str] = []
    internal_review = definition.severity is ObservationSeverity.HIGH and active.independent_reporters >= 1

    emerging = active.independent_reporters >= threshold.emerging_independent_reporters
    established = active.independent_reporters >= threshold.established_independent_reporters
    if definition.requires_photo_for_public_signal:
        emerging = emerging and active.photo_reporters >= threshold.emerging_photo_reporters
        established = established and active.photo_reporters >= threshold.established_photo_reporters
        if active.photo_reporters < threshold.emerging_photo_reporters:
            reasons.append("community.reason.active_photos_below_public_floor")
    if definition.requires_condition_context:
        emerging = emerging and active.condition_context_reporters >= threshold.emerging_independent_reporters
        established = established and active.condition_context_reporters >= threshold.established_independent_reporters
        if active.condition_context_reporters < threshold.emerging_independent_reporters:
            reasons.append("community.reason.active_condition_context_below_public_floor")
        reasons.append("community.reason.condition_context_required")
    if active.independent_reporters < threshold.emerging_independent_reporters:
        reasons.append("community.reason.active_reports_below_public_floor")

    stage = SignalStage.ESTABLISHED if established else SignalStage.EMERGING if emerging else SignalStage.COLLECTING
    return SignalDecision(
        policy_version=COMMUNITY_SIGNAL_POLICY_VERSION,
        observation_code=code,
        signal_kind=definition.signal_kind,
        stage=stage,
        scope=_scope_for(definition, evidence, reasons),
        public=stage is not SignalStage.COLLECTING,
        internal_review=internal_review,
        analysis_score_eligible=False,
        official_finding=False,
        evidence_summary=evidence,
        reason_keys=tuple(reasons),
        disclosure_keys=COMMUNITY_DISCLOSURE_KEYS,
    )


__all__ = [
    "ACTIVE_POLICY_WINDOW_DAYS",
    "COMMUNITY_DISCLOSURE_KEYS",
    "COMMUNITY_SIGNAL_POLICY_VERSION",
    "DISPLAY_THRESHOLDS",
    "MAX_DOMINANT_BATCH_SHARE_FOR_PRODUCT_SCOPE",
    "MIN_GEOGRAPHY_AGGREGATION_REPORTERS",
    "OBSERVATIONS",
    "ConditionContext",
    "DeduplicationOutcome",
    "DisplayThreshold",
    "EvidenceWindowSummary",
    "GeographyEvidenceSummary",
    "ObservationDefinition",
    "ObservationSeverity",
    "ObservationTimingCategory",
    "PreparationUseConditionCategory",
    "ScopePolicy",
    "SignalDecision",
    "SignalEvidenceSummary",
    "SignalKind",
    "SignalScope",
    "SignalStage",
    "StorageConditionCategory",
    "evaluate_signal",
    "observation_definition",
]
