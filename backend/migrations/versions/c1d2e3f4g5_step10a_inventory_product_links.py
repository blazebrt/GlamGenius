"""exact inventory scan-origin product identity for Step 10A

Revision ID: c1d2e3f4g5
Revises: b0c1d2e3f4
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1d2e3f4g5"
down_revision = "b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Label snapshots are global exact-pack authority and retain a required
    # scan-event provenance link.  Deleting an account must therefore
    # anonymise its scan event rather than cascade-delete the event and block
    # deletion on the snapshot's RESTRICT foreign key.
    op.drop_constraint("scan_events_account_id_fkey", "scan_events", type_="foreignkey")
    op.create_foreign_key(
        "scan_events_account_id_fkey", "scan_events", "accounts",
        ["account_id"], ["id"], ondelete="SET NULL",
    )
    op.create_table(
        "inventory_product_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inventory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("label_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_version", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="explicit_scan"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inventory_item_id"], ["inventory_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_record_id"], ["product_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["label_snapshot_id"], ["product_label_snapshots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inventory_item_id", name="uq_inventory_product_link_item"),
    )
    op.create_index("ix_inventory_product_links_account_barcode", "inventory_product_links", ["account_id", "barcode"])
    op.create_index("ix_inventory_product_links_snapshot", "inventory_product_links", ["label_snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_product_links_snapshot", table_name="inventory_product_links")
    op.drop_index("ix_inventory_product_links_account_barcode", table_name="inventory_product_links")
    op.drop_table("inventory_product_links")
    op.drop_constraint("scan_events_account_id_fkey", "scan_events", type_="foreignkey")
    op.create_foreign_key(
        "scan_events_account_id_fkey", "scan_events", "accounts",
        ["account_id"], ["id"], ondelete="CASCADE",
    )
