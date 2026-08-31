"""Allow the environment_response rule kind on evidence rule links.

The ten environment rules resolve under their own rule_kind so their evidence
links are distinguishable from routine guidance. The column carries a check
constraint, so widening the vocabulary is a schema change.

Revision ID: q7r8s9t0u1
Revises: p6q7r8s9t0
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'q7r8s9t0u1'
down_revision: Union[str, None] = 'p6q7r8s9t0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_rule_evidence_links_kind"
TABLE = "rule_evidence_links"
KINDS_WITH_ENVIRONMENT = ("'ingredient_compatibility', 'ingredient_contraindication', 'ingredient_sensitivity', 'routine_guidance', 'environment_response', 'nutrition_context', 'supplement_context'")
KINDS_WITHOUT_ENVIRONMENT = ("'ingredient_compatibility', 'ingredient_contraindication', 'ingredient_sensitivity', 'routine_guidance', 'nutrition_context', 'supplement_context'")


def _replace(values: str) -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"rule_kind IN ({values})")


def upgrade() -> None:
    _replace(KINDS_WITH_ENVIRONMENT)


def downgrade() -> None:
    # Links using the new kind would violate the narrower constraint, so they
    # go first. They are reference data and the seed rewrites them.
    op.execute(
        "DELETE FROM rule_evidence_links WHERE rule_kind = 'environment_response'"
    )
    _replace(KINDS_WITHOUT_ENVIRONMENT)
