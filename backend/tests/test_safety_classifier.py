"""The structured safety classifier, and the fact that anything calls it.

``safety_classifier.py`` describes itself as the primary defence, with the
banned-word list demoted to "the second line of defence, not the first". Its
docstring cites this file. This file did not exist, and neither did a single
caller: 212 lines of reviewed rules that no request ever reached, while the
word list it had supposedly replaced did all the work alone.

A word list cannot be the whole defence — that is the argument in that module
and it is right. It never predicts every synonym, and each miss is a policy
incident: "you have rosacea" was caught because *rosacea* is on the list, while
"this is rosacea" and "guaranteed to clear your skin" sailed through.

So the classifier is now consulted by ``safety.narrative_is_safe``, which is
the one gate every AI-written string passes. What these tests hold up:

* every reviewed rule actually fires, and fires as the category claimed;
* the blocking set refuses text and the informational set does not — our own
  disclaimers are written as referrals, and blocking those would mean the
  boundary refusing the sentence that states the boundary;
* refinement is additive only, so a second opinion can tighten and never
  loosen;
* every rule id appears in the coverage document, which is what the module
  says makes a rule reviewable;
* and the gate is wired in, which is the part that was missing.
"""
from __future__ import annotations

import pathlib

import pytest
from app.domains.nutrition.safety import (
    NUTRITION_DISCLAIMER,
    NUTRITION_HYDRATION_NO_TARGET,
)
from app.domains.routines import safety
from app.domains.routines.safety_classifier import (
    BLOCKING,
    SafetyCategory,
    all_rule_ids,
    classify,
    is_blocked_for_display,
    refine_more_restrictive,
    rule_ids_for,
)

COVERAGE_DOC = pathlib.Path(__file__).parents[2] / "docs/stabilisation/INGREDIENT_COVERAGE.md"


# One sentence per reviewed rule, in the shape the rule is meant to catch.
BLOCKED_EXAMPLES: tuple[tuple[str, SafetyCategory], ...] = (
    ("You may have rosacea on both cheeks.", SafetyCategory.DIAGNOSIS),
    ("This is eczema, plainly.", SafetyCategory.DIAGNOSIS),
    ("You are suffering from something here.", SafetyCategory.DIAGNOSIS),
    ("It treats your dermatitis well.", SafetyCategory.TREATMENT),
    ("It heals your acne over a fortnight.", SafetyCategory.TREATMENT),
    ("This will cure it.", SafetyCategory.TREATMENT),
    ("Have 500 mg per day of this.", SafetyCategory.DOSAGE),
    ("Take two capsules with water.", SafetyCategory.DOSAGE),
    ("You should stop taking that one.", SafetyCategory.DOSAGE),
    ("It interacts with your medication.", SafetyCategory.MEDICATION_INTERACTION),
    ("It is safe to take with your prescription.", SafetyCategory.MEDICATION_INTERACTION),
    ("This prevents diabetes.", SafetyCategory.DISEASE_CLAIM),
    ("It will eliminate eczema.", SafetyCategory.DISEASE_CLAIM),
    ("You are deficient in vitamin D.", SafetyCategory.DEFICIENCY_CLAIM),
    ("That is an iron deficiency.", SafetyCategory.DEFICIENCY_CLAIM),
    ("Proven to clear everything.", SafetyCategory.GUARANTEED_OUTCOME),
    ("In 7 days your skin will be flawless.", SafetyCategory.GUARANTEED_OUTCOME),
    ("Your overall score is up this week.", SafetyCategory.HARMFUL_BODY_JUDGEMENT),
    ("That was money wasted.", SafetyCategory.HARMFUL_BODY_JUDGEMENT),
    ("This hides your problem areas.", SafetyCategory.HARMFUL_BODY_JUDGEMENT),
    ("It flatters your chubby arms.", SafetyCategory.HARMFUL_BODY_JUDGEMENT),
    ("You could lose weight for this look.", SafetyCategory.HARMFUL_BODY_JUDGEMENT),
    ("Dairy causes acne, so skip the paneer.", SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM),
    ("Your hormones are causing this.", SafetyCategory.UNSUPPORTED_CAUSAL_CLAIM),
)

INFORMATIONAL_EXAMPLES: tuple[tuple[str, SafetyCategory], ...] = (
    ("Swelling of the lips needs attention now.", SafetyCategory.EMERGENCY_SYMPTOM),
    ("Difficulty breathing is not something we can help with.", SafetyCategory.EMERGENCY_SYMPTOM),
    ("Heavy bleeding needs a clinician.", SafetyCategory.EMERGENCY_SYMPTOM),
    ("Anaphylaxis is an emergency.", SafetyCategory.EMERGENCY_SYMPTOM),
    ("You are allergic to this.", SafetyCategory.ALLERGY_CERTAINTY),
    ("It will definitely cause an allergic reaction.", SafetyCategory.ALLERGY_CERTAINTY),
    ("Do not use during pregnancy.", SafetyCategory.PREGNANCY_OR_BREASTFEEDING),
    ("If you are pregnant, ask first.", SafetyCategory.PREGNANCY_OR_BREASTFEEDING),
    ("See a dermatologist about it.", SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED),
    ("Please talk to a doctor.", SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED),
    ("Ask a qualified professional.", SafetyCategory.PROFESSIONAL_REFERRAL_REQUIRED),
)

ORDINARY_COPY = (
    "A crisp linen shirt reads well for a daytime Delhi wedding.",
    "The olive trousers repeat a colour you already wear often.",
    "This is past the date you recorded. Replace it rather than using it.",
    "Two of your products contain the same active, so use them on different nights.",
    "Air is very bad today (NAQI 340, CPCB), so move the exfoliant to Saturday.",
    "You already own shoes that finish this, so nothing new is needed.",
)


class TestEveryReviewedRuleFires:
    @pytest.mark.parametrize(("text", "category"), BLOCKED_EXAMPLES)
    def test_a_blocking_rule_produces_its_category(self, text, category):
        assert category in classify(text), rule_ids_for(text)

    @pytest.mark.parametrize(("text", "category"), BLOCKED_EXAMPLES)
    def test_a_blocking_rule_refuses_the_text(self, text, category):
        assert is_blocked_for_display(text) is True

    @pytest.mark.parametrize(("text", "category"), INFORMATIONAL_EXAMPLES)
    def test_an_informational_rule_produces_its_category(self, text, category):
        assert category in classify(text), rule_ids_for(text)

    @pytest.mark.parametrize(("text", "category"), INFORMATIONAL_EXAMPLES)
    def test_an_informational_rule_does_not_refuse_on_its_own(self, text, category):
        """The caller routes these; it does not withhold the text."""
        assert category not in BLOCKING

    def test_every_rule_in_the_table_has_an_example_here(self):
        """A rule nobody exercises is a rule nobody knows still works."""
        covered = set()
        for text, _ in BLOCKED_EXAMPLES + INFORMATIONAL_EXAMPLES:
            covered.update(rule_ids_for(text))
        missing = sorted(set(all_rule_ids()) - covered)
        assert not missing, f"no example exercises these rules: {missing}"


class TestOrdinaryCopyIsNotRefused:
    @pytest.mark.parametrize("text", ORDINARY_COPY)
    def test_product_wording_passes(self, text):
        assert is_blocked_for_display(text) is False, rule_ids_for(text)

    @pytest.mark.parametrize(
        "text",
        [
            safety.PROFESSIONAL_BOUNDARY,
            safety.SUPPLEMENT_DISCLAIMER,
            safety.ROUTINE_DISCLAIMER,
            NUTRITION_DISCLAIMER,
            NUTRITION_HYDRATION_NO_TARGET,
            *safety.SUPPLEMENT_FLAG_TEXT.values(),
        ],
    )
    def test_our_own_boundary_copy_is_never_refused(self, text):
        """The sentence that states the boundary must survive the boundary."""
        assert is_blocked_for_display(text) is False, rule_ids_for(text)
        assert safety.narrative_is_safe(text) is True, safety.first_violation(text)

    def test_empty_text_classifies_as_nothing(self):
        assert classify(None) == frozenset()
        assert classify("") == frozenset()
        assert is_blocked_for_display(None) is False


class TestRefinementIsAdditiveOnly:
    def test_a_second_opinion_can_add_a_category(self):
        result = refine_more_restrictive(
            frozenset({SafetyCategory.DIAGNOSIS}), frozenset({SafetyCategory.DOSAGE})
        )
        assert result == frozenset({SafetyCategory.DIAGNOSIS, SafetyCategory.DOSAGE})

    def test_a_second_opinion_cannot_remove_one(self):
        """The deterministic result is the floor."""
        result = refine_more_restrictive(frozenset({SafetyCategory.TREATMENT}), frozenset())
        assert SafetyCategory.TREATMENT in result

    def test_refinement_never_shrinks_the_set(self):
        base = frozenset({SafetyCategory.DIAGNOSIS, SafetyCategory.DOSAGE})
        assert refine_more_restrictive(base, frozenset()) >= base


class TestTheModuleKeepsItsOwnPromises:
    def test_every_rule_id_appears_in_the_coverage_document(self):
        """The module says a rule cannot land without an evidence row."""
        assert COVERAGE_DOC.exists(), COVERAGE_DOC
        doc = COVERAGE_DOC.read_text()
        missing = [rule_id for rule_id in all_rule_ids() if rule_id not in doc]
        assert not missing, f"rules with no row in the coverage document: {missing}"

    def test_rule_ids_are_unique(self):
        ids = all_rule_ids()
        assert len(ids) == len(set(ids))

    def test_classification_is_a_pure_function_of_the_text(self):
        text = "You may have rosacea on both cheeks."
        assert classify(text) == classify(text) == classify(text)


class TestTheGateActuallyConsultsIt:
    """The part that was missing: a caller."""

    @pytest.mark.parametrize(
        "text",
        [
            "This is melasma, plainly.",
            "Guaranteed to clear your skin in 7 days.",
            "Your hormones are causing this.",
            "It is safe to take with your medication.",
        ],
    )
    def test_wording_no_word_list_would_catch_is_now_refused(self, text):
        """None of these trips BANNED_TERMS or the dose pattern."""
        lowered = text.lower()
        assert not any(term in lowered for term in safety.BANNED_TERMS), (
            "pick an example the word list does not already catch"
        )
        assert safety.narrative_is_safe(text) is False

    def test_the_reported_violation_names_the_rule(self):
        violation = safety.first_violation("Guaranteed to clear your skin in 7 days.")
        assert violation == "safety.outcome.guarantee"
