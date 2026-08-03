"""Feature flag storage."""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin


class FeatureFlag(TimestampMixin, Base):
    """One row per flag. The key is the primary key — flags are named, not numbered."""

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    # Reserved for percentage or cohort rollout. Unused in Phase 1; the column
    # exists so adding rollout later is not a migration on a live table.
    rollout: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
