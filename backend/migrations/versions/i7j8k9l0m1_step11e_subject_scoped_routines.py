"""Step 11E: subject-scoped persisted Care routines and provenance.

Revision ID: i7j8k9l0m1
Revises: h6i7j8k9l0
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "i7j8k9l0m1"
down_revision = "h6i7j8k9l0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "routines",
        sa.Column("household_subject_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_routines_household_subject_id_family_profiles",
        "routines",
        "family_profiles",
        ["household_subject_id"],
        ["id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_constraint("uq_routine_account_kind", "routines", type_="unique")
    op.drop_index("ix_routines_account", table_name="routines")
    op.create_index(
        "uq_routine_account_kind_legacy",
        "routines",
        ["account_id", "kind"],
        unique=True,
        postgresql_where=sa.text("household_subject_id IS NULL"),
    )
    op.create_index(
        "uq_routine_account_subject_kind",
        "routines",
        ["account_id", "household_subject_id", "kind"],
        unique=True,
        postgresql_where=sa.text("household_subject_id IS NOT NULL"),
    )
    op.create_index(
        "ix_routines_account_subject_status",
        "routines",
        ["account_id", "household_subject_id", "status"],
        unique=False,
    )

    op.add_column(
        "routine_recommendation_runs",
        sa.Column("household_subject_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_routine_runs_household_subject_id_family_profiles",
        "routine_recommendation_runs",
        "family_profiles",
        ["household_subject_id"],
        ["id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_index("ix_routine_runs_account", table_name="routine_recommendation_runs")
    op.create_index(
        "ix_routine_runs_account_subject_created",
        "routine_recommendation_runs",
        ["account_id", "household_subject_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()

    subject_routine = bind.execute(
        sa.text("SELECT 1 FROM routines WHERE household_subject_id IS NOT NULL LIMIT 1")
    ).first()
    subject_run = bind.execute(
        sa.text(
            "SELECT 1 FROM routine_recommendation_runs "
            "WHERE household_subject_id IS NOT NULL LIMIT 1"
        )
    ).first()
    if subject_routine is not None or subject_run is not None:
        raise RuntimeError(
            "Step 11E downgrade refused: subject-attributed routine state exists."
        )

    op.drop_index(
        "ix_routine_runs_account_subject_created",
        table_name="routine_recommendation_runs",
    )
    op.drop_constraint(
        "fk_routine_runs_household_subject_id_family_profiles",
        "routine_recommendation_runs",
        type_="foreignkey",
    )
    op.drop_column("routine_recommendation_runs", "household_subject_id")

    op.drop_index("ix_routines_account_subject_status", table_name="routines")
    op.drop_index("uq_routine_account_subject_kind", table_name="routines")
    op.drop_index("uq_routine_account_kind_legacy", table_name="routines")
    op.drop_constraint(
        "fk_routines_household_subject_id_family_profiles",
        "routines",
        type_="foreignkey",
    )
    op.drop_column("routines", "household_subject_id")
    op.create_unique_constraint(
        "uq_routine_account_kind",
        "routines",
        ["account_id", "kind"],
    )
    op.create_index(
        "ix_routines_account",
        "routines",
        ["account_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_routine_runs_account",
        "routine_recommendation_runs",
        ["account_id", "created_at"],
        unique=False,
    )
