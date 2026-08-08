"""SQLAlchemy models for global evidence provenance."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.evidence.enums import (
    CLAIM_SOURCE_RELATIONSHIPS,
    CLAIM_STATUSES,
    CLAIM_TYPES,
    EVIDENCE_DOMAINS,
    EVIDENCE_STRENGTHS,
    REGULATORY_CONTEXTS,
    REVIEW_STATUSES,
    RULE_EVIDENCE_RELATIONSHIPS,
    RULE_KINDS,
    SOURCE_STATUSES,
    SOURCE_TYPES,
)
from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey


def _check(name: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    literal = ", ".join("'%s'" % value for value in values)
    return CheckConstraint(f"{column} IN ({literal})", name=name)


class EvidenceSource(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "evidence_sources"

    source_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    source_series_key: Mapped[str] = mapped_column(String(160), nullable=False)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    publisher: Mapped[str] = mapped_column(String(256), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(128))
    publication_date: Mapped[date | None] = mapped_column(Date)
    version_or_revision: Mapped[str | None] = mapped_column(String(160))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", server_default="active")
    supersedes_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_sources.id", ondelete="RESTRICT")
    )
    license_or_use_note: Mapped[str | None] = mapped_column(Text)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _check("ck_evidence_sources_source_type", "source_type", SOURCE_TYPES),
        _check("ck_evidence_sources_status", "status", SOURCE_STATUSES),
        CheckConstraint("supersedes_source_id IS NULL OR supersedes_source_id <> id", name="ck_evidence_sources_not_self_supersede"),
        Index("ix_evidence_sources_series", "source_series_key"),
        Index("ix_evidence_sources_canonical_url", "canonical_url"),
        Index("ix_evidence_sources_type_jurisdiction_status", "source_type", "jurisdiction", "status"),
        Index("ix_evidence_sources_review_due_status", "next_review_due_at", "status"),
    )


class EvidenceClaim(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "evidence_claims"

    claim_key: Mapped[str] = mapped_column(String(200), nullable=False)
    claim_version: Mapped[int] = mapped_column(nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(48), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_strength: Mapped[str | None] = mapped_column(String(32))
    strength_rationale: Mapped[str | None] = mapped_column(Text)
    claim_status: Mapped[str | None] = mapped_column(String(24))
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", server_default="draft")
    regulatory_context: Mapped[str] = mapped_column(String(48), nullable=False, default="unknown", server_default="unknown")
    structured_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(160))
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_claims.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        UniqueConstraint("claim_key", "claim_version", name="uq_evidence_claim_key_version"),
        CheckConstraint("claim_version > 0", name="ck_evidence_claim_version_positive"),
        _check("ck_evidence_claims_domain", "domain", EVIDENCE_DOMAINS),
        _check("ck_evidence_claims_claim_type", "claim_type", CLAIM_TYPES),
        _check("ck_evidence_claims_strength", "evidence_strength", EVIDENCE_STRENGTHS),
        CheckConstraint("evidence_strength IS NULL OR (strength_rationale IS NOT NULL AND btrim(strength_rationale) <> '')", name="ck_evidence_claims_strength_rationale"),
        _check("ck_evidence_claims_status", "claim_status", CLAIM_STATUSES),
        _check("ck_evidence_claims_review_status", "review_status", REVIEW_STATUSES),
        _check("ck_evidence_claims_regulatory_context", "regulatory_context", REGULATORY_CONTEXTS),
        CheckConstraint("supersedes_claim_id IS NULL OR supersedes_claim_id <> id", name="ck_evidence_claims_not_self_supersede"),
        Index("ix_evidence_claims_subject", "domain", "subject_type", "subject_key"),
    )


class EvidenceClaimSource(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "evidence_claim_sources"

    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_claims.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_sources.id", ondelete="RESTRICT"), nullable=False)
    relationship: Mapped[str] = mapped_column(String(24), nullable=False)
    locator: Mapped[str | None] = mapped_column(String(512))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        _check("ck_evidence_claim_sources_relationship", "relationship", CLAIM_SOURCE_RELATIONSHIPS),
        Index("ix_evidence_claim_sources_claim", "claim_id"),
        Index("ix_evidence_claim_sources_source", "source_id"),
        UniqueConstraint("claim_id", "source_id", "relationship", "locator", name="uq_evidence_claim_source_identity"),
        Index(
            "uq_evidence_claim_source_identity_normalized",
            "claim_id", "source_id", "relationship", text("COALESCE(locator, '')"), unique=True,
        ),
    )


class RuleEvidenceLink(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "rule_evidence_links"

    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_claims.id", ondelete="RESTRICT"), nullable=False)
    relationship: Mapped[str] = mapped_column(String(24), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(160))
    review_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("domain", "rule_kind", "rule_id", "rule_version", "claim_id", "relationship", name="uq_rule_evidence_link_identity"),
        _check("ck_rule_evidence_links_domain", "domain", EVIDENCE_DOMAINS),
        _check("ck_rule_evidence_links_kind", "rule_kind", RULE_KINDS),
        _check("ck_rule_evidence_links_relationship", "relationship", RULE_EVIDENCE_RELATIONSHIPS),
        Index("ix_rule_evidence_links_rule", "domain", "rule_kind", "rule_id", "rule_version"),
    )
