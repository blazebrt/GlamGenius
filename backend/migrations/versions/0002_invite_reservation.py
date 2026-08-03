"""Alembic revision — Add invite_registration_reservations.

Adds the reservation model used by ``POST /api/v2/access/reserve``. The
reservation is created *before* the Supabase Auth sign-up so an invalid
invite cannot leave an orphan Supabase identity behind (§1 of the
Supabase hardening spec).

Revision ID: 0002_invite_reservation
Revises: 0001_initial_supabase_schema
Create Date: 2026-02-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_invite_reservation"
down_revision: Union[str, None] = "0001_initial_supabase_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invite_registration_reservations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invite_id", sa.UUID(), nullable=False),
        sa.Column("email_normalised", sa.String(length=320), nullable=False),
        sa.Column("challenge_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supabase_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invite_id"], ["invites.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "challenge_hash",
            name="uq_invite_reservation_challenge_hash",
        ),
    )
    op.create_index(
        "ix_invite_reservation_invite_active",
        "invite_registration_reservations",
        ["invite_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_invite_reservation_email_active",
        "invite_registration_reservations",
        ["email_normalised", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_invite_reservation_email_active",
        table_name="invite_registration_reservations",
    )
    op.drop_index(
        "ix_invite_reservation_invite_active",
        table_name="invite_registration_reservations",
    )
    op.drop_table("invite_registration_reservations")
