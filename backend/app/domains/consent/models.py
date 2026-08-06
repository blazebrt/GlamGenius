"""Consent records.

Append-only by design. Granting and revoking both write a new row rather than
mutating an old one, so "when did they agree, and to which wording" is always
answerable. The current state is the newest row for that consent type.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

CONSENT_PHOTO_ANALYSIS = "photo_analysis"
CONSENT_MEDIA_STORAGE = "media_storage"


class Consent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "consents"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Which wording they agreed to. If the wording changes, the version changes
    # and the old agreement no longer counts.
    version: Mapped[str] = mapped_column(String(32), nullable=False)

    # app | api | admin — where the answer came from.
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="app", server_default="app"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_consents_account_type", "account_id", "consent_type", "recorded_at"),
    )
