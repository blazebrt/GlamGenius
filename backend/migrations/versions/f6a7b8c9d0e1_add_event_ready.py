"""Add the VC-02 Event Ready orchestration tables."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_ready_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("calendar_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selected_look_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("looks.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(32), nullable=False, server_default="preparing"),
        sa.Column("engine_version", sa.String(32), nullable=False, server_default="vc-02-v1"),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "calendar_event_id", name="uq_event_ready_plan_account_event"),
    )
    op.create_index("ix_event_ready_plans_account_event", "event_ready_plans", ["account_id", "calendar_event_id"])
    op.create_table(
        "event_ready_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_ready_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("event_ready_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_key", sa.String(96), nullable=False),
        sa.Column("domain", sa.String(24), nullable=False),
        sa.Column("timing", sa.String(24), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("relevance", sa.String(240), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("inventory_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_items.id", ondelete="SET NULL")),
        sa.Column("material_fingerprint", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_ready_plan_id", "action_key", name="uq_event_ready_action_plan_key"),
    )
    op.create_index("ix_event_ready_actions_plan_priority", "event_ready_actions", ["event_ready_plan_id", "priority"])


def downgrade() -> None:
    op.drop_index("ix_event_ready_actions_plan_priority", table_name="event_ready_actions")
    op.drop_table("event_ready_actions")
    op.drop_index("ix_event_ready_plans_account_event", table_name="event_ready_plans")
    op.drop_table("event_ready_plans")
