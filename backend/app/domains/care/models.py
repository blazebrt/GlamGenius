"""Subject-owned Care preference state.

Physical inventory remains account-owned; these rows record how one household
subject interprets that shared product.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class CareProductPreference(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "care_product_preferences"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    household_subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "family_profiles.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ), nullable=False
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False
    )
    preference_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    authority_source: Mapped[str] = mapped_column(String(24), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "preference_kind IN ('paused', 'preferred')",
            name="ck_care_product_preference_kind",
        ),
        CheckConstraint(
            "authority_source IN ('direct_user', 'shelf_manager', 'legacy_adopted')",
            name="ck_care_product_preference_source",
        ),
        UniqueConstraint(
            "account_id", "household_subject_id", "inventory_item_id", "preference_kind",
            name="uq_care_product_preference_subject_item_kind",
        ),
        Index("ix_care_product_preferences_subject_kind", "account_id", "household_subject_id", "preference_kind"),
        Index("ix_care_product_preferences_subject_item", "account_id", "household_subject_id", "inventory_item_id"),
    )
