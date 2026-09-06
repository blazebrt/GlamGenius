"""Governed personal evidence over exact Step 7C substance identities.

Step 8B joins two already-decided inputs: Step 8A trusted body context and
Step 7C exact formula interpretation. It does not reopen Profile, formula
parsing, identity resolution, or reference-role interpretation, and it never
produces a score, verdict, recommendation, or safety conclusion.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.enums import (
    ClaimType,
    EvidenceDomain,
    EvidenceStrength,
    EvidenceTier,
    SourceType,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.evidence.service import (
    claim_is_public_knowledge_path,
    source_path_is_public_knowledge,
)
from app.domains.personal_applicability.enums import (
    PersonalApplicabilityCategory,
    PersonalApplicabilityOperator,
    PersonalApplicabilityStatus,
)
from app.domains.personal_applicability.schema import (
    PersonalApplicabilityPayload,
    parse_personal_applicability_payload,
)
from app.domains.personal_lens import (
    PersonalLensCategory,
    PersonalLensContext,
    PersonalLensHandoff,
    PersonalLensSafetyInput,
    PersonalLensStatus,
    build_personal_lens_context,
)
from app.domains.product.formula_projection import FormulaProjectionProvenance
from app.domains.product.models import LabelSnapshot
from app.domains.substance_interpretation import (
    InterpretationCategory,
    LabelSnapshotFormulaInterpretation,
    ProjectedIdentityStatus,
    interpret_label_snapshot,
)

PERSONAL_APPLICABILITY_SOURCE_TYPES: frozenset[str] = frozenset({
    SourceType.OFFICIAL_GUIDELINE.value,
    SourceType.GOVERNMENT_REFERENCE.value,
    SourceType.SYSTEMATIC_REVIEW.value,
    SourceType.PEER_REVIEWED_RESEARCH.value,
    SourceType.PROFESSIONAL_CONSENSUS.value,
})

PERSONAL_APPLICABILITY_STRENGTHS: frozenset[str] = frozenset({
    EvidenceStrength.STRONG.value,
    EvidenceStrength.MODERATE.value,
    EvidenceStrength.LIMITED.value,
})

_EVIDENCE_DOMAIN_BY_CATEGORY: dict[PersonalApplicabilityCategory, EvidenceDomain] = {
    PersonalApplicabilityCategory.PACKAGED_FOOD: EvidenceDomain.NUTRITION,
    PersonalApplicabilityCategory.SKIN_CARE: EvidenceDomain.SKIN_CARE,
    PersonalApplicabilityCategory.HAIR_CARE: EvidenceDomain.HAIR_CARE,
    PersonalApplicabilityCategory.COSMETICS: EvidenceDomain.COSMETICS,
}


def evidence_domain_for_category(
    category: PersonalApplicabilityCategory,
) -> EvidenceDomain:
    """Return the one explicit Step 8B evidence domain for a category."""
    if not isinstance(category, PersonalApplicabilityCategory):
        raise ValueError("category must be a PersonalApplicabilityCategory")
    return _EVIDENCE_DOMAIN_BY_CATEGORY[category]


@dataclass(frozen=True, slots=True)
class PersonalApplicabilitySource:
    source_id: uuid.UUID
    source_key: str
    source_type: str
    title: str
    publisher: str
    canonical_url: str
    locator: str | None
    publication_date: date | None
    version_or_revision: str | None
    jurisdiction: str | None


@dataclass(frozen=True, slots=True)
class MatchedPersonalFact:
    fact_key: str
    profile_attribute_id: uuid.UUID
    value: object


@dataclass(frozen=True, slots=True)
class ApplicableSubstancePersonalClaim:
    claim_id: uuid.UUID
    claim_key: str
    claim_version: int
    summary: str
    scope: str
    evidence_strength: str
    evidence_tier: str
    matched_facts: tuple[MatchedPersonalFact, ...]
    sources: tuple[PersonalApplicabilitySource, ...]


@dataclass(frozen=True, slots=True)
class IngredientPersonalApplicability:
    position: int
    raw_name: str
    normalized_name: str | None
    identity_status: ProjectedIdentityStatus
    substance_key: str | None
    entity_kind: str | None
    candidate_substance_keys: tuple[str, ...]
    personal_applicability_status: PersonalApplicabilityStatus
    claims: tuple[ApplicableSubstancePersonalClaim, ...]


@dataclass(frozen=True, slots=True)
class LabelSnapshotPersonalApplicability:
    provenance: FormulaProjectionProvenance | None
    category: PersonalApplicabilityCategory
    formula_status: str | None
    profile_id: uuid.UUID | None
    profile_version: int | None
    context_status: PersonalLensStatus
    ingredients: tuple[IngredientPersonalApplicability, ...]
    handoff: PersonalLensHandoff | None


def _lens_category(category: PersonalApplicabilityCategory) -> PersonalLensCategory:
    return PersonalLensCategory(category.value)


def _interpretation_category(category: PersonalApplicabilityCategory) -> InterpretationCategory:
    return InterpretationCategory(category.value)


def _snapshot_provenance(snapshot: LabelSnapshot) -> FormulaProjectionProvenance:
    return FormulaProjectionProvenance(
        label_snapshot_id=snapshot.id,
        barcode=snapshot.barcode,
        version_number=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
        scan_event_id=snapshot.scan_event_id,
    )


def _source_view(
    link: EvidenceClaimSource,
    source: EvidenceSource,
) -> PersonalApplicabilitySource:
    assert source.canonical_url is not None
    return PersonalApplicabilitySource(
        source_id=source.id,
        source_key=source.source_key,
        source_type=source.source_type,
        title=source.title,
        publisher=source.publisher,
        canonical_url=source.canonical_url,
        locator=link.locator,
        publication_date=source.publication_date,
        version_or_revision=source.version_or_revision,
        jurisdiction=source.jurisdiction,
    )


def _matched_facts(
    payload: PersonalApplicabilityPayload,
    context: PersonalLensContext,
) -> tuple[MatchedPersonalFact, ...] | None:
    facts_by_key = {
        fact.key: fact
        for fact in context.body_facts
        if not fact.explicit_unknown
    }
    matched: list[MatchedPersonalFact] = []
    for condition in payload.all_of:
        fact = facts_by_key.get(condition.fact_key)
        if fact is None:
            return None
        if condition.operator is PersonalApplicabilityOperator.EQUALS_ANY:
            condition_matches = isinstance(fact.value, str) and fact.value in condition.values
        else:
            condition_matches = (
                isinstance(fact.value, tuple)
                and any(item in condition.values for item in fact.value)
            )
        if not condition_matches:
            return None
        matched.append(MatchedPersonalFact(
            fact_key=fact.key,
            profile_attribute_id=fact.profile_attribute_id,
            value=fact.value,
        ))
    return tuple(matched)


def _eligible_claim(
    claim: EvidenceClaim,
    paths: list[tuple[EvidenceClaimSource, EvidenceSource]],
    *,
    category: PersonalApplicabilityCategory,
    evidence_domain: EvidenceDomain,
    context: PersonalLensContext,
) -> ApplicableSubstancePersonalClaim | None:
    payload = parse_personal_applicability_payload(claim.structured_value)
    if payload is None or payload.category is not category:
        return None
    if claim.domain != evidence_domain.value:
        return None
    if not claim_is_public_knowledge_path(claim) or claim.ai_generated is not False:
        return None
    if claim.evidence_tier != EvidenceTier.CLINICALLY_STUDIED.value:
        return None
    if claim.evidence_strength not in PERSONAL_APPLICABILITY_STRENGTHS:
        return None

    matched = _matched_facts(payload, context)
    if matched is None:
        return None

    sources = [
        _source_view(link, source)
        for link, source in paths
        if source_path_is_public_knowledge(
            link,
            source,
            allowed_source_types=PERSONAL_APPLICABILITY_SOURCE_TYPES,
        )
    ]
    if not sources:
        return None
    sources.sort(key=lambda row: (row.source_key, row.locator or "", str(row.source_id)))
    return ApplicableSubstancePersonalClaim(
        claim_id=claim.id,
        claim_key=claim.claim_key,
        claim_version=claim.claim_version,
        summary=claim.summary,
        scope=claim.scope,
        evidence_strength=claim.evidence_strength,
        evidence_tier=claim.evidence_tier,
        matched_facts=matched,
        sources=tuple(sources),
    )


async def apply_personal_evidence(
    session: AsyncSession,
    formula_interpretation: LabelSnapshotFormulaInterpretation,
    personal_context: PersonalLensContext,
    *,
    category: PersonalApplicabilityCategory,
) -> tuple[IngredientPersonalApplicability, ...]:
    """Match one Step 7C formula and one Step 8A context without rebuilding either."""
    if not isinstance(category, PersonalApplicabilityCategory):
        raise ValueError("category must be a PersonalApplicabilityCategory")
    if formula_interpretation.category is not _interpretation_category(category):
        raise ValueError("formula interpretation category does not match Step 8B category")
    if personal_context.category is not _lens_category(category):
        raise ValueError("personal context category does not match Step 8B category")
    if personal_context.status is PersonalLensStatus.HANDOFF_REQUIRED:
        raise ValueError("handoff-required context cannot enter personal evidence matching")

    resolved_keys = sorted({
        ingredient.substance_key
        for ingredient in formula_interpretation.ingredients
        if (
            ingredient.identity_status is ProjectedIdentityStatus.RESOLVED
            and ingredient.substance_key is not None
        )
    })

    claims_by_key: dict[str, tuple[ApplicableSubstancePersonalClaim, ...]] = {}
    if resolved_keys:
        evidence_domain = evidence_domain_for_category(category)
        candidates = list((await session.execute(
            select(EvidenceClaim).where(
                EvidenceClaim.domain == evidence_domain.value,
                EvidenceClaim.subject_type == "substance",
                EvidenceClaim.subject_key.in_(resolved_keys),
                EvidenceClaim.claim_type == ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value,
            )
        )).scalars().all())

        paths_by_claim: dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]] = defaultdict(list)
        if candidates:
            rows = (await session.execute(
                select(EvidenceClaimSource, EvidenceSource)
                .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
                .where(EvidenceClaimSource.claim_id.in_([claim.id for claim in candidates]))
            )).all()
            for link, source in rows:
                paths_by_claim[link.claim_id].append((link, source))

        accepted: dict[str, list[ApplicableSubstancePersonalClaim]] = defaultdict(list)
        for claim in candidates:
            projected = _eligible_claim(
                claim,
                paths_by_claim[claim.id],
                category=category,
                evidence_domain=evidence_domain,
                context=personal_context,
            )
            if projected is not None:
                accepted[claim.subject_key].append(projected)
        for key, rows in accepted.items():
            rows.sort(key=lambda row: (row.claim_key, row.claim_version, str(row.claim_id)))
            claims_by_key[key] = tuple(rows)

    results: list[IngredientPersonalApplicability] = []
    for ingredient in formula_interpretation.ingredients:
        if ingredient.identity_status is ProjectedIdentityStatus.UNRESOLVED:
            status = PersonalApplicabilityStatus.IDENTITY_UNRESOLVED
            claims = ()
        elif ingredient.identity_status is ProjectedIdentityStatus.AMBIGUOUS:
            status = PersonalApplicabilityStatus.IDENTITY_AMBIGUOUS
            claims = ()
        else:
            claims = claims_by_key.get(ingredient.substance_key or "", ())
            status = (
                PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE
                if claims
                else PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION
            )
        results.append(IngredientPersonalApplicability(
            position=ingredient.position,
            raw_name=ingredient.raw_name,
            normalized_name=ingredient.normalized_name,
            identity_status=ingredient.identity_status,
            substance_key=ingredient.substance_key,
            entity_kind=ingredient.entity_kind,
            candidate_substance_keys=ingredient.candidate_substance_keys,
            personal_applicability_status=status,
            claims=claims,
        ))
    return tuple(results)


async def interpret_label_snapshot_for_account(
    session: AsyncSession,
    snapshot: LabelSnapshot,
    *,
    account_id: uuid.UUID,
    category: PersonalApplicabilityCategory,
    safety: PersonalLensSafetyInput | None = None,
) -> LabelSnapshotPersonalApplicability:
    """Orchestrate Step 8A first, then exact Step 7C, then Step 8B."""
    if not isinstance(category, PersonalApplicabilityCategory):
        raise ValueError("category must be a PersonalApplicabilityCategory")

    context = await build_personal_lens_context(
        session,
        account_id=account_id,
        category=_lens_category(category),
        safety=safety,
    )
    if context.status is PersonalLensStatus.HANDOFF_REQUIRED:
        return LabelSnapshotPersonalApplicability(
            provenance=None,
            category=category,
            formula_status=None,
            profile_id=None,
            profile_version=None,
            context_status=context.status,
            ingredients=(),
            handoff=context.handoff,
        )
    if context.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT:
        return LabelSnapshotPersonalApplicability(
            provenance=_snapshot_provenance(snapshot),
            category=category,
            formula_status=None,
            profile_id=context.profile_id,
            profile_version=context.profile_version,
            context_status=context.status,
            ingredients=(),
            handoff=None,
        )

    formula_interpretation = await interpret_label_snapshot(
        session,
        snapshot,
        category=_interpretation_category(category),
    )
    ingredients = await apply_personal_evidence(
        session,
        formula_interpretation,
        context,
        category=category,
    )
    return LabelSnapshotPersonalApplicability(
        provenance=formula_interpretation.provenance,
        category=category,
        formula_status=formula_interpretation.formula_status,
        profile_id=context.profile_id,
        profile_version=context.profile_version,
        context_status=context.status,
        ingredients=ingredients,
        handoff=None,
    )


__all__ = [
    "PERSONAL_APPLICABILITY_SOURCE_TYPES",
    "PERSONAL_APPLICABILITY_STRENGTHS",
    "ApplicableSubstancePersonalClaim",
    "IngredientPersonalApplicability",
    "LabelSnapshotPersonalApplicability",
    "MatchedPersonalFact",
    "PersonalApplicabilitySource",
    "apply_personal_evidence",
    "evidence_domain_for_category",
    "interpret_label_snapshot_for_account",
]
