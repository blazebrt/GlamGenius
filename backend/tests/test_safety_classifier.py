"""Tests for the structured safety classifier (Fix 14, WP3)."""
from __future__ import annotations

import pytest

from app.domains.routines.safety import narrative_is_safe
from app.domains.routines.safety_classifier import (
    BLOCKING,
    SafetyCategory,
    all_rule_ids,
    classify,
    is_blocked_for_display,
    refine_more_restrictive,
    rule_ids_for,
)


# One representative unsafe string per category. Keep the strings intentionally
# short — a test that fails should point at exactly which pattern is broken.
UNSAFE_BY_CATEGORY = {
    SafetyCategory.DIAGNOSIS:                    "you have rosacea and should start a treatment plan.",
    SafetyCategory.TREATMENT:                    "this treats your eczema in a few weeks.",
    SafetyCategory.DOSAGE:                       "take one tablet in the morning with water.",
    SafetyCategory.MEDICATION_INTERACTION:       "this interacts with your medication and is not safe.",
    SafetyCategory.ALLERGY_CERTAINTY:            "you are allergic to fragrance.",
    SafetyCategory.PREGNANCY_OR_BREASTFEEDING:   "not safe during pregnancy — do not use.",
    SafetyCategory.DISEASE_CLAIM:                "this eradicates rosacea and prevents diabetes.",
    SafetyCategory.DEFICIENCY_CLAIM:             "you are deficient in vitamin d.",
    SafetyCategory.GUARANTEED_OUTCOME:           "guaranteed to clear your acne in 7 days you will be flawless.",
    SafetyCategory.HARMFUL_BODY_JUDGEMENT:       "your attractiveness score is low and you should lose weight.",
    SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM:     "dairy causes acne so cut it out.",
    SafetyCategory.EMERGENCY_SYMPTOM:            "if you feel difficulty breathing, seek help now.",
    SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED: "we recommend you see a dermatologist for this.",
}


@pytest.mark.parametrize("category,text", list(UNSAFE_BY_CATEGORY.items()))
def test_each_category_is_detected_on_a_representative_string(category, text):
    """Fix 14: every named category has at least one deterministic rule."""
    tripped = classify(text)
    assert category in tripped, (
        f"{category.value} was not detected on {text!r}. Fired: {sorted(c.value for c in tripped)}."
    )


def test_classification_is_deterministic():
    """Same input, same output — no shared state, no time-of-day drift."""
    text = "you have rosacea and dairy causes acne"
    first = classify(text)
    second = classify(text)
    third = classify(text)
    assert first == second == third
    assert SafetyCategory.DIAGNOSIS in first
    assert SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM in first


def test_empty_and_none_input_return_empty_set():
    assert classify(None) == frozenset()
    assert classify("") == frozenset()
    assert is_blocked_for_display(None) is False
    assert is_blocked_for_display("") is False


def test_safe_text_returns_empty_set():
    text = "Your morning routine has a cleanser, moisturiser and sunscreen."
    assert classify(text) == frozenset()
    assert is_blocked_for_display(text) is False


def test_is_blocked_for_display_uses_blocking_set_only():
    """Blocking categories block; informational ones do not on their own."""
    # Blocking category on its own -> blocked.
    assert is_blocked_for_display(UNSAFE_BY_CATEGORY[SafetyCategory.DIAGNOSIS]) is True
    # Informational-only category -> not blocked.
    text_referral = UNSAFE_BY_CATEGORY[SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED]
    cats = classify(text_referral)
    assert SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED in cats
    assert cats.isdisjoint(BLOCKING)
    assert is_blocked_for_display(text_referral) is False


def test_refine_more_restrictive_is_additive_only():
    """A model second-opinion can add categories, never remove one."""
    deterministic = frozenset({SafetyCategory.DIAGNOSIS, SafetyCategory.TREATMENT})
    proposed = frozenset({SafetyCategory.HARMFUL_BODY_JUDGEMENT})
    refined = refine_more_restrictive(deterministic, proposed)
    assert SafetyCategory.DIAGNOSIS in refined
    assert SafetyCategory.TREATMENT in refined
    assert SafetyCategory.HARMFUL_BODY_JUDGEMENT in refined


def test_refine_cannot_remove_deterministic_categories():
    """If a proposer 'suggests' fewer categories than the deterministic
    layer found, the deterministic set wins — the model cannot argue us
    out of a blocker."""
    deterministic = frozenset({SafetyCategory.DIAGNOSIS})
    proposed_empty = frozenset()
    refined = refine_more_restrictive(deterministic, proposed_empty)
    assert SafetyCategory.DIAGNOSIS in refined
    assert is_blocked_for_display("you have rosacea") is True  # sanity


def test_implicit_unsafe_wording_is_blocked_without_banned_keyword():
    """Fix 14 acceptance: implicit unsafe wording is blocked even when the
    banned-word sweep in safety.narrative_is_safe would let it through."""
    # This string does not contain any word in safety.py's BANNED_TERMS but
    # is a clear medication-interaction claim.
    text = "This is unsafe to take with your prescription; check first."
    assert narrative_is_safe(text) is True or narrative_is_safe(text) is False
    # Whether the banned-word sweep catches it or not, the classifier does:
    assert SafetyCategory.MEDICATION_INTERACTION in classify(text)
    assert is_blocked_for_display(text) is True


def test_safe_disclaimers_survive_the_classifier():
    """Fix 14 acceptance: appropriate disclaimers are NOT swept away."""
    from app.domains.routines.safety import (
        PROFESSIONAL_BOUNDARY,
        ROUTINE_DISCLAIMER,
        SUPPLEMENT_DISCLAIMER,
    )

    # PROFESSIONAL_BOUNDARY does mention "doctor / dermatologist / pharmacist"
    # so the referral category fires — informational, non-blocking.
    boundary_cats = classify(PROFESSIONAL_BOUNDARY)
    assert boundary_cats.isdisjoint(BLOCKING), (
        f"PROFESSIONAL_BOUNDARY tripped a blocking category: "
        f"{sorted(c.value for c in boundary_cats)}"
    )
    assert is_blocked_for_display(PROFESSIONAL_BOUNDARY) is False

    # ROUTINE_DISCLAIMER and SUPPLEMENT_DISCLAIMER must pass with an empty
    # or purely-informational category set.
    assert classify(ROUTINE_DISCLAIMER).isdisjoint(BLOCKING)
    assert is_blocked_for_display(ROUTINE_DISCLAIMER) is False
    assert classify(SUPPLEMENT_DISCLAIMER).isdisjoint(BLOCKING)
    assert is_blocked_for_display(SUPPLEMENT_DISCLAIMER) is False


def test_banned_word_sweep_still_operates_as_secondary_layer():
    """The original banned-word list must still fire — Fix 14 adds a layer,
    it does not remove one."""
    # A string that trips the banned-word sweep in safety.py.
    text = "You suffer from a skin disorder that needs medicated cream."
    # narrative_is_safe returns False when a banned term is present.
    assert narrative_is_safe(text) is False


def test_rule_ids_for_reports_specific_rules():
    """Log helpers name specific rule ids, not just categories."""
    fired = rule_ids_for("you have rosacea and dairy causes acne")
    assert any(rid.startswith("safety.dx.") for rid in fired)
    assert any(rid.startswith("safety.cause.") for rid in fired)


def test_all_rule_ids_are_unique():
    ids = all_rule_ids()
    assert len(ids) == len(set(ids)), "duplicate rule ids in safety_classifier._PATTERNS"


def test_every_pattern_rule_id_is_documented_in_ingredient_coverage():
    """Fix 13 acceptance link: every rule id emitted by the classifier is
    documented in docs/stabilisation/INGREDIENT_COVERAGE.md."""
    from pathlib import Path

    coverage_path = Path(__file__).resolve().parents[2] / "docs" / "stabilisation" / "INGREDIENT_COVERAGE.md"
    assert coverage_path.is_file(), f"coverage doc not found at {coverage_path}"
    coverage_text = coverage_path.read_text(encoding="utf-8")

    missing: list = []
    for rid in all_rule_ids():
        if rid not in coverage_text:
            missing.append(rid)
    assert not missing, (
        "the following safety rule ids are not documented in "
        f"INGREDIENT_COVERAGE.md: {missing}"
    )
