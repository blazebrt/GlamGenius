"""Relational models for billing, entitlements, experiments and release tooling.

The money tables follow one principle: **nothing is ever overwritten**.
``payment_events`` and ``billing_audit_events`` are append-only, so the answer
to "why does this person have access?" is always reconstructible from history
rather than inferred from current state. When a dispute happens in eighteen
months, current state is the one thing nobody will trust.

Three constraints carry most of the correctness:

* ``payment_events.provider_event_id`` is unique per account, so a webhook
  delivered five times is processed once. That is a database constraint, not an
  application check — two workers cannot both pass a check and both grant.
* ``entitlements`` is unique per ``(account_id, feature, period_key, source_id)``,
  so a duplicate grant collides instead of stacking.
* ``entitlement_usage`` is append-only with an idempotency key, so a retried
  request cannot consume somebody's allowance twice.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

BILLING_VERSION = "phase8-v1"


# --- Catalogue ------------------------------------------------------------------


class BillingPlan(UUIDPrimaryKey, TimestampMixin, Base):
    """A commercial plan, seeded from ``catalogue.py``.

    The limits live here so support can answer "what was this person entitled
    to in March?" against the row that was live in March.
    """

    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    tagline: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    valid_days: Mapped[Optional[int]] = mapped_column(Integer)
    limits: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    benefits: Mapped[List[Dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    catalogue_version: Mapped[str] = mapped_column(String(24), nullable=False, default=BILLING_VERSION, server_default=BILLING_VERSION)


class BillingOffer(UUIDPrimaryKey, TimestampMixin, Base):
    """A plan at a price, for a period.

    Prices are stored when an order is placed, so a later price change never
    rewrites what somebody was actually charged.
    """

    __tablename__ = "offers"

    key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    plan_key: Mapped[str] = mapped_column(ForeignKey("plans.key", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    compare_at_inr: Mapped[Optional[int]] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


# --- Orders and payments -----------------------------------------------------------


class Order(UUIDPrimaryKey, TimestampMixin, Base):
    """An intent to buy. Never itself proof of payment.

    ``status`` moves created → paid → (refunded), and only a verified webhook
    moves it past created.
    """

    __tablename__ = "orders"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    offer_key: Mapped[str] = mapped_column(String(48), nullable=False)
    plan_key: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(80))
    # What was actually charged, frozen at order time.
    amount_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created", server_default="created")
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # For the event pass: which occasion this was bought for.
    occasion_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("occasions.id", ondelete="SET NULL"))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80))
    notes: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("provider", "provider_order_id", name="uq_order_provider_ref"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_order_idempotency"),
        Index("ix_orders_account_status", "account_id", "status"),
    )


class PaymentEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """One webhook, recorded whether or not it changed anything.

    Append-only. The unique constraint on ``(account_id, provider_event_id)`` is
    what makes processing idempotent — a duplicate delivery collides on insert
    and is answered "already handled" rather than replayed.

    ``occurred_at_epoch`` is the provider's own timestamp, kept so out-of-order
    delivery can be detected: an event older than the last one applied to a
    subscription is recorded and then ignored.
    """

    __tablename__ = "payment_events"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_event_name: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(80))
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(80))
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(String(80))
    provider_refund_id: Mapped[Optional[str]] = mapped_column(String(80))
    amount_inr: Mapped[Optional[int]] = mapped_column(Integer)
    occurred_at_epoch: Mapped[Optional[int]] = mapped_column(Integer)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="processed", server_default="processed")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_event_provider_id"),
        Index("ix_payment_events_account", "account_id", "created_at"),
    )


class Subscription(UUIDPrimaryKey, TimestampMixin, Base):
    """A recurring purchase, and where it is in its life.

    States: ``active`` → ``grace`` (renewal failed, still working) →
    ``past_due`` → ``cancelled`` / ``expired``. ``cancelled`` keeps access until
    ``current_period_end``, because somebody who has paid for this month has
    paid for this month.
    """

    __tablename__ = "subscriptions"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    plan_key: Mapped[str] = mapped_column(String(32), nullable=False)
    offer_key: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    current_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    current_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    grace_until: Mapped[Optional[date]] = mapped_column(Date)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    renewal_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The provider timestamp of the last event applied. Guards against
    # out-of-order delivery moving the state machine backwards.
    last_event_epoch: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id", name="uq_subscription_provider_ref"),
        Index("ix_subscriptions_account_status", "account_id", "status"),
    )


class Refund(UUIDPrimaryKey, TimestampMixin, Base):
    """Money returned, and the entitlement revocation that followed."""

    __tablename__ = "refunds"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_refund_id: Mapped[Optional[str]] = mapped_column(String(80))
    amount_inr: Mapped[Optional[int]] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(240), nullable=False, default="", server_default="")
    entitlements_revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("provider", "provider_refund_id", name="uq_refund_provider_ref"),
        Index("ix_refunds_account", "account_id", "created_at"),
    )


class BillingAuditEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """Every change to what somebody is entitled to, and why.

    Append-only and never deleted, including when the account is. This is the
    record that answers a chargeback.
    """

    __tablename__ = "billing_audit_events"

    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("account_links.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    actor: Mapped[str] = mapped_column(String(24), nullable=False, default="system", server_default="system")
    subject_type: Mapped[Optional[str]] = mapped_column(String(32))
    subject_id: Mapped[Optional[str]] = mapped_column(String(80))
    detail: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (Index("ix_billing_audit_account", "account_id", "created_at"),)


# --- Entitlements -------------------------------------------------------------------


class Entitlement(UUIDPrimaryKey, TimestampMixin, Base):
    """What one account may do, for one feature, for one period.

    ``limit_value`` of ``None`` means unmetered *for that feature* — never
    "unlimited AI", which this product does not sell.

    ``source_id`` points at the order or subscription that granted it, so a
    refund can revoke precisely what that payment bought and nothing else.
    """

    __tablename__ = "entitlements"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    feature: Mapped[str] = mapped_column(String(48), nullable=False)
    plan_key: Mapped[str] = mapped_column(String(32), nullable=False)
    # YYYY-MM for recurring, or "pass:<uuid>" for one-off grants — which is 41
    # characters, so this is comfortably wider than the longest real value.
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    limit_value: Mapped[Optional[int]] = mapped_column(Integer)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="plan", server_default="plan")
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    # The identity of the grant, and it is NOT NULL for a reason. PostgreSQL
    # treats NULL as distinct from NULL, so a unique constraint containing a
    # nullable ``source_id`` silently never fires for free grants — every read
    # would insert another row and reset the user's allowance. This column
    # holds the source id as text, or the literal "free", so the constraint
    # actually constrains.
    grant_key: Mapped[str] = mapped_column(String(48), nullable=False, default="free", server_default="free")
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    expires_on: Mapped[Optional[date]] = mapped_column(Date)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint("account_id", "feature", "period_key", "grant_key", name="uq_entitlement_grant"),
        Index("ix_entitlements_account_feature", "account_id", "feature", "revoked_at"),
    )


class EntitlementUsage(UUIDPrimaryKey, TimestampMixin, Base):
    """One consumption, recorded before the work is done.

    Append-only, with an idempotency key, so a client retrying a request that
    already succeeded does not pay for it twice.
    """

    __tablename__ = "entitlement_usage"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    entitlement_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("entitlements.id", ondelete="SET NULL"))
    feature: Mapped[str] = mapped_column(String(48), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80))
    detail: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("account_id", "feature", "idempotency_key", name="uq_entitlement_usage_idempotency"),
        Index("ix_entitlement_usage_account", "account_id", "feature", "period_key"),
    )


# --- Experiments -----------------------------------------------------------------------


class Experiment(UUIDPrimaryKey, TimestampMixin, Base):
    """A running experiment. Pricing and copy only.

    Deliberately not a general on/off switch for behaviour: safety rules,
    medical boundaries and privacy defaults must never be A/B tested, and
    ``service.assign`` refuses any experiment keyed outside pricing and
    presentation.
    """

    __tablename__ = "experiments"

    key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    surface: Mapped[str] = mapped_column(String(24), nullable=False)
    variants: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default="running")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class ExperimentAssignment(UUIDPrimaryKey, TimestampMixin, Base):
    """Which variant an account got. Stable, so the app does not flicker."""

    __tablename__ = "experiment_assignments"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    experiment_key: Mapped[str] = mapped_column(ForeignKey("experiments.key", ondelete="CASCADE"), nullable=False)
    variant: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "experiment_key", name="uq_experiment_assignment"),
    )


# --- Cost, quality and support ------------------------------------------------------------


class FeatureCostDaily(UUIDPrimaryKey, TimestampMixin, Base):
    """AI spend, rolled up per feature per day.

    Rolled up from ``ai_runs`` rather than stored twice, so the dashboard and
    the ledger cannot disagree. Feature-level is the point: "we spent $40 today"
    is not actionable, "shopping evaluation costs 8× what styling costs" is.
    """

    __tablename__ = "feature_cost_daily"

    on_date: Mapped[date] = mapped_column(Date, nullable=False)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")

    __table_args__ = (
        UniqueConstraint("on_date", "feature", name="uq_feature_cost_day"),
        Index("ix_feature_cost_date", "on_date"),
    )


class ModelEvaluation(UUIDPrimaryKey, TimestampMixin, Base):
    """One graded sample from the model-quality pipeline.

    Grading is against the deterministic engine's output and the safety sweeps,
    never against a human's opinion of whether somebody looks good.
    """

    __tablename__ = "model_evaluations"

    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    sample_key: Mapped[str] = mapped_column(String(80), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    checks: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    __table_args__ = (
        Index("ix_model_evaluations_feature", "feature", "created_at"),
    )


class SupportCase(UUIDPrimaryKey, TimestampMixin, Base):
    """Somebody asking for help, with the billing context attached.

    Whatever the user typed is stored verbatim. The snapshot beside it is what
    lets support answer a billing question without asking them to explain their
    own subscription.
    """

    __tablename__ = "support_cases"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", server_default="normal")
    billing_snapshot: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (Index("ix_support_cases_account", "account_id", "status"),)


class StylistReview(UUIDPrimaryKey, TimestampMixin, Base):
    """A request for a human stylist to look at something.

    The seam for professional integration. Requests are accepted and queued;
    nothing pretends a human has replied when none has, and the status says so.
    """

    __tablename__ = "stylist_reviews"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    reviewer_ref: Mapped[Optional[str]] = mapped_column(String(80))
    response: Mapped[Optional[str]] = mapped_column(Text)
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_stylist_reviews_account", "account_id", "status"),)


class VirtualTryOnJob(UUIDPrimaryKey, TimestampMixin, Base):
    """A try-on request, for a provider that does not exist yet.

    The brief asks for a decoupled interface and explicitly says not to build
    the product until a provider is chosen and approved. So this table and its
    provider protocol exist, the feature flag defaults off, and the only
    implementation refuses honestly.
    """

    __tablename__ = "virtual_tryon_jobs"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="unconfigured", server_default="unconfigured")
    provider_job_id: Mapped[Optional[str]] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    source_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    result_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    item_ids: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (Index("ix_tryon_jobs_account", "account_id", "status"),)
