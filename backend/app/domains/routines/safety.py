"""The safety boundary for Phase 6.

This is the file that keeps a styling app from drifting into being a medical
one. It is deliberately blunt, and it fails closed.

Four things it enforces:

1. **No diagnosis, ever.** The product may say what a product *is* and what a
   user *told us*. It may never name a condition, assert a deficiency, or
   describe anything as a treatment or cure.

2. **No supplement dosage.** Supplements are inventory. The app records what
   you own and when it expires. It does not say how much to take, when to start
   one, or what it will do for you — that is a conversation with a qualified
   professional, and the boundary below says so.

3. **No calorie counting, no deficiency claims.** Nutrition here is
   appearance-adjacent food context, and nothing else.

4. **The model cannot route around any of this.** ``narrative_is_safe`` is
   applied to every piece of AI-written text before it is stored or shown, and
   a failure discards the wording — never the deterministic result underneath.

When a user genuinely needs a clinician, they get
:data:`PROFESSIONAL_BOUNDARY` rather than a worse answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domains.nutrition.safety import NUTRITION_DISCLAIMER  # noqa: F401

PROFESSIONAL_BOUNDARY = (
    "This is outside what GlamGenius can help with. We track what you own and how you use it — "
    "we are not able to look at symptoms, conditions or medicines. Please talk to a doctor, "
    "dermatologist or pharmacist about this one."
)

SUPPLEMENT_DISCLAIMER = (
    "Inventory tracking only. GlamGenius does not provide supplement dosage or medical advice. "
    "Follow the product label and guidance from a qualified professional."
)

# Worded carefully. The obvious phrasing — "not calorie tracking, not a way of
# diagnosing anything" — trips the banned-term sweep below, and weakening the
# sweep so a disclaimer can pass would weaken it for everything else. The
# promise is the same; the wording just avoids naming what it rules out.
ROUTINE_DISCLAIMER = (
    "A routine built from products you told us you own. Not medical advice, and not a treatment plan."
)


# --- Language that must never appear in generated output --------------------
# Two groups. Diagnostic language names or asserts a condition. Prescriptive
# language tells someone to take, stop or dose something.

DIAGNOSTIC_TERMS: tuple[str, ...] = (
    "you have ", "diagnos", "your condition", "symptom of", "suffering from",
    "eczema", "psoriasis", "rosacea", "dermatitis", "fungal acne", "seborrheic",
    "alopecia", "folliculitis", "infection", "disease", "disorder", "syndrome",
    "deficiency", "deficient in", "hormonal imbalance", "pcos", "thyroid",
    "cure", "cures", "treats ", "treatment for", "heal your", "clinically proven to treat",
    "medicated", "prescription strength", "anti-fungal", "antifungal treatment",
)

PRESCRIPTIVE_TERMS: tuple[str, ...] = (
    "mg per day", "mg daily", "iu per day", "iu daily", "mcg per day", "mcg daily",
    "take one tablet", "take two tablets", "take a tablet", "take one capsule",
    "twice daily dose", "recommended dosage", "recommended dose", "your dosage",
    "start taking", "you should take", "begin supplementing", "stop taking",
    "increase your dose", "decrease your dose", "double the dose",
)

# Appearance-scoring and body-shaming wording is banned across the whole product
# (Phases 1 and 4); repeated here so Phase 6 output is swept by the same list.
APPEARANCE_TERMS: tuple[str, ...] = (
    "money wasted", "slimming", "flattering for your body", "problem area",
    "problem areas", "body type", "lose weight", "gain weight", "attractiveness score",
    "beauty score", "overall score", "calorie", "calories", "kcal", "bmi",
)

BANNED_TERMS: tuple[str, ...] = DIAGNOSTIC_TERMS + PRESCRIPTIVE_TERMS + APPEARANCE_TERMS

# Dosage written as a bare quantity, which the term list alone would miss.
_DOSE_PATTERN = re.compile(
    r"\b\d+\s?(mg|mcg|iu|g|ml)\b(?!\s*(bottle|tube|jar|pack|size|container))",
    re.IGNORECASE,
)


def narrative_is_safe(text: str | None) -> bool:
    """True when this text may be shown to a user.

    Applied to every AI-written string and to the deterministic strings too —
    the rules that generate them are reviewed, but a sweep costs nothing and
    catches a careless edit later.
    """
    lowered = (text or "").lower()
    if any(term in lowered for term in BANNED_TERMS):
        return False
    return not _DOSE_PATTERN.search(lowered)


def first_violation(text: str | None) -> str | None:
    """Which term tripped the check. For logs and tests, never for users."""
    lowered = (text or "").lower()
    for term in BANNED_TERMS:
        if term in lowered:
            return term
    match = _DOSE_PATTERN.search(lowered)
    return match.group(0) if match else None


# --- Questions that need a professional --------------------------------------
# Matched on the user's own words. Deliberately generous: a false positive shows
# a polite boundary, a false negative lets the product answer something it has
# no business answering.

_MEDICAL_QUESTION_PATTERNS: tuple[str, ...] = (
    r"\bdiagnos\w*", r"\bprescri\w*", r"\bdosage\b", r"\bdose\b", r"\bhow much (should|do) i take\b",
    r"\bis (this|it) safe (to take|with|during)\b", r"\bcan i take\b", r"\bshould i take\b",
    r"\binteract\w* with\b", r"\bmedication\b", r"\bmedicine\b", r"\btablet\b", r"\bantibiotic\b",
    r"\bpregnan\w*", r"\bbreastfeed\w*", r"\bnursing\b",
    r"\beczema\b", r"\bpsoriasis\b", r"\brosacea\b", r"\bdermatitis\b", r"\balopecia\b",
    r"\bhair (loss|fall)\b", r"\bbald\w*", r"\binfection\b", r"\brash\b", r"\bbleeding\b",
    r"\bpcos\b", r"\bthyroid\b", r"\bdeficien\w*", r"\bblood test\b", r"\bside effect\b",
    r"\ballergic reaction\b", r"\bswelling\b", r"\bcure\b", r"\btreat(ment)? for\b",
)

_MEDICAL_QUESTION = re.compile("|".join(_MEDICAL_QUESTION_PATTERNS), re.IGNORECASE)


def needs_professional(text: str | None) -> bool:
    """True when a question belongs with a clinician, not with this app."""
    return bool(_MEDICAL_QUESTION.search(text or ""))


@dataclass
class BoundaryResponse:
    """What we return instead of answering something we should not."""

    boundary: bool
    message: str
    matched: str | None = None

    def as_dict(self) -> dict:
        return {
            "boundary": self.boundary,
            "message": self.message,
            "can_help_with": [
                "What you own, and when it runs out",
                "The order to use your products in",
                "Which of your products overlap",
                "Food ideas related to how skin and hair look",
            ],
        }


def boundary_for(text: str | None) -> BoundaryResponse | None:
    match = _MEDICAL_QUESTION.search(text or "")
    if match is None:
        return None
    return BoundaryResponse(boundary=True, message=PROFESSIONAL_BOUNDARY, matched=match.group(0))


# --- Supplements -------------------------------------------------------------
# The only things Phase 6 is allowed to say about a supplement.

SUPPLEMENT_ALLOWED_FACTS = (
    "name", "brand", "user_entered_purpose", "expiry_date", "opened_date",
    "use_frequency", "label_information",
)

SUPPLEMENT_FLAG_EXPIRING = "expiring"
SUPPLEMENT_FLAG_EXPIRED = "expired"
SUPPLEMENT_FLAG_NO_EXPIRY = "no_expiry_recorded"
SUPPLEMENT_FLAG_PROFESSIONAL = "professional_question"

SUPPLEMENT_FLAG_TEXT = {
    SUPPLEMENT_FLAG_EXPIRED: "This is past the date you recorded. Replace it rather than using it.",
    SUPPLEMENT_FLAG_EXPIRING: "This is close to the date you recorded.",
    SUPPLEMENT_FLAG_NO_EXPIRY: "No expiry date recorded, so we cannot tell you how long it has left.",
    SUPPLEMENT_FLAG_PROFESSIONAL: (
        "You noted a health reason for this one. We track it as inventory only — "
        "anything about whether, when or how much to take belongs with a qualified professional."
    ),
}


def supplement_response_is_clean(payload: str) -> bool:
    """A supplement response must carry no dosage and no claimed effect."""
    return narrative_is_safe(payload)
