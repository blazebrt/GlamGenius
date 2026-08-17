"""The exact, technique-only V3-03.18 Home Care rule registry.

The review marker and source wording are repository governance metadata; they
are not a professional credential or a claim of clinician review. The exact
source, claim, and rule wording is visible in the PR, and merging is the
repository approval boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domains.care.evidence_applicability import CareApplicabilitySignals
from app.domains.care.guidance_rules import GUIDANCE_RULE_BY_ID

HOME_CARE_VERSION = "v3-03.18"
HOME_CARE_RULESET_VERSION = "v3-03.18-r1"


@dataclass(frozen=True, slots=True)
class HomeCareRule:
    domain: str
    rule_kind: str
    rule_id: str
    rule_version: str
    priority: int
    title: str
    body: str
    applicability_signals: CareApplicabilitySignals


HOME_CARE_RULES: tuple[HomeCareRule, ...] = (
    HomeCareRule(
        domain="home_care", rule_kind="routine_guidance",
        rule_id="care.home.skin_gentle_bathing", rule_version=HOME_CARE_RULESET_VERSION,
        priority=10, title="Keep showers gentle",
        body=(
            "You’ve said your skin often feels dry or tight, and today’s air is dry. "
            "Keep baths or showers to about 5–10 minutes with warm—not hot—water, "
            "then gently pat your skin dry."
        ),
        applicability_signals=CareApplicabilitySignals(
            jurisdictions=(), populations=("general_population",),
            formulations=("non_product_home_care",), usage_contexts=("dry_skin_bathing",),
        ),
    ),
    HomeCareRule(
        domain="home_care", rule_kind="routine_guidance",
        rule_id="care.home.hair_gentle_drying", rule_version=HOME_CARE_RULESET_VERSION,
        priority=20, title="Dry hair gently after washing",
        body=(
            "Your wash routine is due today. After washing, gently wrap your hair in "
            "a towel or T-shirt to absorb moisture, or let it air-dry when practical. "
            "Avoid rough rubbing."
        ),
        applicability_signals=CareApplicabilitySignals(
            jurisdictions=(), populations=("general_population",),
            formulations=("non_product_home_care",), usage_contexts=("post_wash_hair_drying",),
        ),
    ),
)

HOME_CARE_RULE_BY_ID = {rule.rule_id: rule for rule in HOME_CARE_RULES}

assert len(HOME_CARE_RULES) == 2
assert not {rule.rule_id for rule in HOME_CARE_RULES}.intersection(GUIDANCE_RULE_BY_ID)

__all__ = [
    "HOME_CARE_VERSION", "HOME_CARE_RULESET_VERSION", "HomeCareRule",
    "HOME_CARE_RULES", "HOME_CARE_RULE_BY_ID",
]
