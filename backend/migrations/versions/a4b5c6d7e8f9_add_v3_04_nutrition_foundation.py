"""Add V3-04.0 nutrition authority and composition foundation.

Revision ID: a4b5c6d7e8f9
Revises: c4e5f6a7b8c9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "c4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "food_composition_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("dataset_key", sa.String(length=160), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("jurisdiction", sa.String(length=64), nullable=False),
        sa.Column("rights_status", sa.String(length=32), nullable=False),
        sa.Column("import_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("rights_note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_key"),
        sa.ForeignKeyConstraint(["source_id"], ["evidence_sources.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("rights_status IN ('restricted_reference', 'permission_granted', 'open_licensed')", name="ck_food_composition_datasets_rights_status"),
        sa.CheckConstraint("import_status IN ('metadata_only', 'ready_for_import', 'imported', 'retired')", name="ck_food_composition_datasets_import_status"),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_food_composition_datasets_status"),
        sa.CheckConstraint("NOT (rights_status = 'restricted_reference' AND import_status IN ('ready_for_import', 'imported'))", name="ck_food_composition_datasets_restricted_import"),
    )
    op.create_index("ix_food_composition_datasets_source_id", "food_composition_datasets", ["source_id"])

    op.create_table(
        "food_reference_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_food_code", sa.String(length=96), nullable=False),
        sa.Column("canonical_name", sa.String(length=256), nullable=False),
        sa.Column("food_group", sa.String(length=160)),
        sa.Column("scientific_name", sa.String(length=256)),
        sa.Column("local_names", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "source_food_code", name="uq_food_reference_items_dataset_source_code"),
        sa.ForeignKeyConstraint(["dataset_id"], ["food_composition_datasets.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_food_reference_items_status"),
    )
    op.create_index("ix_food_reference_items_dataset_id", "food_reference_items", ["dataset_id"])
    op.create_index("ix_food_reference_items_canonical_name", "food_reference_items", ["canonical_name"])

    op.create_table(
        "food_nutrient_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("food_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nutrient_key", sa.String(length=96), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("basis", sa.String(length=96), nullable=False),
        sa.Column("source_locator", sa.String(length=512)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("food_id", "nutrient_key", "unit", "basis", name="uq_food_nutrient_values_identity"),
        sa.ForeignKeyConstraint(["food_id"], ["food_reference_items.id"], ondelete="CASCADE"),
        sa.CheckConstraint("amount >= 0", name="ck_food_nutrient_values_amount_nonnegative"),
    )
    op.create_index("ix_food_nutrient_values_food_id", "food_nutrient_values", ["food_id"])


def downgrade() -> None:
    op.drop_index("ix_food_nutrient_values_food_id", table_name="food_nutrient_values")
    op.drop_table("food_nutrient_values")
    op.drop_index("ix_food_reference_items_canonical_name", table_name="food_reference_items")
    op.drop_index("ix_food_reference_items_dataset_id", table_name="food_reference_items")
    op.drop_table("food_reference_items")
    op.drop_index("ix_food_composition_datasets_source_id", table_name="food_composition_datasets")
    op.drop_table("food_composition_datasets")
