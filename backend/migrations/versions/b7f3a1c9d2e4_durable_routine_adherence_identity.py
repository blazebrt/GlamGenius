"""Preserve routine adherence by logical slot across regeneration.

Revision ID: b7f3a1c9d2e4
Revises: f3e02e1b7a91
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7f3a1c9d2e4"
down_revision: Union[str, None] = "f3e02e1b7a91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Never hide pre-existing ambiguity before making slot the durable key.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM routine_steps
                GROUP BY routine_id, slot
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot add uq_routine_step_slot: duplicate current routine step slots exist';
            END IF;
        END $$;
        """
    )

    op.add_column("routine_adherence", sa.Column("slot", sa.String(length=32), nullable=True))
    op.execute(
        """
        UPDATE routine_adherence AS adherence
        SET slot = step.slot
        FROM routine_steps AS step
        WHERE adherence.step_id = step.id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM routine_adherence WHERE slot IS NULL) THEN
                RAISE EXCEPTION 'cannot make routine_adherence.slot NOT NULL: an adherence row has no valid RoutineStep slot';
            END IF;
        END $$;
        """
    )
    op.alter_column("routine_adherence", "slot", nullable=False)

    op.drop_constraint("uq_routine_adherence_step_day", "routine_adherence", type_="unique")
    op.drop_constraint("routine_adherence_step_id_fkey", "routine_adherence", type_="foreignkey")
    op.alter_column("routine_adherence", "step_id", nullable=True)
    op.create_foreign_key(
        "fk_routine_adherence_step_id_routine_steps",
        "routine_adherence", "routine_steps", ["step_id"], ["id"], ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_routine_adherence_slot_day", "routine_adherence", ["routine_id", "slot", "done_on"],
    )
    op.create_unique_constraint("uq_routine_step_slot", "routine_steps", ["routine_id", "slot"])


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM routine_adherence WHERE step_id IS NULL) THEN
                RAISE EXCEPTION 'cannot downgrade durable routine adherence: detached historical rows would be deleted';
            END IF;
        END $$;
        """
    )

    op.drop_constraint("uq_routine_step_slot", "routine_steps", type_="unique")
    op.drop_constraint("uq_routine_adherence_slot_day", "routine_adherence", type_="unique")
    op.drop_constraint("fk_routine_adherence_step_id_routine_steps", "routine_adherence", type_="foreignkey")
    op.alter_column("routine_adherence", "step_id", nullable=False)
    op.create_foreign_key(
        "routine_adherence_step_id_fkey",
        "routine_adherence", "routine_steps", ["step_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_column("routine_adherence", "slot")
    op.create_unique_constraint(
        "uq_routine_adherence_step_day", "routine_adherence", ["step_id", "done_on"],
    )
