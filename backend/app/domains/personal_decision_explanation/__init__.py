"""Step 8F governed personal decision explanation and source contract.

The last governed layer before an API or a screen. It answers whether a
reviewed Step 8E decision may actually be shown, which reviewed reason
accompanies it, and which exact already-eligible openable source supports
that reason.

No reviewed explanation and source means no presentable BUY / WAIT / SKIP:
Step 8F withholds an action Step 8E already selected rather than show an
unsourced claim. It writes no prose, chooses no source, derives no action, and
the production explanation registry is empty in V1.
"""

from app.domains.personal_decision_explanation.enums import (
    PersonalDecisionPresentationReason,
    PersonalDecisionPresentationStatus,
)
from app.domains.personal_decision_explanation.rules import (
    PERSONAL_DECISION_EXPLANATION_RULES,
    ExplanationTarget,
    PersonalDecisionExplanationRegistryError,
    PersonalDecisionExplanationRule,
    build_explanation_index,
)
from app.domains.personal_decision_explanation.service import (
    REASON_KEY_DECISION_POLICY,
    REASON_KEY_EXPLANATION,
    REASON_KEY_FORMULA,
    REASON_KEY_HANDOFF,
    REASON_KEY_PERSONAL_CONTEXT,
    REASON_KEY_SEMANTIC_MAPPING,
    PersonalDecisionPresentation,
    PersonalDecisionPresentationInvariantError,
    PersonalDecisionSourceCitation,
    present_personal_decision,
)

__all__ = [
    "PERSONAL_DECISION_EXPLANATION_RULES",
    "REASON_KEY_DECISION_POLICY",
    "REASON_KEY_EXPLANATION",
    "REASON_KEY_FORMULA",
    "REASON_KEY_HANDOFF",
    "REASON_KEY_PERSONAL_CONTEXT",
    "REASON_KEY_SEMANTIC_MAPPING",
    "ExplanationTarget",
    "PersonalDecisionExplanationRegistryError",
    "PersonalDecisionExplanationRule",
    "PersonalDecisionPresentation",
    "PersonalDecisionPresentationInvariantError",
    "PersonalDecisionPresentationReason",
    "PersonalDecisionPresentationStatus",
    "PersonalDecisionSourceCitation",
    "build_explanation_index",
    "present_personal_decision",
]
