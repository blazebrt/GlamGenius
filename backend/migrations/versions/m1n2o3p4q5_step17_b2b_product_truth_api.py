"""B2B Product Truth API for Step 17

Revision ID: m1n2o3p4q5
Revises: l0m1n2o3p4

Three small, additive tables for controlled pilot access to the read-only
B2B Product Truth API. Nothing existing is altered.

1. ``b2b_api_clients`` — an organisation GlamGenius chose to give access to:
   a unique operator slug, an operator-only label, ``active``/``suspended``,
   and two bounded limits (per minute, per day). No price, contract value,
   commission or charging field, and no free-form JSON.

2. ``b2b_api_keys`` — one row per issued credential: a unique public prefix
   and the SHA-256 of the whole credential. ``key_hash`` is constrained to 64
   lowercase hex characters, so the database itself refuses a raw ``ggb_…``
   key. Revoked or expired, never deleted in the normal lifecycle.

3. ``b2b_api_usage_daily`` — aggregate counts per client per UTC day,
   keyed ``(client_id, usage_date)`` so the daily allowance is taken by one
   atomic ``INSERT … ON CONFLICT DO UPDATE … WHERE`` against that key. No
   barcode, no answer, no request detail.

No table references ``accounts``. Every foreign key is ``RESTRICT`` and
written out explicitly: Alembic's autogenerate does not compare ``ondelete``,
so an implicit one would pass ``alembic check`` and behave differently.

No Product Truth answer is stored anywhere: it is computed per request from
Store B and the published rules. Nothing here touches Store A.

Downgrade drops exactly these three tables, children first, and nothing else.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m1n2o3p4q5"
down_revision = "l0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "b2b_api_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("client_key", sa.String(48), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("requests_per_day", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_key", name="uq_b2b_api_clients_client_key"),
        sa.CheckConstraint(
            "client_key ~ '^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$'", name="ck_b2b_api_clients_client_key",
        ),
        sa.CheckConstraint(
            "length(btrim(display_name)) BETWEEN 1 AND 120", name="ck_b2b_api_clients_display_name",
        ),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_b2b_api_clients_status"),
        sa.CheckConstraint(
            "requests_per_minute BETWEEN 1 AND 600", name="ck_b2b_api_clients_requests_per_minute",
        ),
        sa.CheckConstraint(
            "requests_per_day BETWEEN 1 AND 1000000", name="ck_b2b_api_clients_requests_per_day",
        ),
    )

    op.create_table(
        "b2b_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["client_id"], ["b2b_api_clients.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("key_prefix", name="uq_b2b_api_keys_key_prefix"),
        sa.UniqueConstraint("key_hash", name="uq_b2b_api_keys_key_hash"),
        sa.CheckConstraint("key_prefix ~ '^[0-9a-f]{12}$'", name="ck_b2b_api_keys_key_prefix"),
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_b2b_api_keys_key_hash"),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="ck_b2b_api_keys_expiry_after_issue",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at", name="ck_b2b_api_keys_revoked_after_issue",
        ),
    )
    op.create_index("ix_b2b_api_keys_client_created", "b2b_api_keys", ["client_id", "created_at"])

    op.create_table(
        "b2b_api_usage_daily",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_enough_information_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_limited_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("client_id", "usage_date"),
        sa.ForeignKeyConstraint(["client_id"], ["b2b_api_clients.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "request_count >= 0 AND successful_count >= 0 "
            "AND not_enough_information_count >= 0 AND rate_limited_count >= 0",
            name="ck_b2b_api_usage_daily_non_negative",
        ),
        sa.CheckConstraint(
            "successful_count + not_enough_information_count <= request_count",
            name="ck_b2b_api_usage_daily_outcomes_within_requests",
        ),
    )


def downgrade() -> None:
    op.drop_table("b2b_api_usage_daily")
    op.drop_index("ix_b2b_api_keys_client_created", table_name="b2b_api_keys")
    op.drop_table("b2b_api_keys")
    op.drop_table("b2b_api_clients")
