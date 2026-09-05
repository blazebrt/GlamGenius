"""Controlled Step 8A personal-context vocabulary."""

from enum import StrEnum


class PersonalLensCategory(StrEnum):
    """The explicit product category supplied by the future caller."""

    PACKAGED_FOOD = "packaged_food"
    SKIN_CARE = "skin_care"
    HAIR_CARE = "hair_care"
    COSMETICS = "cosmetics"


class PersonalLensStatus(StrEnum):
    """Readiness of trusted body context, never a product decision."""

    CONTEXT_AVAILABLE = "context_available"
    PARTIAL_CONTEXT = "partial_context"
    NOT_ENOUGH_PERSONAL_CONTEXT = "not_enough_personal_context"
    HANDOFF_REQUIRED = "handoff_required"


class PersonalFactKind(StrEnum):
    """Keep preferences structurally separate from body facts."""

    BODY = "body"
    PREFERENCE = "preference"


class PersonalFactMissingReason(StrEnum):
    """Closed reasons why an allowlisted fact is not usable."""

    MISSING = "missing"
    UNTRUSTED_SOURCE = "untrusted_source"
    NOT_CONFIRMED = "not_confirmed"
    EXPLICIT_UNKNOWN = "explicit_unknown"
