"""Relational models for the Phase 4 decision engine.

Two shapes of work live here and they are deliberately kept apart:

* **Styling** — ``occasions`` → ``style_requests`` → ``recommendation_runs`` →
  ``looks`` → ``look_items``, with ``look_adjustments`` and ``look_feedback``
  recording what the user did afterwards.
* **Shopping** — ``shopping_candidates`` → ``purchase_evaluations`` →
  ``purchase_evaluation_factors``, with ``purchase_decisions`` recording the
  outcome.

``recommendation_inputs`` is what makes a run auditable: every fact that went
into a recommendation is written down with where it came from, so "why did it
suggest this" can be answered from the database months later.

Every table hangs off ``accounts.id``. No table here stores user identity,
and no route takes an account id from a request body.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

# Bumped whenever the deterministic scoring changes, and stored on every run so
# an old recommendation still says which rules produced it.
ENGINE_VERSION = "phase4-v1"
ROI_VERSION = "appearance-roi-v1"

VARIANT_RECOMMENDED = "recommended"
VARIANT_COMFORTABLE = "comfortable"
VARIANT_EXPRESSIVE = "expressive"
VARIANTS = (VARIANT_RECOMMENDED, VARIANT_COMFORTABLE, VARIANT_EXPRESSIVE)

OWNERSHIP_OWNED = "owned"
OWNERSHIP_OPTIONAL = "optional_addition"

VERDICT_BUY = "buy"
VERDICT_WAIT = "wait"
VERDICT_SKIP = "skip"


class OccasionRecord(UUIDPrimaryKey, TimestampMixin, Base):
    """One real event the user is dressing for."""

    __tablename__ = "occasions"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    occasion_key: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(160))
    event_date: Mapped[date | None] = mapped_column(Date)
    time_of_day: Mapped[str | None] = mapped_column(String(24))
    location: Mapped[str | None] = mapped_column(String(160))
    setting: Mapped[str | None] = mapped_column(String(16))
    dress_code: Mapped[str | None] = mapped_column(String(32))
    weather: Mapped[str | None] = mapped_column(String(24))
    comfort_preference: Mapped[str | None] = mapped_column(String(24))
    optional_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    preparation_time: Mapped[str | None] = mapped_column(String(48))
    notes: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        Index("ix_occasions_account_created", "account_id", "created_at"),
        Index("ix_occasions_account_key", "account_id", "occasion_key"),
    )


class StyleRequest(UUIDPrimaryKey, TimestampMixin, Base):
    """A single ask: "style me for this occasion"."""

    __tablename__ = "style_requests"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    occasion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("occasions.id", ondelete="CASCADE"), nullable=False)
    preferred_item_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    client_mutation_id: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        UniqueConstraint("account_id", "client_mutation_id", name="uq_style_request_client_mutation"),
        Index("ix_style_requests_account_created", "account_id", "created_at"),
    )


class RecommendationRun(UUIDPrimaryKey, TimestampMixin, Base):
    """One pass of the orchestrator, styling or shopping."""

    __tablename__ = "recommendation_runs"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    style_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("style_requests.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default="running")
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default=ENGINE_VERSION, server_default=ENGINE_VERSION)
    explanation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="deterministic", server_default="deterministic")
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    candidates_considered: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_recommendation_runs_account_kind", "account_id", "kind", "created_at"),)


class RecommendationInput(UUIDPrimaryKey, TimestampMixin, Base):
    """Every fact that fed a run, with where it came from."""

    __tablename__ = "recommendation_inputs"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_recommendation_inputs_run", "run_id", "input_type"),)


class Look(UUIDPrimaryKey, TimestampMixin, Base):
    """One complete, wearable option."""

    __tablename__ = "looks"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False)
    variant: Mapped[str] = mapped_column(String(24), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    why_it_works: Mapped[str] = mapped_column(Text, nullable=False)
    weather_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    dress_code_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    preparation_steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    missing_information: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    factor_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    explanation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="deterministic", server_default="deterministic")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    saved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        UniqueConstraint("run_id", "variant", name="uq_look_run_variant"),
        Index("ix_looks_account_created", "account_id", "created_at"),
    )


class LookItem(UUIDPrimaryKey, TimestampMixin, Base):
    """One slot of a look.

    ``inventory_item_id`` is set for everything the user owns and is NULL only
    for an explicitly optional addition. The pairing of the two columns is what
    lets the API guarantee that an "owned" item is always a real row.
    """

    __tablename__ = "look_items"

    look_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("looks.id", ondelete="CASCADE"), nullable=False)
    slot: Mapped[str] = mapped_column(String(24), nullable=False)
    ownership: Mapped[str] = mapped_column(String(24), nullable=False)
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_note: Mapped[str] = mapped_column(String(400), nullable=False, default="", server_default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    estimated_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")

    __table_args__ = (Index("ix_look_items_look_slot", "look_id", "slot", "position"),)


class LookAdjustment(UUIDPrimaryKey, TimestampMixin, Base):
    """A revision or a swap the user asked for, kept as history."""

    __tablename__ = "look_adjustments"

    look_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("looks.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    adjustment_type: Mapped[str] = mapped_column(String(24), nullable=False)
    slot: Mapped[str | None] = mapped_column(String(24))
    from_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    to_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(400))
    actor: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user")

    __table_args__ = (Index("ix_look_adjustments_look", "look_id", "created_at"),)


class LookFeedback(UUIDPrimaryKey, TimestampMixin, Base):
    """What the user thought, which later runs read back."""

    __tablename__ = "look_feedback"

    look_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("looks.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    rating: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500))
    worn_on: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("look_id", "account_id", name="uq_look_feedback_once"),
        Index("ix_look_feedback_account", "account_id", "created_at"),
    )


class ShoppingCandidate(UUIDPrimaryKey, TimestampMixin, Base):
    """Something the user is thinking about buying. Never inventory."""

    __tablename__ = "shopping_candidates"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL"))
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    colour: Mapped[str | None] = mapped_column(String(80))
    size: Mapped[str | None] = mapped_column(String(40))
    fabric: Mapped[str | None] = mapped_column(String(100))
    fit: Mapped[str | None] = mapped_column(String(80))
    formality: Mapped[str | None] = mapped_column(String(80))
    occasion_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    season_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    product_url: Mapped[str | None] = mapped_column(String(2048))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    uncertain_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    # Retry key. A dropped response on a mobile connection must not cost the
    # user a second run from their allowance.
    client_mutation_id: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        UniqueConstraint("account_id", "client_mutation_id", name="uq_shopping_candidate_client_mutation"),
        Index("ix_shopping_candidates_account_created", "account_id", "created_at"),
    )


class PurchaseEvaluation(UUIDPrimaryKey, TimestampMixin, Base):
    """Buy, Wait or Skip, with the ROI that produced it."""

    __tablename__ = "purchase_evaluations"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shopping_candidates.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    roi_score: Mapped[float] = mapped_column(Float, nullable=False)
    roi_version: Mapped[str] = mapped_column(String(32), nullable=False, default=ROI_VERSION, server_default=ROI_VERSION)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    new_combinations: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    duplicate_item_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    alternative_item_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    fit_risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    colour_risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    climate_notes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    missing_information: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    explanation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="deterministic", server_default="deterministic")

    __table_args__ = (Index("ix_purchase_evaluations_account_created", "account_id", "created_at"),)


class PurchaseEvaluationFactor(UUIDPrimaryKey, TimestampMixin, Base):
    """One line of the ROI arithmetic, kept so the total can be shown as a sum."""

    __tablename__ = "purchase_evaluation_factors"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_evaluations.id", ondelete="CASCADE"), nullable=False)
    factor_key: Mapped[str] = mapped_column(String(48), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_value: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    contribution: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("evaluation_id", "factor_key", name="uq_purchase_factor_key"),
        Index("ix_purchase_factors_evaluation", "evaluation_id", "position"),
    )


class PurchaseDecision(UUIDPrimaryKey, TimestampMixin, Base):
    """What the user actually did about it."""

    __tablename__ = "purchase_decisions"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_evaluations.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    followed_recommendation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        UniqueConstraint("evaluation_id", "account_id", name="uq_purchase_decision_once"),
        Index("ix_purchase_decisions_account", "account_id", "created_at"),
    )


class CompatibilityEdge(UUIDPrimaryKey, TimestampMixin, Base):
    """A cached deterministic score between two owned items.

    Recomputing every pair on every request is wasted work, and storing the
    basis alongside the score means a cached edge can still explain itself.
    """

    __tablename__ = "compatibility_edges"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    item_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    metric_version: Mapped[str] = mapped_column(String(32), nullable=False, default=ENGINE_VERSION, server_default=ENGINE_VERSION)

    __table_args__ = (
        UniqueConstraint("account_id", "item_a_id", "item_b_id", "metric_version", name="uq_compatibility_edge_pair"),
        Index("ix_compatibility_edges_account", "account_id", "score"),
    )


class RecommendationEntitlement(UUIDPrimaryKey, TimestampMixin, Base):
    """How much of the paid decision engine this account may use this period.

    Backend-controlled on purpose: the app displays what this table says and
    never decides it. Billing is unavailable, so every row is granted by the
    backend and no payment path can create one.
    """

    __tablename__ = "recommendation_entitlements"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    feature: Mapped[str] = mapped_column(String(48), nullable=False)
    period_key: Mapped[str] = mapped_column(String(7), nullable=False)
    included: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="beta_grant", server_default="beta_grant")

    __table_args__ = (
        UniqueConstraint("account_id", "feature", "period_key", name="uq_recommendation_entitlement_period"),
        Index("ix_recommendation_entitlements_account", "account_id", "period_key"),
    )
