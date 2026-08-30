"""The hard handoff gate — the cases it must never miss.

A missed handoff is a legal and safety failure, so these tests are written as
"this must hand off", not "this should behave sensibly". Where a case is
genuinely ambiguous the expected answer is still a handoff.
"""
from __future__ import annotations

import pytest
from app.domains.routines import hard_handoff
from app.domains.routines.hard_handoff import HandoffReason, evaluate, requires_handoff
from app.domains.routines.safety import narrative_is_safe, needs_professional


# ---------------------------------------------------------------------------
# The acceptance cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("I am 9", HandoffReason.AGE_UNDER_MINIMUM),
        ("I'm pregnant", HandoffReason.PREGNANCY),
        ("I take metformin", HandoffReason.MEDICATION),
        ("I take a thyroid tablet", HandoffReason.MEDICATION),
        ("my doctor said I have PCOS", HandoffReason.CLINICAL_CONDITION),
        ("My doctor mentioned something about this.", HandoffReason.UNCERTAIN),
    ],
)
def test_the_required_cases_hand_off(text: str, reason: HandoffReason) -> None:
    decision = evaluate(text)
    assert decision.handoff is True, f"{text!r} did not hand off"
    assert decision.reason is reason, f"{text!r} handed off as {decision.reason}, expected {reason}"
    assert decision.message


# ---------------------------------------------------------------------------
# Age and children
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("age", [0, 1, 5, 9, 11])
def test_structured_age_below_the_minimum_hands_off(age: int) -> None:
    decision = evaluate("what moisturiser should I use", stated_age=age)
    assert decision.handoff is True
    assert decision.reason is HandoffReason.AGE_UNDER_MINIMUM


@pytest.mark.parametrize("age", [12, 16, 30, 80])
def test_structured_age_at_or_above_the_minimum_does_not_hand_off(age: int) -> None:
    assert evaluate("what moisturiser should I use", stated_age=age).handoff is False


@pytest.mark.parametrize(
    "text",
    [
        "I am 9",
        "I'm 8 years old",
        "she is 10",
        "he's 7",
        "my 6 year old",
        "aged 11",
        "turning 9 next week",
        "a 4-year-old",
    ],
)
def test_an_age_stated_in_text_hands_off(text: str) -> None:
    assert requires_handoff(text) is True, f"{text!r} did not hand off"


@pytest.mark.parametrize(
    "text",
    ["my daughter", "for my son", "my kid", "my toddler", "my child", "for my baby"],
)
def test_a_child_subject_hands_off(text: str) -> None:
    decision = evaluate(f"{text} — what should we use?")
    assert decision.handoff is True
    assert decision.reason is HandoffReason.CHILD_SUBJECT


def test_a_child_flagged_by_structured_context_hands_off() -> None:
    decision = evaluate("anything at all", subject_is_child=True)
    assert decision.handoff is True
    assert decision.reason is HandoffReason.CHILD_SUBJECT


def test_an_adult_age_in_text_is_not_a_handoff() -> None:
    assert requires_handoff("I'm 34 and my skin feels dry") is False


# ---------------------------------------------------------------------------
# Pregnancy and breastfeeding, in any phrasing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "I'm pregnant",
        "I am pregnant",
        "im pregnant",
        "currently expecting",
        "expecting a baby in June",
        "I'm in my second trimester",
        "we are trying to conceive",
        "I'm on prenatal vitamins",
        "36 weeks pregnant",
        "I have a baby bump now",
        "I'm 9 months pregnant",
    ],
)
def test_pregnancy_in_any_phrasing_hands_off(text: str) -> None:
    assert requires_handoff(text) is True, f"{text!r} did not hand off"


def test_pregnancy_wins_over_a_number_that_looks_like_an_age() -> None:
    """'9 months pregnant' must read as pregnancy, not as a nine-year-old."""
    assert evaluate("I'm 9 months pregnant").reason is HandoffReason.PREGNANCY


@pytest.mark.parametrize(
    "text",
    [
        "I'm breastfeeding",
        "I am breast feeding",
        "still nursing",
        "I'm chestfeeding",
        "I'm lactating",
        "I just had a baby",
        "postpartum hair loss",
        "expressing milk at work",
    ],
)
def test_breastfeeding_in_any_phrasing_hands_off(text: str) -> None:
    assert requires_handoff(text) is True, f"{text!r} did not hand off"


# ---------------------------------------------------------------------------
# Medication — by the pattern of a drug name, not a list
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "drug",
    [
        "metformin", "atenolol", "propranolol", "lisinopril", "losartan",
        "atorvastatin", "fluconazole", "amoxicillin", "azithromycin",
        "fluoxetine", "diazepam", "alprazolam", "ranitidine", "omeprazole",
        "warfarin" and "enoxaparin", "imatinib", "adalimumab", "acyclovir",
        "amlodipine", "sumatriptan", "pioglitazone", "sitagliptin",
        "empagliflozin", "ciprofloxacin", "doxycycline", "oxycodone",
        "lidocaine", "ibuprofen", "betamethasone", "prednisolone",
        "levothyroxine", "alendronate", "ondansetron", "amitriptyline",
        "risperidone", "olanzapine", "salmeterol", "hydrochlorothiazide",
    ],
)
def test_a_generic_drug_name_hands_off(drug: str) -> None:
    """These are recognised by how drug names are built, not by being listed."""
    decision = evaluate(f"I take {drug}")
    assert decision.handoff is True, f"{drug!r} was not recognised as a medication"
    assert decision.reason is HandoffReason.MEDICATION


def test_a_drug_the_author_never_saw_still_hands_off() -> None:
    """Invented names built from real stems must be caught, or the gate is a list."""
    for invented in ("zaltoprofen", "bexasartan", "novaformin", "quiletidine"):
        assert requires_handoff(f"I take {invented}") is True, invented


@pytest.mark.parametrize(
    "text",
    [
        "I take a thyroid tablet",
        "I'm on two tablets a day",
        "my doctor put me on something new",
        "I was prescribed a cream",
        "I have a prescription for it",
        "I take 500mg every morning",
        "I use an inhaler",
        "I'm on antibiotics",
        "I take insulin",
        "I'm on birth control",
        "I take my medication at night",
        "I take something for my heart",
    ],
)
def test_a_medication_described_without_naming_it_hands_off(text: str) -> None:
    assert requires_handoff(text) is True, f"{text!r} did not hand off"


@pytest.mark.parametrize(
    "text",
    [
        "I take vitamin D every day",
        "I take a multivitamin",
        "I use collagen powder",
        "I take omega 3",
        "I'm taking protein after the gym",
        "I take biotin for my hair",
    ],
)
def test_tracking_an_ordinary_supplement_is_not_a_handoff(text: str) -> None:
    """The product exists to track these. If they hand off, it has no purpose."""
    assert requires_handoff(text) is False, f"{text!r} handed off but should not"


def test_a_supplement_with_a_prescription_word_still_hands_off() -> None:
    """The supplement carve-out clears the weakest frame only."""
    assert requires_handoff("my doctor prescribed vitamin D") is True
    assert requires_handoff("I take vitamin D 800mg") is True


# ---------------------------------------------------------------------------
# Conditions a clinician is already handling
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "my doctor said I have PCOS",
        "I was diagnosed with eczema",
        "I have been diagnosed with diabetes",
        "I suffer from psoriasis",
        "the doctor told me I have anaemia",
        "I tested positive for it",
        "I'm under a specialist",
        "my condition flares up",
        "I have hypothyroidism",
        "I have asthma",
        "I've got arthritis",
    ],
)
def test_a_clinical_condition_hands_off(text: str) -> None:
    assert requires_handoff(text) is True, f"{text!r} did not hand off"


# ---------------------------------------------------------------------------
# Fail closed: uncertainty resolves toward the handoff
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "My doctor mentioned something about this.",
        "will this interact with anything?",
        "is it safe for me?",
        "I had a bad reaction last time",
        "I get a flare up sometimes",
        "I'm allergic to something in this",
        "the clinic told me to be careful",
        "I have a blood test next week",
        "any side effects?",
        "I'm going in for surgery",
    ],
)
def test_an_ambiguous_medical_mention_hands_off(text: str) -> None:
    """A false handoff costs a little usefulness. A missed one is the failure."""
    assert requires_handoff(text) is True, f"{text!r} did not hand off"


# ---------------------------------------------------------------------------
# It must still let the product work
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "which moisturiser suits dry skin?",
        "what should I wear to a wedding?",
        "is this shampoo good for frizz?",
        "how often should I wash my hair?",
        "I bought this in April",
        "my cholesterol powder arrived",
        "I read about it in a magazine",
        "",
        "   ",
    ],
)
def test_ordinary_product_use_is_not_a_handoff(text: str) -> None:
    assert requires_handoff(text) is False, f"{text!r} handed off but should not"


def test_no_text_and_no_context_is_not_a_handoff() -> None:
    assert evaluate(None).handoff is False
    assert evaluate().handoff is False


# ---------------------------------------------------------------------------
# The decision itself
# ---------------------------------------------------------------------------
def test_every_message_survives_the_product_language_sweep() -> None:
    """A handoff message that the safety sweep rejects could never be shown."""
    for reason, message in hard_handoff.HANDOFF_MESSAGES.items():
        assert narrative_is_safe(message), f"{reason} message is not showable: {message!r}"


def test_every_reason_has_a_message() -> None:
    for reason in HandoffReason:
        assert hard_handoff.HANDOFF_MESSAGES.get(reason), f"{reason} has no message"


def test_a_decision_never_carries_the_users_words() -> None:
    """Signals name the rule, so a decision can be logged without leaking health data."""
    secret = "metformin"
    decision = evaluate(f"I take {secret} for my heart")
    assert decision.handoff is True
    assert secret not in str(decision.signal)
    assert secret not in decision.message
    assert secret not in str(decision.as_dict())


def test_a_clean_decision_carries_no_message() -> None:
    decision = evaluate("what should I wear today?")
    assert decision.handoff is False
    assert decision.message == ""
    assert decision.reason is None


def test_as_dict_is_the_shape_a_route_can_return() -> None:
    body = evaluate("I take metformin").as_dict()
    assert body["handoff"] is True
    assert body["reason"] == "medication"
    assert body["message"]


# ---------------------------------------------------------------------------
# The existing narrow check is left alone
# ---------------------------------------------------------------------------
def test_needs_professional_was_not_widened() -> None:
    """The hard handoff is a separate, stricter gate — not an overload of the old one.

    These are exactly the cases the old check was never able to catch. They must
    still slip past it, and must be caught by the new gate. If this test starts
    failing because needs_professional got stricter, the two have been merged and
    the narrow check has lost its original meaning.
    """
    missed_by_the_old_check = (
        "I am 9",                      # no age parameter, and none in its word list
        "I take metformin",            # a drug name it was never given
        "my daughter needs something",  # a child, which it cannot represent at all
        "I take 500mg every morning",  # a dose with no named drug
    )
    for text in missed_by_the_old_check:
        assert needs_professional(text) is False, f"needs_professional was widened: {text!r}"
        assert requires_handoff(text) is True, f"the hard handoff missed {text!r}"
