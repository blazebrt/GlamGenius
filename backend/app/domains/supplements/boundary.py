"""The professional boundary for supplement questions.

A supplement question is never answered here. The only choice this module makes
is whether to show the professional boundary, and it makes it in three steps,
in this order:

1. **The hard handoff gate** (``routines.hard_handoff.evaluate``). The
   constitution says a feature in hard-handoff territory satisfies the rule only
   when it *calls* the gate, and supplements are squarely in that territory:
   pregnancy, breastfeeding, children, medicines and diagnosed conditions. Its
   decision is never overridden, softened or second-guessed here.
2. **The narrow medical-question check** (``routines.safety.needs_professional``),
   unchanged.
3. **Supplement decisions** — the handful of question shapes neither of the
   above was written to catch, each of which asks GlamGenius to make a health
   decision it does not make: whether to start or stop, whether it is needed,
   how many, too much, what a lab result means, a condition described in
   ordinary words, a reaction, and which one to buy. These patterns exist
   because the first two demonstrably miss them (see
   ``tests/test_step13_supplements.py``); they are not a second copy of either.

A false boundary costs a sentence. A missed one lets a supplement surface answer
a health question, so every uncertain case resolves toward the boundary.

Privacy: the result names the rule family that fired, never the words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domains.routines.hard_handoff import evaluate as evaluate_hard_handoff
from app.domains.routines.safety import needs_professional
from app.domains.supplements import strings as copy

# The words are GlamGenius's own and live in ``strings.py`` under their keys,
# none of it use guidance: no order of use, no timing, no "when to take", no
# combinations, no food pairing and no effect. The generic routine boundary's
# alternatives ("the order to use your products in", food ideas) are right for
# skin care and wrong here, which is why the supplement boundary carries its own.

#: Shown when nothing fired. It answers nothing either; it says what is kept.
NO_BOUNDARY_MESSAGE = copy.text("supplement.boundary.none")

#: The professional boundary in supplement terms.
SUPPLEMENT_PROFESSIONAL_BOUNDARY = copy.text("supplement.boundary.professional")

#: What the supplement surface can do instead: records and labels, nothing about use.
CAN_HELP_WITH = tuple(copy.text(key) for key in copy.CAN_HELP_WITH_KEYS)

# Question shapes that ask for a supplement health decision. Matched on the
# person's own words, case-insensitively. Deliberately generous.
_SUPPLEMENT_DECISION_PATTERNS: tuple[tuple[str, str], ...] = (
    # Whether to start, stop, keep going, or whether it is needed at all.
    ("start_or_stop", r"\b(?:should|can|could|may|must)\s+(?:i|we|he|she|they)\s+(?:start|stop|quit|begin|continue|keep|pause|restart|switch)\b"),
    ("start_or_stop", r"\b(?:start|stop|quit|begin|continue|keep|pause)\s+(?:taking|using|having)\b"),
    ("need", r"\bdo\s+(?:i|we|you)\s+(?:really\s+)?need\b"),
    ("need", r"\b(?:is|are)\s+(?:it|this|these|they)\s+(?:worth|necessary|needed|required|enough)\b"),
    # How many, how often, too much.
    ("amount", r"\bhow\s+(?:many|much|often)\b"),
    ("amount", r"\bover\s*-?\s*dos\w*\b"),
    ("amount", r"\btoo\s+(?:much|many|high|strong|often)\b"),
    ("amount", r"\b(?:safe|upper|daily|maximum|max)\s+(?:limit|dose|amount|intake)\b"),
    # Deficiency and results described in ordinary words.
    ("deficiency", r"\b(?:low|lacking|short)\s+(?:in|on)\b"),
    ("deficiency", r"\bmy\s+[\w\s-]{0,24}?\b(?:is|are|was|were|came\s+back)\s+(?:low|high|normal|borderline)\b"),
    ("deficiency", r"\b(?:is|are)\s+my\s+[\w\s-]{0,24}?\b(?:low|high|normal|borderline)\b"),
    ("deficiency", r"\bdo\s+i\s+have\s+(?:low|high|enough)\b"),
    ("lab_result", r"\b(?:lab|test|blood|urine)\s+(?:result|results|report|reports|work|value|values|level|levels)\b"),
    ("lab_result", r"\b(?:ferritin|haemoglobin|hemoglobin|hba1c|tsh|serum|vitamin\s+d\s+level|b12\s+level)\b"),
    # Conditions, symptoms and reactions described in ordinary words.
    ("condition", r"\b(?:disease|condition|illness|disorder|syndrome|infection|problem\s+with\s+my|heart\s+problem|kidney|liver(?!\s+oil))\b"),
    ("reaction", r"\b(?:reaction|reacted|side\s*-?\s*effects?|felt\s+(?:sick|ill|dizzy|unwell)|feel\s+(?:sick|ill|dizzy|unwell)|nausea|vomit\w*|headache|stomach\s+(?:ache|pain|upset)|rash|itch\w*|hives)\b"),
    # Children, however they are named.
    ("child", r"\b(?:kid|kids|child|children|toddler|toddlers|infant|infants|baby|babies|teen|teenager|son|daughter)\b"),
    # Help with, cure, treat, prevent: an efficacy question in disguise.
    ("efficacy", r"\b(?:help|helps|good|work|works|effective)\s+(?:with|for|against)\b"),
    ("efficacy", r"\b(?:prevent|prevents|treat|treats|cure|cures|heal|heals|boost|boosts)\b"),
    # Which one to buy, which is best: a recommendation GlamGenius does not make.
    ("recommendation", r"\bwhich\s+(?:one|supplement|brand|form|product)?\s*(?:should|is|to)\b"),
    ("recommendation", r"\b(?:best|better|recommend\w*|suggest\w*)\b"),
    ("recommendation", r"\bshould\s+i\s+(?:buy|get|try|choose|pick)\b"),
)

_COMPILED = tuple((family, re.compile(pattern, re.IGNORECASE)) for family, pattern in _SUPPLEMENT_DECISION_PATTERNS)


@dataclass(frozen=True)
class SupplementBoundary:
    """Whether to show the boundary, and which rule family decided."""

    boundary: bool
    #: ``hard_handoff:<reason>``, ``medical_question`` or ``supplement_decision:<family>``.
    rule: str | None = None
    message: str = NO_BOUNDARY_MESSAGE

    def as_dict(self) -> dict:
        if not self.boundary:
            return {"boundary": False, "message": self.message}
        return {
            "boundary": True,
            "reason": self.rule,
            "message": self.message,
            "can_help_with": list(CAN_HELP_WITH),
        }


def supplement_decision_family(text: str | None) -> str | None:
    """The supplement-decision family this text matches, or None."""
    body = text or ""
    for family, pattern in _COMPILED:
        if pattern.search(body):
            return family
    return None


def evaluate(
    text: str | None,
    *,
    question: bool = True,
    stated_age: int | None = None,
    subject_is_child: bool = False,
) -> SupplementBoundary:
    """Decide whether supplement text gets the professional boundary.

    ``question=False`` is for a person's own inventory note ("for hair"),
    which is a record rather than a request. The gate and the medical check
    still apply to it in full; only the question-shape patterns are skipped,
    because "better sleep" written as a note is not a request to rank products.
    """
    handoff = evaluate_hard_handoff(text, stated_age=stated_age, subject_is_child=subject_is_child)
    if handoff.handoff:
        return SupplementBoundary(True, f"hard_handoff:{handoff.reason}", handoff.message)
    if needs_professional(text):
        return SupplementBoundary(True, "medical_question", SUPPLEMENT_PROFESSIONAL_BOUNDARY)
    if question:
        family = supplement_decision_family(text)
        if family is not None:
            return SupplementBoundary(True, f"supplement_decision:{family}", SUPPLEMENT_PROFESSIONAL_BOUNDARY)
    return SupplementBoundary(False)


def requires_boundary(text: str | None, *, question: bool = True) -> bool:
    return evaluate(text, question=question).boundary


__all__ = [
    "CAN_HELP_WITH",
    "NO_BOUNDARY_MESSAGE",
    "SUPPLEMENT_PROFESSIONAL_BOUNDARY",
    "SupplementBoundary",
    "evaluate",
    "requires_boundary",
    "supplement_decision_family",
]
