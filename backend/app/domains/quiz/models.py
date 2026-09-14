"""Legacy quiz storage retained solely for migration and privacy-export safety.

The appearance quiz is no longer an active GlamGenius product surface.  This
mapping deliberately remains registered so an upgrade does not silently drop
historical customer records.  No router or active domain service may import it.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class QuizSubmission(UUIDPrimaryKey, TimestampMixin, Base):
    """LEGACY_STORAGE_ONLY: historical appearance-quiz submission."""

    __tablename__ = "quiz_submissions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(24), nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    derived_style_vibe: Mapped[str] = mapped_column(
        String(48), nullable=False, default="", server_default=""
    )

    __table_args__ = (
        Index("ix_quiz_submissions_account_created", "account_id", "created_at"),
    )
