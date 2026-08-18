"""Deterministic, evidence-gated V3-04.2 guidance."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.service import assess_rule_evidence
from app.domains.nutrition.evidence_applicability import resolve_nutrition_evidence_applicability
from app.domains.nutrition.food_options import (
    NUTRITION_FOOD_OPTIONS_VERSION,
    NutritionFoodOption,
    options_for_rule,
)
from app.domains.nutrition.guidance_rules import (
    NUTRITION_GUIDANCE_RULES,
    NUTRITION_GUIDANCE_RULESET_VERSION,
)

NUTRITION_GUIDANCE_VERSION = "v3-04.2"


@dataclass(frozen=True, slots=True)
class NutritionGuidanceItem:
    rule_id: str
    rule_version: str
    priority: int
    title: str
    body: str
    trigger_codes: tuple[str, ...]
    evidence_claim_ids: tuple[uuid.UUID, ...]
    evidence_applicability_version: str
    food_options: tuple[NutritionFoodOption, ...] = ()


@dataclass(frozen=True, slots=True)
class NutritionGuidanceSet:
    guidance_version: str
    ruleset_version: str
    items: tuple[NutritionGuidanceItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(sorted(self.items, key=lambda row: (row.priority, row.rule_id))))

    @property
    def fingerprint(self) -> str:
        return nutrition_guidance_fingerprint(self)


def nutrition_guidance_fingerprint(guidance: NutritionGuidanceSet) -> str:
    material = {"guidance_version": guidance.guidance_version, "ruleset_version": guidance.ruleset_version, "food_options_version": NUTRITION_FOOD_OPTIONS_VERSION, "items": [
        {"rule_id": i.rule_id, "rule_version": i.rule_version, "priority": i.priority, "title": i.title, "body": i.body, "trigger_codes": list(i.trigger_codes), "evidence_claim_ids": sorted(map(str, i.evidence_claim_ids)), "evidence_applicability_version": i.evidence_applicability_version, "food_option_ids": [option.option_id for option in i.food_options]} for i in guidance.items
    ]}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


async def build_nutrition_guidance(session: AsyncSession, *, nutrition_enabled: bool, protein_focus: bool, hydration_enabled: bool, hot_weather: bool, hot_weather_only: bool, diet: str | None = None, avoid_foods: tuple[str, ...] | list[str] = ()) -> NutritionGuidanceSet:
    if not nutrition_enabled:
        return NutritionGuidanceSet(NUTRITION_GUIDANCE_VERSION, NUTRITION_GUIDANCE_RULESET_VERSION)
    items: list[NutritionGuidanceItem] = []
    for rule in NUTRITION_GUIDANCE_RULES:
        if rule.rule_id.endswith("balanced_variety"):
            triggered, codes = True, ("nutrition_enabled", "balanced_variety_context")
        elif rule.rule_id.endswith("protein_food_first"):
            triggered, codes = protein_focus, ("nutrition_enabled", "explicit_protein_focus")
        else:
            triggered = hydration_enabled and (not hot_weather_only or hot_weather)
            codes = ("nutrition_enabled", "explicit_hydration_opt_in", "hot_weather_hydration_context") if hot_weather_only else ("nutrition_enabled", "explicit_hydration_opt_in")
        if not triggered:
            continue
        assessment = await assess_rule_evidence(session, domain=rule.domain, rule_kind=rule.rule_kind, rule_id=rule.rule_id, rule_version=rule.rule_version)
        applicability = resolve_nutrition_evidence_applicability(assessment, rule.applicability_signals)
        if applicability.applicable:
            items.append(NutritionGuidanceItem(rule.rule_id, rule.rule_version, rule.priority, rule.title, rule.body, codes, applicability.matching_claim_ids, applicability.applicability_version, options_for_rule(rule.rule_id, diet=diet, avoid_foods=avoid_foods)))
    return NutritionGuidanceSet(NUTRITION_GUIDANCE_VERSION, NUTRITION_GUIDANCE_RULESET_VERSION, tuple(items[:3]))


def public_nutrition_guidance(guidance: NutritionGuidanceSet) -> dict:
    return {"guidance_version": guidance.guidance_version, "ruleset_version": guidance.ruleset_version, "fingerprint": guidance.fingerprint, "suggestions": [{"rule_id": i.rule_id, "rule_version": i.rule_version, "title": i.title, "body": i.body, "trigger_codes": list(i.trigger_codes), "food_options": [option.label for option in i.food_options]} for i in guidance.items]}


__all__ = ["NUTRITION_FOOD_OPTIONS_VERSION", "NUTRITION_GUIDANCE_VERSION", "NutritionGuidanceItem", "NutritionGuidanceSet", "build_nutrition_guidance", "nutrition_guidance_fingerprint", "public_nutrition_guidance"]
