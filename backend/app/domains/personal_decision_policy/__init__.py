"""Step 8E governed personal decision policy.

The first layer permitted to carry a machine-readable BUY / WAIT / SKIP, and
the layer most concerned with refusing to. An action comes only from an
explicitly reviewed policy rule keyed to the exact set of Step 8C semantic
rule identities and versions, the direction set, the category and the upstream
epistemic gap flags. The production registry is empty in V1, so production
emits no action at all.

WAIT is a reviewed product action, never a stand-in for uncertainty. Handoff,
incomplete personal context, an unparsed formula and unmapped semantics each
produce their own explicit non-decision instead.
"""

from app.domains.personal_decision_policy.enums import (
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
    PersonalDecisionPolicyReason,
    PersonalDecisionPolicyStatus,
)
from app.domains.personal_decision_policy.rules import (
    PERSONAL_DECISION_POLICY_RULES,
    PersonalDecisionPolicyRegistryError,
    PersonalDecisionPolicyRule,
    PolicyTarget,
    SemanticRuleIdentity,
    build_policy_index,
)
from app.domains.personal_decision_policy.service import (
    PersonalDecisionPolicyInvariantError,
    PersonalDecisionPolicyResult,
    evaluate_personal_decision_policy,
)

__all__ = [
    "PERSONAL_DECISION_POLICY_RULES",
    "PersonalDecisionAction",
    "PersonalDecisionPolicyCategory",
    "PersonalDecisionPolicyInvariantError",
    "PersonalDecisionPolicyReason",
    "PersonalDecisionPolicyRegistryError",
    "PersonalDecisionPolicyResult",
    "PersonalDecisionPolicyRule",
    "PersonalDecisionPolicyStatus",
    "PolicyTarget",
    "SemanticRuleIdentity",
    "build_policy_index",
    "evaluate_personal_decision_policy",
]
