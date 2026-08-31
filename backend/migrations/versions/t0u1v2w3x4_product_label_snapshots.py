"""Store confirmed physical-label facts in Store B.

Revision ID: t0u1v2w3x4
Revises: s9t0u1v2w3
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "t0u1v2w3x4"
down_revision: Union[str, None] = "s9t0u1v2w3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table("product_label_snapshots",
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("scan_event_id", sa.Uuid(), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.String(length=32), server_default="unverified", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["scan_devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scan_event_id"], ["scan_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("scan_event_id"),
        sa.CheckConstraint("confidence IN ('verified', 'community', 'unverified', 'not_enough_information')", name="ck_product_label_snapshots_confidence"),
    )
    op.create_index("ix_product_label_snapshots_barcode_created", "product_label_snapshots", ["barcode", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_product_label_snapshots_barcode_created", table_name="product_label_snapshots")
    op.drop_table("product_label_snapshots")
