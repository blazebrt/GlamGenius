"""Global food-composition reference data models."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class FoodCompositionDataset(UUIDPrimaryKey, TimestampMixin, Base):
    """Metadata for a food-composition reference dataset."""

    __tablename__ = "food_composition_datasets"

    dataset_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_sources.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False)
    rights_status: Mapped[str] = mapped_column(String(32), nullable=False)
    import_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    rights_note: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "rights_status IN ('restricted_reference', 'permission_granted', 'open_licensed')",
            name="ck_food_composition_datasets_rights_status",
        ),
        CheckConstraint(
            "import_status IN ('metadata_only', 'ready_for_import', 'imported', 'retired')",
            name="ck_food_composition_datasets_import_status",
        ),
        CheckConstraint("status IN ('active', 'retired')", name="ck_food_composition_datasets_status"),
        CheckConstraint(
            "NOT (rights_status = 'restricted_reference' AND import_status IN ('ready_for_import', 'imported'))",
            name="ck_food_composition_datasets_restricted_import",
        ),
    )


class FoodReferenceItem(UUIDPrimaryKey, TimestampMixin, Base):
    """One food identity from an authorized composition dataset."""

    __tablename__ = "food_reference_items"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("food_composition_datasets.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    source_food_code: Mapped[str] = mapped_column(String(96), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    food_group: Mapped[str | None] = mapped_column(String(160))
    scientific_name: Mapped[str | None] = mapped_column(String(256))
    local_names: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")

    __table_args__ = (
        UniqueConstraint("dataset_id", "source_food_code", name="uq_food_reference_items_dataset_source_code"),
        CheckConstraint("status IN ('active', 'retired')", name="ck_food_reference_items_status"),
    )


class FoodNutrientValue(UUIDPrimaryKey, TimestampMixin, Base):
    """A numeric nutrient value pending reviewed source-format semantics."""

    __tablename__ = "food_nutrient_values"

    food_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("food_reference_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    nutrient_key: Mapped[str] = mapped_column(String(96), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    basis: Mapped[str] = mapped_column(String(96), nullable=False)
    source_locator: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        UniqueConstraint("food_id", "nutrient_key", "unit", "basis", name="uq_food_nutrient_values_identity"),
        CheckConstraint("amount >= 0", name="ck_food_nutrient_values_amount_nonnegative"),
    )
