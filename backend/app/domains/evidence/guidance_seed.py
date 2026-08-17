"""Reviewed V3-03.17 evidence catalogue for deterministic Care guidance.

The review marker identifies repository governance approval metadata. It is not
a credential and does not claim clinician review; merging remains the human
approval boundary. Every source, claim, and link is explicit and idempotent.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.guidance_rules import GUIDANCE_RULES
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import assert_rule_exists

GUIDANCE_EVIDENCE_SEED_VERSION = "2026.08.17-v3-03.17-guidance-1"
GUIDANCE_EVIDENCE_SEED_NOTE = "V3-03.17 reviewed guidance evidence catalogue"
GUIDANCE_EVIDENCE_CATALOGUE_ROWS = 12
GUIDANCE_ACCESSED_AT = datetime(2026, 8, 17, tzinfo=UTC)
GOVERNANCE_REVIEW_MARKER = "repository_review:v3-03.17"
REVIEW_NOTE = "Reviewed for V3-03.17 deterministic advice-only guidance; merge is the human approval boundary."

SOURCE_DEFS: tuple[dict[str, Any], ...] = (
    {"source_key": "who.ultraviolet_radiation.fact_sheet.2022", "source_series_key": "who.ultraviolet_radiation", "source_type": "official_guideline", "title": "Ultraviolet radiation", "publisher": "World Health Organization", "jurisdiction": None, "publication_date": date(2022, 6, 21), "version_or_revision": None, "canonical_url": "https://www.who.int/news-room/fact-sheets/detail/ultraviolet-radiation", "accessed_at": GUIDANCE_ACCESSED_AT, "status": "active", "license_or_use_note": "Official WHO source; consult source terms."},
    {"source_key": "aad.dry_skin.relief_tips", "source_series_key": "aad.dry_skin", "source_type": "professional_consensus", "title": "Dermatologists' top tips for relieving dry skin", "publisher": "American Academy of Dermatology Association", "jurisdiction": None, "publication_date": None, "version_or_revision": None, "canonical_url": "https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin", "accessed_at": GUIDANCE_ACCESSED_AT, "status": "active", "license_or_use_note": "Professional association source; consult source terms."},
    {"source_key": "aad.healthy_hair.tips.2024", "source_series_key": "aad.healthy_hair", "source_type": "professional_consensus", "title": "Tips for healthy hair", "publisher": "American Academy of Dermatology Association", "jurisdiction": None, "publication_date": date(2024, 8, 12), "version_or_revision": None, "canonical_url": "https://www.aad.org/public/everyday-care/hair-scalp-care/hair/healthy-hair-tips", "accessed_at": GUIDANCE_ACCESSED_AT, "status": "active", "license_or_use_note": "Professional association source; consult source terms."},
)

def _app(population: str, formulation: str, usage: str) -> dict[str, Any]:
    return {"behavior_applicability": {"schema_version": "v3-03.15", "jurisdictions": ["global"], "populations": [population], "formulations": [formulation], "usage_contexts": [usage]}}

CLAIM_DEFS: tuple[dict[str, Any], ...] = (
    {"claim_key": "skin.uv_index_3_sun_protection", "claim_version": 1, "domain": "skin_care", "subject_type": "routine_guidance", "subject_key": "uv_protection", "claim_type": "usage_context", "summary": "Sun-protection measures are recommended when the UV Index reaches 3 or above.", "scope": "General sun-protection guidance only. It does not diagnose UV injury, score individual risk, select a sunscreen product, or replace individual medical advice.", "evidence_strength": "moderate", "strength_rationale": "Direct WHO public-health guidance for the exact UV threshold, consumed only as narrow advice-only behavior.", "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown", "structured_value": _app("general_population", "sun_protection", "outdoor_uv_exposure"), "ai_generated": True, "reviewed_at": GUIDANCE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER},
    {"claim_key": "skin.dry_air_moisture_support", "claim_version": 1, "domain": "skin_care", "subject_type": "routine_guidance", "subject_key": "dry_air_moisture_support", "claim_type": "usage_context", "summary": "Low humidity can contribute to dry skin, and applying moisturizer while skin is still damp can help support dry-skin self-care.", "scope": "General non-diagnostic self-care guidance for explicit dry/tight skin context. It does not diagnose a skin condition or choose a moisturizer, ingredient, strength, or formulation.", "evidence_strength": "moderate", "strength_rationale": "Direct AAD dermatologist-reviewed self-care guidance, consumed only with explicit user dry/tight skin context, observed dry air, and an already-selected owned moisturiser.", "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown", "structured_value": _app("general_population", "moisturiser", "after_cleansing"), "ai_generated": True, "reviewed_at": GUIDANCE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER},
    {"claim_key": "hair.frequent_heat_styling_protection", "claim_version": 1, "domain": "hair_care", "subject_type": "routine_guidance", "subject_key": "heat_styling", "claim_type": "usage_context", "summary": "Excessive heat can damage hair; limiting heat, using low or medium settings, and using heat protection are recommended hair-care practices.", "scope": "General non-diagnostic hair-care guidance. It does not diagnose damage, select a heat-protection product, change Hair wash cadence, or prescribe treatment.", "evidence_strength": "moderate", "strength_rationale": "Direct dermatologist-reviewed AAD general hair-care guidance, limited to advice-only behavior for a user who explicitly recorded frequent or daily heat styling.", "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown", "structured_value": _app("general_population", "heat_protection", "heat_styling"), "ai_generated": True, "reviewed_at": GUIDANCE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER},
)

LINK_DEFS = (
    (CLAIM_DEFS[0]["claim_key"], SOURCE_DEFS[0]["source_key"], "WHO ultraviolet radiation Q&A"),
    (CLAIM_DEFS[1]["claim_key"], SOURCE_DEFS[1]["source_key"], "AAD dermatologists' top tips"),
    (CLAIM_DEFS[2]["claim_key"], SOURCE_DEFS[2]["source_key"], "AAD tips for healthy hair"),
)

def _mismatch(existing: Any, expected: dict[str, Any]) -> list[str]:
    return [key for key, value in expected.items() if getattr(existing, key) != value]

async def _get_or_add(session: AsyncSession, model: Any, where: Any, values: dict[str, Any]) -> Any:
    row = (await session.execute(select(model).where(*where))).scalar_one_or_none()
    if row is not None:
        mismatch = _mismatch(row, values)
        if mismatch:
            raise ValueError(f"guidance evidence drift for {values}: {', '.join(mismatch)}")
        return row
    row = model(**values)
    session.add(row)
    await session.flush()
    return row

async def run(session: AsyncSession) -> dict[str, int | str]:
    sources = {d["source_key"]: await _get_or_add(session, EvidenceSource, (EvidenceSource.source_key == d["source_key"],), d) for d in SOURCE_DEFS}
    claims = {d["claim_key"]: await _get_or_add(session, EvidenceClaim, (EvidenceClaim.claim_key == d["claim_key"], EvidenceClaim.claim_version == d["claim_version"]), d) for d in CLAIM_DEFS}
    for claim_key, source_key, locator in LINK_DEFS:
        values = {"claim_id": claims[claim_key].id, "source_id": sources[source_key].id, "relationship": "supports", "locator": locator, "review_note": REVIEW_NOTE, "reviewed_at": GUIDANCE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER}
        await _get_or_add(session, EvidenceClaimSource, (EvidenceClaimSource.claim_id == values["claim_id"], EvidenceClaimSource.source_id == values["source_id"], EvidenceClaimSource.relationship == values["relationship"], EvidenceClaimSource.locator == values["locator"]), values)
    for rule, claim in zip(GUIDANCE_RULES, CLAIM_DEFS, strict=True):
        await assert_rule_exists(session, domain=rule.domain, rule_kind=rule.rule_kind, rule_id=rule.rule_id, rule_version=rule.rule_version)
        values = {"domain": rule.domain, "rule_kind": rule.rule_kind, "rule_id": rule.rule_id, "rule_version": rule.rule_version, "claim_id": claims[claim["claim_key"]].id, "relationship": "supports", "reviewed_at": GUIDANCE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER, "review_note": REVIEW_NOTE}
        await _get_or_add(session, RuleEvidenceLink, tuple(getattr(RuleEvidenceLink, key) == values[key] for key in ("domain", "rule_kind", "rule_id", "rule_version", "claim_id", "relationship")), values)
    from app.domains.reference import SeedVersionRecord
    audit = await _get_or_add(session, SeedVersionRecord, (SeedVersionRecord.seed_domain == "evidence_guidance", SeedVersionRecord.seed_version == GUIDANCE_EVIDENCE_SEED_VERSION), {"seed_domain": "evidence_guidance", "seed_version": GUIDANCE_EVIDENCE_SEED_VERSION, "applied_at": GUIDANCE_ACCESSED_AT, "rows_written": GUIDANCE_EVIDENCE_CATALOGUE_ROWS, "note": GUIDANCE_EVIDENCE_SEED_NOTE})
    if audit.rows_written != GUIDANCE_EVIDENCE_CATALOGUE_ROWS or audit.note != GUIDANCE_EVIDENCE_SEED_NOTE:
        raise ValueError("guidance evidence seed audit drift")
    return {"seed_version": GUIDANCE_EVIDENCE_SEED_VERSION, "sources": 3, "claims": 3, "claim_source_links": 3, "rule_links": 3, "rows_written": GUIDANCE_EVIDENCE_CATALOGUE_ROWS}
