"""Idempotent V3-03.18 Home Care claims reusing reviewed AAD sources."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.home_care_rules import HOME_CARE_RULES
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import assert_rule_exists

HOME_CARE_EVIDENCE_SEED_VERSION = "2026.08.17-v3-03.18-home-care-1"
HOME_CARE_EVIDENCE_SEED_NOTE = "V3-03.18 reviewed technique-only Home Care evidence catalogue"
HOME_CARE_EVIDENCE_CATALOGUE_ROWS = 6
HOME_CARE_ACCESSED_AT = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
GOVERNANCE_REVIEW_MARKER = "repository_review:v3-03.18"
REVIEW_NOTE = (
    "Repository governance metadata; not a professional credential and not a claim of clinician review. "
    "Exact source, claim, and rule wording is visible in the PR; merging is the repository approval boundary."
)


def _app(usage: str) -> dict[str, Any]:
    return {"behavior_applicability": {
        "schema_version": "v3-03.15", "jurisdictions": ["global"],
        "populations": ["general_population"], "formulations": ["non_product_home_care"],
        "usage_contexts": [usage],
    }}


CLAIM_DEFS: tuple[dict[str, Any], ...] = (
    {
        "claim_key": "home.skin.gentle_bathing_for_dry_skin", "claim_version": 1,
        "domain": "home_care", "subject_type": "routine_guidance", "subject_key": "gentle_bathing",
        "claim_type": "usage_context",
        "summary": "For dry skin, short baths or showers using warm water and gentle pat drying are recommended at-home care measures.",
        "scope": "General non-diagnostic at-home care for explicit dry/tight skin context. It does not diagnose a skin condition, prescribe treatment, select products, or recommend a DIY ingredient preparation.",
        "evidence_strength": "moderate",
        "strength_rationale": "Direct dermatologist-reviewed AAD self-care guidance, consumed only as a narrow technique-only Home Care action.",
        "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown",
        "ai_generated": True, "structured_value": _app("dry_skin_bathing"),
        "reviewed_at": HOME_CARE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER,
    },
    {
        "claim_key": "home.hair.gentle_drying_after_wash", "claim_version": 1,
        "domain": "home_care", "subject_type": "routine_guidance", "subject_key": "gentle_hair_drying",
        "claim_type": "usage_context",
        "summary": "After washing, gently absorbing hair moisture with a towel or T-shirt, or allowing hair to air-dry, is recommended instead of rough rubbing.",
        "scope": "General non-diagnostic at-home Hair Care technique. It does not diagnose hair damage, change Hair wash cadence, select products, or recommend a DIY ingredient preparation.",
        "evidence_strength": "moderate",
        "strength_rationale": "Direct dermatologist-reviewed AAD general Hair Care guidance, consumed only when the user's deterministic HairWashCadenceDecision says the wash is due.",
        "claim_status": "supported", "review_status": "approved", "regulatory_context": "unknown",
        "ai_generated": True, "structured_value": _app("post_wash_hair_drying"),
        "reviewed_at": HOME_CARE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER,
    },
)

SOURCE_KEYS = ("aad.dry_skin.relief_tips", "aad.healthy_hair.tips.2024")
LINK_DEFS = (
    (CLAIM_DEFS[0]["claim_key"], SOURCE_KEYS[0], "AAD dry-skin bathing and pat-drying guidance"),
    (CLAIM_DEFS[1]["claim_key"], SOURCE_KEYS[1], "AAD wet-hair towel/T-shirt and no-rubbing guidance"),
)


def _mismatch(existing: Any, expected: dict[str, Any]) -> list[str]:
    return [key for key, value in expected.items() if getattr(existing, key) != value]


async def _get_or_add(session: AsyncSession, model: Any, where: tuple[Any, ...], values: dict[str, Any]) -> Any:
    row = (await session.execute(select(model).where(*where))).scalar_one_or_none()
    if row is not None:
        mismatch = _mismatch(row, values)
        if mismatch:
            raise ValueError(f"Home Care evidence drift: {', '.join(mismatch)}")
        return row
    row = model(**values)
    session.add(row)
    await session.flush()
    return row


async def run(session: AsyncSession) -> dict[str, int | str]:
    sources: dict[str, EvidenceSource] = {}
    for source_key in SOURCE_KEYS:
        source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == source_key))).scalar_one_or_none()
        if source is None:
            raise ValueError(f"Home Care evidence source missing: {source_key}")
        sources[source_key] = source

    claims = {
        values["claim_key"]: await _get_or_add(
            session, EvidenceClaim,
            (EvidenceClaim.claim_key == values["claim_key"], EvidenceClaim.claim_version == values["claim_version"]),
            values,
        ) for values in CLAIM_DEFS
    }
    for claim_key, source_key, locator in LINK_DEFS:
        values = {
            "claim_id": claims[claim_key].id, "source_id": sources[source_key].id,
            "relationship": "supports", "locator": locator, "review_note": REVIEW_NOTE,
            "reviewed_at": HOME_CARE_ACCESSED_AT, "reviewed_by": GOVERNANCE_REVIEW_MARKER,
        }
        await _get_or_add(
            session, EvidenceClaimSource,
            (EvidenceClaimSource.claim_id == values["claim_id"], EvidenceClaimSource.source_id == values["source_id"], EvidenceClaimSource.relationship == values["relationship"], EvidenceClaimSource.locator == values["locator"]),
            values,
        )
    for rule, claim in zip(HOME_CARE_RULES, CLAIM_DEFS, strict=True):
        await assert_rule_exists(session, domain=rule.domain, rule_kind=rule.rule_kind, rule_id=rule.rule_id, rule_version=rule.rule_version)
        values = {
            "domain": rule.domain, "rule_kind": rule.rule_kind, "rule_id": rule.rule_id,
            "rule_version": rule.rule_version, "claim_id": claims[claim["claim_key"]].id,
            "relationship": "supports", "reviewed_at": HOME_CARE_ACCESSED_AT,
            "reviewed_by": GOVERNANCE_REVIEW_MARKER, "review_note": REVIEW_NOTE,
        }
        await _get_or_add(
            session, RuleEvidenceLink,
            tuple(getattr(RuleEvidenceLink, key) == values[key] for key in ("domain", "rule_kind", "rule_id", "rule_version", "claim_id", "relationship")),
            values,
        )
    from app.domains.reference import SeedVersionRecord
    audit = await _get_or_add(
        session, SeedVersionRecord,
        (SeedVersionRecord.seed_domain == "evidence_home_care", SeedVersionRecord.seed_version == HOME_CARE_EVIDENCE_SEED_VERSION),
        {"seed_domain": "evidence_home_care", "seed_version": HOME_CARE_EVIDENCE_SEED_VERSION, "applied_at": HOME_CARE_ACCESSED_AT, "rows_written": HOME_CARE_EVIDENCE_CATALOGUE_ROWS, "note": HOME_CARE_EVIDENCE_SEED_NOTE},
    )
    if audit.rows_written != HOME_CARE_EVIDENCE_CATALOGUE_ROWS or audit.note != HOME_CARE_EVIDENCE_SEED_NOTE:
        raise ValueError("Home Care evidence seed audit drift")
    await session.flush()
    return {"seed_version": HOME_CARE_EVIDENCE_SEED_VERSION, "sources": 0, "claims": 2, "claim_source_links": 2, "rule_links": 2, "rows_written": HOME_CARE_EVIDENCE_CATALOGUE_ROWS}


__all__ = ["HOME_CARE_EVIDENCE_SEED_VERSION", "HOME_CARE_EVIDENCE_CATALOGUE_ROWS", "run"]
