"""shelf manager decision events for Step 10B

Revision ID: d2e3f4g5h6
Revises: c1d2e3f4g5

The two foreign keys are written out with their ``ondelete`` behaviour
explicitly. Alembic's autogenerate does not compare delete rules, so a table
created without them passes ``alembic check`` while leaving an account's rows
behind at deletion time. Step 10A found that the hard way; this migration does
not rely on autogenerate for either edge.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d2e3f4g5h6"
down_revision = "c1d2e3f4g5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shelf_manager_decision_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_key", sa.String(200), nullable=False),
        sa.Column("decision_fingerprint", sa.String(64), nullable=False),
        sa.Column("choice", sa.String(24), nullable=False),
        sa.Column("action_kind", sa.String(32), nullable=False),
        sa.Column("target_inventory_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_mutation_id", sa.String(80), nullable=False),
        # Erasing the account erases the log. Deleting the product the decision
        # acted on erases the row that names it, so nothing is left pointing at
        # an item that no longer exists.
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_inventory_item_id"], ["inventory_items.id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "choice IN ('accepted', 'overridden', 'restored', 'restore_overridden')",
            name="ck_shelf_manager_event_choice",
        ),
        sa.CheckConstraint(
            "action_kind IN ('pause_product', 'resume_product', 'prefer_product', "
            "'unprefer_product', 'confirm_label', 'record_date', 'add_owned_product', "
            "'open_routine', 'open_inventory_item', 'none')",
            name="ck_shelf_manager_event_action_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "client_mutation_id", name="uq_shelf_manager_event_client_mutation",
        ),
    )
    op.create_index(
        "ix_shelf_manager_events_account_key",
        "shelf_manager_decision_events",
        ["account_id", "decision_key", "created_at"],
    )
    op.create_index(
        "ix_shelf_manager_events_account_target",
        "shelf_manager_decision_events",
        ["account_id", "target_inventory_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_shelf_manager_events_account_target", table_name="shelf_manager_decision_events")
    op.drop_index("ix_shelf_manager_events_account_key", table_name="shelf_manager_decision_events")
    op.drop_table("shelf_manager_decision_events")
