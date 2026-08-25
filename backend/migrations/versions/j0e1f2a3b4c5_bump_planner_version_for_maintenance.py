"""Advance the stored planner engine version for the VC-06 compiler rules.

``PLANNER_VERSION`` identifies the deterministic rule set that produced a plan.
VC-06 added a maintenance rule to ``compile_day``, so the version advances and
the column defaults follow it. Existing rows keep the version they were written
with, which is the whole point of storing it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREVIOUS = "phase5-v1"
CURRENT = "vc06-v1"


def upgrade() -> None:
    for table in ("daily_plans", "weekly_plans"):
        op.alter_column(
            table, "engine_version",
            existing_type=sa.String(length=32), existing_nullable=False,
            server_default=CURRENT,
        )


def downgrade() -> None:
    for table in ("daily_plans", "weekly_plans"):
        op.alter_column(
            table, "engine_version",
            existing_type=sa.String(length=32), existing_nullable=False,
            server_default=PREVIOUS,
        )
