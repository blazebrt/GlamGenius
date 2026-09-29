"""Scan ORM.

No image base64 stored — only the structured analysis, model provenance and
timing. Face photos are analysed then discarded.

``idempotency_key`` is the client's name for one logical photo check, kept on
the successful row only, so a retry with the same key replays that row instead
of analysing — and paying for — the photo again. It is the client's identifier,
never anything derived from the image: no bytes, hash or fingerprint.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.beta_access.models import IDEMPOTENCY_KEY_MAX_LENGTH
from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

#: The status of an analysis that succeeded and was counted.
SCAN_STATUS_OK = "ok"


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
    # The client's logical-operation key, on a successful row only. A failed
    # attempt never carries it, so a known failure never uses the key up.
    # Internal request control: not exported.
    idempotency_key: Mapped[str | None] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), nullable=True)

    __table_args__ = (
        Index("ix_scans_account_created", "account_id", "created_at"),
        # One successful result per account and key: the row a retry replays.
        Index(
            "uq_scans_account_idempotency_key",
            "account_id", "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint(
            f"idempotency_key IS NULL OR status = '{SCAN_STATUS_OK}'",
            name="ck_scans_idempotency_key_successful_only",
        ),
    )
