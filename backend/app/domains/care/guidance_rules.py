"""The three deterministic, advice-only V3-03.17 guidance identities."""
from __future__ import annotations

from dataclasses import dataclass

from app.domains.care.evidence_applicability import CareApplicabilitySignals

CARE_GUIDANCE_RULESET_VERSION = "v3-03.17-r1"


@dataclass(frozen=True, slots=True)
class CareGuidanceRule:
    domain: str
    rule_kind: str
    rule_id: str
    rule_version: str
    priority: int
    title: str
    body: str
    applicability_signals: CareApplicabilitySignals


GUIDANCE_RULES: tuple[CareGuidanceRule, ...] = (
    CareGuidanceRule(
        domain="skin_care",
        rule_kind="routine_guidance",
        rule_id="care.skin.uv_protection_uvi_3",
        rule_version=CARE_GUIDANCE_RULESET_VERSION,
        priority=10,
        title="Sun protection matters today",
        body=(
            "The UV Index is 3 or higher. Prioritise shade and protective clothing, "
            "and use broad-spectrum sunscreen on exposed skin if it is already part "
            "of your routine."
        ),
        applicability_signals=CareApplicabilitySignals(
            jurisdictions=(),
            populations=("general_population",),
            formulations=("sun_protection",),
            usage_contexts=("outdoor_uv_exposure",),
        ),
    ),
    CareGuidanceRule(
        domain="skin_care",
        rule_kind="routine_guidance",
        rule_id="care.skin.dry_air_moisture_support",
        rule_version=CARE_GUIDANCE_RULESET_VERSION,
        priority=20,
        title="Extra moisture support today",
        body=(
            "You’ve said your skin often feels dry or tight, and today’s air is dry. "
            "Apply your planned moisturiser after cleansing while your skin is still "
            "slightly damp."
        ),
        applicability_signals=CareApplicabilitySignals(
            jurisdictions=(),
            populations=("general_population",),
            formulations=("moisturiser",),
            usage_contexts=("after_cleansing",),
        ),
    ),
    CareGuidanceRule(
        domain="hair_care",
        rule_kind="routine_guidance",
        rule_id="care.hair.frequent_heat_styling_protection",
        rule_version=CARE_GUIDANCE_RULESET_VERSION,
        priority=30,
        title="Go gentler with heat",
        body=(
            "You’ve recorded frequent heat styling. When you use heat, keep the "
            "setting low or medium and use your heat-protection step when it is "
            "already part of your routine."
        ),
        applicability_signals=CareApplicabilitySignals(
            jurisdictions=(),
            populations=("general_population",),
            formulations=("heat_protection",),
            usage_contexts=("heat_styling",),
        ),
    ),
)

GUIDANCE_RULE_BY_ID = {rule.rule_id: rule for rule in GUIDANCE_RULES}


__all__ = ["CARE_GUIDANCE_RULESET_VERSION", "CareGuidanceRule", "GUIDANCE_RULES", "GUIDANCE_RULE_BY_ID"]
