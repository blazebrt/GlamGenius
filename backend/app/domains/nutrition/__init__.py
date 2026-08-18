"""Nutrition owns opt-in deterministic guidance and preference-safe food context.

It also owns preference taxonomy, food-composition provenance metadata, and the
composition rights gate. Evidence owns source provenance, claims, and rule
support; Planning owns normalized environment context.

Nutrition does not provide diagnosis, therapeutic diets, deficiency assessment,
IFCT values while rights remain restricted, RDA/EAR/TUL calculations,
supplement prescriptions, AI decisions, shopping, or meal planning.
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
