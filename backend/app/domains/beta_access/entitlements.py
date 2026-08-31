"""Private-beta access contract.

Invitees receive the complete beta surface without an upgrade prompt; cost
limits remain abuse controls, not commercial-access gates.
"""
from __future__ import annotations

BETA_ENTITLEMENTS: tuple[dict[str, str | bool], ...] = (
    {"key": "core_appearance", "label": "Style, Care and planning", "available": True},
    {"key": "product_verdicts", "label": "Product verdicts and shared shelf", "available": True},
    {"key": "personal_modes", "label": "Personal modes", "available": True},
    {"key": "family_circle", "label": "Family Circle", "available": True},
    {"key": "history_and_manager", "label": "History and daily manager", "available": True},
)


def beta_entitlement_matrix() -> dict[str, object]:
    """Return the transparent current beta access matrix without pricing copy."""
    return {
        "access_model": "invite_beta",
        "commercial_access_active": False,
        "items": [dict(item) for item in BETA_ENTITLEMENTS],
        "note": "All listed beta features are available to invited accounts.",
    }
