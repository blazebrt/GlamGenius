"""V2 foundation tables

Creates the ten PostgreSQL tables that Phase 1 introduces. MongoDB is not
touched by this migration or any other — V1 keeps its own database.

Revision ID: 0001_v2_foundation
Revises:
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_v2_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIMESTAMPS = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)


def upgrade() -> None:
    # --- identity: the bridge to the V1 Mongo user -------------------------
    op.create_table(
        "account_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("v1_user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="active", nullable=False
        ),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        *TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("v1_user_id"),
    )
    op.create_index(
        "ix_account_links_v1_user_id", "account_links", ["v1_user_id"], unique=False
    )

    # --- feature flags ------------------------------------------------------
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "rollout",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        *TIMESTAMPS,
        sa.PrimaryKeyConstraint("key"),
    )

    # --- transactional outbox ----------------------------------------------
    op.create_table(
        "domain_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_domain_outbox_undispatched",
        "domain_outbox",
        ["dispatched_at", "created_at"],
        unique=False,
    )

    # --- consent ------------------------------------------------------------
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("consent_type", sa.String(length=64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="app", nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["account_id"], ["account_links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_consents_account_type",
        "consents",
        ["account_id", "consent_type", "recorded_at"],
        unique=False,
    )

    # --- media --------------------------------------------------------------
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("storage_backend", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["account_id"], ["account_links.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_media_assets_account_status",
        "media_assets",
        ["account_id", "status"],
        unique=False,
    )
    op.create_index("ix_media_assets_sha256", "media_assets", ["sha256"], unique=False)

    # --- AI runs ------------------------------------------------------------
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_type", sa.String(length=48), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("validation_passed", sa.Boolean(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "allowance_consumed", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(
            ["account_id"], ["account_links.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_runs_account_created", "ai_runs", ["account_id", "created_at"], unique=False
    )
    op.create_index("ix_ai_runs_feature", "ai_runs", ["feature"], unique=False)
    op.create_index(
        "ix_ai_runs_status_created", "ai_runs", ["status", "created_at"], unique=False
    )

    op.create_table(
        "ai_run_outputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ai_run_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=24),
            server_default="unverified",
            nullable=False,
        ),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["ai_run_id"], ["ai_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_run_outputs_run", "ai_run_outputs", ["ai_run_id"], unique=False)

    # --- entitlements -------------------------------------------------------
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(length=7), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("ai_run_id", sa.Uuid(), nullable=True),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(["account_id"], ["account_links.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ai_run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_ledger_account_period",
        "usage_ledger",
        ["account_id", "period_key"],
        unique=False,
    )
    op.create_index("ix_usage_ledger_feature", "usage_ledger", ["feature"], unique=False)

    # --- audit --------------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column(
            "actor_type", sa.String(length=16), server_default="user", nullable=False
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=True),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(
            ["account_id"], ["account_links.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_account_created",
        "audit_events",
        ["account_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"], unique=False)

    # --- analytics ----------------------------------------------------------
    op.create_table(
        "app_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        *TIMESTAMPS,
        sa.ForeignKeyConstraint(
            ["account_id"], ["account_links.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_events_account", "app_events", ["account_id"], unique=False)
    op.create_index(
        "ix_app_events_name_created", "app_events", ["name", "created_at"], unique=False
    )


def downgrade() -> None:
    # Reverse dependency order so foreign keys never block a drop.
    op.drop_index("ix_app_events_name_created", table_name="app_events")
    op.drop_index("ix_app_events_account", table_name="app_events")
    op.drop_table("app_events")

    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_account_created", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_usage_ledger_feature", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_account_period", table_name="usage_ledger")
    op.drop_table("usage_ledger")

    op.drop_index("ix_ai_run_outputs_run", table_name="ai_run_outputs")
    op.drop_table("ai_run_outputs")

    op.drop_index("ix_ai_runs_status_created", table_name="ai_runs")
    op.drop_index("ix_ai_runs_feature", table_name="ai_runs")
    op.drop_index("ix_ai_runs_account_created", table_name="ai_runs")
    op.drop_table("ai_runs")

    op.drop_index("ix_media_assets_sha256", table_name="media_assets")
    op.drop_index("ix_media_assets_account_status", table_name="media_assets")
    op.drop_table("media_assets")

    op.drop_index("ix_consents_account_type", table_name="consents")
    op.drop_table("consents")

    op.drop_index("ix_domain_outbox_undispatched", table_name="domain_outbox")
    op.drop_table("domain_outbox")

    op.drop_table("feature_flags")

    op.drop_index("ix_account_links_v1_user_id", table_name="account_links")
    op.drop_table("account_links")
