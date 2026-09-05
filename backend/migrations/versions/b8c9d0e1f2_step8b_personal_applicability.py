"""Step 8B evidence claim vocabulary for personal applicability.

No result rows, cache tables, columns, or indexes are created. The migration
only widens the existing controlled claim-type CHECK vocabulary.

Revision ID: b8c9d0e1f2
Revises: a7b8c9d0e1
"""
from __future__ import annotations

from alembic import op

revision = "b8c9d0e1f2"
down_revision = "a7b8c9d0e1"
branch_labels = None
depends_on = None


def _in_list(column: str, values: tuple[str, ...]) -> str:
    literal = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({literal})"


_OLD_CLAIM_TYPES = (
    "compatibility_context", "contraindication_context", "sensitivity_context",
    "usage_context", "regulatory_context", "nutrition_reference",
    "product_provenance", "traditional_use", "substance_identity",
    "substance_category_interpretation",
)
_NEW_CLAIM_TYPES = (*_OLD_CLAIM_TYPES, "substance_personal_applicability")


def upgrade() -> None:
    op.drop_constraint("ck_evidence_claims_claim_type", "evidence_claims", type_="check")
    op.create_check_constraint(
        "ck_evidence_claims_claim_type",
        "evidence_claims",
        _in_list("claim_type", _NEW_CLAIM_TYPES),
    )


def downgrade() -> None:
    # The old vocabulary has no truthful equivalent. Remove only Step 8B
    # claims and exact references to them before narrowing the CHECK.
    step8b_claims = (
        "SELECT id FROM evidence_claims "
        "WHERE claim_type = 'substance_personal_applicability'"
    )
    op.execute(f"DELETE FROM evidence_claim_sources WHERE claim_id IN ({step8b_claims})")
    op.execute(f"DELETE FROM rule_evidence_links WHERE claim_id IN ({step8b_claims})")
    op.execute(
        "UPDATE evidence_claims SET supersedes_claim_id = NULL "
        f"WHERE supersedes_claim_id IN ({step8b_claims})"
    )
    op.execute("DELETE FROM evidence_claims WHERE claim_type = 'substance_personal_applicability'")
    op.drop_constraint("ck_evidence_claims_claim_type", "evidence_claims", type_="check")
    op.create_check_constraint(
        "ck_evidence_claims_claim_type",
        "evidence_claims",
        _in_list("claim_type", _OLD_CLAIM_TYPES),
    )
