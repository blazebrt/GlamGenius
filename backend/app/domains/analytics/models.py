"""Product analytics.

Separate from ``audit_events`` on purpose. Audit is a compliance record that
must never be pruned; analytics is disposable product telemetry with a retention
policy. Mixing them means either keeping telemetry forever or deleting evidence.

No free-text user content goes in ``properties`` — event names and counts only.
There are exactly two writers, each with its own closed whitelist of event
names and property values: ``app.domains.growth.analytics`` (Step 15) and
``app.domains.commerce.analytics`` (Step 16). Rows older than the shared
retention window are pruned by ``growth.analytics.prune_opportunistically``,
which both writers run after a successful write. Nothing else may insert here.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class AppEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "app_events"

    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Step 15. An opaque operation id the client mints once per interaction, so
    # a retried telemetry write is recognised rather than counted twice. It is a
    # random UUID and never a device, install or advertising identifier.
    client_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_app_events_name_created", "name", "created_at"),
        Index("ix_app_events_account", "account_id"),
        Index(
            "uq_app_events_account_name_client_event",
            "account_id", "name", "client_event_id",
            unique=True,
            postgresql_where=text("client_event_id IS NOT NULL"),
        ),
    )
