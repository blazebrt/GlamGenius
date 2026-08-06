"""Structured safety categories (Fix 14, WP3).

The previous safety layer was a single banned-word sweep in
``safety.narrative_is_safe``. That layer stays — banned words are cheap, fast,
and catch obvious mistakes — but as the primary defence a banned-word list is
brittle: it never predicts every synonym for "diagnosis", and every miss
becomes a policy incident.

Fix 14 adds a typed, deterministic classifier that answers "does this text
belong in one of the categories this product is not allowed to enter?"
Every category is a rule the reviewer signed off on, keyed to a stable id
and covered by tests. The banned-word list is now the second line of
defence, not the first.

Design rules for this module:

1. **Deterministic.** ``classify`` is a pure function of the input string.
   No timestamps, no shared state, no model call.
2. **Reviewed regex only.** Patterns live in this file; a change is a
   reviewed diff. No pattern is generated at runtime.
3. **More-restrictive-only refinement.** A model-based second opinion may
   ADD a category to the result (see ``refine_more_restrictive``). It may
   never remove a category the deterministic layer set. That property is
   enforced by the API — the refiner takes the current set and returns
   an ADDITIVE-only set.
4. **Fail closed.** ``is_blocked_for_display`` returns True when any
   ``BLOCKING`` category is present. A caller that skips the check still
   trips the banned-word sweep in ``safety.narrative_is_safe``.

Coverage of the categories called out by the stabilisation brief lives in
``docs/stabilisation/INGREDIENT_COVERAGE.md`` (rule metadata) and in the
tests at ``backend/tests/test_safety_classifier.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SafetyCategory(StrEnum):
    """The categories a piece of user-facing text can trip.

    Names are chosen so a caller reading a category set understands why the
    text is refused without needing to open this file.
    """

    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"
    DOSAGE = "dosage"
    MEDICATION_INTERACTION = "medication_interaction"
    ALLERGY_CERTAINTY = "allergy_certainty"
    PREGNANCY_OR_BREASTFEEDING = "pregnancy_or_breastfeeding"
    DISEASE_CLAIM = "disease_claim"
    DEFICIENCY_CLAIM = "deficiency_claim"
    GUARANTEED_OUTCOME = "guaranteed_outcome"
    HARMFUL_BODY_JUDGEMENT = "harmful_body_judgement"
    UNSUPPORTED_CAUSAL_CLAIM = "unsupported_causal_claim"
    EMERGENCY_SYMPTOM = "emergency_symptom"
    PROFESSIONAL_REFERRAL_REQUIRED = "professional_referral_required"


# Categories that block the text from being shown to the user at all. Every
# other category is informational (the caller MAY use it to route to a
# clarifying disclaimer, e.g. PROFESSIONAL_REFERRAL_REQUIRED renders the
# "please talk to a doctor" boundary card).
BLOCKING: frozenset[SafetyCategory] = frozenset({
    SafetyCategory.DIAGNOSIS,
    SafetyCategory.TREATMENT,
    SafetyCategory.DOSAGE,
    SafetyCategory.MEDICATION_INTERACTION,
    SafetyCategory.DISEASE_CLAIM,
    SafetyCategory.DEFICIENCY_CLAIM,
    SafetyCategory.GUARANTEED_OUTCOME,
    SafetyCategory.HARMFUL_BODY_JUDGEMENT,
    SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM,
})


@dataclass(frozen=True)
class _Pattern:
    """One reviewed regex."""

    category: SafetyCategory
    rule_id: str          # stable id, referenced by INGREDIENT_COVERAGE.md
    pattern: re.Pattern


def _p(category: SafetyCategory, rule_id: str, pattern: str, flags: int = re.IGNORECASE) -> _Pattern:
    return _Pattern(category=category, rule_id=rule_id, pattern=re.compile(pattern, flags))


# The full pattern table. Every entry is a reviewed rule with a stable id
# that appears in docs/stabilisation/INGREDIENT_COVERAGE.md. The tests at
# backend/tests/test_safety_classifier.py assert every id in this table
# appears in that doc — a rule cannot land here without an evidence row.
_PATTERNS: tuple = (
    # -------- diagnosis (naming a condition, or asserting the user has one) --
    _p(SafetyCategory.DIAGNOSIS,             "safety.dx.name_condition",       r"\byou (may )?have (rosacea|eczema|psoriasis|dermatitis|acne vulgaris|folliculitis)\b"),
    _p(SafetyCategory.DIAGNOSIS,             "safety.dx.confirmed_condition",  r"\b(this is|that is|looks like) (rosacea|eczema|psoriasis|dermatitis|folliculitis|alopecia|melasma)\b"),
    _p(SafetyCategory.DIAGNOSIS,             "safety.dx.assert_person",        r"\byou (are|seem) (suffering from|diagnosed with)\b"),

    # -------- treatment (prescribing a course of action for a condition) -----
    _p(SafetyCategory.TREATMENT,             "safety.tx.treats",               r"\btreat(s|ment)? (your|the) (rosacea|eczema|acne|dermatitis|hair loss|dandruff|infection)\b"),
    _p(SafetyCategory.TREATMENT,             "safety.tx.heal",                 r"\bheal(s|ing)? (your|the) (rosacea|eczema|acne|skin condition|scars)\b"),
    _p(SafetyCategory.TREATMENT,             "safety.tx.cure",                 r"\b(cure|cures|curing|reverses)\b"),

    # -------- dosage (supplement / medication amounts) -----------------------
    _p(SafetyCategory.DOSAGE,                "safety.dose.per_day",            r"\b\d+\s?(mg|mcg|iu|g)\s?(per|/|a)\s?day\b"),
    _p(SafetyCategory.DOSAGE,                "safety.dose.take_amount",        r"\btake (one|two|three|\d+) (tablet|capsule|pill|drop|scoop)s?\b"),
    _p(SafetyCategory.DOSAGE,                "safety.dose.start_stop",         r"\b(start|stop|increase|decrease|double|halve) (taking|the dose|your dose|your intake)\b"),

    # -------- medication interaction ----------------------------------------
    _p(SafetyCategory.MEDICATION_INTERACTION,"safety.rx.interacts",            r"\binteract(s|ing)? with (your|the)? ?(medication|medicine|prescription|antibiotic|blood thinner)\b"),
    _p(SafetyCategory.MEDICATION_INTERACTION,"safety.rx.safe_with",            r"\b(safe|unsafe|okay) (to take|to use) with (your|the)? ?(medication|medicine|prescription)\b"),

    # -------- allergy certainty ---------------------------------------------
    _p(SafetyCategory.ALLERGY_CERTAINTY,     "safety.allergy.definitive",      r"\byou (are|will be) allergic to\b"),
    _p(SafetyCategory.ALLERGY_CERTAINTY,     "safety.allergy.diagnose",        r"\b(this|it) will (definitely )?(cause|trigger) (a|an) allerg(y|ic reaction)\b"),

    # -------- pregnancy / breastfeeding advice ------------------------------
    _p(SafetyCategory.PREGNANCY_OR_BREASTFEEDING, "safety.preg.safe",          r"\b(safe|unsafe|okay|do not use) (during|in|while) (pregnancy|pregnant|breastfeeding|nursing|lactation)\b"),
    _p(SafetyCategory.PREGNANCY_OR_BREASTFEEDING, "safety.preg.direct",        r"\b(if|while) (you are )?pregnant\b"),

    # -------- disease claim (product claims to affect a disease) ------------
    _p(SafetyCategory.DISEASE_CLAIM,         "safety.disease.claim",           r"\b(prevents|reduces|reverses) (cancer|diabetes|arthritis|osteoporosis|thyroid|pcos)\b"),
    _p(SafetyCategory.DISEASE_CLAIM,         "safety.disease.eradicate",       r"\b(eradicate|eliminate) (rosacea|eczema|acne|dermatitis|infection)\b"),

    # -------- deficiency claim ----------------------------------------------
    _p(SafetyCategory.DEFICIENCY_CLAIM,      "safety.deficiency.you_are",      r"\byou are (deficient|low) in (vitamin|iron|zinc|magnesium|b12|d3?)\b"),
    _p(SafetyCategory.DEFICIENCY_CLAIM,      "safety.deficiency.condition",    r"\b(iron|vitamin d|vitamin b12|zinc|magnesium) deficiency\b"),

    # -------- guaranteed outcome --------------------------------------------
    _p(SafetyCategory.GUARANTEED_OUTCOME,    "safety.outcome.guarantee",       r"\b(guaranteed|100%|proven) to (clear|fix|remove|eliminate)\b"),
    _p(SafetyCategory.GUARANTEED_OUTCOME,    "safety.outcome.days",            r"\bin (\d+ (day|week)s?)\s+(you will|your (skin|hair) will) (be|look) (clear|glowing|flawless|perfect)\b"),

    # -------- harmful body judgement ----------------------------------------
    _p(SafetyCategory.HARMFUL_BODY_JUDGEMENT,"safety.body.score",              r"\b(attractiveness|beauty|overall) score\b"),
    _p(SafetyCategory.HARMFUL_BODY_JUDGEMENT,"safety.body.money_wasted",       r"\bmoney wasted\b"),
    _p(SafetyCategory.HARMFUL_BODY_JUDGEMENT,"safety.body.problem_area",       r"\bproblem areas?\b"),
    _p(SafetyCategory.HARMFUL_BODY_JUDGEMENT,"safety.body.body_type_judge",    r"\b(fat|slim|skinny|chubby) (arms|legs|face|body)\b"),
    _p(SafetyCategory.HARMFUL_BODY_JUDGEMENT,"safety.body.lose_weight",        r"\b(lose|gain) weight\b"),

    # -------- unsupported causal claim --------------------------------------
    _p(SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM, "safety.cause.dairy_acne",     r"\b(dairy|milk|sugar) (causes|causing) (acne|pimples|breakouts)\b"),
    _p(SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM, "safety.cause.hormones",       r"\byour (hormones|hormonal imbalance) (is|are) causing\b"),

    # -------- emergency symptom (route to a clinician immediately) ----------
    _p(SafetyCategory.EMERGENCY_SYMPTOM,     "safety.emergency.swelling_lips", r"\b(swelling of|swollen) (lips|throat|tongue|face)\b"),
    _p(SafetyCategory.EMERGENCY_SYMPTOM,     "safety.emergency.breathing",     r"\b(difficulty breathing|trouble breathing|shortness of breath)\b"),
    _p(SafetyCategory.EMERGENCY_SYMPTOM,     "safety.emergency.bleeding",      r"\b(heavy|severe) bleeding\b"),
    _p(SafetyCategory.EMERGENCY_SYMPTOM,     "safety.emergency.anaphylaxis",   r"\banaphylaxis\b"),

    # -------- professional-referral-required (informational, non-blocking) --
    _p(SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED, "safety.refer.dermatologist", r"\bsee a dermatologist\b"),
    _p(SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED, "safety.refer.doctor",        r"\btalk to (a|your) (doctor|gp|physician|dermatologist|pharmacist)\b"),
    _p(SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED, "safety.refer.professional",  r"\bqualified professional\b"),
)


def all_rule_ids() -> list[str]:
    """Every reviewed safety rule id. Used by INGREDIENT_COVERAGE.md tests."""
    return [p.rule_id for p in _PATTERNS]


def classify(text: str | None) -> frozenset[SafetyCategory]:
    """Deterministic, additive-only classifier.

    Returns the set of categories the text trips. An empty set means the
    text passed every reviewed rule; a caller still runs
    ``safety.narrative_is_safe`` as the second line of defence.
    """
    if not text:
        return frozenset()
    found: set = set()
    for entry in _PATTERNS:
        if entry.pattern.search(text):
            found.add(entry.category)
    return frozenset(found)


def is_blocked_for_display(text: str | None) -> bool:
    """Would we refuse to show this text to a user?

    Any BLOCKING category is enough. Informational categories
    (PROFESSIONAL_REFERRAL_REQUIRED, EMERGENCY_SYMPTOM, ALLERGY_CERTAINTY,
    PREGNANCY_OR_BREASTFEEDING) do not block on their own; the caller
    decides how to route them.
    """
    return not classify(text).isdisjoint(BLOCKING)


def refine_more_restrictive(
    deterministic: frozenset[SafetyCategory],
    proposed_extra: frozenset[SafetyCategory],
) -> frozenset[SafetyCategory]:
    """Union the two sets. The proposed-extra can only ADD categories.

    A model-based second opinion runs against the same text and proposes a
    set of categories. If the model says "also add DIAGNOSIS", we honour
    it. If the model says "remove TREATMENT that the deterministic layer
    caught", we ignore that: the deterministic result is the floor and
    the model can only tighten, never loosen.
    """
    return frozenset(deterministic | proposed_extra)


def rule_ids_for(text: str | None) -> list[str]:
    """Which specific rule ids fired. For logs and tests; not for users."""
    if not text:
        return []
    return [p.rule_id for p in _PATTERNS if p.pattern.search(text)]
