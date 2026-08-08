"""Add V3-02.1 global evidence provenance foundation.

Revision ID: f3e02e1b7a91
Revises: ee2713cab5de
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3e02e1b7a91"
down_revision: Union[str, None] = "ee2713cab5de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _check(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    literal = ", ".join("'%s'" % value for value in values)
    return sa.CheckConstraint(f"{column} IN ({literal})", name=name)


def upgrade() -> None:
    op.create_table(
        "evidence_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source_key", sa.String(160), nullable=False),
        sa.Column("source_series_key", sa.String(160), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("publisher", sa.String(256), nullable=False),
        sa.Column("jurisdiction", sa.String(128)),
        sa.Column("publication_date", sa.Date()),
        sa.Column("version_or_revision", sa.String(160)),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
        sa.Column("supersedes_source_id", postgresql.UUID(as_uuid=True)),
        sa.Column("license_or_use_note", sa.Text()),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("next_review_due_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("source_key"),
        _check("ck_evidence_sources_source_type", "source_type", ("official_regulation", "official_guideline", "government_reference", "systematic_review", "peer_reviewed_research", "professional_consensus", "ingredient_reference_database", "manufacturer_label", "manufacturer_technical_document", "manufacturer_claim", "independent_lab_report", "traditional_reference", "other")),
        _check("ck_evidence_sources_status", "status", ("active", "superseded", "retired", "unavailable")),
        sa.CheckConstraint("supersedes_source_id IS NULL OR supersedes_source_id <> id", name="ck_evidence_sources_not_self_supersede"),
        sa.ForeignKeyConstraint(["supersedes_source_id"], ["evidence_sources.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_evidence_sources_series", "evidence_sources", ["source_series_key"])
    op.create_index("ix_evidence_sources_canonical_url", "evidence_sources", ["canonical_url"])
    op.create_index("ix_evidence_sources_type_jurisdiction_status", "evidence_sources", ["source_type", "jurisdiction", "status"])
    op.create_index("ix_evidence_sources_review_due_status", "evidence_sources", ["next_review_due_at", "status"])

    op.create_table(
        "evidence_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claim_key", sa.String(200), nullable=False), sa.Column("claim_version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False), sa.Column("subject_type", sa.String(64), nullable=False), sa.Column("subject_key", sa.String(200), nullable=False),
        sa.Column("claim_type", sa.String(48), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("evidence_strength", sa.String(32)), sa.Column("strength_rationale", sa.Text()), sa.Column("claim_status", sa.String(24)),
        sa.Column("review_status", sa.String(24), server_default="draft", nullable=False), sa.Column("regulatory_context", sa.String(48), server_default="unknown", nullable=False),
        sa.Column("structured_value", postgresql.JSONB()), sa.Column("ai_generated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("reviewed_by", sa.String(160)), sa.Column("last_reviewed_at", sa.DateTime(timezone=True)), sa.Column("next_review_due_at", sa.DateTime(timezone=True)),
        sa.Column("supersedes_claim_id", postgresql.UUID(as_uuid=True)),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("claim_key", "claim_version", name="uq_evidence_claim_key_version"),
        sa.CheckConstraint("claim_version > 0", name="ck_evidence_claim_version_positive"),
        _check("ck_evidence_claims_domain", "domain", ("skin_care", "hair_care", "home_care", "nutrition", "supplements", "product_quality")),
        _check("ck_evidence_claims_claim_type", "claim_type", ("compatibility_context", "contraindication_context", "sensitivity_context", "usage_context", "regulatory_context", "nutrition_reference", "product_provenance", "traditional_use")),
        _check("ck_evidence_claims_strength", "evidence_strength", ("strong", "moderate", "limited", "traditional_uncertain", "insufficient")),
        sa.CheckConstraint("evidence_strength IS NULL OR (strength_rationale IS NOT NULL AND btrim(strength_rationale) <> '')", name="ck_evidence_claims_strength_rationale"),
        _check("ck_evidence_claims_status", "claim_status", ("supported", "qualified", "conflicting", "unsupported")),
        _check("ck_evidence_claims_review_status", "review_status", ("draft", "reviewed", "approved", "superseded", "retired")),
        _check("ck_evidence_claims_regulatory_context", "regulatory_context", ("cosmetic", "otc_or_regulated", "professional_guidance_required", "jurisdiction_sensitive", "unknown")),
        sa.CheckConstraint("supersedes_claim_id IS NULL OR supersedes_claim_id <> id", name="ck_evidence_claims_not_self_supersede"),
        sa.ForeignKeyConstraint(["supersedes_claim_id"], ["evidence_claims.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_evidence_claims_subject", "evidence_claims", ["domain", "subject_type", "subject_key"])

    op.create_table(
        "evidence_claim_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship", sa.String(24), nullable=False), sa.Column("locator", sa.String(512)), sa.Column("review_note", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("reviewed_by", sa.String(160)),
        sa.PrimaryKeyConstraint("id"),
        _check("ck_evidence_claim_sources_relationship", "relationship", ("supports", "qualifies", "limits", "contradicts", "background")),
        sa.ForeignKeyConstraint(["claim_id"], ["evidence_claims.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["source_id"], ["evidence_sources.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("claim_id", "source_id", "relationship", "locator", name="uq_evidence_claim_source_identity"),
    )
    op.create_index("ix_evidence_claim_sources_claim", "evidence_claim_sources", ["claim_id"])
    op.create_index("ix_evidence_claim_sources_source", "evidence_claim_sources", ["source_id"])
    op.create_index("uq_evidence_claim_source_identity_normalized", "evidence_claim_sources", ["claim_id", "source_id", "relationship", sa.text("COALESCE(locator, '')")], unique=True)

    op.create_table(
        "rule_evidence_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False), sa.Column("rule_kind", sa.String(48), nullable=False), sa.Column("rule_id", sa.String(160), nullable=False), sa.Column("rule_version", sa.String(64), nullable=False), sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("relationship", sa.String(24), nullable=False), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("reviewed_by", sa.String(160)), sa.Column("review_note", sa.Text()),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("domain", "rule_kind", "rule_id", "rule_version", "claim_id", "relationship", name="uq_rule_evidence_link_identity"),
        _check("ck_rule_evidence_links_domain", "domain", ("skin_care", "hair_care", "home_care", "nutrition", "supplements", "product_quality")), _check("ck_rule_evidence_links_kind", "rule_kind", ("ingredient_compatibility", "ingredient_contraindication", "ingredient_sensitivity", "routine_guidance", "nutrition_context", "supplement_context")), _check("ck_rule_evidence_links_relationship", "relationship", ("supports", "qualifies", "limits", "background")),
        sa.ForeignKeyConstraint(["claim_id"], ["evidence_claims.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_rule_evidence_links_rule", "rule_evidence_links", ["domain", "rule_kind", "rule_id", "rule_version"])


def downgrade() -> None:
    op.drop_index("ix_rule_evidence_links_rule", table_name="rule_evidence_links")
    op.drop_table("rule_evidence_links")
    op.drop_index("uq_evidence_claim_source_identity_normalized", table_name="evidence_claim_sources")
    op.drop_index("ix_evidence_claim_sources_source", table_name="evidence_claim_sources")
    op.drop_index("ix_evidence_claim_sources_claim", table_name="evidence_claim_sources")
    op.drop_table("evidence_claim_sources")
    op.drop_index("ix_evidence_claims_subject", table_name="evidence_claims")
    op.drop_table("evidence_claims")
    op.drop_index("ix_evidence_sources_canonical_url", table_name="evidence_sources")
    op.drop_index("ix_evidence_sources_series", table_name="evidence_sources")
    op.execute("DROP INDEX IF EXISTS ix_evidence_sources_type_jurisdiction_status")
    op.execute("DROP INDEX IF EXISTS ix_evidence_sources_review_due_status")
    op.drop_table("evidence_sources")
