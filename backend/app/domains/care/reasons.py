"""Stable source and missing-information vocabulary for Care foundations."""
from __future__ import annotations

from enum import StrEnum


class CareFactSource(StrEnum):
    CARE_USER_DECLARED = "care_user_declared"
    LEGACY_PROFILE_CONFIRMED = "legacy_profile_confirmed"
    INVENTORY_CONFIRMED = "inventory_confirmed"
    INGREDIENT_CONFIRMED = "ingredient_confirmed"
    CONTEXT_OBSERVED = "context_observed"
    CONTEXT_NORMALIZED = "context_normalized"
    EVENT_CONFIRMED = "event_confirmed"
    EVENT_INFERRED = "event_inferred"
    MISSING = "missing"


class CareMissingReason(StrEnum):
    MISSING = "missing"
    UNTRUSTED = "untrusted"
    UNKNOWN_VALUE = "unknown_value"
    MISSING_SKIN_CONTEXT = "missing_skin_context"
    MISSING_HAIR_CONTEXT = "missing_hair_context"
    ENVIRONMENT_MISSING = "environment_missing"
    ENVIRONMENT_AVAILABLE = "environment_available"
    EVENT_AVAILABLE = "event_available"
    EVENT_INFERRED = "event_inferred"


__all__ = ["CareFactSource", "CareMissingReason"]
