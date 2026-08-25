"""The code-owned catalogue of Skin and Hair maintenance kinds (VC-06).

Maintenance is about *timing*: when an upkeep act is next worth doing. It
never selects a product — ``CareRoutinePlan`` is the only product authority —
and it never books, recommends or prices a service. The catalogue lives in
code so the engine and the seed cannot drift apart.

Every default interval here is an ordinary upkeep rhythm, not a rule about how
anyone should look. A kind is only ever considered once the customer has
explicitly chosen to track it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAINTENANCE_VERSION = "vc-06.0"
MAINTENANCE_CATALOGUE_VERSION = "vc-06.0-r1"

#: Hard bounds on a customer-declared interval. Wide enough to hold any real
#: rhythm, narrow enough that an obvious typo cannot become a silent schedule.
MIN_INTERVAL_DAYS = 3
MAX_INTERVAL_DAYS = 365


class MaintenanceDomain(StrEnum):
    """Which Care surface a kind belongs to. Customer-facing wording only."""

    HAIR = "hair_care"
    SKIN = "skin_care"


@dataclass(frozen=True, slots=True)
class MaintenanceKind:
    key: str
    label: str
    domain: MaintenanceDomain
    default_interval_days: int
    #: What the act is, in the customer's words. Never a reason they "should".
    description: str

    @property
    def lead_days(self) -> int:
        """How far ahead this kind starts reading as coming up.

        A quarter of the interval, capped at a week, so a short rhythm gets a
        short heads-up and a long one does not sit in "coming up" for a month.
        """
        return max(1, min(7, self.default_interval_days // 4))


MAINTENANCE_KINDS: tuple[MaintenanceKind, ...] = (
    MaintenanceKind(
        key="haircut", label="Haircut", domain=MaintenanceDomain.HAIR,
        default_interval_days=42,
        description="Keeping your usual cut in the shape you like.",
    ),
    MaintenanceKind(
        key="hair_trim", label="Hair trim", domain=MaintenanceDomain.HAIR,
        default_interval_days=84,
        description="Tidying the ends while keeping your length.",
    ),
    MaintenanceKind(
        key="hair_colour_upkeep", label="Hair colour upkeep", domain=MaintenanceDomain.HAIR,
        default_interval_days=35,
        description="Refreshing colour you already wear, including roots.",
    ),
    MaintenanceKind(
        key="beard_upkeep", label="Beard upkeep", domain=MaintenanceDomain.HAIR,
        default_interval_days=14,
        description="Keeping facial hair at the length and shape you prefer.",
    ),
    MaintenanceKind(
        key="brow_upkeep", label="Brow upkeep", domain=MaintenanceDomain.SKIN,
        default_interval_days=28,
        description="Keeping brows in the shape you already wear.",
    ),
    MaintenanceKind(
        key="nail_care", label="Nail care", domain=MaintenanceDomain.SKIN,
        default_interval_days=21,
        description="Routine upkeep for nails on hands and feet.",
    ),
    MaintenanceKind(
        key="body_hair_upkeep", label="Body hair upkeep", domain=MaintenanceDomain.SKIN,
        default_interval_days=28,
        description="Whatever body hair routine you already follow, on your own schedule.",
    ),
)

MAINTENANCE_KIND_BY_KEY: dict[str, MaintenanceKind] = {kind.key: kind for kind in MAINTENANCE_KINDS}

MAINTENANCE_KIND_KEYS: tuple[str, ...] = tuple(kind.key for kind in MAINTENANCE_KINDS)


def get_kind(key: str) -> MaintenanceKind | None:
    return MAINTENANCE_KIND_BY_KEY.get(key)


__all__ = [
    "MAINTENANCE_CATALOGUE_VERSION",
    "MAINTENANCE_KINDS",
    "MAINTENANCE_KIND_BY_KEY",
    "MAINTENANCE_KIND_KEYS",
    "MAINTENANCE_VERSION",
    "MAX_INTERVAL_DAYS",
    "MIN_INTERVAL_DAYS",
    "MaintenanceDomain",
    "MaintenanceKind",
    "get_kind",
]
