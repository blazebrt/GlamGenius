"""Evidence authoring tool: tiers, values, notes and the publish/reject states.

Additive. Existing claims keep their review_status and gain NULL authoring
fields, which is exactly what a claim written before the tool looks like.

The review_status check constraint has to be replaced rather than altered:
PostgreSQL cannot widen an IN list in place, so the old constraint is dropped
and a new one covering published and rejected is created.

Revision ID: m3n4o5p6q7
Revises: l2m3n4o5p6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m3n4o5p6q7"
down_revision: Union[str, None] = "l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_REVIEW_STATUSES = ("draft", "reviewed", "approved", "superseded", "retired")
_NEW_REVIEW_STATUSES = _OLD_REVIEW_STATUSES + ("published", "rejected")
_TIERS = (
    "clinically_studied", "classical_text", "traditional_use",
    "not_enough_information", "avoid",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return "%s IN (%s)" % (column, ", ".join("'%s'" % value for value in values))


def upgrade() -> None:
    op.add_column("evidence_claims", sa.Column("evidence_tier", sa.String(length=32), nullable=True))
    op.add_column("evidence_claims", sa.Column("value_text", sa.String(length=256), nullable=True))
    op.add_column("evidence_claims", sa.Column("value_unit", sa.String(length=64), nullable=True))
    op.add_column("evidence_claims", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("evidence_claims", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("evidence_claims", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("evidence_claims", sa.Column("published_by", sa.String(length=160), nullable=True))

    op.drop_constraint("ck_evidence_claims_review_status", "evidence_claims", type_="check")
    op.create_check_constraint(
        "ck_evidence_claims_review_status", "evidence_claims",
        _in_list("review_status", _NEW_REVIEW_STATUSES),
    )
    op.create_check_constraint(
        "ck_evidence_claims_tier", "evidence_claims",
        _in_list("evidence_tier", _TIERS),
    )
    op.create_check_constraint(
        "ck_evidence_claims_rejection_reason", "evidence_claims",
        "review_status <> 'rejected' OR (rejection_reason IS NOT NULL AND btrim(rejection_reason) <> '')",
    )
    op.create_index(
        "ix_evidence_claims_subject_type_review_status", "evidence_claims",
        ["subject_type", "review_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_claims_subject_type_review_status", table_name="evidence_claims")
    op.drop_constraint("ck_evidence_claims_rejection_reason", "evidence_claims", type_="check")
    op.drop_constraint("ck_evidence_claims_tier", "evidence_claims", type_="check")

    # Rows in a state the old constraint does not allow would make the narrowed
    # constraint invalid, so fold them back to the nearest state it does allow.
    op.execute("UPDATE evidence_claims SET review_status = 'approved' WHERE review_status = 'published'")
    op.execute("UPDATE evidence_claims SET review_status = 'draft' WHERE review_status = 'rejected'")

    op.drop_constraint("ck_evidence_claims_review_status", "evidence_claims", type_="check")
    op.create_check_constraint(
        "ck_evidence_claims_review_status", "evidence_claims",
        _in_list("review_status", _OLD_REVIEW_STATUSES),
    )

    for column in (
        "published_by", "published_at", "rejection_reason",
        "notes", "value_unit", "value_text", "evidence_tier",
    ):
        op.drop_column("evidence_claims", column)
