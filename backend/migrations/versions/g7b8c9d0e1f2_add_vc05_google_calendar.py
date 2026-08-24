"""Add VC-05 Google Calendar OAuth and stable synchronization state."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("external_integrations", sa.Column("sync_cursor", sa.Text(), nullable=True))
    op.add_column(
        "calendar_events",
        sa.Column("user_overrides", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_unique_constraint(
        "uq_calendar_event_integration_external", "calendar_events", ["integration_id", "external_id"]
    )
    op.create_table(
        "external_oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("state_hash", name="uq_external_oauth_states_hash"),
    )
    op.create_index("ix_external_oauth_states_account_provider", "external_oauth_states", ["account_id", "provider"])


def downgrade() -> None:
    op.drop_index("ix_external_oauth_states_account_provider", table_name="external_oauth_states")
    op.drop_table("external_oauth_states")
    op.drop_constraint("uq_calendar_event_integration_external", "calendar_events", type_="unique")
    op.drop_column("calendar_events", "user_overrides")
    op.drop_column("external_integrations", "sync_cursor")
