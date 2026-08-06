"""Reference-data seed catalogue models.

These tables hold the versioned reference data written by
``python -m app.bootstrap.reference_data``. They are **not** account-owned;
every row is global and identifies itself by a natural key so the seed
bootstrap can upsert safely and idempotently.

This module adds the reference tables
the seed catalogue completion required:

* ``seed_version_records`` — a small audit trail that says which seed version
  ran when, so operators can trace which release wrote which rows.
* ``routine_templates`` + ``routine_template_steps`` — the versioned
  skincare/hair-care templates the routine engine picks from.
* ``perfume_context_rules`` — the layering/occasion/time-of-day guidance the
  perfume assistant reads.
* ``supplement_context_rules`` — general appearance-and-wellness context and
  its safety disclaimer category.
* ``ingredient_contraindication_rules`` and ``ingredient_sensitivity_rules``
  — the safety layers the routine engine reasons about explicitly.
* ``inventory_subtype_definitions`` — per-category allowed subtypes with
  their required and optional attribute keys.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class SeedVersionRecord(UUIDPrimaryKey, TimestampMixin, Base):
    """One row per seed run. The most recent per-domain row wins."""

    __tablename__ = "seed_version_records"

    seed_domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seed_version: Mapped[str] = mapped_column(String(32), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "seed_domain", "seed_version", name="uq_seed_versions_domain_version"
        ),
    )


class RoutineTemplateStep(TimestampMixin, Base):
    """A single ordered step in a routine template.

    Complementary to the existing ``routine_templates`` table declared in
    :mod:`app.domains.routines.models`. That table stores the template shape
    (``kind``, ``label``, ``frequency``, ``slots``); this one adds the ordered
    step-by-step guidance the routine engine renders. Joined by
    ``template_kind`` = ``routine_templates.kind``.
    """

    __tablename__ = "routine_template_steps"

    template_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    optional: Mapped[bool] = mapped_column(nullable=False, default=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)


class SupplementContextRule(TimestampMixin, Base):
    """General appearance/wellness context with the applicable safety
    disclaimer category. Never diagnosis or dosage."""

    __tablename__ = "supplement_context_rules"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    context_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    # ``general_wellness`` | ``consult_professional`` | ``pregnancy_flag``
    safety_category: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)


class IngredientContraindicationRule(TimestampMixin, Base):
    """Explicit contraindications the routine engine treats as blocking."""

    __tablename__ = "ingredient_contraindication_rules"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingredient_key: Mapped[str] = mapped_column(String(64), nullable=False)
    condition_key: Mapped[str] = mapped_column(String(64), nullable=False)
    headline: Mapped[str] = mapped_column(String(256), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "ingredient_key", "condition_key",
            name="uq_contraindication_ingredient_condition",
        ),
    )


class IngredientSensitivityRule(TimestampMixin, Base):
    """Sensitivity cautions — softer than contraindications."""

    __tablename__ = "ingredient_sensitivity_rules"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingredient_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    headline: Mapped[str] = mapped_column(String(256), nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "ingredient_key", "sensitivity_key",
            name="uq_sensitivity_ingredient_key",
        ),
    )


class InventorySubtypeDefinition(TimestampMixin, Base):
    """A canonical subtype inside an inventory category and its attribute schema."""

    __tablename__ = "inventory_subtype_definitions"

    category_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    subtype_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    required_attributes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    optional_attributes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    supports_expiry: Mapped[bool] = mapped_column(nullable=False, default=False)
    supports_condition: Mapped[bool] = mapped_column(nullable=False, default=False)
    supports_usage: Mapped[bool] = mapped_column(nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
