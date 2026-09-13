"""append-only purchase decision events for Step 9A

Revision ID: a9b0c1d2e3
Revises: d0e1f2g3h4
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9b0c1d2e3"
down_revision = "d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_decision_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("strategy_key", sa.String(length=32), nullable=False),
        sa.Column("candidate_display_name", sa.String(length=200), nullable=False),
        sa.Column("identity_version", sa.String(length=32), nullable=False),
        sa.Column("identity_state", sa.String(length=24), nullable=False),
        sa.Column("identity_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("recommendation_verdict", sa.String(length=16), nullable=False),
        sa.Column("recommendation_version", sa.String(length=64), nullable=False),
        sa.Column("recommendation_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("recommendation_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("followed_recommendation", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["shopping_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decision_id"], ["purchase_decisions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchase_decision_events_account_created", "purchase_decision_events", ["account_id", "created_at"])
    op.create_index("ix_purchase_decision_events_account_identity_created", "purchase_decision_events", ["account_id", "identity_fingerprint", "created_at"])
    op.create_index("ix_purchase_decision_events_candidate", "purchase_decision_events", ["candidate_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_purchase_decision_events_candidate", table_name="purchase_decision_events")
    op.drop_index("ix_purchase_decision_events_account_identity_created", table_name="purchase_decision_events")
    op.drop_index("ix_purchase_decision_events_account_created", table_name="purchase_decision_events")
    op.drop_table("purchase_decision_events")
