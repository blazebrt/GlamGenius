"""The one fact Step 15 persists that nothing else already knows.

An :class:`~app.domains.beta_access.models.Invite` is the registration
capability, and it stays the only authority for its code, use count, ceiling,
expiry and whether it is switched on. None of those are copied here. What an
invite does not know is *who* was given it to share: ``Invite.created_by`` is
the issuing admin's provenance, and overloading it with a customer would mix
two meanings in one column and leave an inviter's UUID on a row that outlives
their account.

So the binding lives in its own account-owned table. It says exactly one
thing — this invite capability was issued to this account under this growth
program — and it leaves with the account: ``inviter_account_id`` cascades, and
the invite itself is switched off before that happens (see
``app/domains/privacy/deletion_service.py``). ``invite_id`` is ``RESTRICT``:
invites are never deleted, and a delete that tried should be refused rather
than silently orphan the lifetime ceiling this row counts toward.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

#: The only program this table can describe. A later program is a new value and
#: a reviewed migration, never a silent reuse of this one's ceiling.
PROGRAM_VERSION = "consumer-referral-v1"


class ConsumerReferralInvite(UUIDPrimaryKey, TimestampMixin, Base):
    """This invite was issued to this account to share. Nothing more."""

    __tablename__ = "consumer_referral_invites"

    inviter_account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    invite_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("invites.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    program_version: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"program_version IN ('{PROGRAM_VERSION}')",
            name="ck_consumer_referral_invites_program",
        ),
        Index("ix_consumer_referral_invites_inviter", "inviter_account_id", "created_at"),
    )
