"""Transactional outbox.

An event row is written in the *same transaction* as the change that caused it.
Either both land or neither does, so background work can never be triggered for
a change that was rolled back — and can never be silently skipped for one that
committed.

Nothing consumes these yet. Phase 1 establishes the write side and the relay;
handlers arrive with the first feature that needs asynchronous work.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class OutboxEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "domain_outbox"

    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    dispatched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # The relay's only query: undispatched, oldest first.
        Index("ix_domain_outbox_undispatched", "dispatched_at", "created_at"),
    )
