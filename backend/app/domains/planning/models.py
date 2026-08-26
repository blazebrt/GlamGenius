"""Relational models for the Today engine and the weekly planner.

Four groups:

* **What we know** — ``external_integrations``, ``calendar_events``,
  ``weather_snapshots``, ``laundry_state_events``.
* **What we decided** — ``daily_plans``, ``daily_plan_inputs``,
  ``daily_plan_actions``, ``weekly_plans``, ``weekly_plan_days``,
  ``outfit_schedule``.
* **Why we decided again** — ``plan_recalculation_events``.
* **What we told them** — ``notification_preferences``,
  ``notification_deliveries``.

``daily_plans.cache_key`` is what keeps Today cheap. It is a hash of every
material input; if it still matches, the stored plan is served straight back and
nothing is recomputed. When it stops matching, one row goes into
``plan_recalculation_events`` saying which input moved, so a plan that changed
under the user can always be explained.

No table stores an OAuth token. ``external_integrations.credential_ref`` holds an
opaque reference only — see the docstring there.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey, utcnow

# Bumped when the compiler's deterministic rules change. Stored on every plan so
# an old plan still says which rules produced it.
PLANNER_VERSION = "vc06-v1"

PLAN_SOURCE_CACHE = "cache"
PLAN_SOURCE_FRESH = "fresh"

LAUNDRY_CLEAN = "clean"
LAUNDRY_WORN = "worn"
LAUNDRY_IN_WASH = "in_wash"
LAUNDRY_UNAVAILABLE = "unavailable"
LAUNDRY_STATES = (LAUNDRY_CLEAN, LAUNDRY_WORN, LAUNDRY_IN_WASH, LAUNDRY_UNAVAILABLE)

# Optional Today modules. They are shown when relevant, never all at once.
MODULE_OUTFIT = "outfit"
MODULE_SKINCARE = "skincare"
MODULE_HAIR = "hair"
MODULE_PERFUME = "perfume"
MODULE_HYDRATION = "hydration"
MODULE_NUTRITION = "nutrition"
MODULE_SHOPPING = "shopping"
MODULE_MAINTENANCE = "maintenance"
MODULES = (
    MODULE_OUTFIT, MODULE_SKINCARE, MODULE_HAIR, MODULE_PERFUME,
    MODULE_HYDRATION, MODULE_NUTRITION, MODULE_SHOPPING, MODULE_MAINTENANCE,
)


class ExternalIntegration(UUIDPrimaryKey, TimestampMixin, Base):
    """A connection to an outside calendar or weather service.

    ``credential_ref`` is an opaque handle, never a token. Access tokens and
    refresh tokens are secrets; this project keeps secrets in environment
    configuration and secret stores, not in application tables where an
    export, a log line or a backup would carry them. A real adapter resolves
    this reference against its own secret store.
    """

    __tablename__ = "external_integrations"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)          # calendar | weather
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected", server_default="connected")
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    credential_ref: Mapped[str | None] = mapped_column(String(200))
    external_account_label: Mapped[str | None] = mapped_column(String(160))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(240))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_cursor: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("account_id", "kind", "provider", name="uq_integration_account_kind_provider"),
        Index("ix_external_integrations_account_status", "account_id", "status"),
    )


class CalendarEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """One commitment. ``dedup_key`` is what stops a re-sync duplicating it."""

    __tablename__ = "calendar_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_integrations.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(400), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    location: Mapped[str | None] = mapped_column(String(200))
    # Inferred from the title, and always overridable by the user.
    occasion_key: Mapped[str | None] = mapped_column(String(32))
    dress_code_hint: Mapped[str | None] = mapped_column(String(32))
    inference_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    user_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    # Field-level provenance for corrections made inside GlamGenius. Provider
    # syncs may update only fields absent from this object.
    user_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("account_id", "dedup_key", name="uq_calendar_event_dedup"),
        Index(
            "uq_calendar_event_integration_external_google",
            "integration_id", "external_id", unique=True,
            postgresql_where=text("provider = 'google' AND integration_id IS NOT NULL AND external_id IS NOT NULL"),
        ),
        Index("ix_calendar_events_account_start", "account_id", "starts_at"),
    )


class ExternalOAuthState(UUIDPrimaryKey, TimestampMixin, Base):
    """One-time, account-bound OAuth CSRF/replay state; raw state is never stored."""

    __tablename__ = "external_oauth_states"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_external_oauth_states_account_provider", "account_id", "provider"),)


class WeatherSnapshot(UUIDPrimaryKey, TimestampMixin, Base):
    """What the weather was expected to be for one local day."""

    __tablename__ = "weather_snapshots"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    for_date: Mapped[date] = mapped_column(Date, nullable=False)
    location: Mapped[str | None] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    condition: Mapped[str] = mapped_column(String(24), nullable=False)
    temp_min_c: Mapped[float | None] = mapped_column(Float)
    temp_max_c: Mapped[float | None] = mapped_column(Float)
    precipitation_chance: Mapped[int | None] = mapped_column(Integer)
    humidity: Mapped[int | None] = mapped_column(Integer)
    uv_index: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (Index("ix_weather_snapshots_account_date", "account_id", "for_date", "created_at"),)


class AirQualitySnapshot(UUIDPrimaryKey, TimestampMixin, Base):
    """What the air quality was expected to be for one local day."""

    __tablename__ = "air_quality_snapshots"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    for_date: Mapped[date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", server_default="manual")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    location: Mapped[str | None] = mapped_column(String(128))
    aqi: Mapped[int] = mapped_column(Integer, nullable=False)
    index_system: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    prominent_pollutant: Mapped[str | None] = mapped_column(String(32))
    pm2_5: Mapped[float | None] = mapped_column(Float)
    pm10: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (Index("ix_air_quality_snapshots_account_date", "account_id", "for_date", "created_at"),)


class DailyPlan(UUIDPrimaryKey, TimestampMixin, Base):
    """One person's answer to "what do I wear today"."""

    __tablename__ = "daily_plans"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(48), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ready", server_default="ready")
    headline: Mapped[str] = mapped_column(String(240), nullable=False, default="", server_default="")
    look_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("looks.id", ondelete="SET NULL"))
    weather_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("weather_snapshots.id", ondelete="SET NULL"))
    air_quality_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("air_quality_snapshots.id", ondelete="SET NULL"))
    weather_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    event_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    generated_from: Mapped[str] = mapped_column(String(16), nullable=False, default=PLAN_SOURCE_FRESH, server_default=PLAN_SOURCE_FRESH)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default=PLANNER_VERSION, server_default=PLANNER_VERSION)
    # The hash of every material input. Equal hash means nothing that could
    # change the plan has changed, so the stored plan can be served as-is.
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    used_llm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    needs_clarification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    clarification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    missing_information: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_id", "plan_date", name="uq_daily_plan_account_date"),
        Index("ix_daily_plans_account_date", "account_id", "plan_date"),
    )


class DailyPlanInput(UUIDPrimaryKey, TimestampMixin, Base):
    """Every fact that fed one day's plan, with where it came from."""

    __tablename__ = "daily_plan_inputs"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_plans.id", ondelete="CASCADE"), nullable=False)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_daily_plan_inputs_plan", "plan_id", "input_type"),)


class DailyPlanAction(UUIDPrimaryKey, TimestampMixin, Base):
    """One thing on the Today screen.

    ``relevance`` is why this is here at all. A module with nothing relevant to
    say produces no row, which is how Today stays a short list rather than a
    dashboard.
    """

    __tablename__ = "daily_plan_actions"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_plans.id", ondelete="CASCADE"), nullable=False)
    module: Mapped[str] = mapped_column(String(24), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    relevance: Mapped[str] = mapped_column(String(240), nullable=False, default="", server_default="")
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_daily_plan_actions_plan_priority", "plan_id", "priority"),)


class WeeklyPlan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "weekly_plans"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)   # always a Monday
    timezone_name: Mapped[str] = mapped_column(String(48), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready", server_default="ready")
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default=PLANNER_VERSION, server_default=PLANNER_VERSION)
    repetition_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    notes: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_id", "week_start", name="uq_weekly_plan_account_week"),
        Index("ix_weekly_plans_account_week", "account_id", "week_start"),
    )


class WeeklyPlanDay(UUIDPrimaryKey, TimestampMixin, Base):
    """One day of the week view. ``locked`` survives regeneration."""

    __tablename__ = "weekly_plan_days"

    weekly_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_plans.id", ondelete="CASCADE"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("daily_plans.id", ondelete="SET NULL"))
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    note: Mapped[str | None] = mapped_column(String(240))

    __table_args__ = (
        UniqueConstraint("weekly_plan_id", "plan_date", name="uq_weekly_plan_day"),
        Index("ix_weekly_plan_days_plan", "weekly_plan_id", "position"),
    )


class OutfitSchedule(UUIDPrimaryKey, TimestampMixin, Base):
    """What is planned, and what was actually worn.

    The worn history is what stops the planner suggesting the same shirt three
    days running, so it is a first-class table rather than a derived view.
    """

    __tablename__ = "outfit_schedule"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    look_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("looks.id", ondelete="SET NULL"))
    item_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned", server_default="planned")
    worn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(240))

    __table_args__ = (
        UniqueConstraint("account_id", "plan_date", name="uq_outfit_schedule_account_date"),
        Index("ix_outfit_schedule_account_date", "account_id", "plan_date"),
    )


class LaundryStateEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """Append-only record of whether an item is wearable right now."""

    __tablename__ = "laundry_state_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    available_from: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(240))
    actor: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user")

    __table_args__ = (Index("ix_laundry_state_events_item", "account_id", "item_id", "created_at"),)


class NotificationPreference(UUIDPrimaryKey, TimestampMixin, Base):
    """How much this person wants to hear from us.

    The default is one proactive appearance notification per day, and that
    default is enforced in the sender, not merely suggested here.
    """

    __tablename__ = "notification_preferences"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Backend preference and OS consent are deliberately separate. Enabling
    # the in-app setting never implies that a device may receive a push.
    native_push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    quiet_hours_start: Mapped[int] = mapped_column(Integer, nullable=False, default=21, server_default="21")
    quiet_hours_end: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    preferred_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    modules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    timezone_name: Mapped[str] = mapped_column(String(48), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata")


class NotificationDevice(UUIDPrimaryKey, TimestampMixin, Base):
    """An account-owned Expo device registration.

    ``expo_push_token`` is operational secret-like data: it is never exposed
    through normal serializers or privacy exports and is only read by the
    worker while sending to this account's own device.
    """

    __tablename__ = "notification_devices"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    device_key: Mapped[str] = mapped_column(String(160), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    expo_push_token: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=text("now()"))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_id", "device_key", name="uq_notification_device_account_key"),
        Index("ix_notification_devices_account_status", "account_id", "status"),
        CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="ck_notification_device_status"),
        CheckConstraint("platform IN ('ios', 'android', 'web', 'unknown')", name="ck_notification_device_platform"),
    )


class NotificationDelivery(UUIDPrimaryKey, TimestampMixin, Base):
    """One notification we decided about.

    Rows are written for suppressed notifications too, with the reason. Without
    that, "why didn't I get told about X" is unanswerable.
    """

    __tablename__ = "notification_deliveries"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    notification_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="push", server_default="push")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    suppressed_reason: Mapped[str | None] = mapped_column(String(64))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deep_link: Mapped[str | None] = mapped_column(String(240))
    source_kind: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(96))
    provider_ticket_id: Mapped[str | None] = mapped_column(String(160))
    provider_error_code: Mapped[str | None] = mapped_column(String(80))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_id", "dedup_hash", name="uq_notification_dedup"),
        Index("ix_notification_deliveries_account_date", "account_id", "plan_date"),
        CheckConstraint("status IN ('suppressed', 'queued', 'provider_accepted', 'provider_failed', 'receipt_ok', 'receipt_failed')", name="ck_notification_delivery_status"),
    )


class PlanRecalculationEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """Why a plan was rebuilt. The audit trail for "this changed on me"."""

    __tablename__ = "plan_recalculation_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    trigger: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[str] = mapped_column(String(400), nullable=False, default="", server_default="")
    changed_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    old_cache_key: Mapped[str | None] = mapped_column(String(64))
    new_cache_key: Mapped[str | None] = mapped_column(String(64))
    recomputed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (Index("ix_plan_recalculation_account_date", "account_id", "plan_date", "created_at"),)


class EventReadyPlan(UUIDPrimaryKey, TimestampMixin, Base):
    """Persisted orchestration state for one exact CalendarEvent."""

    __tablename__ = "event_ready_plans"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    calendar_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False)
    selected_look_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("looks.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="preparing", server_default="preparing")
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default="vc-02-v1", server_default="vc-02-v1")
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("account_id", "calendar_event_id", name="uq_event_ready_plan_account_event"),
        Index("ix_event_ready_plans_account_event", "account_id", "calendar_event_id"),
    )


class EventReadyAction(UUIDPrimaryKey, TimestampMixin, Base):
    """A small, deterministic preparation action owned by an EventReadyPlan."""

    __tablename__ = "event_ready_actions"

    event_ready_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_ready_plans.id", ondelete="CASCADE"), nullable=False)
    action_key: Mapped[str] = mapped_column(String(96), nullable=False)
    domain: Mapped[str] = mapped_column(String(24), nullable=False)
    timing: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    relevance: Mapped[str] = mapped_column(String(240), nullable=False, default="", server_default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    material_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("event_ready_plan_id", "action_key", name="uq_event_ready_action_plan_key"),
        Index("ix_event_ready_actions_plan_priority", "event_ready_plan_id", "priority"),
    )
