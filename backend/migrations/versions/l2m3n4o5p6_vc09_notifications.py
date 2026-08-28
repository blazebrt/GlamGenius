"""Close VC-09 notification outbox and device registration semantics.

Revision ID: l2m3n4o5p6
Revises: k1l2m3n4o5p6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "l2m3n4o5p6"
down_revision: Union[str, None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notification_preferences", sa.Column("native_push_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("notification_preferences", sa.Column("topics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.alter_column("notification_deliveries", "status", type_=sa.String(length=24), existing_type=sa.String(length=16))
    op.create_check_constraint(
        "ck_notification_delivery_status",
        "notification_deliveries",
        "status IN ('suppressed', 'queued', 'sending', 'provider_accepted', 'provider_failed', 'receipt_ok', 'receipt_failed')",
    )
    for name, column in (
        ("scheduled_for", sa.DateTime(timezone=True)),
        ("deep_link", sa.String(length=240)),
        ("source_kind", sa.String(length=32)),
        ("source_id", sa.String(length=96)),
        ("provider_ticket_id", sa.String(length=160)),
        ("provider_error_code", sa.String(length=80)),
        ("attempted_at", sa.DateTime(timezone=True)),
        ("destination_params", postgresql.JSONB()),
        ("claimed_at", sa.DateTime(timezone=True)),
        ("claim_token", sa.String(length=64)),
    ):
        op.add_column("notification_deliveries", sa.Column(name, column, nullable=True))
    # Before VC-09 no transport existed. Existing rows must not imply a push
    # was sent merely because the old compiler queued a decision.
    op.execute(sa.text("UPDATE notification_deliveries SET sent_at = NULL WHERE status = 'queued'"))
    op.create_table(
        "notification_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_key", sa.String(length=160), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("expo_push_token", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "device_key", name="uq_notification_device_account_key"),
        sa.CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="ck_notification_device_status"),
        sa.CheckConstraint("platform IN ('ios', 'android', 'web', 'unknown')", name="ck_notification_device_platform"),
    )
    op.create_index("ix_notification_devices_account_status", "notification_devices", ["account_id", "status"])
    op.create_index(
        "uq_notification_device_active_token", "notification_devices", ["expo_push_token"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_notification_device_active_token", table_name="notification_devices")
    op.drop_index("ix_notification_devices_account_status", table_name="notification_devices")
    op.drop_table("notification_devices")
    op.drop_constraint("ck_notification_delivery_status", "notification_deliveries", type_="check")
    for name in ("claim_token", "claimed_at", "destination_params", "attempted_at", "provider_error_code", "provider_ticket_id", "source_id", "source_kind", "deep_link", "scheduled_for"):
        op.drop_column("notification_deliveries", name)
    # Explicitly collapse every post-VC status before narrowing the column.
    op.execute(sa.text("UPDATE notification_deliveries SET sent_at = NULL WHERE status IN ('provider_accepted', 'provider_failed', 'receipt_ok', 'receipt_failed')"))
    op.execute(sa.text("UPDATE notification_deliveries SET status = CASE status WHEN 'sending' THEN 'queued' WHEN 'provider_accepted' THEN 'queued' WHEN 'provider_failed' THEN 'queued' WHEN 'receipt_ok' THEN 'queued' WHEN 'receipt_failed' THEN 'queued' WHEN 'suppressed' THEN 'suppressed' WHEN 'queued' THEN 'queued' ELSE 'queued' END"))
    op.alter_column("notification_deliveries", "status", type_=sa.String(length=16), existing_type=sa.String(length=24))
    op.drop_column("notification_preferences", "native_push_enabled")
    op.drop_column("notification_preferences", "topics")
