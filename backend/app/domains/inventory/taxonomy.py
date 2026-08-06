"""Controlled Phase 3 inventory taxonomy and detail fields."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

CATEGORIES: dict[str, str] = {
    "wardrobe": "Wardrobe",
    "shoes": "Shoes",
    "accessories": "Accessories",
    "beauty": "Beauty",
    "hair": "Hair",
    "perfumes": "Perfumes",
    "supplements": "Supplements",
}

DETAIL_FIELDS: dict[str, set[str]] = {
    "wardrobe": {"colour", "pattern", "fabric", "fit", "size", "season", "occasion", "formality", "laundry_state", "care_instructions"},
    "shoes": {"shoe_type", "colour", "size", "heel_height", "comfort", "occasion", "weather_suitability"},
    "accessories": {"accessory_type", "colour", "metal", "material", "style", "occasion"},
    "beauty": {"product_type", "size", "opened_date", "expiry_date", "period_after_opening_months", "purpose", "ingredients_text", "active_ingredients", "use_frequency", "routine_position", "remaining_percent"},
    "hair": {"product_type", "size", "opened_date", "expiry_date", "period_after_opening_months", "purpose", "ingredients_text", "active_ingredients", "use_frequency", "routine_position", "remaining_percent"},
    "perfumes": {"fragrance_family", "concentration", "season", "occasion", "longevity_user_reported", "usage_frequency", "remaining_percent"},
    "supplements": {"supplement_name", "brand", "user_entered_purpose", "expiry_date", "opened_date", "use_frequency", "label_information"},
}

LIST_FIELDS = {"season", "occasion", "weather_suitability", "active_ingredients"}
DATE_FIELDS = {"opened_date", "expiry_date"}
INTEGER_FIELDS = {"period_after_opening_months", "remaining_percent"}
NUMBER_FIELDS = {"heel_height"}
SEARCHABLE_ATTRIBUTE_KEYS = {
    "colour", "ingredients_text", "active_ingredients", "occasion", "season",
    "condition", "brand", "product_type", "fragrance_family", "material", "style",
}

SUPPLEMENT_DISCLAIMER = (
    "Inventory tracking only. GlamGenius does not provide supplement dosage or medical advice. "
    "Follow the product label and guidance from a qualified professional."
)


def validate_details(category: str, details: dict[str, Any]) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError("Choose one of the seven supported inventory categories.")
    unknown = set(details) - DETAIL_FIELDS[category]
    if unknown:
        raise ValueError(f"Unsupported {category} detail: {sorted(unknown)[0]}")
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        if value is None or value == "":
            cleaned[key] = None
        elif key in LIST_FIELDS:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{key} must be a list of text values.")
            cleaned[key] = [v.strip()[:120] for v in value if v.strip()][:30]
        elif key in INTEGER_FIELDS:
            number = int(value)
            if key == "remaining_percent" and not 0 <= number <= 100:
                raise ValueError("remaining_percent must be between 0 and 100.")
            if key == "period_after_opening_months" and not 1 <= number <= 60:
                raise ValueError("period_after_opening_months must be between 1 and 60.")
            cleaned[key] = number
        elif key in NUMBER_FIELDS:
            cleaned[key] = float(value)
        else:
            cleaned[key] = value.strip()[:4000] if isinstance(value, str) else value
    return cleaned


def normalise_text(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def iter_search_values(details: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    for key, value in details.items():
        if key in SEARCHABLE_ATTRIBUTE_KEYS and value not in (None, "", []):
            yield key, value


def validate_attribute_keys(category: str, keys: Iterable[str]) -> None:
    allowed = DETAIL_FIELDS[category] | {"brand", "condition", "subcategory"}
    unknown = set(keys) - allowed
    if unknown:
        raise ValueError(f"Unsupported {category} attribute: {sorted(unknown)[0]}")
