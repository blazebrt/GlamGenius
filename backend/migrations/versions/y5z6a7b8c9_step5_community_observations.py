"""Add structured shopper observation reports.

Revision ID: y5z6a7b8c9
Revises: x4y5z6a7b8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "y5z6a7b8c9"
down_revision: Union[str, None] = "x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OBSERVATION_CODES = (
    "barcode_result_differs_from_pack",
    "date_marking_unreadable",
    "ingredients_list_differs_from_app",
    "insect_observed",
    "nutrition_panel_differs_from_app",
    "pack_leaking",
    "pack_size_differs_from_app",
    "pack_swollen",
    "seal_broken",
    "visible_foreign_material",
)
STATUSES = ("accepted", "invalid", "under_review", "withdrawn")
MODERATION_REASONS = (
    "duplicate_evidence",
    "policy_violation",
    "unsupported_media",
    "wrong_batch_context",
    "wrong_product_context",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "community_observation_reports",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_report_id", sa.String(64), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("observation_code", sa.String(48), nullable=False),
        # Nullable although required at submission: a photo may be deleted
        # later, and the audit row must outlive that without blocking it. A row
        # with no live photo stops supporting a public signal.
        sa.Column("photo_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        # The exact scan that established the report's context — the
        # authoritative physical-pack provenance.
        sa.Column("scan_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("batch_number", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="accepted"),
        sa.Column("moderation_reason", sa.String(48), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # The person's report leaves with the person.
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["scan_devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["photo_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scan_event_id"], ["scan_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["label_snapshot_id"], ["product_label_snapshots.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("account_id", "client_report_id", name="uq_community_report_idempotency"),
        sa.CheckConstraint(_in_list("observation_code", OBSERVATION_CODES),
                           name="ck_community_observation_code"),
        sa.CheckConstraint(_in_list("status", STATUSES), name="ck_community_report_status"),
        sa.CheckConstraint(
            f"moderation_reason IS NULL OR {_in_list('moderation_reason', MODERATION_REASONS)}",
            name="ck_community_moderation_reason",
        ),
    )
    op.create_index(
        "ix_community_report_product_signal", "community_observation_reports",
        ["barcode", "observation_code", "status", "created_at"],
    )
    op.create_index(
        "ix_community_report_batch_signal", "community_observation_reports",
        ["barcode", "observation_code", "batch_number", "status", "created_at"],
    )
    op.create_index(
        "ix_community_report_account", "community_observation_reports",
        ["account_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_community_report_account", table_name="community_observation_reports")
    op.drop_index("ix_community_report_batch_signal", table_name="community_observation_reports")
    op.drop_index("ix_community_report_product_signal", table_name="community_observation_reports")
    op.drop_table("community_observation_reports")
