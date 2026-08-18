"""Authoritative, backwards-compatible Nutrition preference taxonomy."""
from __future__ import annotations

from typing import Literal

Diet = Literal[
    "vegan",
    "vegetarian",
    "jain",
    "eggetarian",
    "non_vegetarian",
    "pescatarian",
]

SUPPORTED_DIETS: tuple[Diet, ...] = (
    "vegan",
    "vegetarian",
    "jain",
    "eggetarian",
    "non_vegetarian",
    "pescatarian",
)

SUPPORTED_FOCUS_KEYS: tuple[str, ...] = (
    "protein",
    "vitamin_c",
    "vitamin_a",
    "vitamin_e",
    "iron",
    "zinc",
    "copper",
    "omega_3",
    "collagen_support",
    "hydration",
)

NUTRITION_PREFERENCE_TAXONOMY_VERSION = "v3-04.2"

_DIET_LABELS = {
    "vegan": "vegan",
    "vegetarian": "vegetarian",
    "jain": "Jain",
    "eggetarian": "eggetarian",
    "non_vegetarian": "non-vegetarian",
    "pescatarian": "pescatarian",
}


def normalize_diet(value: str | None) -> str:
    """Normalize stored/display input without changing stored values."""
    if value is None:
        return "non_vegetarian"
    normalized = "_".join(str(value).casefold().strip().replace("-", " ").split())
    return normalized if normalized in SUPPORTED_DIETS else normalized


normalise_diet = normalize_diet


def diet_label(value: str | None) -> str:
    return _DIET_LABELS.get(value or "", value or "non-vegetarian")


__all__ = [
    "Diet",
    "NUTRITION_PREFERENCE_TAXONOMY_VERSION",
    "SUPPORTED_DIETS",
    "SUPPORTED_FOCUS_KEYS",
    "diet_label",
    "normalize_diet",
    "normalise_diet",
]
