"""Relational models for the Phase 2 appearance digital twin."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class AppearanceProfile(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "appearance_profiles"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    baseline_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_started", server_default="not_started")
    baseline_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ProfileAttribute(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "profile_attributes"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="confirmed", server_default="confirmed")
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint("profile_id", "key", name="uq_profile_attribute_key"), Index("ix_profile_attributes_profile_section", "profile_id", "key"))


class StylePreference(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "style_preferences"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False, unique=True)
    preferred_style: Mapped[Optional[str]] = mapped_column(String(120))
    favourite_colours: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    disliked_colours: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    brand_preferences: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    formality_preference: Mapped[Optional[str]] = mapped_column(String(120))
    style_experimentation: Mapped[Optional[str]] = mapped_column(String(24))
    indian_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    western_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    fusion_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class FitPreference(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "fit_preferences"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False, unique=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Numeric(5, 1))
    usual_top_size: Mapped[Optional[str]] = mapped_column(String(32))
    usual_bottom_size: Mapped[Optional[str]] = mapped_column(String(32))
    shoulder_fit: Mapped[Optional[str]] = mapped_column(String(120))
    waist_fit: Mapped[Optional[str]] = mapped_column(String(120))
    hip_fit: Mapped[Optional[str]] = mapped_column(String(120))
    preferred_coverage: Mapped[Optional[str]] = mapped_column(String(120))
    sleeve_preferences: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    neckline_preferences: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    footwear_comfort: Mapped[Optional[str]] = mapped_column(String(120))
    silhouettes_liked: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    silhouettes_avoided: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class LifestyleContext(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "lifestyle_context"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False, unique=True)
    city: Mapped[Optional[str]] = mapped_column(String(120))
    climate: Mapped[Optional[str]] = mapped_column(String(120))
    work_context: Mapped[Optional[str]] = mapped_column(String(160))
    routine: Mapped[Optional[str]] = mapped_column(String(200))
    activity_level: Mapped[Optional[str]] = mapped_column(String(80))
    travel_frequency: Mapped[Optional[str]] = mapped_column(String(80))
    budget: Mapped[Optional[str]] = mapped_column(String(80))
    calendar_patterns: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class AppearanceGoal(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "appearance_goals"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False)
    goal: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    __table_args__ = (Index("ix_appearance_goals_profile_status", "profile_id", "status"),)


class UserConstraint(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "user_constraints"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(300), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    __table_args__ = (Index("ix_user_constraints_profile_active", "profile_id", "active"),)


class AttributeObservation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "attribute_observations"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    why: Mapped[str] = mapped_column(Text, nullable=False)
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified", server_default="unverified")
    source_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_attribute_observations_profile_state", "profile_id", "verification_state", "created_at"), Index("ix_attribute_observations_run_key", "source_ai_run_id", "key"))


class OnboardingSession(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "onboarding_sessions"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="in_progress", server_default="in_progress")
    current_step: Mapped[str] = mapped_column(String(48), nullable=False, default="goal", server_default="goal")
    completed_steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    skipped_steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    recommendation_preview: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_onboarding_sessions_profile_status", "profile_id", "status", "created_at"),)


class ProfileChangeEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "profile_change_events"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appearance_profiles.id", ondelete="CASCADE"), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attribute_key: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[Optional[Any]] = mapped_column(JSONB)
    new_value: Mapped[Optional[Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    __table_args__ = (Index("ix_profile_change_events_profile_version", "profile_id", "profile_version", "created_at"),)
