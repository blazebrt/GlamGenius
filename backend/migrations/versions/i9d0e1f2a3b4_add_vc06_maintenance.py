"""Add VC-06 Skin and Hair maintenance timing tables."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maintenance_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind_key", sa.String(length=48), nullable=False),
        sa.Column("tracked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=True),
        sa.Column("reminders_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "kind_key", name="uq_maintenance_preference_account_kind"),
        sa.CheckConstraint(
            "interval_days IS NULL OR (interval_days >= 3 AND interval_days <= 365)",
            name="ck_maintenance_preference_interval_range",
        ),
    )
    op.create_table(
        "maintenance_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind_key", sa.String(length=48), nullable=False),
        sa.Column("done_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="user_declared", nullable=False),
        sa.Column("note", sa.String(length=240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "kind_key", "done_on", name="uq_maintenance_event_account_kind_date"),
    )
    op.create_index(
        "ix_maintenance_events_account_kind_date", "maintenance_events",
        ["account_id", "kind_key", "done_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_events_account_kind_date", table_name="maintenance_events")
    op.drop_table("maintenance_events")
    op.drop_table("maintenance_preferences")
