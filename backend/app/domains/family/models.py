"""Account-local Family Circle profiles."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class FamilyCircle(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "family_circles"
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class FamilyProfile(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "family_profiles"
    circle_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("family_circles.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # A controlled relation, not free-text identity data.
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    __table_args__ = (
        UniqueConstraint("circle_id", "position", name="uq_family_profile_position"),
        CheckConstraint("position >= 1 AND position <= 8", name="ck_family_profile_position"),
        CheckConstraint("relation IN ('self', 'adult', 'child', 'other')", name="ck_family_profile_relation"),
        Index("ix_family_profiles_circle_active", "circle_id", "active"),
    )
