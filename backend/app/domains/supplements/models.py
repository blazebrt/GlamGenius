"""Account-owned structured facts transcribed from supplement labels."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
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
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_supplement_label_amount_nonnegative"),
        CheckConstraint("source IN ('user_declared', 'photo_extracted')", name="ck_supplement_label_source"),
        CheckConstraint("verification_state IN ('draft', 'confirmed')", name="ck_supplement_label_verification_state"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_supplement_label_confidence"),
        UniqueConstraint("account_id", "client_mutation_id", name="uq_supplement_label_client_mutation"),
        Index("ix_supplement_label_account_item", "account_id", "item_id"),
        Index("ix_supplement_label_overlap", "account_id", "canonical_component_key", "verification_state"),
    )


class SupplementComponentKnowledge(UUIDPrimaryKey, TimestampMixin, Base):
    """Reviewed knowledge about one compound form of one nutrient.

    Keyed on the same ``canonical_component_key`` that
    ``engine.component_identity()`` produces, so a photographed label and an
    entry here meet on one key.

    Release-owned reference data: global, not account-owned, and never written
    by a customer. ``evidence_claim_id`` points at the authoring-tool entry that
    carries this row's review state, so the review workflow is not duplicated
    here.

    The two numbers are kept apart on purpose. ``elemental_percent`` is
    arithmetic on atomic weights and needs no citation. Everything under
    ``absorption_`` comes from a study, varies between people, and carries a
    confidence and, where the literature disagrees, both figures.
    """

    __tablename__ = "supplement_component_knowledge"

    canonical_component_key: Mapped[str] = mapped_column(String(160), nullable=False)
    nutrient: Mapped[str] = mapped_column(String(120), nullable=False)
    compound_form: Mapped[str] = mapped_column(String(160), nullable=False)

    # Arithmetic. High confidence by construction.
    elemental_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    percent_kind: Mapped[str | None] = mapped_column(String(32))
    hydration_note: Mapped[str | None] = mapped_column(Text)

    # From studies. Absent means not enough information, which is an answer.
    absorption_summary: Mapped[str | None] = mapped_column(Text)
    absorption_value: Mapped[str | None] = mapped_column(String(256))
    absorption_unit: Mapped[str | None] = mapped_column(String(64))
    disagreement: Mapped[str | None] = mapped_column(Text)

    source_name: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_identifier: Mapped[str | None] = mapped_column(String(120))

    confidence: Mapped[str | None] = mapped_column(String(16))
    evidence_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    # Whether a person has opened the source and confirmed the number.
    verification: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified", server_default="unverified",
    )
    notes: Mapped[str | None] = mapped_column(Text)
    evidence_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_claims.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        UniqueConstraint("canonical_component_key", "compound_form", name="uq_supplement_knowledge_form"),
        CheckConstraint(
            "elemental_percent IS NULL OR (elemental_percent > 0 AND elemental_percent <= 100)",
            name="ck_supplement_knowledge_percent_range",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence IN ('high', 'medium', 'low')",
            name="ck_supplement_knowledge_confidence",
        ),
        CheckConstraint(
            "verification IN ('unverified', 'confirmed', 'disputed')",
            name="ck_supplement_knowledge_verification",
        ),
        # An absorption figure without a source is exactly what this must never hold.
        CheckConstraint(
            "absorption_value IS NULL OR (source_url IS NOT NULL AND btrim(source_url) <> '')",
            name="ck_supplement_knowledge_absorption_needs_source",
        ),
        # And it must carry a confidence rating.
        CheckConstraint(
            "absorption_value IS NULL OR confidence IS NOT NULL",
            name="ck_supplement_knowledge_absorption_needs_confidence",
        ),
        Index("ix_supplement_knowledge_key", "canonical_component_key"),
    )
