from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class OfficialSourceFetch(UUIDPrimaryKey, TimestampMixin, Base):
    """One attempted read of the official FSSAI public recall surface."""

    __tablename__ = "official_source_fetches"
    authority: Mapped[str] = mapped_column(String(64), nullable=False)
    record_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed')", name="ck_official_fetch_status"),
        Index("ix_official_fetch_authority_fetched", "authority", "fetched_at"),
    )


class OfficialRecord(UUIDPrimaryKey, TimestampMixin, Base):
    """Canonical identity for an official record; never deleted on absence."""

    __tablename__ = "official_records"
    authority: Mapped[str] = mapped_column(String(64), nullable=False)
    record_type: Mapped[str] = mapped_column(String(48), nullable=False)
    external_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fbo_name: Mapped[str | None] = mapped_column(String(256))
    licence: Mapped[str | None] = mapped_column(String(32))
    batch_lot: Mapped[str | None] = mapped_column(String(160))
    brand_name: Mapped[str | None] = mapped_column(String(256))
    product_name: Mapped[str | None] = mapped_column(String(512))
    reason: Mapped[str | None] = mapped_column(Text)
    recall_start_date: Mapped[date | None] = mapped_column(Date)
    recall_status: Mapped[str | None] = mapped_column(String(80))
    recall_termination_date: Mapped[date | None] = mapped_column(Date)
    nature_of_recall: Mapped[str | None] = mapped_column(String(256))
    license_type: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        UniqueConstraint("authority", "record_type", "external_record_id", name="uq_official_record_identity"),
        Index("ix_official_record_pack_match", "licence", "batch_lot"),
        Index("ix_official_record_type_status", "record_type", "recall_status"),
    )


class OfficialRecordRevision(UUIDPrimaryKey, TimestampMixin, Base):
    """Immutable raw revision, retained even when the source changes or vanishes."""

    __tablename__ = "official_record_revisions"
    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("official_records.id", ondelete="CASCADE"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("record_id", "revision_number", name="uq_official_record_revision"),
        Index("ix_official_revision_record_observed", "record_id", "observed_at"),
    )
