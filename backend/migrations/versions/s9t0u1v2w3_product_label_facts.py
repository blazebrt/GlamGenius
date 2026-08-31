"""Confirmed label facts, so a photographed pack becomes an answer.

Ours, not Open Food Facts'. Somebody photographed a pack we had no record of,
checked the transcription and confirmed it. Store A is untouched by this: the
two databases are still joined only in memory, on barcode.

Revision ID: s9t0u1v2w3
Revises: r8s9t0u1v2
Create Date: 2026-08-31
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "s9t0u1v2w3"
down_revision: str | None = "r8s9t0u1v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_label_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("printed_name", sa.String(length=200), nullable=True),
        sa.Column("printed_brand", sa.String(length=160), nullable=True),
        sa.Column("printed_ingredients", sa.Text(), nullable=True),
        sa.Column("printed_nutrition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("printed_serving_size", sa.String(length=80), nullable=True),
        sa.Column("printed_net_quantity", sa.String(length=80), nullable=True),
        sa.Column("printed_veg_mark", sa.String(length=24), nullable=True),
        sa.Column("printed_allergens", sa.Text(), nullable=True),
        sa.Column("transcription_confidence", sa.Float(), nullable=True),
        sa.Column("uncertain_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # One row per barcode: the newest confirmed reading of the pack wins.
        sa.UniqueConstraint("barcode", name="uq_product_label_facts_barcode"),
    )


def downgrade() -> None:
    op.drop_table("product_label_facts")
