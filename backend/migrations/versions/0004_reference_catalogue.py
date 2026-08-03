"""Alembic revision — Package E reference-data catalogue tables.

Adds the tables that hold the versioned reference data written by
``python -m app.bootstrap.reference_data``:

    * ``seed_version_records`` — per-domain seed-version audit trail.
    * ``routine_templates`` + ``routine_template_steps``.
    * ``perfume_context_rules``.
    * ``supplement_context_rules``.
    * ``ingredient_contraindication_rules``.
    * ``ingredient_sensitivity_rules``.
    * ``inventory_subtype_definitions``.

See ``app/domains/reference/__init__.py`` for the ORM definitions.

Revision ID: 0004_reference_catalogue
Revises: 0003_account_deletion_jobs
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_reference_catalogue"
down_revision: Union[str, None] = "0003_account_deletion_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_columns():
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "seed_version_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seed_domain", sa.String(length=64), nullable=False),
        sa.Column("seed_version", sa.String(length=32), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "seed_domain", "seed_version", name="uq_seed_versions_domain_version"
        ),
    )
    op.create_index(
        "ix_seed_version_records_seed_domain",
        "seed_version_records",
        ["seed_domain"],
    )

    op.create_table(
        "routine_template_steps",
        sa.Column("template_kind", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False),
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.String(length=32), nullable=False),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("template_kind", "position"),
    )

    op.create_table(
        "supplement_context_rules",
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("context_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False),
        sa.Column("safety_category", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("rule_id"),
        sa.UniqueConstraint("context_key", name="uq_supplement_ctx_context_key"),
    )

    op.create_table(
        "ingredient_contraindication_rules",
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("ingredient_key", sa.String(length=64), nullable=False),
        sa.Column("condition_key", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.String(length=256), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("rule_id"),
        sa.UniqueConstraint(
            "ingredient_key", "condition_key",
            name="uq_contraindication_ingredient_condition",
        ),
    )

    op.create_table(
        "ingredient_sensitivity_rules",
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("ingredient_key", sa.String(length=64), nullable=False),
        sa.Column("sensitivity_key", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.String(length=256), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("rule_id"),
        sa.UniqueConstraint(
            "ingredient_key", "sensitivity_key",
            name="uq_sensitivity_ingredient_key",
        ),
    )

    op.create_table(
        "inventory_subtype_definitions",
        sa.Column("category_key", sa.String(length=32), nullable=False),
        sa.Column("subtype_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("required_attributes", sa.Text(), nullable=False, server_default=""),
        sa.Column("optional_attributes", sa.Text(), nullable=False, server_default=""),
        sa.Column("supports_expiry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_condition", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_usage", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.String(length=32), nullable=False),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("category_key", "subtype_key"),
    )


def downgrade() -> None:
    op.drop_table("inventory_subtype_definitions")
    op.drop_table("ingredient_sensitivity_rules")
    op.drop_table("ingredient_contraindication_rules")
    op.drop_table("supplement_context_rules")
    op.drop_table("routine_template_steps")
    op.drop_index(
        "ix_seed_version_records_seed_domain",
        table_name="seed_version_records",
    )
    op.drop_table("seed_version_records")
