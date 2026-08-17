"""Nutrition authority and food-composition provenance foundation.

This domain owns global reference metadata and the future food-composition
storage contract. It deliberately does not own user recommendations.
"""

from app.domains.nutrition.models import FoodCompositionDataset, FoodNutrientValue, FoodReferenceItem

NUTRITION_REFERENCE_FOUNDATION_VERSION = "v3-04.0"
FOOD_COMPOSITION_SCHEMA_VERSION = "v3-04.0"
NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION = "2026.08.17-v3-04.0-authority-1"
FOOD_COMPOSITION_METADATA_SEED_VERSION = "2026.08.17-v3-04.0-composition-1"

__all__ = [
    "FoodCompositionDataset", "FoodReferenceItem", "FoodNutrientValue",
    "NUTRITION_REFERENCE_FOUNDATION_VERSION", "FOOD_COMPOSITION_SCHEMA_VERSION",
    "NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION", "FOOD_COMPOSITION_METADATA_SEED_VERSION",
]
