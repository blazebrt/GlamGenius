"""Media asset records.

The ``storage_key`` is internal and is never returned by any route — the audit
called this out, and exposing it would leak the bucket layout and invite people
to guess at other users' keys. Callers only ever see the asset's own UUID.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

MEDIA_STATUS_ACTIVE = "active"
MEDIA_STATUS_DELETED = "deleted"

# Why the image was uploaded. Drives which consent applies.
MEDIA_PURPOSE_ANALYSIS = "photo_analysis"
MEDIA_PURPOSE_INVENTORY = "inventory_item"


class MediaAsset(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "media_assets"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )

    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)

    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Lets a duplicate upload be spotted without storing the image twice, and
    # gives deletion something to verify against.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MEDIA_STATUS_ACTIVE, server_default="active"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_media_assets_account_status", "account_id", "status"),
        Index("ix_media_assets_sha256", "sha256"),
    )
