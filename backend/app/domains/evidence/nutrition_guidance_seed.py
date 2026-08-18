"""Idempotent V3-04.1 claims and rule links over the existing ICMR-NIN source."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import assert_rule_exists
from app.domains.nutrition.guidance_rules import NUTRITION_GUIDANCE_RULES
from app.domains.reference import SeedVersionRecord

NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION = "2026.08.18-v3-04.1-guidance-1"
SEED_DOMAIN = "evidence_nutrition_guidance"
SEED_NOTE = "V3-04.1 evidence-backed Indian food guidance"
ACCESSED_AT = datetime(2026, 8, 18, tzinfo=UTC)
SOURCE_KEY = "icmr_nin.dietary_guidelines_for_indians.2024"
GOVERNANCE_MARKER = "repository_review:v3-04.1"
REVIEW_NOTE = "Repository/product governance review for V3-04.1; not doctor review, dietitian review, or clinician review."


def _app(formulation: str, usage: str) -> dict[str, Any]:
    return {"behavior_applicability": {"schema_version": "v3-03.15", "jurisdictions": ["india"], "populations": ["general_population"], "formulations": [formulation], "usage_contexts": [usage]}}


CLAIM_DEFS = (
    {"claim_key": "nutrition.food_pattern_balanced_variety", "claim_version": 1, "domain": "nutrition", "subject_type": "nutrition_guidance", "subject_key": "balanced_variety", "claim_type": "nutrition_reference", "summary": "A balanced dietary pattern is supported by eating a variety of foods rather than relying on a single food or nutrient.", "scope": "General food-pattern guidance only. It does not calculate nutrient intake, diagnose deficiency, prescribe a diet, or generate a meal plan.", "evidence_strength": "moderate", "strength_rationale": "Reviewed against the existing ICMR-NIN Dietary Guidelines for Indians 2024 source for narrow food-pattern guidance.", "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown", "ai_generated": False, "structured_value": _app("food_pattern_guidance", "nutrition_enabled"), "reviewed_at": ACCESSED_AT, "reviewed_by": GOVERNANCE_MARKER},
    {"claim_key": "nutrition.protein_food_first", "claim_version": 1, "domain": "nutrition", "subject_type": "nutrition_guidance", "subject_key": "protein_food_first", "claim_type": "nutrition_reference", "summary": "For general protein guidance, ordinary foods should remain the first-line source rather than treating protein supplements as the default.", "scope": "General food-first guidance for an explicit user interest in protein. It does not calculate protein requirements, assess protein adequacy, recommend a supplement, or diagnose deficiency.", "evidence_strength": "moderate", "strength_rationale": "Reviewed against the existing ICMR-NIN Dietary Guidelines for Indians 2024 source for narrow food-first context.", "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown", "ai_generated": False, "structured_value": _app("food_first_protein", "explicit_protein_focus"), "reviewed_at": ACCESSED_AT, "reviewed_by": GOVERNANCE_MARKER},
    {"claim_key": "nutrition.general_hydration_water", "claim_version": 1, "domain": "nutrition", "subject_type": "nutrition_guidance", "subject_key": "hydration_context", "claim_type": "nutrition_reference", "summary": "Water is part of general dietary guidance, while an individual fluid target depends on context beyond this application's non-clinical scope.", "scope": "General hydration context only. It does not prescribe a fluid volume, assess dehydration, diagnose a condition, or replace medical advice.", "evidence_strength": "moderate", "strength_rationale": "Reviewed against the existing ICMR-NIN Dietary Guidelines for Indians 2024 source for narrow hydration context.", "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown", "ai_generated": False, "structured_value": _app("hydration_guidance", "explicit_hydration_opt_in"), "reviewed_at": ACCESSED_AT, "reviewed_by": GOVERNANCE_MARKER},
)


async def _get_or_add(session: AsyncSession, model: Any, where: tuple[Any, ...], values: dict[str, Any]) -> Any:
    row = (await session.execute(select(model).where(*where))).scalar_one_or_none()
    if row is not None:
        mismatch = [key for key, value in values.items() if getattr(row, key) != value]
        if mismatch:
            raise ValueError(f"Nutrition guidance evidence drift: {', '.join(mismatch)}")
        return row
    row = model(**values)
    session.add(row)
    await session.flush()
    return row


async def run(session: AsyncSession) -> dict[str, int | str]:
    source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == SOURCE_KEY))).scalar_one_or_none()
    if source is None:
        raise ValueError(f"Nutrition guidance evidence source missing: {SOURCE_KEY}")
    claims = {}
    for values in CLAIM_DEFS:
        claims[values["claim_key"]] = await _get_or_add(session, EvidenceClaim, (EvidenceClaim.claim_key == values["claim_key"], EvidenceClaim.claim_version == 1), values)
    for values in CLAIM_DEFS:
        link_values = {"claim_id": claims[values["claim_key"]].id, "source_id": source.id, "relationship": "supports", "locator": f"Dietary Guidelines 2024: {values['subject_key']} guidance topic", "review_note": REVIEW_NOTE, "reviewed_at": ACCESSED_AT, "reviewed_by": GOVERNANCE_MARKER}
        await _get_or_add(session, EvidenceClaimSource, tuple(getattr(EvidenceClaimSource, key) == link_values[key] for key in ("claim_id", "source_id", "relationship", "locator")), link_values)
    for rule, claim in zip(NUTRITION_GUIDANCE_RULES, CLAIM_DEFS, strict=True):
        await assert_rule_exists(session, domain=rule.domain, rule_kind=rule.rule_kind, rule_id=rule.rule_id, rule_version=rule.rule_version)
        values = {"domain": rule.domain, "rule_kind": rule.rule_kind, "rule_id": rule.rule_id, "rule_version": rule.rule_version, "claim_id": claims[claim["claim_key"]].id, "relationship": "supports", "reviewed_at": ACCESSED_AT, "reviewed_by": GOVERNANCE_MARKER, "review_note": REVIEW_NOTE}
        await _get_or_add(session, RuleEvidenceLink, tuple(getattr(RuleEvidenceLink, key) == values[key] for key in ("domain", "rule_kind", "rule_id", "rule_version", "claim_id", "relationship")), values)
    audit_values = {"seed_domain": SEED_DOMAIN, "seed_version": NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION, "applied_at": ACCESSED_AT, "rows_written": 9, "note": SEED_NOTE}
    await _get_or_add(session, SeedVersionRecord, (SeedVersionRecord.seed_domain == SEED_DOMAIN, SeedVersionRecord.seed_version == NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION), audit_values)
    await session.flush()
    return {"seed_version": NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION, "sources": 0, "claims": 3, "claim_source_links": 3, "rule_links": 3, "rows_written": 9}


__all__ = ["NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION", "run"]
