"""Account-owned structured facts transcribed from supplement labels."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Float, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


class SupplementLabelComponent(UUIDPrimaryKey, TimestampMixin, Base):
    """One printed component on one owned supplement label.

    Amounts are preserved package facts. They are never interpreted as an
    intake, recommendation, target, or daily total.
    """

    __tablename__ = "supplement_label_components"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False,
    )
    raw_name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_component_key: Mapped[str | None] = mapped_column(String(160))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    unit: Mapped[str | None] = mapped_column(String(32))
    serving_text: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_declared", server_default="user_declared")
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False, default="confirmed", server_default="confirmed")
    confidence: Mapped[float | None] = mapped_column(Float)
    source_ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id", ondelete="SET NULL"))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="vc-07-v1", server_default="vc-07-v1")
    client_mutation_id: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        UniqueConstraint("account_id", "client_mutation_id", name="uq_supplement_label_client_mutation"),
        Index("ix_supplement_label_account_item", "account_id", "item_id"),
        Index("ix_supplement_label_overlap", "account_id", "canonical_component_key", "verification_state"),
    )
