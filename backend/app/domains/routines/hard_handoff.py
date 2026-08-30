"""The hard handoff — the gate that stops this app answering for a clinician.

This is a legal and safety boundary, not a quality feature. Five situations are
outside what GlamGenius may ever decide about:

* someone under 12, or something being asked on a child's behalf
* pregnancy
* breastfeeding
* any named medication
* any condition a clinician is already looking after

It **fails closed**. Where the text is medical but the specifics are unclear, it
hands off anyway. A false handoff costs a little usefulness; a missed handoff is
the failure that matters, so every uncertain case resolves toward the handoff.

Relationship to ``safety.needs_professional``
---------------------------------------------
``needs_professional()`` in ``safety.py`` keeps its own narrow job: spotting a
question that reads like a medical one so a routine answer can be replaced by
the professional boundary. It is a word-list check and is deliberately **not**
widened here. This module is the stricter, separate gate: it takes structured
context as well as text, it recognises medications by how drug names are built
rather than by listing them, and it defaults to handing off.

Privacy
-------
A decision never carries the user's words. ``signal`` names the rule that fired,
never the text that fired it, so a decision can be logged without writing
somebody's health details into a log line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Below this age the product does not answer at all.
MINIMUM_AGE = 12


class HandoffReason(StrEnum):
    """Why the handoff fired. Stable identifiers — callers may branch on these."""

    AGE_UNDER_MINIMUM = "age_under_minimum"
    CHILD_SUBJECT = "child_subject"
    PREGNANCY = "pregnancy"
    BREASTFEEDING = "breastfeeding"
    MEDICATION = "medication"
    CLINICAL_CONDITION = "clinical_condition"
    UNCERTAIN = "uncertain"


# --- The strings this gate produces -----------------------------------------
# Every one of these is shown to a person. They state the fact and hand over.
# None of them names a condition, offers a judgement, or gives advice, and each
# is swept by ``safety.narrative_is_safe`` in the tests so a message can never
# drift into the language the rest of the product bans.

HANDOFF_MESSAGES: dict[HandoffReason, str] = {
    HandoffReason.AGE_UNDER_MINIMUM: (
        "GlamGenius is built for adults, so we are not able to help here. "
        "Please talk to a doctor or a pharmacist instead."
    ),
    HandoffReason.CHILD_SUBJECT: (
        "GlamGenius is built for adults, so we are not able to help with something "
        "for a child. Please talk to a doctor or a pharmacist instead."
    ),
    HandoffReason.PREGNANCY: (
        "We are not able to help with this during pregnancy. Please talk to a doctor, "
        "midwife or pharmacist — they can take your whole situation into account."
    ),
    HandoffReason.BREASTFEEDING: (
        "We are not able to help with this while you are breastfeeding. Please talk to "
        "a doctor, midwife or pharmacist — they can take your whole situation into account."
    ),
    HandoffReason.MEDICATION: (
        "It looks like a medicine came up. We are not able to help where medicines are "
        "involved, because what is safe depends on the person. Please talk to a doctor "
        "or pharmacist."
    ),
    HandoffReason.CLINICAL_CONDITION: (
        "It sounds like this is something a doctor is already looking after. That is "
        "outside what GlamGenius can help with — please talk to them about it."
    ),
    HandoffReason.UNCERTAIN: (
        "This may be a health matter, and we would rather hand it over than guess. "
        "Please talk to a doctor or pharmacist."
    ),
}

NO_HANDOFF_MESSAGE = ""


@dataclass(frozen=True)
class HandoffDecision:
    """The verdict. ``signal`` names the rule, never the user's words."""

    handoff: bool
    reason: HandoffReason | None = None
    signal: str | None = None

    @property
    def message(self) -> str:
        return HANDOFF_MESSAGES[self.reason] if self.reason is not None else NO_HANDOFF_MESSAGE

    def as_dict(self) -> dict:
        return {
            "handoff": self.handoff,
            "reason": str(self.reason) if self.reason else None,
            "message": self.message,
        }


_ALLOWED = HandoffDecision(handoff=False)


def _fires(reason: HandoffReason, signal: str) -> HandoffDecision:
    return HandoffDecision(handoff=True, reason=reason, signal=signal)


# --- Pregnancy and breastfeeding --------------------------------------------
# "Any phrasing" is the requirement, so this reaches past the clinical words to
# the ordinary ones people actually use.

_PREGNANCY = re.compile(
    r"\b("
    r"pregnan\w*|expecting(?:\s+a\s+baby)?|trimester|prenatal|antenatal|ante-natal|"
    r"with\s+child|having\s+a\s+baby|due\s+date|conceiv\w*|ttc|ivf|"
    r"morning\s+sickness|baby\s+bump|gestation\w*"
    r")\b",
    re.IGNORECASE,
)

_BREASTFEEDING = re.compile(
    r"\b("
    r"breast\s?-?\s?feed\w*|chest\s?-?\s?feed\w*|nursing|nurse\s+my\s+baby|"
    r"lactat\w*|express(?:ing)?\s+milk|breast\s+milk|postpartum|post-partum|"
    r"just\s+had\s+a\s+baby|newborn"
    r")\b",
    re.IGNORECASE,
)


# --- Age and children --------------------------------------------------------
# A bare number is not an age. These require age context around it, then check
# whether the number falls under the minimum.

_AGE_PATTERNS = (
    re.compile(r"\b(?:i\s*(?:'m|\s+am)|he\s+is|she\s+is|they\s+are|he's|she's)\s+(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:-|\s)?\s*(?:years?|yrs?|yo)\b(?:\s*-?\s*old)?", re.IGNORECASE),
    re.compile(r"\b(?:age[d]?|turning|turns)\s*:?\s*(\d{1,2})\b", re.IGNORECASE),
)

# A person other than the account holder, who is a child.
_CHILD_SUBJECT = re.compile(
    r"\b("
    r"my\s+(?:son|daughter|kid|kids|child|children|baby|toddler|infant|little\s+one)|"
    r"for\s+(?:my\s+)?(?:son|daughter|kid|child|baby|toddler)|"
    r"schoolchild|pre-?schooler"
    r")\b",
    re.IGNORECASE,
)


# --- Medication --------------------------------------------------------------
# Not a list of drugs. Generic drug names are *built* from WHO INN stems, which
# mark the pharmacological class, so matching the stems recognises medications
# the author never saw — including ones approved after this was written.

_INN_STEMS = (
    "formin", "olol", "pril", "sartan", "statin", "azole", "cillin", "mycin", "micin",
    "oxetine", "zepam", "zolam", "tidine", "prazole", "parin", "tinib", "mab", "vir",
    "dipine", "triptan", "glitazone", "gliptin", "flozin", "floxacin", "oxacin",
    "cycline", "codone", "morphone", "caine", "profen", "asone", "solone", "olone",
    "thyroxine", "dronate", "setron", "barbital", "ipramine", "triptyline", "azine",
    "idone", "apine", "terol", "curium", "tecan", "rubicin", "platin", "semide",
    "thiazide", "olimus", "sulfonamide", "cortisone", "peridol", "azepine",
)
_INN_STEM_PATTERN = re.compile(
    r"\b[a-z]{2,}(?:" + "|".join(_INN_STEMS) + r")\b",
    re.IGNORECASE,
)

# Ordinary words that end in a drug stem by coincidence. Without these the gate
# would hand off on "April" and "cholesterol", which is noise rather than safety.
_STEM_FALSE_FRIENDS = frozenset({
    "april", "cholesterol", "magazine", "imagine", "machine", "routine", "vaccine",
    "cuisine", "gasoline", "sunshine", "combine", "determine", "examine", "medicine",
    "genuine", "marine", "engine", "discipline", "baseline", "airline", "decline",
    "outline", "sardine", "caffeine", "protein", "casein", "gelatine", "cocaine",
})

# The shape of someone describing putting something in their body. Kept
# separate from "use", because "what moisturiser should I use" is the product's
# core question and must not read as evidence of medication.
_TAKING_FRAME = re.compile(
    r"\b(?:i|we|he|she|they)\s*(?:'m|'re|\s+am|\s+are|\s+is)?\s*"
    r"(?:take|takes|taking|took|started|starting|stopped|on)\b",
    re.IGNORECASE,
)
# "I use X" is far weaker evidence, so on its own it decides nothing. It counts
# only alongside a form word, and the strong and stem rules catch the rest.
_USING_FRAME = re.compile(
    r"\b(?:i|we|he|she|they)\s*(?:'m|'re|\s+am|\s+are|\s+is)?\s*(?:use|uses|using)\b",
    re.IGNORECASE,
)
_FORM_WORD = re.compile(
    r"\b(tablet|tablets|capsule|capsules|pill|pills|injection|injections|inhaler|"
    r"syrup|ointment|drops|patch|sachet|dose|doses|dosage)\b",
    re.IGNORECASE,
)
_STRONG_MEDICATION = re.compile(
    r"\b("
    r"prescri\w*|medication\w*|medicine\w*|\bmeds\b|pharmacy|pharmacist|chemist|"
    r"doctor\s+(?:put|has\s+put|started|placed)\s+me\s+on|"
    r"over[\s-]the[\s-]counter|antibiotic\w*|steroid\w*|inhaler|insulin|"
    r"blood\s+thinner\w*|painkiller\w*|contraceptive\w*|birth\s+control|"
    r"hormone\s+replacement|\bhrt\b|chemotherapy"
    r")\b",
    re.IGNORECASE,
)
_DOSE = re.compile(r"\b\d+\s?(?:mg|mcg|iu|ml|g)\b", re.IGNORECASE)

# Nutrition supplements this product exists to track. These clear the *weakest*
# frame only ("I take vitamin D"); a prescription word, a dose or a medication
# stem still hands off, whatever else is in the sentence.
_SUPPLEMENT_SUBJECT = re.compile(
    r"\b("
    r"vitamin\s?[a-k]?\d*|multivitamin\w*|vitamins|calcium|magnesium|zinc|"
    r"omega\s?-?\s?3|fish\s+oil|cod\s+liver\s+oil|protein|whey|creatine|collagen|"
    r"biotin|probiotic\w*|ashwagandha|turmeric|curcumin|shilajit|spirulina"
    r")\b",
    re.IGNORECASE,
)


# --- Conditions --------------------------------------------------------------
# Frames first: how somebody says a clinician has already named something.

_CONDITION_FRAME = re.compile(
    r"\b("
    r"diagnos\w*|"
    r"doctor\s+(?:said|says|told\s+me|thinks|reckons)|"
    r"(?:i|we)\s+(?:was|were|have\s+been|got)\s+told\s+(?:i|we)\s+ha(?:ve|d)|"
    r"(?:i|he|she|they)\s+suffers?\s+from|suffering\s+from|"
    r"my\s+(?:condition|illness|diagnosis)|"
    r"(?:i|he|she|they)\s+(?:have|has|had)\s+been\s+diagnos\w*|"
    r"tested\s+positive|"
    r"under\s+(?:a\s+)?(?:doctor|specialist|consultant)"
    r")\b",
    re.IGNORECASE,
)

# Named conditions, as a backstop for "I have PCOS" with no frame around it.
_NAMED_CONDITION = re.compile(
    r"\b("
    r"pcos|pcod|diabet\w*|thyroid|hypothyroid\w*|hyperthyroid\w*|hypertension|"
    r"eczema|psoriasis|rosacea|dermatitis|alopecia|vitiligo|lupus|asthma|epilep\w*|"
    r"arthritis|anaemia|anemia|migraine|endometriosis|fibroid\w*|"
    r"kidney\s+(?:disease|failure|stones)|liver\s+(?:disease|failure)|"
    r"heart\s+(?:condition|disease|failure)|cancer|tumour|tumor|"
    r"crohn\w*|colitis|coeliac|celiac|ibs\b|hiv\b|hepatitis|"
    r"depression|anxiety\s+disorder|bipolar|adhd|autoimmune"
    r")\b",
    re.IGNORECASE,
)


# --- Uncertainty -------------------------------------------------------------
# Medical framing with nothing specific attached. This is the rule that makes
# the gate fail closed rather than fail silent.

_MEDICAL_CONTEXT = re.compile(
    r"\b("
    r"doctor|gp\b|clinic|hospital|specialist|consultant|dermatologist|gynaecologist|"
    r"gynecologist|physician|nurse|treatment|therapy|surgery|operation|"
    r"side\s+effect\w*|interact\w*|contraindicat\w*|allergic|allergy|"
    r"bad\s+reaction|adverse\s+reaction|reacted\s+to|"
    r"is\s+(?:it|this)\s+safe|safe\s+to\s+(?:take|use|combine)|"
    r"can\s+i\s+(?:still\s+)?(?:take|use)|"
    r"blood\s+test|blood\s+pressure|blood\s+sugar|"
    r"my\s+health|health\s+issue|medical|unwell|"
    r"symptom\w*|flare[\s-]?up"
    r")\b",
    re.IGNORECASE,
)


def _stated_ages(text: str) -> list[int]:
    found: list[int] = []
    for pattern in _AGE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                found.append(int(match.group(1)))
            except (TypeError, ValueError):  # pragma: no cover - guarded by the pattern
                continue
    return found


def _mentions_medication(text: str) -> str | None:
    """Return the name of the rule that spotted a medication, or None."""
    if _STRONG_MEDICATION.search(text):
        return "medication_word"
    if _DOSE.search(text):
        return "dose"
    for match in _INN_STEM_PATTERN.finditer(text):
        if match.group(0).lower() not in _STEM_FALSE_FRIENDS:
            return "drug_name_stem"

    # A frame with no recognised object. If the object is a supplement this
    # product tracks, that is ordinary use; otherwise it is something being
    # taken that we cannot identify, which is exactly the uncertain case that
    # must hand off.
    if _SUPPLEMENT_SUBJECT.search(text):
        return None
    if _TAKING_FRAME.search(text):
        if _FORM_WORD.search(text):
            return "unidentified_medicine_form"
        return "unidentified_thing_taken"
    if _USING_FRAME.search(text) and _FORM_WORD.search(text):
        return "unidentified_medicine_form"
    return None


def evaluate(
    text: str | None = None,
    *,
    stated_age: int | None = None,
    subject_is_child: bool = False,
) -> HandoffDecision:
    """Decide whether this must be handed to a clinician.

    ``text`` is anything the person wrote. ``stated_age`` and ``subject_is_child``
    are structured context a caller already holds — a profile, an onboarding
    answer — and are trusted over the text.

    Ordered so the reason returned is the most specific one that applies.
    """
    if stated_age is not None and stated_age < MINIMUM_AGE:
        return _fires(HandoffReason.AGE_UNDER_MINIMUM, "structured_age")
    if subject_is_child:
        return _fires(HandoffReason.CHILD_SUBJECT, "structured_subject")

    body = (text or "").strip()
    if not body:
        return _ALLOWED

    # Pregnancy first: "9 months pregnant" carries a number that the age rules
    # would otherwise read as an age.
    if _PREGNANCY.search(body):
        return _fires(HandoffReason.PREGNANCY, "pregnancy_language")
    if _BREASTFEEDING.search(body):
        return _fires(HandoffReason.BREASTFEEDING, "breastfeeding_language")

    for age in _stated_ages(body):
        if age < MINIMUM_AGE:
            return _fires(HandoffReason.AGE_UNDER_MINIMUM, "stated_age")
    if _CHILD_SUBJECT.search(body):
        return _fires(HandoffReason.CHILD_SUBJECT, "child_subject")

    medication_signal = _mentions_medication(body)
    if medication_signal is not None:
        return _fires(HandoffReason.MEDICATION, medication_signal)

    if _CONDITION_FRAME.search(body):
        return _fires(HandoffReason.CLINICAL_CONDITION, "condition_frame")
    if _NAMED_CONDITION.search(body):
        return _fires(HandoffReason.CLINICAL_CONDITION, "named_condition")

    # Nothing specific matched, but the text is medical in shape. Hand off.
    if _MEDICAL_CONTEXT.search(body):
        return _fires(HandoffReason.UNCERTAIN, "medical_context")

    return _ALLOWED


def requires_handoff(
    text: str | None = None,
    *,
    stated_age: int | None = None,
    subject_is_child: bool = False,
) -> bool:
    """Convenience wrapper for callers that only need the yes/no."""
    return evaluate(text, stated_age=stated_age, subject_is_child=subject_is_child).handoff
