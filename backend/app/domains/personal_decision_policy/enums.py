"""Controlled Step 8E policy vocabulary.

The action words enter the backend here. That is the whole weight of this
module: `buy`, `wait` and `skip` are the first machine-readable product
actions in the governed personal chain, and every one of them must come from
an explicitly reviewed policy rule rather than from anything this code infers.
"""

from enum import StrEnum


class PersonalDecisionAction(StrEnum):
    """A reviewed product action. Never derived, only looked up.

    WAIT is the one most likely to be abused. It is a real reviewed action --
    a reviewer decided this exact governed state warrants waiting -- and it is
    never the answer to uncertainty. Missing context, an unparsed formula, an
    unmapped claim or an absent policy all mean the system was not entitled to
    decide, which is NOT_ENOUGH_INFORMATION, not WAIT.
    """

    BUY = "buy"
    WAIT = "wait"
    SKIP = "skip"


class PersonalDecisionPolicyStatus(StrEnum):
    """Whether a decision was permitted, and if not, which kind of "no".

    The three negative statuses are deliberately distinct. Collapsing them
    would lose the difference between "we must not decide", "nobody has
    decided this yet" and "a professional should be involved" -- three
    situations that a later layer must handle in completely different ways.
    """

    DECISION_AVAILABLE = "decision_available"
    NOT_ENOUGH_INFORMATION = "not_enough_information"
    NOT_ENOUGH_DECISION_POLICY = "not_enough_decision_policy"
    HANDOFF_REQUIRED = "handoff_required"


class PersonalDecisionPolicyReason(StrEnum):
    """Which structural gate stopped the decision. Machine-readable only.

    These are not user-facing sentences and must never be shown as copy.
    Step 8F owns the explanation contract; this vocabulary exists so that
    layer knows exactly what happened without re-deriving it.
    """

    PROFESSIONAL_HANDOFF_REQUIRED = "professional_handoff_required"
    PERSONAL_CONTEXT_NOT_COMPLETE = "personal_context_not_complete"
    FORMULA_NOT_PROJECTABLE = "formula_not_projectable"
    SEMANTIC_MAPPING_NOT_COMPLETE = "semantic_mapping_not_complete"
    NO_EXACT_POLICY_RULE = "no_exact_policy_rule"


class PersonalDecisionPolicyCategory(StrEnum):
    """Step 8E's own category vocabulary, on purpose.

    These strings match Step 8B's category values, and the duplication is
    deliberate rather than lazy: a policy target must not be keyed on an
    enumeration that a lower domain is free to extend. Conversion happens by
    explicit string value, and an unrecognised category is an invariant error
    rather than a silently unmatched policy.
    """

    PACKAGED_FOOD = "packaged_food"
    SKIN_CARE = "skin_care"
    HAIR_CARE = "hair_care"
    COSMETICS = "cosmetics"
