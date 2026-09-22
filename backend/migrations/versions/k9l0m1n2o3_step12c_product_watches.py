"""product watches for Step 12C

Revision ID: k9l0m1n2o3
Revises: j8k9l0m1n2

One table and nothing else: the customer's explicit request to hear about one
exact confirmed pack, its two anchors, and the semantic cursor that remembers
what was already known when the watch began. No queue, no event bus and no
second delivery table — delivery is the existing notification outbox.

Every foreign key carries its delete rule explicitly. Alembic's autogenerate
does not compare ``ondelete``, so a table created without them passes
``alembic check`` while leaving rows behind at account deletion; Step 10A found
that the hard way.

* ``account_id`` CASCADE — erasing the account erases its watches.
* ``anchor_scan_event_id`` CASCADE — the watch's lot and licence come from this
  capture; without it the watch has nothing to stand on.
* ``anchor_label_snapshot_id`` RESTRICT — label versions are never deleted, and
  a delete that tried should be refused rather than silently detach a watch.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "k9l0m1n2o3"
down_revision = "j8k9l0m1n2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_watches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("anchor_scan_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anchor_label_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anchor_label_version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "notice_cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["anchor_scan_event_id"], ["scan_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["anchor_label_snapshot_id"], ["product_label_snapshots.id"], ondelete="RESTRICT",
        ),
        sa.CheckConstraint("anchor_label_version >= 1", name="ck_product_watch_anchor_version"),
        sa.CheckConstraint(
            "(active AND stopped_at IS NULL) OR (NOT active AND stopped_at IS NOT NULL)",
            name="ck_product_watch_active_stopped",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "barcode", name="uq_product_watch_account_barcode"),
    )
    op.create_index(
        "ix_product_watches_anchor_scan_event", "product_watches", ["anchor_scan_event_id"],
    )
    op.create_index(
        "ix_product_watches_anchor_snapshot", "product_watches", ["anchor_label_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_watches_anchor_snapshot", table_name="product_watches")
    op.drop_index("ix_product_watches_anchor_scan_event", table_name="product_watches")
    op.drop_table("product_watches")
