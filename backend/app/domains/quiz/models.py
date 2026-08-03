"""Quiz ORM."""
from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class QuizSubmission(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "quiz_submissions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # The versioned answer schema this submission was recorded against.
    schema_version: Mapped[str] = mapped_column(String(24), nullable=False)
    # {"question_id": "answer", ...}
    answers: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Derived style_vibe from the answers, cached for cheap read.
    derived_style_vibe: Mapped[str] = mapped_column(String(48), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("ix_quiz_submissions_account_created", "account_id", "created_at"),
    )
