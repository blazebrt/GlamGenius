"""The closed V3-05 purchase strategy and product-quality contract.

This is policy, not a second purchase engine.  Existing Style ROI and purchase
persistence remain authoritative for the one active strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

PURCHASE_INTELLIGENCE_FOUNDATION_VERSION = "v3-05.0"
PURCHASE_STRATEGY_REGISTRY_VERSION = "v3-05.0"
PRODUCT_QUALITY_CONTRACT_VERSION = "v3-05.0"
PURCHASE_CANDIDATE_TRUTH_VERSION = "v3-05.1"
CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION = "v3-05.1"
CARE_PURCHASE_ASSESSMENT_VERSION = "v3-05.2"
CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION = "v3-05.2"

STYLE_PURCHASE_CATEGORIES = ("wardrobe", "shoes", "accessories")
CARE_PURCHASE_CATEGORIES = ("beauty", "hair")
FRAGRANCE_PURCHASE_CATEGORIES = ("perfumes",)
PURCHASE_PROHIBITED_CATEGORIES = ("supplements",)

PURCHASE_CATEGORY_LABELS = MappingProxyType({
    "wardrobe": "Wardrobe",
    "shoes": "Shoes",
    "accessories": "Accessories",
    "beauty": "Skin Care",
    "hair": "Hair Care",
    "perfumes": "Perfumes",
    "supplements": "Supplements",
})

PRODUCT_QUALITY_DIMENSIONS = (
    "identity_confidence",
    "role_utility",
    "redundancy",
    "compatibility",
    "evidence_support",
    "value_context",
)


@dataclass(frozen=True, slots=True)
class PurchaseStrategy:
    key: str
    categories: tuple[str, ...]
    state: Literal["active", "inactive", "prohibited"]
    label: str


PURCHASE_STRATEGY_REGISTRY = (
    PurchaseStrategy("style_purchase", STYLE_PURCHASE_CATEGORIES, "active", "Style purchase"),
    PurchaseStrategy("care_purchase", CARE_PURCHASE_CATEGORIES, "inactive", "Care purchase"),
    PurchaseStrategy("fragrance_purchase", FRAGRANCE_PURCHASE_CATEGORIES, "inactive", "Fragrance purchase"),
    PurchaseStrategy("supplement_purchase", PURCHASE_PROHIBITED_CATEGORIES, "prohibited", "Supplement purchase"),
)

_STRATEGY_BY_CATEGORY = MappingProxyType({
    category: strategy
    for strategy in PURCHASE_STRATEGY_REGISTRY
    for category in strategy.categories
})


class PurchaseStrategyBoundaryError(ValueError):
    """Raised when a strategy cannot safely evaluate a purchase candidate."""

    def __init__(self, category: str, strategy: PurchaseStrategy | None = None) -> None:
        self.category = category
        self.strategy = strategy
        super().__init__(boundary_message(category, strategy))


def resolve_purchase_strategy(category: str) -> PurchaseStrategy | None:
    """Resolve an exact internal category; unknown values fail closed."""
    if not isinstance(category, str):
        return None
    return _STRATEGY_BY_CATEGORY.get(category)


def strategy_for_category(category: str) -> PurchaseStrategy | None:
    """Readable alias used by callers that describe the mapping operation."""
    return resolve_purchase_strategy(category)


def customer_category_label(category: str) -> str | None:
    return PURCHASE_CATEGORY_LABELS.get(category)


def boundary_message(category: str, strategy: PurchaseStrategy | None = None) -> str:
    strategy = strategy or resolve_purchase_strategy(category)
    if strategy is not None and strategy.key == "supplement_purchase":
        return (
            "GlamGenius does not recommend whether to buy supplements. It can track "
            "supplements you already use, their label information and expiry, within "
            "the existing non-medical boundary."
        )
    if strategy is not None and strategy.key == "fragrance_purchase":
        return "Perfume requires fragrance-specific overlap and use context, so the Style purchase model will not be used."
    if strategy is not None and strategy.key == "care_purchase":
        return (
            "This purchase check currently uses style-specific rules for wardrobe, shoes and accessories. "
            "Skin Care and Hair Care require product-specific ingredient, routine and redundancy checks, "
            "so GlamGenius will not judge them using wardrobe rules."
        )
    return "This purchase category is not supported by a trusted purchase strategy."


def is_active_style_category(category: str) -> bool:
    strategy = resolve_purchase_strategy(category)
    return strategy is not None and strategy.key == "style_purchase" and strategy.state == "active"


# Public immutable aliases make the registry auditable without exposing its
# internal category-index construction.
STRATEGY_REGISTRY = PURCHASE_STRATEGY_REGISTRY
STRATEGIES_BY_CATEGORY = _STRATEGY_BY_CATEGORY
