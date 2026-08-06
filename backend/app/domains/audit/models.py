"""Audit events.

Written for anything destructive or privacy-affecting: media deletion, account
deletion requests, consent changes, data export. If a user later asks "who
deleted my photo and when", this table answers it.

Deliberately stores an IP *hash*, not an IP. Enough to spot one actor behind
several actions; not a location log.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

ACTION_MEDIA_UPLOADED = "media.uploaded"
ACTION_MEDIA_DELETED = "media.deleted"
ACTION_CONSENT_GRANTED = "consent.granted"
ACTION_CONSENT_REVOKED = "consent.revoked"
ACTION_ACCOUNT_DELETION_REQUESTED = "account.deletion_requested"
ACTION_PRIVACY_EXPORTED = "privacy.exported"


class AuditEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "audit_events"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # user | system | admin
    actor_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default="user"
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    subject_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    subject_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_audit_events_account_created", "account_id", "created_at"),
        Index("ix_audit_events_action", "action"),
    )
