"""household subject age band for Step 11A

Revision ID: e3f4g5h6i7
Revises: d2e3f4g5h6

One column on an existing table, plus the CHECK that keeps it to the four
bands the product asks for.

``not_stated`` is both the default and what every row written before this
column existed means: nobody was asked how old that person is, so nothing is
claimed about them. Backfilling anything else would invent a fact about a real
human being, and the one band that changes behaviour — ``under_12`` — must only
ever be there because somebody said so.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3f4g5h6i7"
down_revision = "d2e3f4g5h6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "family_profiles",
        sa.Column(
            "age_band",
            sa.String(16),
            nullable=False,
            server_default="not_stated",
        ),
    )
    op.create_check_constraint(
        "ck_family_profile_age_band",
        "family_profiles",
        "age_band IN ('under_12', 'teen_12_17', 'adult_18_plus', 'not_stated')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_family_profile_age_band", "family_profiles", type_="check")
    op.drop_column("family_profiles", "age_band")
