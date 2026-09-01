"""Store normalized community observation reports.

Revision ID: w3x4y5z6a7
Revises: v2w3x4y5z6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "w3x4y5z6a7"
down_revision: Union[str, None] = "v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "community_observation_reports",
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("client_report_id", sa.String(length=64), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("observation_code", sa.String(length=80), nullable=False),
        sa.Column("batch_number", sa.String(length=80), nullable=True),
        sa.Column("photo_asset_id", sa.Uuid(), nullable=True),
        sa.Column("condition_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="accepted", nullable=False),
        sa.Column("validity_state", sa.String(length=16), server_default="valid", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('accepted', 'rejected', 'under_review')", name="ck_community_report_status"),
        sa.CheckConstraint("validity_state IN ('valid', 'invalid')", name="ck_community_report_validity"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["scan_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["photo_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "client_report_id", name="uq_community_report_device_client_id"),
    )
    op.create_index("ix_community_report_account", "community_observation_reports", ["account_id", "created_at"])
    op.create_index(
        "ix_community_report_barcode_code_created", "community_observation_reports", ["barcode", "observation_code", "created_at"]
    )
    op.create_index(
        "ix_community_report_batch_aggregate", "community_observation_reports",
        ["barcode", "observation_code", "batch_number", "created_at"],
    )
    op.create_index(
        "ix_community_report_validity_created", "community_observation_reports", ["status", "validity_state", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_community_report_validity_created", table_name="community_observation_reports")
    op.drop_index("ix_community_report_batch_aggregate", table_name="community_observation_reports")
    op.drop_index("ix_community_report_barcode_code_created", table_name="community_observation_reports")
    op.drop_index("ix_community_report_account", table_name="community_observation_reports")
    op.drop_table("community_observation_reports")
