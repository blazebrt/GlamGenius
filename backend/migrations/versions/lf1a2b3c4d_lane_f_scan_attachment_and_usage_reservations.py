"""Lane F: scan attachment eligibility and allowance reservations

Revision ID: lf1a2b3c4d
Revises: k9l0m1n2o3

Two durable facts the repository could not state, in one revision.

**``scan_events.account_attachment_allowed``.** Account erasure keeps a scan
row (a confirmed capture is shared Product Truth provenance) and severs it from
the person, so the row is left with ``account_id`` NULL. A genuinely anonymous
scan also has ``account_id`` NULL. ``attach_scans_to_account`` could not tell
the two apart, and the next account to claim the same phone was given the
deleted person's history. The column says which rows may still be attached:
``true`` only for a scan that has never belonged to anyone.

*Backfill: privacy wins.* No existing column can prove that an accountless row
was never owned — a device's claim is not a history of claims, and a scan made
while signed in on an unclaimed phone looks exactly like one made signed out
once its account is gone. So every existing row is written ``false`` by the
column default, owned rows included (an owned row is never attachable). The
cost is that anonymous scans made before this revision and never claimed will
not follow their phone into an account signed up afterwards. That is the right
side to err on: the alternative is attaching a deleted person's history to a
stranger. Scans recorded after this revision carry the real value.

A check constraint makes an owned row non-attachable by construction, so a row
an account leaves behind — through the erasure path or the ``ON DELETE SET
NULL`` cascade alone — is never attachable either.

**``beta_usage_reservations``.** The allowance check read usage, called the
provider, then recorded usage, so parallel requests could all read "one left"
and all spend. A reservation is taken atomically, under a transaction lock,
and committed before any provider call; success turns it into the usage event
and deletes it; a known failure deletes it; ``expires_at`` bounds a crash.
Operational only — no prompt, output or customer text — and it cascades with
the account.

Downgrade reverses both. It cannot keep what the column knew: after a
downgrade, erased history is once more indistinguishable from an anonymous
scan to the pre-Lane-F claim code. Re-upgrading writes every row ``false``
again, which is privacy-safe.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "lf1a2b3c4d"
down_revision = "k9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The server default is the backfill: every existing row, whatever its
    # account, becomes non-attachable in the same statement (see above).
    op.add_column(
        "scan_events",
        sa.Column(
            "account_attachment_allowed", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_scan_events_owned_not_attachable",
        "scan_events",
        "account_id IS NULL OR NOT account_attachment_allowed",
    )

    op.create_table(
        "beta_usage_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("period_key", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.CheckConstraint("quantity > 0", name="ck_beta_usage_reservations_quantity_positive"),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_beta_usage_reservations_expiry_after_creation",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_beta_usage_reservations_account_feature_period",
        "beta_usage_reservations",
        ["account_id", "feature", "period_key", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_beta_usage_reservations_account_feature_period", table_name="beta_usage_reservations",
    )
    op.drop_table("beta_usage_reservations")
    op.drop_constraint("ck_scan_events_owned_not_attachable", "scan_events", type_="check")
    op.drop_column("scan_events", "account_attachment_allowed")
