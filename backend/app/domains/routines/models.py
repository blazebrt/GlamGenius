"""Relational models for routine and shelf intelligence.

Two kinds of table live here, and the difference matters.

**Reviewed reference data** — ``ingredients``, ``ingredient_aliases``,
``ingredient_rules``, ``compatibility_rules``, ``contraindication_rules``,
``routine_templates``, ``perfume_context_rules``, ``appearance_nutrition_rules``.
These have no ``account_id``. They are the written-down, human-reviewed body of
what the engine knows, seeded from ``ontology.py`` and ``nutrition.py``. Every
warning the product shows carries the ``rule_id`` of a row in one of them, which
is what makes "no invented safety rules" checkable rather than merely promised.

**User data** — everything else. Scoped to ``accounts.id`` like every other
V2 table, and holding only what the user told us or what the engine computed
from it.

``product_expiry_events`` is deliberately distinct from Phase 3's
``item_expiry_events``: that one records the dates a user *declared*, this one
records the risk assessments the engine *computed* from them, with the rule
that produced each.
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

KNOWLEDGE_VERSION = "phase6-v1"
CARE_EXPERIENCE_FEEDBACK_VERSION = "v3-03.13"
CARE_EXPERIENCE_FEEDBACK_SUBJECT_TYPES = ("product", "routine_step")
CARE_EXPERIENCE_FEEDBACK_DIMENSIONS = ("overall_experience", "comfort", "ease_of_use", "routine_fit")
CARE_EXPERIENCE_FEEDBACK_SENTIMENTS = ("positive", "neutral", "negative")


# --- Reviewed reference data -------------------------------------------------


class Ingredient(UUIDPrimaryKey, TimestampMixin, Base):
    """One ingredient the engine can recognise."""

    __tablename__ = "ingredients"

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    inci_name: Mapped[str | None] = mapped_column(String(160))
    family: Mapped[str] = mapped_column(String(48), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    common_use: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    knowledge_version: Mapped[str] = mapped_column(String(24), nullable=False, default=KNOWLEDGE_VERSION, server_default=KNOWLEDGE_VERSION)

    __table_args__ = (Index("ix_ingredients_family", "family"),)


class IngredientAlias(UUIDPrimaryKey, TimestampMixin, Base):
    """Another spelling for the same ingredient.

    Labels print INCI names, marketing names and abbreviations interchangeably.
    Without aliases the parser reads a full shelf as empty.
    """

    __tablename__ = "ingredient_aliases"

    ingredient_key: Mapped[str] = mapped_column(ForeignKey("ingredients.key", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (
        UniqueConstraint("alias", name="uq_ingredient_alias"),
        Index("ix_ingredient_aliases_key", "ingredient_key"),
    )


class IngredientRule(UUIDPrimaryKey, TimestampMixin, Base):
    """A single-ingredient note: what it is for, how it is usually used."""

    __tablename__ = "ingredient_rules"

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ingredient_key: Mapped[str] = mapped_column(ForeignKey("ingredients.key", ondelete="CASCADE"), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info", server_default="info")
    headline: Mapped[str] = mapped_column(String(200), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class CompatibilityRuleRow(UUIDPrimaryKey, TimestampMixin, Base):
    """A pairwise rule between two ingredient families.

    This is the table a conflict warning points at. Severity is honest:
    ``info`` covers widely repeated claims the evidence does not support, so
    the app can correct them rather than echo them.
    """

    __tablename__ = "compatibility_rules"

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_a: Mapped[str] = mapped_column(String(48), nullable=False)
    family_b: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(String(200), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    knowledge_version: Mapped[str] = mapped_column(String(24), nullable=False, default=KNOWLEDGE_VERSION, server_default=KNOWLEDGE_VERSION)

    __table_args__ = (Index("ix_compatibility_rules_families", "family_a", "family_b"),)


class ContraindicationRule(UUIDPrimaryKey, TimestampMixin, Base):
    """A rule that removes a product from a routine.

    Only two things ever land here: an ingredient the user told us to avoid,
    and a product past the date they recorded. Neither is a clinical judgement —
    one is the user's own instruction, the other is arithmetic on a date.
    """

    __tablename__ = "contraindication_rules"

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trigger: Mapped[str] = mapped_column(String(48), nullable=False)   # user_allergy | expired
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(String(200), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class RoutineTemplate(UUIDPrimaryKey, TimestampMixin, Base):
    """The shape of one routine: which steps, in which order, how often."""

    __tablename__ = "routine_templates"

    kind: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    frequency: Mapped[str] = mapped_column(String(80), nullable=False)
    slots: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    knowledge_version: Mapped[str] = mapped_column(String(24), nullable=False, default=KNOWLEDGE_VERSION, server_default=KNOWLEDGE_VERSION)


class PerfumeContextRule(UUIDPrimaryKey, TimestampMixin, Base):
    """Convention about which fragrance families suit which context.

    Convention, and labelled as such — no claim about chemistry, because the
    app has no data that could support one.
    """

    __tablename__ = "perfume_context_rules"

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    factor: Mapped[str] = mapped_column(String(32), nullable=False)
    match_value: Mapped[str] = mapped_column(String(48), nullable=False)
    families: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    note: Mapped[str] = mapped_column(Text, nullable=False)


class AppearanceNutritionRule(UUIDPrimaryKey, TimestampMixin, Base):
    """A nutrient, what it is associated with, and everyday Indian foods.

    Association only. No amounts, no targets, and nothing that would let the
    product claim someone is short of anything.
    """

    __tablename__ = "appearance_nutrition_rules"

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    nutrient_key: Mapped[str] = mapped_column(String(48), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    appearance_context: Mapped[str] = mapped_column(Text, nullable=False)
    foods: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


# --- User data ---------------------------------------------------------------


class ProductIngredient(UUIDPrimaryKey, TimestampMixin, Base):
    """An ingredient found in one of the user's own products.

    ``needs_confirmation`` is the Phase 3 draft boundary applied to ingredients:
    a low-confidence read is shown for review and never drives a warning.
    """

    __tablename__ = "product_ingredients"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    ingredient_key: Mapped[str] = mapped_column(ForeignKey("ingredients.key", ondelete="CASCADE"), nullable=False)
    matched_text: Mapped[str] = mapped_column(String(160), nullable=False, default="", server_default="")
    position: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("item_id", "ingredient_key", name="uq_product_ingredient"),
        Index("ix_product_ingredients_account", "account_id", "ingredient_key"),
    )


class Routine(UUIDPrimaryKey, TimestampMixin, Base):
    """One compiled routine belonging to a user."""

    __tablename__ = "routines"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    frequency: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    climate: Mapped[str | None] = mapped_column(String(24))
    engine_version: Mapped[str] = mapped_column(String(24), nullable=False, default=KNOWLEDGE_VERSION, server_default=KNOWLEDGE_VERSION)
    explanation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="deterministic", server_default="deterministic")
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    climate_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    skipped_for_allergy: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        UniqueConstraint("account_id", "kind", name="uq_routine_account_kind"),
        Index("ix_routines_account", "account_id", "status"),
    )


class RoutineStep(UUIDPrimaryKey, TimestampMixin, Base):
    """One step. ``inventory_item_id`` is set unless the step is a gap."""

    __tablename__ = "routine_steps"

    routine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routines.id", ondelete="CASCADE"), nullable=False)
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    why: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    product_name: Mapped[str | None] = mapped_column(String(200))
    safety_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    alternative: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    climate_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    is_gap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        UniqueConstraint("routine_id", "slot", name="uq_routine_step_slot"),
        Index("ix_routine_steps_routine", "routine_id", "position"),
    )


class RoutineAdherence(UUIDPrimaryKey, TimestampMixin, Base):
    """Whether a step was done on a given day.

    Consistency, never a streak to break. Missing a day is not a failure and
    the wording around this never treats it as one.
    """

    __tablename__ = "routine_adherence"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    routine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routines.id", ondelete="CASCADE"), nullable=False)
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("routine_steps.id", ondelete="SET NULL"))
    done_on: Mapped[date] = mapped_column(Date, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(String(240))

    __table_args__ = (
        UniqueConstraint("routine_id", "slot", "done_on", name="uq_routine_adherence_slot_day"),
        Index("ix_routine_adherence_account_date", "account_id", "done_on"),
    )


class UserReportedObservation(UUIDPrimaryKey, TimestampMixin, Base):
    """Something the user noticed, in their own words.

    Stored verbatim and never interpreted. The app does not turn "my skin felt
    tight" into a condition — it keeps the note so the user can see it next to
    what they were using at the time.
    """

    __tablename__ = "user_reported_observations"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    area: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    routed_to_professional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (Index("ix_user_observations_account_date", "account_id", "observed_on"),)


class CareExperienceFeedback(UUIDPrimaryKey, TimestampMixin, Base):
    """Explicit, subjective Care experience feedback.

    This is historical user input only.  It is intentionally not connected to
    an inventory item or routine step by foreign key so that it survives
    replacement of the current rendering row.
    """

    __tablename__ = "care_experience_feedback"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    experienced_on: Mapped[date] = mapped_column(Date, nullable=False)
    feedback_version: Mapped[str] = mapped_column(String(16), nullable=False, default=CARE_EXPERIENCE_FEEDBACK_VERSION, server_default=CARE_EXPERIENCE_FEEDBACK_VERSION)
    routine_kind: Mapped[str | None] = mapped_column(String(24))
    routine_slot: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("subject_type IN ('product', 'routine_step')", name="ck_care_feedback_subject_type"),
        CheckConstraint("dimension IN ('overall_experience', 'comfort', 'ease_of_use', 'routine_fit')", name="ck_care_feedback_dimension"),
        CheckConstraint("sentiment IN ('positive', 'neutral', 'negative')", name="ck_care_feedback_sentiment"),
        CheckConstraint("feedback_version = 'v3-03.13'", name="ck_care_feedback_version"),
        CheckConstraint("(subject_type = 'routine_step' AND routine_kind IS NOT NULL AND routine_slot IS NOT NULL) OR (subject_type = 'product' AND routine_kind IS NULL AND routine_slot IS NULL)", name="ck_care_feedback_subject_shape"),
        Index("ix_care_feedback_account_date_created", "account_id", "experienced_on", "created_at"),
        Index("ix_care_feedback_account_subject_created", "account_id", "subject_type", "subject_id", "created_at"),
    )


class ProductExpiryEvent(UUIDPrimaryKey, TimestampMixin, Base):
    """A computed expiry assessment, with the rule that produced it.

    Distinct from Phase 3's ``item_expiry_events``, which records the dates the
    user declared. This records what the engine concluded from them.
    """

    __tablename__ = "product_expiry_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_expiry: Mapped[date | None] = mapped_column(Date)
    days_to_expiry: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    __table_args__ = (Index("ix_product_expiry_account_item", "account_id", "item_id", "created_at"),)


class RoutineRecommendationRun(UUIDPrimaryKey, TimestampMixin, Base):
    """One pass of the routine compiler, for provenance."""

    __tablename__ = "routine_recommendation_runs"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="succeeded", server_default="succeeded")
    engine_version: Mapped[str] = mapped_column(String(24), nullable=False, default=KNOWLEDGE_VERSION, server_default=KNOWLEDGE_VERSION)
    explanation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="deterministic", server_default="deterministic")
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    products_considered: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    routines_built: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    warnings_raised: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (Index("ix_routine_runs_account", "account_id", "created_at"),)


class SupplementSafetyFlag(UUIDPrimaryKey, TimestampMixin, Base):
    """A note about a supplement the user owns.

    The set of things that can land here is closed and contains no dosage, no
    effect and no interaction: expiry status, missing dates, and a pointer to a
    professional when the user's own note reads like a health question.
    """

    __tablename__ = "supplement_safety_flags"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    flag: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("item_id", "flag", name="uq_supplement_flag"),
        Index("ix_supplement_flags_account", "account_id", "flag"),
    )


class NutritionPreference(UUIDPrimaryKey, TimestampMixin, Base):
    """What this person eats. A constraint, not a suggestion."""

    __tablename__ = "nutrition_preferences"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True)
    diet: Mapped[str] = mapped_column(String(32), nullable=False, default="non_vegetarian", server_default="non_vegetarian")
    avoid_foods: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    focus_nutrients: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class HydrationPreference(UUIDPrimaryKey, TimestampMixin, Base):
    """Hydration reminders, off unless the user turns them on.

    No target volume is stored. A litre figure would be a health instruction,
    and this app does not give those.
    """

    __tablename__ = "hydration_preferences"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    remind_in_hot_weather_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(String(240))
