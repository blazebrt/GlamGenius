"""Step 7C evidence vocabulary for category-specific substance interpretation.

No interpretation rows or cache tables are created. The migration only widens
the two existing evidence vocabularies used by the read-only projection layer.

Revision ID: a7b8c9d0e1
Revises: z6a7b8c9d0
"""
from __future__ import annotations

from alembic import op

revision = "a7b8c9d0e1"
down_revision = "z6a7b8c9d0"
branch_labels = None
depends_on = None


def _in_list(column: str, values: tuple[str, ...]) -> str:
    literal = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({literal})"


_OLD_DOMAINS = (
    "skin_care", "hair_care", "home_care", "nutrition", "supplements",
    "product_quality", "substance",
)
_NEW_DOMAINS = (*_OLD_DOMAINS, "cosmetics")

_OLD_CLAIM_TYPES = (
    "compatibility_context", "contraindication_context", "sensitivity_context",
    "usage_context", "regulatory_context", "nutrition_reference",
    "product_provenance", "traditional_use", "substance_identity",
)
_NEW_CLAIM_TYPES = (*_OLD_CLAIM_TYPES, "substance_category_interpretation")


def upgrade() -> None:
    for table, constraint, column, values in (
        ("evidence_claims", "ck_evidence_claims_domain", "domain", _NEW_DOMAINS),
        ("rule_evidence_links", "ck_rule_evidence_links_domain", "domain", _NEW_DOMAINS),
        ("evidence_claims", "ck_evidence_claims_claim_type", "claim_type", _NEW_CLAIM_TYPES),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _in_list(column, values))


def downgrade() -> None:
    # Old vocabularies have no truthful equivalent for a Step 7C claim. Remove
    # only rows that depend on the values being narrowed, clearing every
    # RESTRICT reference by exact claim id before restoring the old checks.
    step7c_claims = (
        "SELECT id FROM evidence_claims "
        "WHERE domain = 'cosmetics' "
        "OR claim_type = 'substance_category_interpretation'"
    )
    op.execute(f"DELETE FROM evidence_claim_sources WHERE claim_id IN ({step7c_claims})")
    op.execute(
        "DELETE FROM rule_evidence_links "
        f"WHERE domain = 'cosmetics' OR claim_id IN ({step7c_claims})"
    )
    op.execute(
        "UPDATE evidence_claims SET supersedes_claim_id = NULL "
        f"WHERE supersedes_claim_id IN ({step7c_claims})"
    )
    op.execute(
        "DELETE FROM evidence_claims "
        "WHERE domain = 'cosmetics' "
        "OR claim_type = 'substance_category_interpretation'"
    )

    for table, constraint, column, values in (
        ("evidence_claims", "ck_evidence_claims_claim_type", "claim_type", _OLD_CLAIM_TYPES),
        ("rule_evidence_links", "ck_rule_evidence_links_domain", "domain", _OLD_DOMAINS),
        ("evidence_claims", "ck_evidence_claims_domain", "domain", _OLD_DOMAINS),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _in_list(column, values))
