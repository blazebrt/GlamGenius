"""Add explicit structured Care experience feedback.

Revision ID: c4e5f6a7b8c9
Revises: b7f3a1c9d2e4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4e5f6a7b8c9"
down_revision: Union[str, None] = "b7f3a1c9d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "care_experience_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("sentiment", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("experienced_on", sa.Date(), nullable=False),
        sa.Column("feedback_version", sa.String(length=16), server_default="v3-03.13", nullable=False),
        sa.Column("routine_kind", sa.String(length=24), nullable=True),
        sa.Column("routine_slot", sa.String(length=32), nullable=True),
        sa.CheckConstraint("subject_type IN ('product', 'routine_step')", name="ck_care_feedback_subject_type"),
        sa.CheckConstraint("dimension IN ('overall_experience', 'comfort', 'ease_of_use', 'routine_fit')", name="ck_care_feedback_dimension"),
        sa.CheckConstraint("sentiment IN ('positive', 'neutral', 'negative')", name="ck_care_feedback_sentiment"),
        sa.CheckConstraint("feedback_version = 'v3-03.13'", name="ck_care_feedback_version"),
        sa.CheckConstraint("(subject_type = 'routine_step' AND routine_kind IS NOT NULL AND routine_slot IS NOT NULL) OR (subject_type = 'product' AND routine_kind IS NULL AND routine_slot IS NULL)", name="ck_care_feedback_subject_shape"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_care_feedback_account_date_created",
        "care_experience_feedback",
        ["account_id", "experienced_on", "created_at"],
    )
    op.create_index(
        "ix_care_feedback_account_subject_created",
        "care_experience_feedback",
        ["account_id", "subject_type", "subject_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_care_feedback_account_subject_created", table_name="care_experience_feedback")
    op.drop_index("ix_care_feedback_account_date_created", table_name="care_experience_feedback")
    op.drop_table("care_experience_feedback")
