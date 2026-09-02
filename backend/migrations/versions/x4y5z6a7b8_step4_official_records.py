"""Add auditable FSSAI official-record fetches and revisions.

Revision ID: x4y5z6a7b8
Revises: w3x4y5z6a7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "x4y5z6a7b8"
down_revision: Union[str, None] = "w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "official_source_fetches",
        sa.Column("authority", sa.String(64), nullable=False),
        sa.Column("record_type", sa.String(48), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("source_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("source_file_sha256", sa.String(64), nullable=True),
        sa.Column("source_format", sa.String(16), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(256), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_official_fetch_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_official_fetch_authority_checked", "official_source_fetches", ["authority", "source_checked_at"])
    op.create_table(
        "official_records",
        sa.Column("authority", sa.String(64), nullable=False), sa.Column("record_type", sa.String(48), nullable=False),
        sa.Column("external_record_id", sa.String(128), nullable=False), sa.Column("fbo_name", sa.String(256)), sa.Column("licence", sa.String(32)),
        sa.Column("batch_lot", sa.String(160)), sa.Column("brand_name", sa.String(256)), sa.Column("product_name", sa.String(512)),
        sa.Column("reason", sa.Text()), sa.Column("recall_start_date", sa.Date()), sa.Column("recall_status", sa.String(80)),
        sa.Column("recall_termination_date", sa.Date()), sa.Column("nature_of_recall", sa.String(256)), sa.Column("license_type", sa.String(80)),
        sa.Column("source_url", sa.Text(), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_fetch_id", sa.Uuid(), nullable=True),
        sa.Column("latest_revision", sa.Integer(), server_default="1", nullable=False), sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["last_seen_fetch_id"], ["official_source_fetches.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("authority", "record_type", "external_record_id", name="uq_official_record_identity"),
    )
    op.create_index("ix_official_record_pack_match", "official_records", ["licence", "batch_lot"])
    op.create_index("ix_official_record_type_status", "official_records", ["record_type", "recall_status"])
    op.create_table(
        "official_record_revisions",
        sa.Column("record_id", sa.Uuid(), nullable=False), sa.Column("source_fetch_id", sa.Uuid(), nullable=False), sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["official_records.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["source_fetch_id"], ["official_source_fetches.id"]), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "revision_number", name="uq_official_record_revision"),
    )
    op.create_index("ix_official_revision_record_observed", "official_record_revisions", ["record_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_official_revision_record_observed", table_name="official_record_revisions")
    op.drop_table("official_record_revisions")
    op.drop_index("ix_official_record_type_status", table_name="official_records")
    op.drop_index("ix_official_record_pack_match", table_name="official_records")
    op.drop_table("official_records")
    op.drop_index("ix_official_fetch_authority_checked", table_name="official_source_fetches")
    op.drop_table("official_source_fetches")
