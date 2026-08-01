"""Relational models for the complete appearance inventory."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(ForeignKey("inventory_categories.key"), nullable=False)
    subcategory: Mapped[Optional[str]] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="confirmed", server_default="confirmed")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    purchase_date: Mapped[Optional[date]] = mapped_column(Date)
    purchase_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_at: Mapped[Optional[date]] = mapped_column(Date)
    condition: Mapped[str] = mapped_column(String(24), nullable=False, default="good", server_default="good")
    replacement_priority: Mapped[str] = mapped_column(String(24), nullable=False, default="none", server_default="none")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    client_mutation_id: Mapped[Optional[str]] = mapped_column(String(80))
    source_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    model_version: Mapped[Optional[str]] = mapped_column(String(64))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32))
    schema_version: Mapped[Optional[str]] = mapped_column(String(32))
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
    source_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    model_version: Mapped[Optional[str]] = mapped_column(String(64))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32))
    schema_version: Mapped[Optional[str]] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("item_id", "key", name="uq_inventory_attribute_key"), Index("ix_inventory_attributes_key_state", "key", "verification_state"))


class WardrobeItemDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "wardrobe_item_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    colour: Mapped[Optional[str]] = mapped_column(String(80)); pattern: Mapped[Optional[str]] = mapped_column(String(80))
    fabric: Mapped[Optional[str]] = mapped_column(String(100)); fit: Mapped[Optional[str]] = mapped_column(String(80)); size: Mapped[Optional[str]] = mapped_column(String(40))
    season: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    occasion: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    formality: Mapped[Optional[str]] = mapped_column(String(80)); laundry_state: Mapped[Optional[str]] = mapped_column(String(40)); care_instructions: Mapped[Optional[str]] = mapped_column(Text)


class ShoeItemDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "shoe_item_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    shoe_type: Mapped[Optional[str]] = mapped_column(String(80)); colour: Mapped[Optional[str]] = mapped_column(String(80)); size: Mapped[Optional[str]] = mapped_column(String(40))
    heel_height: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 1)); comfort: Mapped[Optional[str]] = mapped_column(String(80))
    occasion: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    weather_suitability: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class AccessoryItemDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "accessory_item_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    accessory_type: Mapped[Optional[str]] = mapped_column(String(80)); colour: Mapped[Optional[str]] = mapped_column(String(80)); metal: Mapped[Optional[str]] = mapped_column(String(80)); material: Mapped[Optional[str]] = mapped_column(String(100)); style: Mapped[Optional[str]] = mapped_column(String(100))
    occasion: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class BeautyProductDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "beauty_product_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(80)); size: Mapped[Optional[str]] = mapped_column(String(40)); opened_date: Mapped[Optional[date]] = mapped_column(Date); expiry_date: Mapped[Optional[date]] = mapped_column(Date); period_after_opening_months: Mapped[Optional[int]] = mapped_column(Integer); purpose: Mapped[Optional[str]] = mapped_column(String(200)); ingredients_text: Mapped[Optional[str]] = mapped_column(Text)
    active_ingredients: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    use_frequency: Mapped[Optional[str]] = mapped_column(String(80)); routine_position: Mapped[Optional[str]] = mapped_column(String(80)); remaining_percent: Mapped[Optional[int]] = mapped_column(Integer)


class HairProductDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "hair_product_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(80)); size: Mapped[Optional[str]] = mapped_column(String(40)); opened_date: Mapped[Optional[date]] = mapped_column(Date); expiry_date: Mapped[Optional[date]] = mapped_column(Date); period_after_opening_months: Mapped[Optional[int]] = mapped_column(Integer); purpose: Mapped[Optional[str]] = mapped_column(String(200)); ingredients_text: Mapped[Optional[str]] = mapped_column(Text)
    active_ingredients: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    use_frequency: Mapped[Optional[str]] = mapped_column(String(80)); routine_position: Mapped[Optional[str]] = mapped_column(String(80)); remaining_percent: Mapped[Optional[int]] = mapped_column(Integer)


class PerfumeDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "perfume_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    fragrance_family: Mapped[Optional[str]] = mapped_column(String(100)); concentration: Mapped[Optional[str]] = mapped_column(String(80))
    season: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]"); occasion: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    longevity_user_reported: Mapped[Optional[str]] = mapped_column(String(100)); usage_frequency: Mapped[Optional[str]] = mapped_column(String(80)); remaining_percent: Mapped[Optional[int]] = mapped_column(Integer)


class SupplementDetail(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "supplement_details"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, unique=True)
    supplement_name: Mapped[Optional[str]] = mapped_column(String(160)); brand: Mapped[Optional[str]] = mapped_column(String(120)); user_entered_purpose: Mapped[Optional[str]] = mapped_column(String(240)); expiry_date: Mapped[Optional[date]] = mapped_column(Date); opened_date: Mapped[Optional[date]] = mapped_column(Date); use_frequency: Mapped[Optional[str]] = mapped_column(String(80)); label_information: Mapped[Optional[str]] = mapped_column(Text); safety_disclaimer: Mapped[str] = mapped_column(Text, nullable=False)


class ItemUsageEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_usage_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    used_on: Mapped[date] = mapped_column(Date, nullable=False); quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1"); note: Mapped[Optional[str]] = mapped_column(String(240))
    __table_args__ = (Index("ix_item_usage_events_item_date", "item_id", "used_on"),)


class ItemConditionEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_condition_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    condition: Mapped[str] = mapped_column(String(24), nullable=False); note: Mapped[Optional[str]] = mapped_column(String(240))


class ItemExpiryEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_expiry_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False); source: Mapped[str] = mapped_column(String(32), nullable=False); note: Mapped[Optional[str]] = mapped_column(String(240))


class ItemRelationship(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "item_relationships"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    from_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    to_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(40), nullable=False)


class DuplicateCandidate(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "duplicate_candidates"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    item_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    item_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False); reason: Mapped[str] = mapped_column(String(240), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending"); resolution: Mapped[Optional[str]] = mapped_column(String(32)); resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("account_id", "item_a_id", "item_b_id", name="uq_duplicate_candidate_pair"), Index("ix_duplicate_candidates_account_status", "account_id", "status"))


class InventoryImportJob(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_import_jobs"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    capture_type: Mapped[str] = mapped_column(String(32), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False); media_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("media_assets.id", ondelete="SET NULL")); ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL")); detected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); error_code: Mapped[Optional[str]] = mapped_column(String(64)); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class InventoryValueEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_value_events"
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(16), nullable=False); estimated_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2)); currency: Mapped[str] = mapped_column(String(3), nullable=False); inputs: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False); explanation: Mapped[str] = mapped_column(Text, nullable=False)


class InventoryEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_events"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_links.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(48), nullable=False); actor: Mapped[str] = mapped_column(String(32), nullable=False); payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (Index("ix_inventory_events_account_item", "account_id", "item_id", "created_at"),)
