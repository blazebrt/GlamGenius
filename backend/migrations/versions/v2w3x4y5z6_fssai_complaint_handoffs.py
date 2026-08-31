"""Store structured FSSAI complaint review-and-handoffs.

Revision ID: v2w3x4y5z6
Revises: u1v2w3x4y5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "v2w3x4y5z6"
down_revision: Union[str, None] = "u1v2w3x4y5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fssai_complaint_handoffs",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=160), nullable=False),
        sa.Column("batch_number", sa.String(length=80), nullable=False),
        sa.Column("fssai_licence", sa.String(length=20), nullable=False),
        sa.Column("photo_asset_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="reviewed", nullable=False),
        sa.Column("official_portal_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("reason IN ('food_safety', 'label_information', 'misleading_claim', 'packaging')", name="ck_fssai_handoff_reason"),
        sa.CheckConstraint("status IN ('reviewed', 'official_portal_opened')", name="ck_fssai_handoff_status"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["photo_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fssai_handoff_status_created", "fssai_complaint_handoffs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_fssai_handoff_status_created", table_name="fssai_complaint_handoffs")
    op.drop_table("fssai_complaint_handoffs")
