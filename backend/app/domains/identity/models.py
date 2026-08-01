"""The account link.

**Why a link table and not a copy of the user.**

V1 owns users: email, password hash, plan, quotas, invite. Copying any of that
into PostgreSQL creates two sources of truth and a synchronisation problem, and
the instruction is explicit — do not duplicate existing users carelessly.

So V2 stores no user attributes at all. It stores one row per user containing
the V1 user id and nothing else identifying. Every V2 table hangs off
``account_links.id``. Authentication stays entirely V1.

The link is created lazily on a user's first V2 request, so there is no
migration and no backfill.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class AccountLink(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "account_links"

    # The UUID string from Mongo `users.id`. Not a foreign key — it points at a
    # different database — so it is indexed and unique here instead.
    v1_user_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # active | deletion_requested | deleted
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    deletion_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_account_links_v1_user_id", "v1_user_id"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AccountLink id={self.id} v1_user_id={self.v1_user_id}>"


ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_DELETION_REQUESTED = "deletion_requested"
ACCOUNT_STATUS_DELETED = "deleted"
