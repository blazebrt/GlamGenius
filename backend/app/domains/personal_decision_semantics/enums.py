"""Controlled Step 8C decision-semantic vocabulary.

Two words, and they are narrower than they look. They describe what a reviewed
rule *permits an already-applicable claim to contribute* to a later decision
process. They are not a judgement about the product, the ingredient, or the
person.
"""

from enum import StrEnum


class PersonalDecisionSignal(StrEnum):
    """The direction an exact reviewed rule assigns to an exact claim version.

    SUPPORTING and CAUTIONARY do not mean safe, unsafe, healthy, unhealthy,
    good, bad, suitable, unsuitable, recommended, buy, skip or avoid. They mean
    only that a reviewed mapping permits this claim to contribute in that
    direction to a future decision process that does not exist yet.
    """

    SUPPORTING = "supporting"
    CAUTIONARY = "cautionary"


class PersonalDecisionSemanticStatus(StrEnum):
    """Whether an exact reviewed mapping exists for an exact claim version.

    NOT_ENOUGH_DECISION_SEMANTICS is the fail-closed default: an applicable
    claim with no reviewed mapping contributes nothing, rather than being
    guessed at from its prose, its evidence strength or its sources.
    """

    SEMANTICS_AVAILABLE = "semantics_available"
    NOT_ENOUGH_DECISION_SEMANTICS = "not_enough_decision_semantics"
