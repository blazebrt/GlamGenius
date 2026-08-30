"""Relational models for the complete appearance inventory."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class InventoryCategory(TimestampMixin, Base):
    __tablename__ = "inventory_categories"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class InventoryItem(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_items"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(ForeignKey("inventory_categories.key"), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="confirmed", server_default="confirmed")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    purchase_date: Mapped[date | None] = mapped_column(Date)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_at: Mapped[date | None] = mapped_column(Date)
    condition: Mapped[str] = mapped_column(String(24), nullable=False, default="good", server_default="good")
    replacement_priority: Mapped[str] = mapped_column(String(24), nullable=False, default="none", server_default="none")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_mutation_id: Mapped[str | None] = mapped_column(String(80))
    source_ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (
        UniqueConstraint("account_id", "client_mutation_id", name="uq_inventory_client_mutation"),
        Index("ix_inventory_items_account_category_status", "account_id", "category", "status"),
        Index("ix_inventory_items_account_updated", "account_id", "updated_at"),
        Index("ix_inventory_items_account_brand", "account_id", "brand"),
    )


class InventoryItemImage(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_item_images"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    media_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    __table_args__ = (UniqueConstraint("item_id", "media_asset_id", name="uq_inventory_item_image"), Index("ix_inventory_item_images_item", "item_id", "position"))


class InventoryAttribute(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_attributes"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("item_id", "key", name="uq_inventory_attribute_key"), Index("ix_inventory_attributes_key_state", "key", "verification_state"))


class WardrobeItemDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "wardrobe_item_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    colour: Mapped[str | None] = mapped_column(String(80)); pattern: Mapped[str | None] = mapped_column(String(80))
    fabric: Mapped[str | None] = mapped_column(String(100)); fit: Mapped[str | None] = mapped_column(String(80)); size: Mapped[str | None] = mapped_column(String(40))
    season: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    occasion: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    formality: Mapped[str | None] = mapped_column(String(80)); laundry_state: Mapped[str | None] = mapped_column(String(40)); care_instructions: Mapped[str | None] = mapped_column(Text)


class ShoeItemDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "shoe_item_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    shoe_type: Mapped[str | None] = mapped_column(String(80)); colour: Mapped[str | None] = mapped_column(String(80)); size: Mapped[str | None] = mapped_column(String(40))
    heel_height: Mapped[Decimal | None] = mapped_column(Numeric(5, 1)); comfort: Mapped[str | None] = mapped_column(String(80))
    occasion: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    weather_suitability: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class AccessoryItemDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "accessory_item_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    accessory_type: Mapped[str | None] = mapped_column(String(80)); colour: Mapped[str | None] = mapped_column(String(80)); metal: Mapped[str | None] = mapped_column(String(80)); material: Mapped[str | None] = mapped_column(String(100)); style: Mapped[str | None] = mapped_column(String(100))
    occasion: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class BeautyProductDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "beauty_product_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    product_type: Mapped[str | None] = mapped_column(String(80)); size: Mapped[str | None] = mapped_column(String(40)); opened_date: Mapped[date | None] = mapped_column(Date); expiry_date: Mapped[date | None] = mapped_column(Date); period_after_opening_months: Mapped[int | None] = mapped_column(Integer); purpose: Mapped[str | None] = mapped_column(String(200)); ingredients_text: Mapped[str | None] = mapped_column(Text)
    active_ingredients: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    use_frequency: Mapped[str | None] = mapped_column(String(80)); routine_position: Mapped[str | None] = mapped_column(String(80)); remaining_percent: Mapped[int | None] = mapped_column(Integer)


class HairProductDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "hair_product_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    product_type: Mapped[str | None] = mapped_column(String(80)); size: Mapped[str | None] = mapped_column(String(40)); opened_date: Mapped[date | None] = mapped_column(Date); expiry_date: Mapped[date | None] = mapped_column(Date); period_after_opening_months: Mapped[int | None] = mapped_column(Integer); purpose: Mapped[str | None] = mapped_column(String(200)); ingredients_text: Mapped[str | None] = mapped_column(Text)
    active_ingredients: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    use_frequency: Mapped[str | None] = mapped_column(String(80)); routine_position: Mapped[str | None] = mapped_column(String(80)); remaining_percent: Mapped[int | None] = mapped_column(Integer)


class PerfumeDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "perfume_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    fragrance_family: Mapped[str | None] = mapped_column(String(100)); concentration: Mapped[str | None] = mapped_column(String(80))
    season: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]"); occasion: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    longevity_user_reported: Mapped[str | None] = mapped_column(String(100)); usage_frequency: Mapped[str | None] = mapped_column(String(80)); remaining_percent: Mapped[int | None] = mapped_column(Integer)


class SupplementDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "supplement_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    supplement_name: Mapped[str | None] = mapped_column(String(160)); brand: Mapped[str | None] = mapped_column(String(120)); user_entered_purpose: Mapped[str | None] = mapped_column(String(240)); expiry_date: Mapped[date | None] = mapped_column(Date); opened_date: Mapped[date | None] = mapped_column(Date); use_frequency: Mapped[str | None] = mapped_column(String(80)); label_information: Mapped[str | None] = mapped_column(Text); safety_disclaimer: Mapped[str] = mapped_column(Text, nullable=False)


class ItemUsageEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_usage_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    used_on: Mapped[date] = mapped_column(Date, nullable=False); quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1"); note: Mapped[str | None] = mapped_column(String(240))
    __table_args__ = (Index("ix_item_usage_events_item_date", "item_id", "used_on"),)


class ItemConditionEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_condition_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    condition: Mapped[str] = mapped_column(String(24), nullable=False); note: Mapped[str | None] = mapped_column(String(240))


class ItemExpiryEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_expiry_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False); source: Mapped[str] = mapped_column(String(32), nullable=False); note: Mapped[str | None] = mapped_column(String(240))


class ItemRelationship(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_relationships"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    from_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    to_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(40), nullable=False)


class DuplicateCandidate(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "duplicate_candidates"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    item_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False); reason: Mapped[str] = mapped_column(String(240), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending"); resolution: Mapped[str | None] = mapped_column(String(32)); resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("account_id", "item_a_id", "item_b_id", name="uq_duplicate_candidate_pair"), Index("ix_duplicate_candidates_account_status", "account_id", "status"))


class InventoryImportJob(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_import_jobs"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    capture_type: Mapped[str] = mapped_column(String(32), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False); media_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL")); ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL")); detected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); error_code: Mapped[str | None] = mapped_column(String(64)); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryImportCandidate(UUIDPrimaryKey, TimestampMixin, Base):
    """One thing a shelf photo appeared to show, waiting for one tap.

    A candidate is deliberately **not** an inventory item. Fifteen guesses from
    one photo must not appear on the shelf, feed a routine, or land in the
    duplicates queue before a person has looked at them — "nothing enters the
    shelf unconfirmed" is only true if the unconfirmed thing is not there.

    Confirming turns a candidate into a real, confirmed item through the same
    ``service.create_item`` every other item goes through. Rejecting creates
    nothing and leaves the row as a record of what was offered and refused.
    """

    __tablename__ = "inventory_import_candidates"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_import_jobs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    #: Where it sat in the model's reading of the photo. Keeps the review list
    #: in a stable order between refreshes.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    #: ``[{"key": ..., "value": ..., "confidence": ...}]`` — the same shape the
    #: single-item path writes into inventory_attributes on confirm.
    attributes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    uncertain_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    photo_quality_notes: Mapped[str | None] = mapped_column(Text)
    #: pending | confirmed | rejected. Never anything else.
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    #: Set only when a person confirmed it and an item was created.
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("state IN ('pending', 'confirmed', 'rejected')", name="ck_import_candidate_state"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_import_candidate_confidence"),
        Index("ix_import_candidates_job", "job_id", "position"),
        Index("ix_import_candidates_account_state", "account_id", "state"),
    )


class InventoryValueEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_value_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(16), nullable=False); estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2)); currency: Mapped[str] = mapped_column(String(3), nullable=False); inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False); explanation: Mapped[str] = mapped_column(Text, nullable=False)


class InventoryEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_events"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(48), nullable=False); actor: Mapped[str] = mapped_column(String(32), nullable=False); payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (Index("ix_inventory_events_account_item", "account_id", "item_id", "created_at"),)
