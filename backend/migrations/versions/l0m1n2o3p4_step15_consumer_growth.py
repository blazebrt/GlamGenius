"""consumer growth for Step 15

Revision ID: l0m1n2o3p4
Revises: k9l0m1n2o3

Two narrow changes and nothing else.

1. ``consumer_referral_invites`` — the one genuinely new fact: which account an
   invite was issued to share. Code, use count, ceiling, expiry and the on/off
   switch stay on ``invites`` and are not copied here.

   * ``inviter_account_id`` CASCADE — erasing the account erases the binding.
     The deletion worker switches every bound invite off *before* this cascade
     runs, so no capability outlives the person it was issued to.
   * ``invite_id`` RESTRICT and UNIQUE — one invite has at most one inviter,
     and invites are never deleted; a delete that tried should be refused
     rather than orphan the lifetime ceiling this row counts toward.

   Every foreign key carries its delete rule explicitly: Alembic's
   autogenerate does not compare ``ondelete``, so leaving one implicit passes
   ``alembic check`` and leaves rows behind at account deletion.

2. ``app_events.client_event_id`` — an opaque client-minted operation UUID,
   with a partial unique index over ``(account_id, name, client_event_id)``, so
   a retried telemetry write is recognised rather than counted twice. Existing
   rows keep ``NULL`` and are unaffected. It is never a device or advertising
   identifier.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "l0m1n2o3p4"
down_revision = "k9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consumer_referral_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("inviter_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_version", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["inviter_account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invite_id"], ["invites.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "program_version IN ('consumer-referral-v1')",
            name="ck_consumer_referral_invites_program",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invite_id"),
    )
    op.create_index(
        "ix_consumer_referral_invites_inviter",
        "consumer_referral_invites",
        ["inviter_account_id", "created_at"],
    )

    op.add_column(
        "app_events",
        sa.Column("client_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_app_events_account_name_client_event",
        "app_events",
        ["account_id", "name", "client_event_id"],
        unique=True,
        postgresql_where=sa.text("client_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_app_events_account_name_client_event", table_name="app_events")
    op.drop_column("app_events", "client_event_id")
    op.drop_index("ix_consumer_referral_invites_inviter", table_name="consumer_referral_invites")
    op.drop_table("consumer_referral_invites")
