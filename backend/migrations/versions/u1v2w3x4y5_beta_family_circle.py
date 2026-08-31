"""Add private-beta, account-local Family Circle profiles.

Revision ID: u1v2w3x4y5
Revises: t0u1v2w3x4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "u1v2w3x4y5"
down_revision: Union[str, None] = "t0u1v2w3x4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("family_circles", sa.Column("account_id", sa.Uuid(), nullable=False), sa.Column("active", sa.Boolean(), server_default="true", nullable=False), sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("account_id"))
    op.create_table("family_profiles", sa.Column("circle_id", sa.Uuid(), nullable=False), sa.Column("position", sa.Integer(), nullable=False), sa.Column("relation", sa.String(length=16), nullable=False), sa.Column("active", sa.Boolean(), server_default="true", nullable=False), sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.CheckConstraint("position >= 1 AND position <= 8", name="ck_family_profile_position"), sa.CheckConstraint("relation IN ('self', 'adult', 'child', 'other')", name="ck_family_profile_relation"), sa.ForeignKeyConstraint(["circle_id"], ["family_circles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("circle_id", "position", name="uq_family_profile_position"))
    op.create_index("ix_family_profiles_circle_active", "family_profiles", ["circle_id", "active"])


def downgrade() -> None:
    op.drop_index("ix_family_profiles_circle_active", table_name="family_profiles")
    op.drop_table("family_profiles")
    op.drop_table("family_circles")
