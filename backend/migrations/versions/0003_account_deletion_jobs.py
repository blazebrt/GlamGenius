"""Alembic revision — Add account_deletion_jobs.

Persistent state machine for the durable account-deletion worker. See
``app/domains/privacy/models.py`` and
``app/domains/privacy/deletion_service.py`` for the full behaviour.

Revision ID: 0003_account_deletion_jobs
Revises: 0002_invite_reservation
Create Date: 2026-02-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_account_deletion_jobs"
down_revision: Union[str, None] = "0002_invite_reservation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_deletion_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_stage", sa.String(length=32), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_deletion_jobs_account_id",
        "account_deletion_jobs",
        ["account_id"],
        unique=True,
    )
    op.create_index(
        "ix_account_deletion_jobs_state",
        "account_deletion_jobs",
        ["state", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_deletion_jobs_state", table_name="account_deletion_jobs")
    op.drop_index("ix_account_deletion_jobs_account_id", table_name="account_deletion_jobs")
    op.drop_table("account_deletion_jobs")
