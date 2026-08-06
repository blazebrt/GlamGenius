"""Product analytics.

Separate from ``audit_events`` on purpose. Audit is a compliance record that
must never be pruned; analytics is disposable product telemetry with a retention
policy. Mixing them means either keeping telemetry forever or deleting evidence.

No free-text user content goes in ``properties`` — event names and counts only.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class AppEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "app_events"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_app_events_name_created", "name", "created_at"),
        Index("ix_app_events_account", "account_id"),
    )
