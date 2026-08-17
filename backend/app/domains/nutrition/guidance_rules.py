"""Immutable V3-04.1 food-pattern guidance rules."""
from __future__ import annotations

from dataclasses import dataclass

from app.domains.nutrition.evidence_applicability import NutritionApplicabilitySignals

NUTRITION_GUIDANCE_RULESET_VERSION = "v3-04.1-r1"


@dataclass(frozen=True, slots=True)
class NutritionGuidanceRule:
    domain: str
    rule_kind: str
    rule_id: str
    rule_version: str
    priority: int
    title: str
    body: str
    applicability_signals: NutritionApplicabilitySignals


NUTRITION_GUIDANCE_RULES = (
    NutritionGuidanceRule(
        "nutrition", "nutrition_context", "nutrition.pattern.balanced_variety", "v1", 10,
        "Build variety into your meals",
        "A balanced food pattern comes from variety, not one “perfect” food. Keep the mix flexible around the foods and traditions that fit you.",
        NutritionApplicabilitySignals(("india",), ("general_population",), ("food_pattern_guidance",), ("nutrition_enabled",)),
    ),
    NutritionGuidanceRule(
        "nutrition", "nutrition_context", "nutrition.pattern.protein_food_first", "v1", 20,
        "Keep protein food-first",
        "If protein is something you want to pay attention to, start with ordinary foods that fit your diet rather than treating protein supplements as the default.",
        NutritionApplicabilitySignals(("india",), ("general_population",), ("food_first_protein",), ("explicit_protein_focus",)),
    ),
    NutritionGuidanceRule(
        "nutrition", "nutrition_context", "nutrition.pattern.hydration_context", "v1", 30,
        "Keep water in the day",
        "If you want hydration reminders, keep water part of the day. GlamGenius does not set a litre target or treat hydration as a diagnosis.",
        NutritionApplicabilitySignals(("india",), ("general_population",), ("hydration_guidance",), ("explicit_hydration_opt_in",)),
    ),
)
NUTRITION_GUIDANCE_RULE_BY_ID = {row.rule_id: row for row in NUTRITION_GUIDANCE_RULES}

__all__ = ["NUTRITION_GUIDANCE_RULESET_VERSION", "NutritionGuidanceRule", "NUTRITION_GUIDANCE_RULES", "NUTRITION_GUIDANCE_RULE_BY_ID"]
