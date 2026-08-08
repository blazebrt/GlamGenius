"""Idempotent, deliberately small pilot catalogue for V3-02.1."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import assert_rule_exists
from app.domains.routines.ontology import ONTOLOGY_VERSION

EVIDENCE_SEED_VERSION = "2026.08.09-v3-02.1-pilot-1"
EVIDENCE_SEED_NOTE = "V3-02.1 draft pilot"
EVIDENCE_CATALOGUE_ROWS = 10
PILOT_ACCESSED_AT = datetime(2026, 8, 8, 20, 5, tzinfo=UTC)

EMA_RETINOID_SOURCE_REF = "ema-retinoid-pregnancy-2018"
FDA_M006_SOURCE_REF = "fda-m006-acne-2021"
FDA_TRETINOIN_LABEL_SOURCE_REF = "fda-tretinoin-nda75264-label"
FDA_TRETINOIN_SERIES_REF = "fda-tretinoin-label-series"

SOURCE_DEFS: tuple[dict[str, Any], ...] = (
    {"source_key": EMA_RETINOID_SOURCE_REF, "source_series_key": "ema-retinoid-pregnancy", "source_type": "government_reference", "title": "Updated measures for pregnancy prevention during retinoid use", "publisher": "European Medicines Agency", "jurisdiction": "EU", "publication_date": date(2018, 3, 23), "version_or_revision": "EMA/165360/2018", "canonical_url": "https://www.ema.europa.eu/en/news/updated-measures-pregnancy-prevention-during-retinoid-use", "accessed_at": PILOT_ACCESSED_AT, "status": "active", "license_or_use_note": "Official EMA metadata and locator; consult the source for reuse terms."},
    {"source_key": FDA_M006_SOURCE_REF, "source_series_key": "fda-otc-m006", "source_type": "official_regulation", "title": "OTC Monograph M006 — Topical Acne Drug Products for OTC Human Use", "publisher": "U.S. Food and Drug Administration", "jurisdiction": "US", "publication_date": date(2021, 11, 23), "version_or_revision": "OTC000013; M006", "canonical_url": "https://www.accessdata.fda.gov/drugsatfda_docs/omuf/monographs/OTC%20Monograph_M006-Topical%20Acne%20drug%20products%20for%20OTC%20Human%20Use%2011.23.2021.pdf", "accessed_at": PILOT_ACCESSED_AT, "status": "active", "license_or_use_note": "Official FDA document; consult FDA terms for reuse."},
    {"source_key": FDA_TRETINOIN_LABEL_SOURCE_REF, "source_series_key": FDA_TRETINOIN_SERIES_REF, "source_type": "manufacturer_label", "title": "Tretinoin Cream USP 0.025% — printed labeling", "publisher": "FDA Drugs@FDA archive", "jurisdiction": "US", "publication_date": None, "version_or_revision": "NDA 75-264; printed labeling; DEC 24 1998", "canonical_url": "https://www.accessdata.fda.gov/drugsatfda_docs/nda/98/75264_Tretinoin_prntlbl.pdf", "accessed_at": PILOT_ACCESSED_AT, "status": "active", "license_or_use_note": "Archived official labeling; consult FDA terms for reuse."},
)

CLAIM_DEFS: tuple[dict[str, Any], ...] = (
    {"claim_key": "skin.topical_retinoid_pregnancy_regulatory_context", "claim_version": 1, "domain": "skin_care", "subject_type": "ingredient_family", "subject_key": "retinoid", "claim_type": "contraindication_context", "summary": "EMA-reviewed topical medicinal retinoids must not be used during pregnancy or when planning pregnancy.", "scope": "Applies only to EMA-reviewed topical medicinal retinoids and the stated EU regulatory context; it is not a worldwide claim about every cosmetic vitamin-A product.", "evidence_strength": None, "strength_rationale": None, "claim_status": None, "review_status": "draft", "regulatory_context": "otc_or_regulated", "structured_value": {"jurisdiction": "EU", "product_scope": "topical medicinal retinoids"}, "ai_generated": True},
    {"claim_key": "skin.tretinoin_salicylic_concurrent_irritation_context", "claim_version": 1, "domain": "skin_care", "subject_type": "ingredient_family_pair", "subject_key": "retinoid+bha", "claim_type": "compatibility_context", "summary": "Direct tretinoin and salicylic-acid labeling, together with the FDA OTC acne monograph, supports a qualified concurrent-irritation context.", "scope": "Direct evidence is topical tretinoin plus salicylic acid and general simultaneous topical acne-medication irritation warnings. It does not prove every retinoid, a universal alternating schedule, or a medical protocol.", "evidence_strength": None, "strength_rationale": None, "claim_status": None, "review_status": "draft", "regulatory_context": "otc_or_regulated", "structured_value": {"direct_pair": ["tretinoin", "salicylic_acid"]}, "ai_generated": True},
)

LINK_DEFS = (
    {"claim_key": CLAIM_DEFS[0]["claim_key"], "source_key": EMA_RETINOID_SOURCE_REF, "relationship": "supports", "locator": "EMA/165360/2018; pregnancy prevention measures for topical retinoids"},
    {"claim_key": CLAIM_DEFS[1]["claim_key"], "source_key": FDA_M006_SOURCE_REF, "relationship": "supports", "locator": "M006.50(c)(1)(ii)"},
    {"claim_key": CLAIM_DEFS[1]["claim_key"], "source_key": FDA_TRETINOIN_LABEL_SOURCE_REF, "relationship": "supports", "locator": "Drug Interactions"},
)
RULE_LINK_DEFS = (
    {"claim_key": CLAIM_DEFS[0]["claim_key"], "domain": "skin_care", "rule_kind": "ingredient_contraindication", "rule_id": "retinoid__pregnancy", "relationship": "supports"},
    {"claim_key": CLAIM_DEFS[1]["claim_key"], "domain": "skin_care", "rule_kind": "ingredient_compatibility", "rule_id": "rule.retinoid_bha", "rule_version": ONTOLOGY_VERSION, "relationship": "qualifies"},
)


def _immutable_mismatch(existing: Any, expected: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if getattr(existing, field) != expected.get(field)]


async def _source(session: AsyncSession, values: dict[str, Any]) -> EvidenceSource:
    existing = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == values["source_key"]))).scalar_one_or_none()
    if existing:
        mismatch = _immutable_mismatch(existing, values, tuple(values))
        if mismatch:
            raise ValueError(f"evidence source drift for {values['source_key']}: {', '.join(mismatch)}")
        return existing
    row = EvidenceSource(**values)
    session.add(row)
    await session.flush()
    return row


async def _claim(session: AsyncSession, values: dict[str, Any]) -> EvidenceClaim:
    existing = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key == values["claim_key"], EvidenceClaim.claim_version == values["claim_version"]))).scalar_one_or_none()
    if existing:
        mismatch = _immutable_mismatch(existing, values, tuple(values))
        if mismatch:
            raise ValueError(f"evidence claim drift for {values['claim_key']} v{values['claim_version']}: {', '.join(mismatch)}")
        return existing
    row = EvidenceClaim(**values)
    session.add(row)
    await session.flush()
    return row


async def _resolve_rule_link(session: AsyncSession, values: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(values)
    if resolved["rule_kind"] == "ingredient_contraindication":
        from app.domains.reference import IngredientContraindicationRule

        row = (await session.execute(select(IngredientContraindicationRule).where(IngredientContraindicationRule.rule_id == resolved["rule_id"]))).scalar_one_or_none()
        if row is None:
            raise ValueError(f"unknown contraindication rule {resolved['rule_id']}")
        resolved["rule_version"] = row.version
    await assert_rule_exists(session, **{key: resolved[key] for key in ("domain", "rule_kind", "rule_id", "rule_version")})
    return resolved


async def run(session: AsyncSession) -> dict[str, int | str]:
    sources = {row["source_key"]: await _source(session, row) for row in SOURCE_DEFS}
    claims = {row["claim_key"]: await _claim(session, row) for row in CLAIM_DEFS}
    for values in LINK_DEFS:
        claim, source = claims[values["claim_key"]], sources[values["source_key"]]
        expected = {"claim_id": claim.id, "source_id": source.id, "relationship": values["relationship"], "locator": values["locator"]}
        existing = (await session.execute(select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim.id, EvidenceClaimSource.source_id == source.id, EvidenceClaimSource.relationship == values["relationship"]))).scalars().all()
        if not existing:
            session.add(EvidenceClaimSource(**expected))
        elif any(_immutable_mismatch(row, expected, tuple(expected)) for row in existing):
            raise ValueError(f"evidence claim-source drift for {values['claim_key']} / {values['source_key']}")
    await session.flush()
    for raw in RULE_LINK_DEFS:
        values = await _resolve_rule_link(session, raw)
        claim = claims[values["claim_key"]]
        expected = {"claim_id": claim.id, **{key: values[key] for key in ("domain", "rule_kind", "rule_id", "rule_version", "relationship")}}
        existing = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.domain == values["domain"], RuleEvidenceLink.rule_kind == values["rule_kind"], RuleEvidenceLink.rule_id == values["rule_id"], RuleEvidenceLink.rule_version == values["rule_version"], RuleEvidenceLink.claim_id == claim.id))).scalars().all()
        if not existing:
            session.add(RuleEvidenceLink(**expected))
        elif any(_immutable_mismatch(row, expected, tuple(expected)) for row in existing):
            raise ValueError(f"rule evidence link drift for {values['rule_id']}")
    await session.flush()
    from app.domains.reference import SeedVersionRecord

    audit = (await session.execute(select(SeedVersionRecord).where(SeedVersionRecord.seed_domain == "evidence", SeedVersionRecord.seed_version == EVIDENCE_SEED_VERSION))).scalar_one_or_none()
    if audit is None:
        session.add(SeedVersionRecord(seed_domain="evidence", seed_version=EVIDENCE_SEED_VERSION, applied_at=datetime.now(UTC), rows_written=EVIDENCE_CATALOGUE_ROWS, note=EVIDENCE_SEED_NOTE))
    elif audit.rows_written != EVIDENCE_CATALOGUE_ROWS or audit.note != EVIDENCE_SEED_NOTE:
        raise ValueError("evidence seed audit drift")
    await session.flush()
    return {"seed_version": EVIDENCE_SEED_VERSION, "sources": len(SOURCE_DEFS), "claims": len(CLAIM_DEFS), "claim_source_links": len(LINK_DEFS), "rule_links": len(RULE_LINK_DEFS), "rows_written": EVIDENCE_CATALOGUE_ROWS}
