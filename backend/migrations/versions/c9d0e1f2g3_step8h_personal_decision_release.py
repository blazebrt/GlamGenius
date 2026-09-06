"""Step 8H: the governed personal decision knowledge release table.

One new Store-B table. No data is seeded: the machinery for releasing reviewed
decision knowledge exists after this migration, and no reviewed knowledge does.
Production therefore still emits no BUY / WAIT / SKIP.

The load-bearing object here is the partial unique index. "At most one active
release" cannot be an application convention: two admins pressing activate at
the same moment both read one active release, both retire it, and both insert
their own. The index makes the second one fail in the database, where the race
actually is.

Nothing in Open Food Facts' Store A is touched.

Revision ID: c9d0e1f2g3
Revises: b8c9d0e1f2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9d0e1f2g3"
down_revision = "b8c9d0e1f2"
branch_labels = None
depends_on = None

_STATUSES = ("draft", "approved", "active", "retired")

_ACTIVE_INDEX = "uq_personal_decision_releases_active"


def upgrade() -> None:
    op.create_table(
        "personal_decision_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("release_key", sa.String(length=120), nullable=False),
        sa.Column("release_version", sa.Integer(), nullable=False),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("review_verification", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.String(length=160), nullable=False),
        sa.Column("approved_by", sa.String(length=160), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", sa.String(length=160), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.String(length=160), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_release_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        # RESTRICT: lineage must not be able to outlive what it points at.
        sa.ForeignKeyConstraint(
            ["supersedes_release_id"], ["personal_decision_releases.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "release_key", "release_version", name="uq_personal_decision_release_version"
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in _STATUSES) + ")",
            name="ck_personal_decision_releases_status",
        ),
        sa.CheckConstraint("release_version > 0", name="ck_personal_decision_releases_version"),
        sa.CheckConstraint("btrim(release_key) <> ''", name="ck_personal_decision_releases_key"),
        sa.CheckConstraint(
            "btrim(created_by) <> ''", name="ck_personal_decision_releases_created_by"
        ),
        # The hash is an immutability guard, so its shape is enforced here as
        # well as in the application: a truncated or upper-case value would
        # silently never match a recomputation.
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_personal_decision_releases_hash_shape"
        ),
    )
    # At most one active release per series, enforced by the database rather
    # than by whichever request happened to read first.
    op.create_index(
        _ACTIVE_INDEX,
        "personal_decision_releases",
        ["release_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(_ACTIVE_INDEX, table_name="personal_decision_releases")
    op.drop_table("personal_decision_releases")
