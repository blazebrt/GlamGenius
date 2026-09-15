"""append-only scan decision events for Step 9C

Revision ID: b0c1d2e3f4
Revises: a9b0c1d2e3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b0c1d2e3f4"
down_revision = "a9b0c1d2e3"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'scan_decision_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('barcode', sa.String(length=64), nullable=False),
        sa.Column('label_snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('label_version', sa.Integer(), nullable=False),
        sa.Column('content_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('decision', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.CheckConstraint("decision IN ('BUY', 'WAIT', 'SKIP')", name="ck_scan_decision_event_decision"),
        sa.UniqueConstraint('account_id', 'idempotency_key', name='uq_scan_decision_event_idempotency'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['label_snapshot_id'], ['product_label_snapshots.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_scan_decision_events_account_barcode_created',
        'scan_decision_events',
        ['account_id', 'barcode', 'created_at'],
        unique=False
    )

def downgrade() -> None:
    op.drop_index('ix_scan_decision_events_account_barcode_created', table_name='scan_decision_events')
    op.drop_table('scan_decision_events')
