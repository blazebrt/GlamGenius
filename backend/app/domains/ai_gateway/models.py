"""AI run ledger.

Audit finding F24: no AI call was recorded anywhere, so a bad result could not
be investigated after the fact. Every call through the gateway now writes one
``ai_runs`` row — success or failure — and a successful, schema-validated result
additionally writes one ``ai_run_outputs`` row carrying its provenance.

A row is written even when the provider is not configured. "We never called
anyone" is itself the answer to "why did this fail".
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

AI_STATUS_SUCCEEDED = "succeeded"
AI_STATUS_FAILED = "failed"

VERIFICATION_UNVERIFIED = "unverified"
VERIFICATION_USER_CONFIRMED = "user_confirmed"
VERIFICATION_USER_REJECTED = "user_rejected"


class AIRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ai_runs"

    # Nullable: the signed-out preview has no account, and it still costs money,
    # so it still gets recorded.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    # Message only, never a stack trace and never provider credentials.
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Numeric, not float — this gets summed into spend reports.
    estimated_cost_usd: Mapped[float | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )

    # Did this run cost the user one of their monthly checks? A failed run must
    # always be False, and there is a test that proves it.
    allowance_consumed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_ai_runs_account_created", "account_id", "created_at"),
        Index("ix_ai_runs_status_created", "status", "created_at"),
        Index("ix_ai_runs_feature", "feature"),
    )


class AIRunOutput(UUIDPrimaryKey, TimestampMixin, Base):
    """A validated AI result, with the provenance needed to trust or discard it."""

    __tablename__ = "ai_run_outputs"

    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # The model's own stated confidence, not ours.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=VERIFICATION_UNVERIFIED,
        server_default=VERIFICATION_UNVERIFIED,
    )

    __table_args__ = (Index("ix_ai_run_outputs_run", "ai_run_id"),)
