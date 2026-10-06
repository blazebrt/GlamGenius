"""Durable authority for uncertain label-report uploads.

Revision ID: o3p4q5r6s7
Revises: n2o3p4q5r6
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "o3p4q5r6s7"
down_revision = "n2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "label_report_resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scan_devices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("client_report_id", sa.String(64), nullable=False),
        sa.Column("photo_key", sa.String(200), nullable=False),
        sa.Column("photo_byte_size", sa.Integer(), nullable=False),
        sa.Column("write_state", sa.String(16), server_default="unknown", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("photo_key", name="uq_label_report_resources_photo_key"),
        sa.UniqueConstraint("device_id", "client_report_id", name="uq_label_report_resource_identity"),
        sa.CheckConstraint("photo_byte_size BETWEEN 1 AND 6291456", name="ck_label_report_resource_size"),
        sa.CheckConstraint("write_state IN ('unknown', 'complete')", name="ck_label_report_resource_state"),
    )
    op.create_index("ix_label_report_resources_account_id", "label_report_resources", ["account_id"])
    # Server-side operational authority only, never a public Data API table.
    op.execute("ALTER TABLE label_report_resources ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON label_report_resources FROM PUBLIC")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON label_report_resources FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON label_report_resources FROM authenticated;
        END IF;
    END $$""")


def downgrade() -> None:
    # Dropping unresolved authority would recreate the orphan vulnerability.
    # Block concurrent reservations as well as checking populated state.
    op.execute("LOCK TABLE label_report_resources IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM label_report_resources)")):
        raise RuntimeError("Reconcile label report resources before downgrade; durable upload authority cannot be discarded")
    op.drop_table("label_report_resources")
