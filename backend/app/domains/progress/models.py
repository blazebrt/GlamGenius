"""Relational models for progress, memory and milestones.

Three groups.

**Reviewed reference data** — ``metric_definitions`` and ``milestone_rules``.
No ``account_id``; seeded from :mod:`.registry` and :mod:`.milestones` so a
stored number can always be read back against the formula that produced it,
even years later after the formula has moved on.

**Computed history** — ``metric_events``, ``progress_snapshots``,
``score_explanations``. Every event stores the formula version and the exact
inputs it used, which is what makes a metric reproducible rather than merely
plausible.

**User data** — goals, memory, feedback, gamification, streaks.

Two naming notes worth stating.

``progress_goals`` is a new table rather than an extension of Phase 2's
``appearance_goals``. That is deliberate: ``appearance_goals`` is a *projection*
of a profile attribute, and ``profile.service.sync_projections`` deletes and
rebuilds every row in it whenever the profile is patched. Attaching months of
progress history to a table that gets truncated on an unrelated profile edit
would lose that history silently. A Phase 2 goal can be promoted into a tracked
Phase 7 goal via ``progress_goals.appearance_goal_id``.

``memory_facts.fact`` is blanked on deletion rather than the row being dropped,
so the audit trail in ``memory_revisions`` still points at something real while
the text itself is genuinely gone.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

PROGRESS_VERSION = "phase7-v1"


# --- Reviewed reference data --------------------------------------------------


class MetricDefinition(UUIDPrimaryKey, TimestampMixin, Base):
    """One metric's contract, as shown to the user and as used by the engine.

    Seeded from ``registry.py``. Rows are keyed on ``(key, formula_version)``,
    so changing a formula adds a row rather than rewriting history — an event
    recorded under v1 stays readable after v2 ships.
    """

    __tablename__ = "metric_definitions"

    key: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    formula_version: Mapped[str] = mapped_column(String(16), nullable=False)
    inputs: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    missing_data_behaviour: Mapped[str] = mapped_column(String(48), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    update_frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    not_a_measure_of: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    registry_version: Mapped[str] = mapped_column(String(24), nullable=False, default=PROGRESS_VERSION, server_default=PROGRESS_VERSION)

    __table_args__ = (
        UniqueConstraint("key", "formula_version", name="uq_metric_definition_version"),
    )


class MilestoneRule(UUIDPrimaryKey, TimestampMixin, Base):
    """A thing worth marking, and what it takes to reach it.

    Seeded from ``milestones.py``. Every rule names the useful behaviour it
    rewards; there is no rule for opening the app.
    """

    __tablename__ = "milestone_rules"

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    behaviour: Mapped[str] = mapped_column(String(48), nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    repeatable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    registry_version: Mapped[str] = mapped_column(String(24), nullable=False, default=PROGRESS_VERSION, server_default=PROGRESS_VERSION)


# --- Computed history ----------------------------------------------------------


class MetricEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """One computation of one metric, with everything needed to reproduce it.

    ``inputs`` holds the actual counts the formula consumed, not a summary.
    Together with ``formula_version`` that makes the number checkable by hand,
    which is the difference between an explainable metric and a plausible one.

    ``dedup_hash`` covers account, metric, formula version, period and inputs.
    Computing the same metric twice from the same data writes one row, so a
    background job that runs twice cannot inflate a history.
    """

    __tablename__ = "metric_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(48), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="point", server_default="point")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok", server_default="ok")
    inputs: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    missing_inputs: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "dedup_hash", name="uq_metric_event_dedup"),
        Index("ix_metric_events_account_metric_date", "account_id", "metric_key", "period_start"),
    )


class ProgressSnapshot(UUIDPrimaryKey, TimestampMixin, Base):
    """Every metric for one account for one week or month, frozen."""

    __tablename__ = "progress_snapshots"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    registry_version: Mapped[str] = mapped_column(String(24), nullable=False, default=PROGRESS_VERSION, server_default=PROGRESS_VERSION)

    __table_args__ = (
        UniqueConstraint("account_id", "period", "period_start", name="uq_progress_snapshot_period"),
        Index("ix_progress_snapshots_account", "account_id", "period_start"),
    )


class ScoreExplanation(UUIDPrimaryKey, TimestampMixin, Base):
    """The stored "how was this worked out?" for one displayed number.

    Written at the same time as the event, so the explanation a user reads is
    the one that belongs to the number in front of them rather than today's
    version of the formula.
    """

    __tablename__ = "score_explanations"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    metric_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("metric_events.id", ondelete="CASCADE"), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(48), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(16), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    plain_english: Mapped[str] = mapped_column(Text, nullable=False)
    contributing_factors: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    __table_args__ = (Index("ix_score_explanations_event", "metric_event_id"),)


class ComparisonSession(UUIDPrimaryKey, TimestampMixin, Base):
    """Two photos, and whether they may honestly be shown side by side.

    ``comparable`` is false far more often than true, and that is correct. The
    reasons are stored so the app can say *why* rather than just refusing.
    """

    __tablename__ = "comparison_sessions"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    baseline_media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False)
    current_media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False)
    body_area: Mapped[str] = mapped_column(String(32), nullable=False)
    comparable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    checks: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    blocking_reasons: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    days_apart: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (Index("ix_comparison_sessions_account", "account_id", "created_at"),)


class ProgressPhoto(UUIDPrimaryKey, TimestampMixin, Base):
    """A photo kept for comparison, with the conditions it was taken in.

    The conditions are recorded by the user, not guessed from the image. That
    keeps comparability honest and means no analysis of the photo is needed to
    decide whether a comparison is fair.
    """

    __tablename__ = "progress_photos"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False)
    body_area: Mapped[str] = mapped_column(String(32), nullable=False)
    taken_on: Mapped[date] = mapped_column(Date, nullable=False)
    lighting: Mapped[str] = mapped_column(String(24), nullable=False)
    angle: Mapped[str] = mapped_column(String(24), nullable=False)
    framing: Mapped[str] = mapped_column(String(24), nullable=False)
    time_of_day: Mapped[Optional[str]] = mapped_column(String(16))
    note: Mapped[Optional[str]] = mapped_column(String(240))

    __table_args__ = (
        UniqueConstraint("media_id", name="uq_progress_photo_media"),
        Index("ix_progress_photos_account_area", "account_id", "body_area", "taken_on"),
    )


# --- Goals -----------------------------------------------------------------------


class ProgressGoal(UUIDPrimaryKey, TimestampMixin, Base):
    """A goal with a target, so progress against it is a fact rather than a feeling."""

    __tablename__ = "progress_goals"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    # Optional link to the Phase 2 declared goal this was promoted from.
    appearance_goal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("appearance_goals.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    # The metric this goal is measured by, when there is one. A goal with no
    # metric is tracked by the user's own updates instead of being scored.
    metric_key: Mapped[Optional[str]] = mapped_column(String(48))
    target_value: Mapped[Optional[float]] = mapped_column(Float)
    starting_value: Mapped[Optional[float]] = mapped_column(Float)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    note: Mapped[Optional[str]] = mapped_column(String(500))

    __table_args__ = (Index("ix_progress_goals_account_status", "account_id", "status"),)


class GoalUpdate(UUIDPrimaryKey, TimestampMixin, Base):
    """One movement on a goal, from the engine or from the user."""

    __tablename__ = "goal_updates"

    goal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("progress_goals.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="metric", server_default="metric")
    note: Mapped[Optional[str]] = mapped_column(String(500))
    recorded_on: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (Index("ix_goal_updates_goal", "goal_id", "recorded_on"),)


# --- Memory -------------------------------------------------------------------------


class MemoryFact(UUIDPrimaryKey, TimestampMixin, Base):
    """One thing the app remembers, with everything needed to judge it.

    ``subject_key`` is what makes reinforcement possible: the same observation
    seen repeatedly becomes one fact we are more confident of, rather than a
    pile of duplicates.
    """

    __tablename__ = "memory_facts"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    verification_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified", server_default="unverified")
    deletion_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_reinforced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reinforcement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        Index("ix_memory_facts_account_state", "account_id", "deletion_state", "category"),
        Index("ix_memory_facts_subject", "account_id", "category", "subject_key"),
    )


class MemorySource(UUIDPrimaryKey, TimestampMixin, Base):
    """Where one observation behind a fact came from. The linked evidence."""

    __tablename__ = "memory_sources"

    fact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_facts.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_memory_sources_fact", "fact_id", "observed_at"),)


class MemoryRevision(UUIDPrimaryKey, TimestampMixin, Base):
    """What a fact used to say, before the user corrected or deleted it."""

    __tablename__ = "memory_revisions"

    fact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_facts.id", ondelete="CASCADE"), nullable=False)
    previous_fact: Mapped[str] = mapped_column(Text, nullable=False)
    previous_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    previous_verification_state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)

    __table_args__ = (Index("ix_memory_revisions_fact", "fact_id", "created_at"),)


class MemoryCategoryPreference(UUIDPrimaryKey, TimestampMixin, Base):
    """A category of memory the user has switched off.

    Switching off stops both writing and reading, but destroys nothing, so it
    can be switched back on with the data intact.
    """

    __tablename__ = "memory_category_preferences"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("account_id", "category", name="uq_memory_category_preference"),
    )


class FeedbackEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """A reaction to something the app suggested, and what we learned from it.

    ``learned_fact_id`` is set when the feedback produced a memory fact, so a
    user deleting that fact can be traced back to the feedback that created it.
    """

    __tablename__ = "feedback_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    signal: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(240))
    learned_fact_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("memory_facts.id", ondelete="SET NULL"))
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (Index("ix_feedback_events_account", "account_id", "created_at"),)


# --- Milestones and streaks ------------------------------------------------------------


class Milestone(UUIDPrimaryKey, TimestampMixin, Base):
    """A milestone this user has actually reached."""

    __tablename__ = "milestones"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(ForeignKey("milestone_rules.rule_id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    earned_on: Mapped[date] = mapped_column(Date, nullable=False)
    evidence: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_id", "rule_id", "earned_on", name="uq_milestone_account_rule_day"),
        Index("ix_milestones_account", "account_id", "earned_on"),
    )


class GamificationEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """A recorded instance of useful behaviour.

    ``dedup_hash`` is the anti-abuse mechanism and it is a unique constraint,
    not a check in application code: the same real-world action cannot be
    counted twice however many times it is submitted.
    """

    __tablename__ = "gamification_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    behaviour: Mapped[str] = mapped_column(String(48), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    subject_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    detail: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "dedup_hash", name="uq_gamification_event_dedup"),
        Index("ix_gamification_events_account", "account_id", "behaviour", "occurred_on"),
    )


class Streak(UUIDPrimaryKey, TimestampMixin, Base):
    """A run of days for one behaviour.

    Kept because a no-buy period genuinely is a count of days. The wording
    around it never treats a break as a failure, and the phase's own routine
    consistency metric deliberately does **not** use a streak.
    """

    __tablename__ = "streaks"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    behaviour: Mapped[str] = mapped_column(String(48), nullable=False)
    current_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    longest_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_on: Mapped[Optional[date]] = mapped_column(Date)
    last_counted_on: Mapped[Optional[date]] = mapped_column(Date)
    last_reset_on: Mapped[Optional[date]] = mapped_column(Date)
    reset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("account_id", "behaviour", name="uq_streak_account_behaviour"),
    )
