"""Controlled Step 8F presentation vocabulary.

Nothing here is customer copy. These are machine-readable states describing
whether a decision may be shown at all and, if not, which governed gate
stopped it. The sentences a customer eventually reads are resolved from copy
keys by a later API/frontend milestone.
"""

from enum import StrEnum


class PersonalDecisionPresentationStatus(StrEnum):
    """Whether a reviewed decision may actually reach the customer.

    Step 8F is deliberately stricter than Step 8E. A reviewed action existing
    inside the policy layer is not sufficient: without a reviewed reason and a
    named openable source, showing it would be an unsourced claim, so it is
    withheld as NOT_ENOUGH_EXPLANATION.

    No status here ever becomes WAIT. WAIT is a reviewed product action; every
    state below is an absence of one.
    """

    DECISION_PRESENTABLE = "decision_presentable"
    NOT_ENOUGH_INFORMATION = "not_enough_information"
    NOT_ENOUGH_DECISION_POLICY = "not_enough_decision_policy"
    NOT_ENOUGH_EXPLANATION = "not_enough_explanation"
    HANDOFF_REQUIRED = "handoff_required"


class PersonalDecisionPresentationReason(StrEnum):
    """Which governed gate produced the presentation state.

    These describe the state of the system, never the product or the person.
    There is deliberately no SAFE, UNSAFE, HEALTHY, GOOD, BAD, TOXIC,
    RECOMMENDED or AVOID: a reason for withholding a decision is not a verdict
    about the thing we declined to judge.
    """

    PROFESSIONAL_HANDOFF_REQUIRED = "professional_handoff_required"
    PERSONAL_CONTEXT_NOT_COMPLETE = "personal_context_not_complete"
    FORMULA_NOT_PROJECTABLE = "formula_not_projectable"
    SEMANTIC_MAPPING_NOT_COMPLETE = "semantic_mapping_not_complete"
    NO_EXACT_DECISION_POLICY = "no_exact_decision_policy"
    NO_EXACT_EXPLANATION_RULE = "no_exact_explanation_rule"
    EXPLANATION_SOURCE_NOT_AVAILABLE = "explanation_source_not_available"
    REVIEWED_EXPLANATION_AVAILABLE = "reviewed_explanation_available"
