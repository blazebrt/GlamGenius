"""The ten environment rules, and the order in which they win.

Air quality, humidity, UV and temperature have been fetched, stored and
required for a while without changing anything. These are the rules that make
them act.

They follow the Care guidance pattern exactly — a frozen dataclass per rule
with a stable ``rule_id``, a versioned ruleset, evidence applicability signals,
and a reviewed source behind each one through the evidence domain. What they
add is what a guidance list cannot express: **precedence**. Several of these
fire on the same winter morning in Delhi, and a person who is told four
environmental things in one day is being nagged, not helped. So the evaluator
returns exactly one primary decision and at most one supporting note, and
``precedence`` below is the total order that decides which.

Two of the ten give something back rather than taking it away (``env.air.
resume_available`` and ``env.rain.recovery_window``). Those are not optional
niceties. A manager that only ever restricts is one a person turns off.

Every string here is written under LEGAL_RULES.md: state the reading and the
published category, never a health outcome, never a diagnosis, and never a
claim that a product prevents anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domains.care.evidence_applicability import CareApplicabilitySignals

CARE_ENVIRONMENT_RULESET_VERSION = "v3-06-r1"

#: The evidence rule_kind these rules resolve under.
ENVIRONMENT_RULE_KIND = "environment_response"


class EnvironmentAction(StrEnum):
    """What a rule does to the day, in one word.

    ``RESTORE`` exists so the engine can tell "we took something away" from
    "you can have it back" without parsing the text.
    """

    DEFER_ACTIVE = "defer_active"
    ADD_STEP = "add_step"
    HOLD_CADENCE = "hold_cadence"
    ADD_SUPPORT = "add_support"
    RESTORE = "restore"
    PROTECT = "protect"


@dataclass(frozen=True, slots=True)
class CareEnvironmentRule:
    domain: str
    rule_kind: str
    rule_id: str
    rule_version: str
    #: Lower wins. This is a total order over all ten; no two rules share a
    #: value, so "several fired, which one is shown" never depends on
    #: dictionary order or on which check ran first.
    precedence: int
    action: EnvironmentAction
    #: What the person is told, in one line.
    headline: str
    #: The reason, stated beneath the decision. Reading and published
    #: category only — never a claim about their body.
    reason_template: str
    #: The one extra line this rule contributes when it is the supporting
    #: note rather than the primary decision. Kept short on purpose.
    note: str
    applicability_signals: CareApplicabilitySignals


def _signals(population: str, formulation: str, usage: str) -> CareApplicabilitySignals:
    return CareApplicabilitySignals(
        jurisdictions=(),
        populations=(population,),
        formulations=(formulation,),
        usage_contexts=(usage,),
    )


ENVIRONMENT_RULES: tuple[CareEnvironmentRule, ...] = (
    # --- Compounded stress outranks its own parts ---------------------------
    # Rule 8. Stated first because it wins first: when dry air and Poor-or-worse
    # air both apply, saying "lighter formulations" (rule 7) or only "defer
    # actives" (rule 1) would be answering half the day.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.dry_air_and_poor_naqi",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=10,
        action=EnvironmentAction.DEFER_ACTIVE,
        headline="Barrier support today, and strong actives are deferred.",
        reason_template=(
            "Humidity is {humidity}% and air is {category_lower} "
            "(NAQI {aqi}, CPCB category {category}). Dry air and this air-quality "
            "band are both barrier stressors, so today is a barrier-support day."
        ),
        note="Dry air and poor air together — barrier support first.",
        applicability_signals=_signals("general_population", "barrier_support", "low_humidity_high_pollution"),
    ),
    # --- Air quality --------------------------------------------------------
    # Rule 2.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.very_poor_post_exposure_cleanse",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=20,
        action=EnvironmentAction.ADD_STEP,
        headline="Add a cleanse when you get home today.",
        reason_template=(
            "Air is {category_lower} (NAQI {aqi}, CPCB category {category}). "
            "Cleansing after you have been outside removes what settled on your "
            "skin while you were out."
        ),
        note="Worth a cleanse when you get home.",
        applicability_signals=_signals("general_population", "cleanser", "post_outdoor_exposure"),
    ),
    # Rule 1.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.poor_defer_strong_actives",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=30,
        action=EnvironmentAction.DEFER_ACTIVE,
        headline="{deferred_label} are deferred to the next clean day.",
        reason_template=(
            "Air is {category_lower} (NAQI {aqi}, CPCB category {category}). "
            "Exfoliation and retinoids are held until the air is Satisfactory or "
            "better; barrier support takes their place today."
        ),
        note="{deferred_label} stay deferred while the air is {category_lower}.",
        applicability_signals=_signals("general_population", "exfoliant_or_retinoid", "high_pollution_day"),
    ),
    # Rule 9. Sun protection is mandatory guidance, not an optional extra, so it
    # sits above the slower-moving rules below it.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.high_uv_photosensitivity",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=40,
        action=EnvironmentAction.PROTECT,
        headline="Move {deferred_label_lower} to the evening and cover up in the sun.",
        reason_template=(
            "The UV Index is {uv_index} today. Ingredients that increase "
            "photosensitivity belong in the PM routine on a day like this. Shade, "
            "covering clothing and broad-spectrum sunscreen on exposed skin are "
            "part of the plan, not an optional extra."
        ),
        note="UV Index {uv_index} — sun protection is part of today's plan.",
        applicability_signals=_signals("general_population", "sun_protection", "outdoor_uv_exposure"),
    ),
    # Rule 3. Hair. Deliberately a cadence *hold*: the intuitive response to
    # dirty air is to wash more, and that is the wrong one.
    CareEnvironmentRule(
        domain="hair_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.very_poor_hair_hold_cadence",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=50,
        action=EnvironmentAction.HOLD_CADENCE,
        headline="Keep your wash days as they are — change the technique, not the frequency.",
        reason_template=(
            "Air is {category_lower} (NAQI {aqi}, CPCB category {category}). "
            "Your wash schedule stays where it is. On the days you do wash, rinse "
            "thoroughly before shampoo and keep lengths tied back outdoors."
        ),
        note="Wash days stay as they are; technique, not frequency.",
        applicability_signals=_signals("general_population", "shampoo", "high_pollution_day"),
    ),
    # Rule 7.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.humid_heat_occlusion",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=60,
        action=EnvironmentAction.ADD_SUPPORT,
        headline="Go lighter today, and keep wash days as they are.",
        reason_template=(
            "It is {temp_max_c}°C with {humidity}% humidity. Heavy layers sit on the "
            "skin in this combination, so use the lightest formulation you own for "
            "each step. Wash frequency stays where it is; keep friction areas — "
            "collar, straps, waistband — dry and loose where you can."
        ),
        note="Lighter formulations while it stays this warm and humid.",
        applicability_signals=_signals("general_population", "light_formulation", "hot_humid_day"),
    ),
    # Rule 4. Sustained exposure, not one bad day.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.sustained_poor_antioxidant_am",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=70,
        action=EnvironmentAction.ADD_SUPPORT,
        headline="Add your antioxidant serum to the morning while this stretch lasts.",
        reason_template=(
            "Air has been Poor or worse for {streak_days} days running "
            "(today NAQI {aqi}, CPCB category {category}). If you own an "
            "antioxidant serum, the morning is where it goes during a stretch "
            "like this."
        ),
        note="{streak_days} days of Poor-or-worse air — antioxidant support in the AM.",
        applicability_signals=_signals("general_population", "antioxidant_serum", "sustained_high_pollution"),
    ),
    # --- Giving things back -------------------------------------------------
    # Rule 5. The gate, and it is a separate rule from the good news because it
    # fires on a different day: one clean day after a poor stretch is not yet a
    # resumption, and saying so is what makes rule 6 mean something.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.resume_needs_two_clean_days",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=80,
        action=EnvironmentAction.DEFER_ACTIVE,
        headline="{deferred_label} stay deferred for one more clean day.",
        reason_template=(
            "Air is {category_lower} today (NAQI {aqi}, CPCB category {category}), "
            "the first clean day after {streak_days} days at Poor or worse. "
            "Deferred actives resume after two clean days in a row."
        ),
        note="One more clean day before {deferred_label_lower} resume.",
        applicability_signals=_signals("general_population", "exfoliant_or_retinoid", "air_quality_recovery"),
    ),
    # Rule 6.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.air_cleared_resume_actives",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=90,
        action=EnvironmentAction.RESTORE,
        headline="{deferred_label} are available again.",
        reason_template=(
            "Air has been Satisfactory or better for {clean_days} days running "
            "(today NAQI {aqi}, CPCB category {category}). What was deferred "
            "during the poor stretch is back on the table."
        ),
        note="{deferred_label} are available again.",
        applicability_signals=_signals("general_population", "exfoliant_or_retinoid", "air_quality_recovery"),
    ),
    # Rule 10.
    CareEnvironmentRule(
        domain="skin_care",
        rule_kind=ENVIRONMENT_RULE_KIND,
        rule_id="care.env.rain_recovery_window",
        rule_version=CARE_ENVIRONMENT_RULESET_VERSION,
        precedence=100,
        action=EnvironmentAction.RESTORE,
        headline="{deferred_label} are available again — rain has cleared the air.",
        reason_template=(
            "Rain today follows {streak_days} days at Poor or worse "
            "(today NAQI {aqi}, CPCB category {category}). This is the window for "
            "what you deferred during the stretch."
        ),
        note="Rain after a poor stretch — a window for what you deferred.",
        applicability_signals=_signals("general_population", "exfoliant_or_retinoid", "post_rainfall_recovery"),
    ),
)

ENVIRONMENT_RULE_BY_ID = {rule.rule_id: rule for rule in ENVIRONMENT_RULES}

#: The explicit total order, worst-first. Read this to answer "which one wins".
PRECEDENCE_ORDER: tuple[str, ...] = tuple(
    rule.rule_id for rule in sorted(ENVIRONMENT_RULES, key=lambda rule: rule.precedence)
)


def _assert_total_order() -> None:
    values = [rule.precedence for rule in ENVIRONMENT_RULES]
    if len(set(values)) != len(values):
        raise ValueError("environment rule precedence must be a total order")


_assert_total_order()


__all__ = [
    "CARE_ENVIRONMENT_RULESET_VERSION",
    "ENVIRONMENT_RULES",
    "ENVIRONMENT_RULE_BY_ID",
    "ENVIRONMENT_RULE_KIND",
    "PRECEDENCE_ORDER",
    "CareEnvironmentRule",
    "EnvironmentAction",
]
