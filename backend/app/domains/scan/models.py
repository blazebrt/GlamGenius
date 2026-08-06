"""Scan ORM.

No image base64 stored — only the structured analysis, model provenance and
timing. Face photos are analysed then discarded.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class Scan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "scans"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # face | hair | hands | full
    scan_type: Mapped[str] = mapped_column(String(24), nullable=False)
    # ok | provider_failure | validation_failure
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok", server_default="ok")
    provider: Mapped[str | None] = mapped_column(String(48), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(48), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(48), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The structured analysis output. Never contains the input image.
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)

    __table_args__ = (
        Index("ix_scans_account_created", "account_id", "created_at"),
    )
