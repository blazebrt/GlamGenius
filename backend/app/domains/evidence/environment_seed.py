"""Reviewed evidence catalogue for the ten environment rules.

What a source here does and does not do
---------------------------------------
Every rule cites something published. But the sources establish the
**environmental fact** — that India publishes a six-category index with these
breakpoints, that reducing exposure when ambient particulate levels are high is
advised, that sun protection is advised from UV Index 3 — and not the routine
mechanics we build on top of them.

"Defer exfoliation on a Poor-air day, resume after two clean days" is
GlamGenius product policy. No source we can open says that, so no claim here
pretends one does; each claim's ``scope`` says exactly where the published fact
stops and our policy begins. That is the same discipline the supplement
knowledge base runs under: state what the source says, and where there is no
source, say so rather than estimate.

The review marker identifies repository governance approval metadata. It is not
a credential and does not claim clinician review; merging remains the human
approval boundary.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.environment_rules import ENVIRONMENT_RULES
from app.domains.evidence.models import (
    EvidenceClaim,
    EvidenceClaimSource,
    EvidenceSource,
    RuleEvidenceLink,
)
from app.domains.evidence.service import assert_rule_exists

ENVIRONMENT_EVIDENCE_SEED_VERSION = "2026.08.30-v3-06-environment-1"
ENVIRONMENT_EVIDENCE_SEED_NOTE = "V3-06 reviewed environment-response evidence catalogue"
ENVIRONMENT_ACCESSED_AT = datetime(2026, 8, 30, tzinfo=UTC)
GOVERNANCE_REVIEW_MARKER = "repository_review:v3-06"
REVIEW_NOTE = (
    "Reviewed for V3-06 deterministic environment responses; merge is the human "
    "approval boundary. Sources establish the environmental fact only; the routine "
    "response is GlamGenius product policy."
)

SOURCE_DEFS: tuple[dict[str, Any], ...] = (
    {
        "source_key": "cpcb.national_air_quality_index.2014",
        "source_series_key": "cpcb.national_air_quality_index",
        "source_type": "government_reference",
        "title": "National Air Quality Index",
        "publisher": "Central Pollution Control Board, Ministry of Environment, Forest and Climate Change, Government of India",
        "jurisdiction": "IN",
        "publication_date": date(2014, 10, 17),
        "version_or_revision": "Report of the Expert Group, 2014",
        "canonical_url": "https://cpcb.nic.in/National-Air-Quality-Index/",
        "accessed_at": ENVIRONMENT_ACCESSED_AT,
        "status": "active",
        "license_or_use_note": "Government of India publication; consult source terms.",
    },
    {
        "source_key": "who.ambient_air_pollution.fact_sheet",
        "source_series_key": "who.ambient_air_pollution",
        "source_type": "official_guideline",
        "title": "Ambient (outdoor) air pollution",
        "publisher": "World Health Organization",
        "jurisdiction": None,
        "publication_date": None,
        "version_or_revision": None,
        "canonical_url": "https://www.who.int/news-room/fact-sheets/detail/ambient-(outdoor)-air-quality-and-health",
        "accessed_at": ENVIRONMENT_ACCESSED_AT,
        "status": "active",
        "license_or_use_note": "Official WHO source; consult source terms.",
    },
    {
        "source_key": "who.air_quality_guidelines.2021",
        "source_series_key": "who.air_quality_guidelines",
        "source_type": "official_guideline",
        "title": "WHO global air quality guidelines: particulate matter (PM2.5 and PM10), ozone, nitrogen dioxide, sulfur dioxide and carbon monoxide",
        "publisher": "World Health Organization",
        "jurisdiction": None,
        "publication_date": date(2021, 9, 22),
        "version_or_revision": "2021",
        "canonical_url": "https://www.who.int/publications/i/item/9789240034228",
        "accessed_at": ENVIRONMENT_ACCESSED_AT,
        "status": "active",
        "license_or_use_note": "Official WHO source; consult source terms.",
    },
    {
        "source_key": "who.ultraviolet_radiation.fact_sheet.2022",
        "source_series_key": "who.ultraviolet_radiation",
        "source_type": "official_guideline",
        "title": "Ultraviolet radiation",
        "publisher": "World Health Organization",
        "jurisdiction": None,
        "publication_date": date(2022, 6, 21),
        "version_or_revision": None,
        "canonical_url": "https://www.who.int/news-room/fact-sheets/detail/ultraviolet-radiation",
        "accessed_at": datetime(2026, 8, 17, tzinfo=UTC),
        "status": "active",
        "license_or_use_note": "Official WHO source; consult source terms.",
    },
    {
        "source_key": "imd.heat_and_humidity_advisory",
        "source_series_key": "imd.public_advisories",
        "source_type": "government_reference",
        "title": "India Meteorological Department public weather services",
        "publisher": "India Meteorological Department, Ministry of Earth Sciences, Government of India",
        "jurisdiction": "IN",
        "publication_date": None,
        "version_or_revision": None,
        "canonical_url": "https://mausam.imd.gov.in/",
        "accessed_at": ENVIRONMENT_ACCESSED_AT,
        "status": "active",
        "license_or_use_note": "Government of India publication; consult source terms.",
    },
)


def _app(population: str, formulation: str, usage: str) -> dict[str, Any]:
    return {
        "behavior_applicability": {
            "schema_version": "v3-03.15",
            "jurisdictions": ["global"],
            "populations": [population],
            "formulations": [formulation],
            "usage_contexts": [usage],
        }
    }


#: The scope sentence every air-quality claim carries. Written once so the
#: boundary cannot drift between ten copies of it.
_AIR_SCOPE = (
    "Establishes the published Indian index, its categories and the general advice to "
    "reduce exposure when ambient particulate levels are high. It does not establish "
    "any effect of air quality on skin or hair, does not diagnose anything, does not "
    "claim any product prevents pollution damage, and does not establish which routine "
    "step to change — that response is GlamGenius product policy."
)


def _claim(
    *,
    key: str,
    domain: str,
    subject_key: str,
    summary: str,
    scope: str,
    strength: str,
    rationale: str,
    population: str,
    formulation: str,
    usage: str,
) -> dict[str, Any]:
    return {
        "claim_key": key,
        "claim_version": 1,
        "domain": domain,
        "subject_type": "environment_response",
        "subject_key": subject_key,
        "claim_type": "usage_context",
        "summary": summary,
        "scope": scope,
        "evidence_strength": strength,
        "strength_rationale": rationale,
        "claim_status": "supported",
        "review_status": "approved",
        "regulatory_context": "unknown",
        "structured_value": _app(population, formulation, usage),
        "ai_generated": True,
        "reviewed_at": ENVIRONMENT_ACCESSED_AT,
        "reviewed_by": GOVERNANCE_REVIEW_MARKER,
    }


_INDEX_SUMMARY = (
    "India's Central Pollution Control Board publishes a National Air Quality Index with "
    "six categories — Good, Satisfactory, Moderate, Poor, Very Poor and Severe — and "
    "defined pollutant breakpoints for each."
)
_EXPOSURE_SUMMARY = (
    "The World Health Organization advises reducing personal exposure to outdoor air "
    "pollution when ambient particulate levels are high."
)

CLAIM_DEFS: tuple[dict[str, Any], ...] = (
    _claim(
        key="environment.dry_air_and_poor_air_compound",
        domain="skin_care",
        subject_key="dry_air_and_poor_naqi",
        summary=(
            f"{_INDEX_SUMMARY} Low ambient humidity and elevated particulate levels are "
            "separately published environmental conditions and can occur on the same day."
        ),
        scope=_AIR_SCOPE + " It does not establish that the two conditions interact.",
        strength="limited",
        rationale=(
            "Both conditions are directly published readings. Treating their co-occurrence "
            "as one combined response is our own precedence policy, not a sourced finding."
        ),
        population="general_population",
        formulation="barrier_support",
        usage="low_humidity_high_pollution",
    ),
    _claim(
        key="environment.very_poor_air_exposure_reduction",
        domain="skin_care",
        subject_key="very_poor_post_exposure_cleanse",
        summary=f"{_INDEX_SUMMARY} {_EXPOSURE_SUMMARY}",
        scope=_AIR_SCOPE,
        strength="moderate",
        rationale=(
            "Direct WHO exposure-reduction guidance plus the CPCB category definition, "
            "consumed only as an advice-only cleansing step after being outdoors."
        ),
        population="general_population",
        formulation="cleanser",
        usage="post_outdoor_exposure",
    ),
    _claim(
        key="environment.poor_air_category_definition",
        domain="skin_care",
        subject_key="poor_defer_strong_actives",
        summary=f"{_INDEX_SUMMARY} The Poor category begins at index 201.",
        scope=_AIR_SCOPE + " It does not establish that any ingredient should be deferred.",
        strength="limited",
        rationale=(
            "The category threshold is directly published. Deferring exfoliation and "
            "retinoids at that threshold is a conservative product policy we chose, and "
            "the claim is deliberately not stated as support for it."
        ),
        population="general_population",
        formulation="exfoliant_or_retinoid",
        usage="high_pollution_day",
    ),
    _claim(
        key="environment.uv_index_3_sun_protection",
        domain="skin_care",
        subject_key="high_uv_photosensitivity",
        summary="Sun-protection measures are recommended when the UV Index reaches 3 or above.",
        scope=(
            "General sun-protection guidance only. It does not diagnose UV injury, score "
            "individual risk, select a sunscreen product, or replace individual medical "
            "advice. It does not establish which ingredients increase photosensitivity."
        ),
        strength="moderate",
        rationale="Direct WHO public-health guidance for the exact UV threshold used.",
        population="general_population",
        formulation="sun_protection",
        usage="outdoor_uv_exposure",
    ),
    _claim(
        key="environment.very_poor_air_hair_cadence",
        domain="hair_care",
        subject_key="very_poor_hair_hold_cadence",
        summary=f"{_INDEX_SUMMARY} {_EXPOSURE_SUMMARY}",
        scope=(
            _AIR_SCOPE.replace("skin or hair", "hair")
            + " It does not establish any wash frequency."
        ),
        strength="limited",
        rationale=(
            "The category and exposure guidance are published. Holding wash cadence steady "
            "is our own conservative policy: it changes nothing rather than adding washing "
            "on no evidence."
        ),
        population="general_population",
        formulation="shampoo",
        usage="high_pollution_day",
    ),
    _claim(
        key="environment.humid_heat_conditions",
        domain="skin_care",
        subject_key="humid_heat_occlusion",
        summary=(
            "The India Meteorological Department publishes daily temperature and relative "
            "humidity, and high temperature combined with high humidity is a recognised "
            "public-advisory condition."
        ),
        scope=(
            "Establishes the published readings only. It does not establish any effect on "
            "skin, does not diagnose anything, and does not establish which formulation to "
            "use — that response is GlamGenius product policy."
        ),
        strength="limited",
        rationale=(
            "The readings are directly published. Preferring lighter formulations in this "
            "combination is our own conservative comfort policy, not a sourced finding."
        ),
        population="general_population",
        formulation="light_formulation",
        usage="hot_humid_day",
    ),
    _claim(
        key="environment.sustained_particulate_exposure",
        domain="skin_care",
        subject_key="sustained_poor_antioxidant_am",
        summary=(
            f"{_INDEX_SUMMARY} {_EXPOSURE_SUMMARY} WHO guideline levels are expressed as "
            "24-hour and annual averages, so sustained exposure is measured over days, not "
            "one reading."
        ),
        scope=(
            _AIR_SCOPE
            + " It does not establish that any topical ingredient counteracts particulate "
            "exposure, and no such claim is made to the user."
        ),
        strength="limited",
        rationale=(
            "The sustained-exposure framing is directly published. Suggesting an antioxidant "
            "serum a person already owns is a product-policy suggestion, offered without any "
            "protective claim attached to it."
        ),
        population="general_population",
        formulation="antioxidant_serum",
        usage="sustained_high_pollution",
    ),
    _claim(
        key="environment.recovery_needs_sustained_clean_air",
        domain="skin_care",
        subject_key="resume_needs_two_clean_days",
        summary=(
            f"{_INDEX_SUMMARY} The Satisfactory category covers index 51 to 100, and daily "
            "index values are published per day rather than as a running state."
        ),
        scope=_AIR_SCOPE + " It does not establish any waiting period before resuming anything.",
        strength="limited",
        rationale=(
            "The per-day category is published. Requiring two consecutive clean days before "
            "resuming is our own conservative policy, chosen so one good reading does not "
            "undo a week's caution."
        ),
        population="general_population",
        formulation="exfoliant_or_retinoid",
        usage="air_quality_recovery",
    ),
    _claim(
        key="environment.air_quality_improvement_is_published_daily",
        domain="skin_care",
        subject_key="air_cleared_resume_actives",
        summary=(
            f"{_INDEX_SUMMARY} A change from Poor or worse to Satisfactory or better is a "
            "published change of category."
        ),
        scope=_AIR_SCOPE,
        strength="moderate",
        rationale=(
            "Reporting a published category change back to the person is a direct, factual "
            "use of the source. Nothing is claimed beyond the reading."
        ),
        population="general_population",
        formulation="exfoliant_or_retinoid",
        usage="air_quality_recovery",
    ),
    _claim(
        key="environment.rainfall_and_particulate_readings",
        domain="skin_care",
        subject_key="rain_recovery_window",
        summary=(
            "The India Meteorological Department publishes daily rainfall, and the Central "
            "Pollution Control Board publishes daily air quality; both are read for the same "
            "day and location."
        ),
        scope=(
            "Establishes the published readings only. It does not establish that rainfall "
            "causes an air-quality improvement; the improvement, if any, is read from the "
            "published index rather than assumed from the rain."
        ),
        strength="limited",
        rationale=(
            "Both readings are directly published. Treating rain after a poor stretch as a "
            "moment to re-offer what was deferred is our own policy, and the index is still "
            "what is reported to the person."
        ),
        population="general_population",
        formulation="exfoliant_or_retinoid",
        usage="post_rainfall_recovery",
    ),
)

#: (claim_key, source_key, locator) — several claims rest on more than one source.
LINK_DEFS: tuple[tuple[str, str, str], ...] = (
    ("environment.dry_air_and_poor_air_compound", "cpcb.national_air_quality_index.2014", "AQI category breakpoints"),
    ("environment.dry_air_and_poor_air_compound", "imd.heat_and_humidity_advisory", "Daily relative humidity"),
    ("environment.very_poor_air_exposure_reduction", "cpcb.national_air_quality_index.2014", "Very Poor category, index 301-400"),
    ("environment.very_poor_air_exposure_reduction", "who.ambient_air_pollution.fact_sheet", "Reducing exposure to outdoor air pollution"),
    ("environment.poor_air_category_definition", "cpcb.national_air_quality_index.2014", "Poor category, index 201-300"),
    ("environment.uv_index_3_sun_protection", "who.ultraviolet_radiation.fact_sheet.2022", "WHO ultraviolet radiation Q&A"),
    ("environment.very_poor_air_hair_cadence", "cpcb.national_air_quality_index.2014", "Very Poor category, index 301-400"),
    ("environment.very_poor_air_hair_cadence", "who.ambient_air_pollution.fact_sheet", "Reducing exposure to outdoor air pollution"),
    ("environment.humid_heat_conditions", "imd.heat_and_humidity_advisory", "Daily temperature and relative humidity"),
    ("environment.sustained_particulate_exposure", "who.air_quality_guidelines.2021", "PM2.5 and PM10 24-hour and annual guideline levels"),
    ("environment.sustained_particulate_exposure", "cpcb.national_air_quality_index.2014", "AQI category breakpoints"),
    ("environment.recovery_needs_sustained_clean_air", "cpcb.national_air_quality_index.2014", "Satisfactory category, index 51-100"),
    ("environment.air_quality_improvement_is_published_daily", "cpcb.national_air_quality_index.2014", "AQI category breakpoints"),
    ("environment.rainfall_and_particulate_readings", "imd.heat_and_humidity_advisory", "Daily rainfall"),
    ("environment.rainfall_and_particulate_readings", "cpcb.national_air_quality_index.2014", "AQI category breakpoints"),
)

ENVIRONMENT_EVIDENCE_CATALOGUE_ROWS = (
    len(SOURCE_DEFS) + len(CLAIM_DEFS) + len(LINK_DEFS) + len(ENVIRONMENT_RULES)
)


def _mismatch(existing: Any, expected: dict[str, Any]) -> list[str]:
    return [key for key, value in expected.items() if getattr(existing, key) != value]


async def _get_or_add(session: AsyncSession, model: Any, where: Any, values: dict[str, Any]) -> Any:
    row = (await session.execute(select(model).where(*where))).scalar_one_or_none()
    if row is not None:
        mismatch = _mismatch(row, values)
        if mismatch:
            raise ValueError(f"environment evidence drift for {values}: {', '.join(mismatch)}")
        return row
    row = model(**values)
    session.add(row)
    await session.flush()
    return row


async def run(session: AsyncSession) -> dict[str, int | str]:
    sources = {
        d["source_key"]: await _get_or_add(
            session, EvidenceSource, (EvidenceSource.source_key == d["source_key"],), d
        )
        for d in SOURCE_DEFS
    }
    claims = {
        d["claim_key"]: await _get_or_add(
            session,
            EvidenceClaim,
            (EvidenceClaim.claim_key == d["claim_key"], EvidenceClaim.claim_version == d["claim_version"]),
            d,
        )
        for d in CLAIM_DEFS
    }
    for claim_key, source_key, locator in LINK_DEFS:
        values = {
            "claim_id": claims[claim_key].id,
            "source_id": sources[source_key].id,
            "relationship": "supports",
            "locator": locator,
            "review_note": REVIEW_NOTE,
            "reviewed_at": ENVIRONMENT_ACCESSED_AT,
            "reviewed_by": GOVERNANCE_REVIEW_MARKER,
        }
        await _get_or_add(
            session,
            EvidenceClaimSource,
            (
                EvidenceClaimSource.claim_id == values["claim_id"],
                EvidenceClaimSource.source_id == values["source_id"],
                EvidenceClaimSource.relationship == values["relationship"],
                EvidenceClaimSource.locator == values["locator"],
            ),
            values,
        )
    for rule, claim in zip(
        sorted(ENVIRONMENT_RULES, key=lambda row: row.precedence), CLAIM_DEFS, strict=True
    ):
        await assert_rule_exists(
            session,
            domain=rule.domain,
            rule_kind=rule.rule_kind,
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
        )
        values = {
            "domain": rule.domain,
            "rule_kind": rule.rule_kind,
            "rule_id": rule.rule_id,
            "rule_version": rule.rule_version,
            "claim_id": claims[claim["claim_key"]].id,
            "relationship": "supports",
            "reviewed_at": ENVIRONMENT_ACCESSED_AT,
            "reviewed_by": GOVERNANCE_REVIEW_MARKER,
            "review_note": REVIEW_NOTE,
        }
        await _get_or_add(
            session,
            RuleEvidenceLink,
            tuple(
                getattr(RuleEvidenceLink, key) == values[key]
                for key in ("domain", "rule_kind", "rule_id", "rule_version", "claim_id", "relationship")
            ),
            values,
        )
    from app.domains.reference import SeedVersionRecord

    audit = await _get_or_add(
        session,
        SeedVersionRecord,
        (
            SeedVersionRecord.seed_domain == "evidence_environment",
            SeedVersionRecord.seed_version == ENVIRONMENT_EVIDENCE_SEED_VERSION,
        ),
        {
            "seed_domain": "evidence_environment",
            "seed_version": ENVIRONMENT_EVIDENCE_SEED_VERSION,
            "applied_at": ENVIRONMENT_ACCESSED_AT,
            "rows_written": ENVIRONMENT_EVIDENCE_CATALOGUE_ROWS,
            "note": ENVIRONMENT_EVIDENCE_SEED_NOTE,
        },
    )
    if audit.rows_written != ENVIRONMENT_EVIDENCE_CATALOGUE_ROWS or audit.note != ENVIRONMENT_EVIDENCE_SEED_NOTE:
        raise ValueError("environment evidence seed audit drift")
    return {
        "seed_version": ENVIRONMENT_EVIDENCE_SEED_VERSION,
        "sources": len(SOURCE_DEFS),
        "claims": len(CLAIM_DEFS),
        "claim_source_links": len(LINK_DEFS),
        "rule_links": len(ENVIRONMENT_RULES),
        "rows_written": ENVIRONMENT_EVIDENCE_CATALOGUE_ROWS,
    }
