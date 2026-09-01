"""Cascade claimed community reports with account deletion.

Revision ID: x4y5z6a7b8
Revises: w3x4y5z6a7
"""
from typing import Sequence, Union

from alembic import op


revision: str = "x4y5z6a7b8"
down_revision: Union[str, None] = "w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("community_observation_reports_account_id_fkey", "community_observation_reports", type_="foreignkey")
    op.create_foreign_key(
        "community_observation_reports_account_id_fkey",
        "community_observation_reports", "accounts", ["account_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("community_observation_reports_account_id_fkey", "community_observation_reports", type_="foreignkey")
    op.create_foreign_key(
        "community_observation_reports_account_id_fkey",
        "community_observation_reports", "accounts", ["account_id"], ["id"], ondelete="SET NULL",
    )
