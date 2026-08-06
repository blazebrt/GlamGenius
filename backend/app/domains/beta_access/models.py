"""ORM models for the beta_access domain."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey, new_uuid


class Invite(UUIDPrimaryKey, TimestampMixin, Base):
    """An invite code issued by an admin.

    The code is unique across the project. ``max_uses`` caps how many distinct
    accounts may redeem it; ``active`` is a soft off-switch that leaves audit
    history intact.
    """

    __tablename__ = "invites"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    uses_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # UUID of the admin (Supabase user) that issued the invite. Nullable
    # because bootstrap invites are seeded before there is an admin row.
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("ix_invites_code_active", "code", "active"),
    )


class InviteRedemption(TimestampMixin, Base):
    """One row per successful redemption. The unique constraint on
    ``(invite_id, account_id)`` blocks re-redemption of the same code by the
    same account."""

    __tablename__ = "invite_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=new_uuid)
    invite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invites.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("invite_id", "account_id", name="uq_invite_redemption"),
        Index("ix_invite_redemptions_account", "account_id"),
    )


class InviteRegistrationReservation(TimestampMixin, Base):
    """Short-lived hold on an invite created BEFORE Supabase sign-up.

    The reservation is the fix for §1 of the hardening spec: the previous
    flow created a Supabase Auth identity first and only then asked the
    backend whether the invite was valid, so a rejected invite left an
    orphan Supabase user. Now the client calls
    ``POST /api/v2/access/reserve`` (unauthenticated), receives a
    single-use challenge, then completes Supabase sign-up, then calls
    ``POST /api/v2/access/register`` with the challenge. Invite usage is
    only bumped when the finalisation transaction commits.

    We store ``challenge_hash`` (SHA-256 of a URL-safe random 32-byte
    challenge) — the raw challenge is only ever held by the client.
    ``email_normalised`` binds the reservation to the email supplied at
    reserve-time; finalisation must present the same email in the Supabase
    JWT. ``supabase_user_id`` is populated at finalisation, purely so an
    audit trail exists.
    """

    __tablename__ = "invite_registration_reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    invite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invites.id", ondelete="CASCADE"), nullable=False
    )
    email_normalised: Mapped[str] = mapped_column(String(320), nullable=False)
    challenge_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # active | consumed | expired
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supabase_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("challenge_hash", name="uq_invite_reservation_challenge_hash"),
        Index(
            "ix_invite_reservation_invite_active",
            "invite_id",
            "status",
        ),
        Index(
            "ix_invite_reservation_email_active",
            "email_normalised",
            "status",
        ),
    )


class BetaUsageEvent(Base):
    """One row per counted, successful, cost-bearing action.

    Not a financial ledger. Read exclusively by the beta abuse-and-cost limiter
    to decide whether an account has exceeded a per-hour or per-month cap.

    Failed AI runs are *not* recorded here. Retried idempotent work is
    deduplicated by the unique index on ``(account_id, feature, idempotency_key)``.
    """

    __tablename__ = "beta_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=new_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "scan.analyse", "style.recommendations", "shopping.evaluate",
    # "ai.request".
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    # An opaque key the caller supplies so the same logical operation retried
    # twice does not count twice. NULL is allowed for events without a
    # deduplication key.
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # YYYY-MM for month buckets, YYYY-MM-DD HH for hour buckets. Duplicated
    # from ``created_at`` so a plain COUNT(*) with a WHERE key match is a
    # cheap indexed scan.
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "feature",
            "idempotency_key",
            name="uq_beta_usage_idempotency",
        ),
        Index("ix_beta_usage_account_feature_period", "account_id", "feature", "period_key"),
        Index("ix_beta_usage_created_at", "created_at"),
    )
