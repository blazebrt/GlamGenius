"""subject-scoped Care preferences and Shelf Manager history (Step 11D)."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h6i7j8k9l0"
down_revision = "g5h6i7j8k9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "care_product_preferences",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("household_subject_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inventory_item_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preference_kind", sa.String(16), nullable=False),
        sa.Column("authority_source", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["household_subject_id"], ["family_profiles.id"],
            ondelete="NO ACTION", deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "preference_kind IN ('paused', 'preferred')", name="ck_care_product_preference_kind",
        ),
        sa.CheckConstraint(
            "authority_source IN ('direct_user', 'shelf_manager', 'legacy_adopted')",
            name="ck_care_product_preference_source",
        ),
        sa.UniqueConstraint(
            "account_id", "household_subject_id", "inventory_item_id", "preference_kind",
            name="uq_care_product_preference_subject_item_kind",
        ),
    )
    op.create_index(
        "ix_care_product_preferences_subject_kind", "care_product_preferences",
        ["account_id", "household_subject_id", "preference_kind"],
    )
    op.create_index(
        "ix_care_product_preferences_subject_item", "care_product_preferences",
        ["account_id", "household_subject_id", "inventory_item_id"],
    )
    for table in ("inventory_events", "shelf_manager_decision_events"):
        op.add_column(
            table,
            sa.Column("household_subject_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_household_subject", table, "family_profiles",
            ["household_subject_id"], ["id"], ondelete="NO ACTION",
            deferrable=True, initially="DEFERRED",
        )


def _refuse_if_populated(connection: sa.Connection) -> None:
    checks = (
        ("care_product_preferences", "SELECT count(*) FROM care_product_preferences"),
        ("shelf_manager_decision_events", "SELECT count(*) FROM shelf_manager_decision_events WHERE household_subject_id IS NOT NULL"),
        ("inventory_events", "SELECT count(*) FROM inventory_events WHERE household_subject_id IS NOT NULL"),
    )
    populated = [(name, connection.execute(sa.text(query)).scalar_one()) for name, query in checks]
    populated = [(name, count) for name, count in populated if count]
    if populated:
        detail = ", ".join(f"{name} ({count} rows)" for name, count in populated)
        raise RuntimeError(
            "Cannot downgrade h6i7j8k9l0: subject-scoped Care/Shelf state exists; "
            f"refusing to erase identity: {detail}."
        )


def downgrade() -> None:
    connection = op.get_bind()
    _refuse_if_populated(connection)
    for table in ("shelf_manager_decision_events", "inventory_events"):
        op.drop_constraint(f"fk_{table}_household_subject", table, type_="foreignkey")
        op.drop_column(table, "household_subject_id")
    op.drop_index("ix_care_product_preferences_subject_item", table_name="care_product_preferences")
    op.drop_index("ix_care_product_preferences_subject_kind", table_name="care_product_preferences")
    op.drop_table("care_product_preferences")
