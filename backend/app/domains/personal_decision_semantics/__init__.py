"""Step 8C governed personal decision semantics.

A pure deterministic mapping from an exact Step 8B applicable claim version to
an explicit reviewed SUPPORTING or CAUTIONARY direction. No prose parsing, no
evidence-strength arithmetic, no aggregation, no score, no verdict, and no
database, network or AI access of any kind.
"""

from app.domains.personal_decision_semantics.enums import (
    PersonalDecisionSemanticStatus,
    PersonalDecisionSignal,
)
from app.domains.personal_decision_semantics.rules import (
    PERSONAL_DECISION_SEMANTIC_RULES,
    PersonalDecisionSemanticRegistryError,
    PersonalDecisionSemanticRule,
    RuleTarget,
    build_rule_index,
)
from app.domains.personal_decision_semantics.service import (
    ClaimDecisionSemanticProjection,
    IngredientDecisionSemantics,
    LabelSnapshotPersonalDecisionSemantics,
    project_personal_decision_semantics,
)

__all__ = [
    "PERSONAL_DECISION_SEMANTIC_RULES",
    "ClaimDecisionSemanticProjection",
    "IngredientDecisionSemantics",
    "LabelSnapshotPersonalDecisionSemantics",
    "PersonalDecisionSemanticRegistryError",
    "PersonalDecisionSemanticRule",
    "PersonalDecisionSemanticStatus",
    "PersonalDecisionSignal",
    "RuleTarget",
    "build_rule_index",
    "project_personal_decision_semantics",
]
