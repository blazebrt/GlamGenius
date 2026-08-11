"""Pure, explicit Care product preference contracts for V3-03.11."""
from __future__ import annotations

CARE_PRODUCT_PREFERENCE_VERSION = "v3-03.11"
CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY = "care_routine_paused"


def is_effective_user_pause(*, value: object, source: str, verification_state: str) -> bool:
    """Return whether an attribute is an authoritative explicit pause.

    The strict type check is intentional: JSON ``1`` is not a user-declared
    boolean preference, even though Python considers it equal to ``True``.
    """
    return (
        isinstance(value, bool)
        and value is True
        and source == "user_declared"
        and verification_state == "confirmed"
    )


__all__ = [
    "CARE_PRODUCT_PREFERENCE_VERSION",
    "CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY",
    "is_effective_user_pause",
]
