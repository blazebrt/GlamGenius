"""Accounts — Supabase-keyed.

The account's primary key **is** the Supabase Auth user UUID. Every V2 table
foreign-keys against ``accounts.id`` and cascades on delete. There is no
separate ``account_id_str`` and no bridge table.

Why keep an explicit table at all rather than a bare UUID?

* Ownership: every child table cascades cleanly on account deletion.
* Lifecycle: we can mark an account as ``deletion_requested`` before the
  destructive privacy workflow runs.
* Provenance: cheap place to store the account's ``created_at``.

The identity itself lives in Supabase Auth. This table stores no email, no
password, no phone, no Supabase-managed profile field.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    # This UUID is the Supabase Auth user id. We never generate one — the
    # caller must supply the value that came from the verified access token.
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)

    # active | deletion_requested | deleted
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Account id={self.id} status={self.status}>"


ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_DELETION_REQUESTED = "deletion_requested"
ACCOUNT_STATUS_DELETED = "deleted"


# Backwards-compatible alias while the domain modules still spell it
