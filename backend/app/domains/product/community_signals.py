"""Policy for structured, normalized community observations.

Community observations can identify fresh pack changes, batch patterns, and
repeated consumer experiences.  They are not laboratory results, a basis for
medical claims, evidence for the scientific product grade, or official
findings.  This module consumes normalized evidence rather than raw reports so
the persistence layer can deduplicate/reject replay attempts before policy is
evaluated.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

COMMUNITY_SIGNAL_POLICY_VERSION: Final = "community-signals-v2"
MAX_PUBLIC_REPORT_FRESHNESS_DAYS: Final = 90
MAX_DOMINANT_BATCH_SHARE_FOR_PRODUCT_SCOPE: Final = 0.70
MIN_GEOGRAPHY_AGGREGATION_REPORTERS: Final = 5


class ObservationSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalStage(StrEnum):
    """Public maturity of an observation, never official confirmation."""

    COLLECTING = "collecting"
    EMERGING = "emerging"
    ESTABLISHED = "established"


class SignalScope(StrEnum):
    """The narrowest scope supported by normalized evidence."""

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
    label_key: str
    requires_photo_for_public_signal: bool = False
    requires_batch_when_available: bool = False
    requires_condition_context: bool = False


# Closed vocabulary: a shopper records an observation, never a conclusion such
# as "adulterated", "fake", "toxic", or "unsafe".
OBSERVATIONS: Final[dict[str, ObservationDefinition]] = {
    "barcode_mismatch": ObservationDefinition("barcode_mismatch", ObservationSeverity.LOW, "community.barcode_mismatch"),
    "ingredients_changed": ObservationDefinition(
        "ingredients_changed", ObservationSeverity.LOW, "community.ingredients_changed", requires_photo_for_public_signal=True
    ),
    "nutrition_changed": ObservationDefinition(
        "nutrition_changed", ObservationSeverity.LOW, "community.nutrition_changed", requires_photo_for_public_signal=True
    ),
    "pack_size_changed": ObservationDefinition(
        "pack_size_changed", ObservationSeverity.LOW, "community.pack_size_changed", requires_photo_for_public_signal=True
    ),
    "date_marking_unreadable": ObservationDefinition(
        "date_marking_unreadable",
        ObservationSeverity.MEDIUM,
        "community.date_marking_unreadable",
        requires_photo_for_public_signal=True,
        requires_batch_when_available=True,
    ),
    "seal_broken": ObservationDefinition(
        "seal_broken", ObservationSeverity.HIGH, "community.seal_broken", True, True
    ),
    "pack_leaking": ObservationDefinition(
        "pack_leaking", ObservationSeverity.HIGH, "community.pack_leaking", True, True
    ),
    "pack_swollen": ObservationDefinition(
        "pack_swollen", ObservationSeverity.HIGH, "community.pack_swollen", True, True
    ),
    "visible_foreign_material": ObservationDefinition(
        "visible_foreign_material", ObservationSeverity.HIGH, "community.visible_foreign_material", True, True
    ),
    "insect_observed": ObservationDefinition(
        "insect_observed", ObservationSeverity.HIGH, "community.insect_observed", True, True
    ),
    "unusual_colour": ObservationDefinition(
        "unusual_colour", ObservationSeverity.MEDIUM, "community.unusual_colour", requires_batch_when_available=True
    ),
    "unusual_smell": ObservationDefinition(
        "unusual_smell", ObservationSeverity.MEDIUM, "community.unusual_smell", requires_batch_when_available=True
    ),
    "appeared_spoiled": ObservationDefinition(
        "appeared_spoiled", ObservationSeverity.HIGH, "community.appeared_spoiled", True, True
    ),
    "did_not_solidify_as_expected": ObservationDefinition(
        "did_not_solidify_as_expected",
        ObservationSeverity.MEDIUM,
        "community.did_not_solidify_as_expected",
        requires_batch_when_available=True,
        requires_condition_context=True,
    ),
    "did_not_curdle_as_expected": ObservationDefinition(
        "did_not_curdle_as_expected",
        ObservationSeverity.MEDIUM,
        "community.did_not_curdle_as_expected",
        requires_batch_when_available=True,
        requires_condition_context=True,
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
    """Closed, non-sensitive context for condition-based observations."""

    storage_condition: StorageConditionCategory
    observation_timing: ObservationTimingCategory
    preparation_or_use_condition: PreparationUseConditionCategory


@dataclass(frozen=True, slots=True)
class GeographyEvidenceSummary:
    """Only privacy-safe aggregate geography; never a precise location."""

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
        if not self.aggregated_reporters:
            return None
        return self.dominant_region_reporters / self.aggregated_reporters


@dataclass(frozen=True, slots=True)
class SignalEvidenceSummary:
    """De-duplicated evidence used by policy, never raw submissions.

    ``reporters_by_batch`` must account for every independent reporter before
    policy can make a product-wide statement.  Partial or absent batch data is
    retained as an uncertainty state rather than guessed into product scope.
    """

    independent_reporters: int
    photo_reporters: int
    reporters_by_batch: Mapping[str, int] = field(default_factory=dict)
    condition_context_reporters: int = 0
    observation_window_days: int | None = None
    freshest_report_age_days: int | None = None
    duplicate_replay_rejections: int = 0
    deduplication_outcome: DeduplicationOutcome = DeduplicationOutcome.NO_REJECTIONS
    geography: GeographyEvidenceSummary | None = None

    def __post_init__(self) -> None:
        normalized_batches = dict(sorted(self.reporters_by_batch.items()))
        if any(not batch_id or count <= 0 for batch_id, count in normalized_batches.items()):
            raise ValueError("batch reporter counts require non-empty identifiers and positive counts")
        for value in (
            self.independent_reporters,
            self.photo_reporters,
            self.condition_context_reporters,
            self.duplicate_replay_rejections,
        ):
            if value < 0:
                raise ValueError("community signal counts cannot be negative")
        if self.photo_reporters > self.independent_reporters:
            raise ValueError("photo reporters cannot exceed independent reporters")
        if self.condition_context_reporters > self.independent_reporters:
            raise ValueError("condition-context reporters cannot exceed independent reporters")
        if sum(normalized_batches.values()) > self.independent_reporters:
            raise ValueError("batch reporter counts cannot exceed independent reporters")
        if self.observation_window_days is not None and self.observation_window_days < 0:
            raise ValueError("observation window cannot be negative")
        if self.freshest_report_age_days is not None and self.freshest_report_age_days < 0:
            raise ValueError("report freshness cannot be negative")
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

    @property
    def report_freshness(self) -> str:
        if self.freshest_report_age_days is None:
            return "unknown"
        if self.freshest_report_age_days > MAX_PUBLIC_REPORT_FRESHNESS_DAYS:
            return "stale"
        return "fresh"


@dataclass(frozen=True, slots=True)
class SignalDecision:
    policy_version: str
    observation_code: str
    stage: SignalStage
    scope: SignalScope
    public: bool
    internal_review: bool
    analysis_score_eligible: bool
    official_finding: bool
    evidence_summary: SignalEvidenceSummary
    reason_codes: tuple[str, ...]


PUBLIC_LANGUAGE: Final[dict[str, str]] = {
    "heading": "Consumer observations",
    "reporter_attribution": "Reported by shoppers",
    "multiple_reports": "Multiple shoppers reported this observation.",
    "batch_association": "Reports are associated with batch {batch}.",
    "not_laboratory_testing": "These reports are not laboratory testing.",
    "not_official_finding": "Not an official finding.",
}


def observation_definition(code: str) -> ObservationDefinition:
    try:
        return OBSERVATIONS[code]
    except KeyError as exc:
        raise ValueError(f"unknown community observation code: {code}") from exc


def _scope_for(evidence: SignalEvidenceSummary, reasons: list[str]) -> SignalScope:
    if not evidence.batch_information_complete:
        reasons.append("batch_information_incomplete")
        return SignalScope.INSUFFICIENT_CONTEXT
    if evidence.distinct_batches == 1:
        reasons.append("signal_scoped_to_reported_batch")
        return SignalScope.BATCH
    if (evidence.dominant_batch_share or 0) >= MAX_DOMINANT_BATCH_SHARE_FOR_PRODUCT_SCOPE:
        reasons.append("batch_pattern_is_concentrated")
        return SignalScope.BATCH
    reasons.append("batch_pattern_is_distributed")
    return SignalScope.PRODUCT


def evaluate_signal(code: str, evidence: SignalEvidenceSummary) -> SignalDecision:
    """Evaluate display maturity without converting reports into product truth."""

    definition = observation_definition(code)
    threshold = DISPLAY_THRESHOLDS[definition.severity]
    reasons: list[str] = []
    internal_review = definition.severity is ObservationSeverity.HIGH and evidence.independent_reporters >= 1

    emerging = evidence.independent_reporters >= threshold.emerging_independent_reporters
    established = evidence.independent_reporters >= threshold.established_independent_reporters
    if definition.requires_photo_for_public_signal:
        emerging = emerging and evidence.photo_reporters >= threshold.emerging_photo_reporters
        established = established and evidence.photo_reporters >= threshold.established_photo_reporters
        if evidence.photo_reporters < threshold.emerging_photo_reporters:
            reasons.append("supporting_photos_below_public_floor")
    if definition.requires_condition_context:
        emerging = emerging and evidence.condition_context_reporters >= threshold.emerging_independent_reporters
        established = established and evidence.condition_context_reporters >= threshold.established_independent_reporters
        if evidence.condition_context_reporters < threshold.emerging_independent_reporters:
            reasons.append("condition_context_below_public_floor")
        reasons.append("observation_requires_condition_context")
    if evidence.independent_reporters < threshold.emerging_independent_reporters:
        reasons.append("independent_reports_below_public_floor")
    if evidence.report_freshness == "stale":
        emerging = False
        established = False
        reasons.append("reports_are_stale")

    stage = SignalStage.ESTABLISHED if established else SignalStage.EMERGING if emerging else SignalStage.COLLECTING
    scope = _scope_for(evidence, reasons)
    return SignalDecision(
        policy_version=COMMUNITY_SIGNAL_POLICY_VERSION,
        observation_code=code,
        stage=stage,
        scope=scope,
        public=stage is not SignalStage.COLLECTING,
        internal_review=internal_review,
        analysis_score_eligible=False,
        official_finding=False,
        evidence_summary=evidence,
        reason_codes=tuple(reasons),
    )


__all__ = [
    "COMMUNITY_SIGNAL_POLICY_VERSION",
    "DISPLAY_THRESHOLDS",
    "MAX_DOMINANT_BATCH_SHARE_FOR_PRODUCT_SCOPE",
    "MAX_PUBLIC_REPORT_FRESHNESS_DAYS",
    "OBSERVATIONS",
    "PUBLIC_LANGUAGE",
    "ConditionContext",
    "DeduplicationOutcome",
    "DisplayThreshold",
    "GeographyEvidenceSummary",
    "ObservationDefinition",
    "ObservationSeverity",
    "ObservationTimingCategory",
    "PreparationUseConditionCategory",
    "SignalDecision",
    "SignalEvidenceSummary",
    "SignalScope",
    "SignalStage",
    "StorageConditionCategory",
    "evaluate_signal",
    "observation_definition",
]
