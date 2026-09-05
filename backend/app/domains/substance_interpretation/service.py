"""Project eligible category evidence onto an already-resolved formula.

Identity and formula parsing are upstream answers. This read-only layer copies
those answers and attaches reviewed public reference-role claims by exact
canonical key. It never resolves a name, chooses a snapshot, scores a product,
or persists its result.
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
from app.domains.product.formula_projection import (
    FormulaProjectionProvenance,
    LabelSnapshotFormulaProjection,
    project_formula_from_label_snapshot,
)
from app.domains.product.models import LabelSnapshot
from app.domains.substance_interpretation.enums import (
    InterpretationCategory,
    InterpretationStatus,
    ProjectedIdentityStatus,
)
from app.domains.substance_interpretation.schema import parse_interpretation_payload

REFERENCE_ROLE_SOURCE_TYPES: frozenset[str] = frozenset({
    SourceType.OFFICIAL_REGULATION.value,
    SourceType.OFFICIAL_GUIDELINE.value,
    SourceType.GOVERNMENT_REFERENCE.value,
    SourceType.INGREDIENT_REFERENCE_DATABASE.value,
    SourceType.MANUFACTURER_TECHNICAL_DOCUMENT.value,
})

REFERENCE_ROLE_STRENGTHS: frozenset[str] = frozenset({
    EvidenceStrength.STRONG.value,
    EvidenceStrength.MODERATE.value,
    EvidenceStrength.LIMITED.value,
})

_EVIDENCE_DOMAIN_BY_CATEGORY: dict[InterpretationCategory, EvidenceDomain] = {
    InterpretationCategory.PACKAGED_FOOD: EvidenceDomain.NUTRITION,
    InterpretationCategory.SKIN_CARE: EvidenceDomain.SKIN_CARE,
    InterpretationCategory.HAIR_CARE: EvidenceDomain.HAIR_CARE,
    InterpretationCategory.COSMETICS: EvidenceDomain.COSMETICS,
}


@dataclass(frozen=True)
class InterpretationSource:
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


@dataclass(frozen=True)
class SubstanceCategoryInterpretationClaim:
    claim_id: uuid.UUID
    claim_key: str
    claim_version: int
    summary: str
    scope: str
    evidence_strength: str
    evidence_tier: str
    sources: tuple[InterpretationSource, ...]


@dataclass(frozen=True)
class FormulaIngredientInterpretation:
    position: int
    raw_name: str
    normalized_name: str | None
    identity_status: ProjectedIdentityStatus
    substance_key: str | None
    entity_kind: str | None
    candidate_substance_keys: tuple[str, ...]
    interpretation_status: InterpretationStatus
    claims: tuple[SubstanceCategoryInterpretationClaim, ...]


@dataclass(frozen=True)
class LabelSnapshotFormulaInterpretation:
    provenance: FormulaProjectionProvenance
    category: InterpretationCategory
    formula_status: str
    ingredients: tuple[FormulaIngredientInterpretation, ...]


def _source_view(link: EvidenceClaimSource, source: EvidenceSource) -> InterpretationSource:
    # The shared public-source predicate has already proved the URL openable.
    assert source.canonical_url is not None
    return InterpretationSource(
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


def _eligible_claim(
    claim: EvidenceClaim,
    paths: list[tuple[EvidenceClaimSource, EvidenceSource]],
    *,
    category: InterpretationCategory,
    evidence_domain: EvidenceDomain,
) -> SubstanceCategoryInterpretationClaim | None:
    payload = parse_interpretation_payload(claim.structured_value)
    if payload is None or payload.category is not category:
        return None
    if claim.domain != evidence_domain.value:
        return None
    if not claim_is_public_knowledge_path(claim) or claim.ai_generated is not False:
        return None
    if claim.evidence_tier != EvidenceTier.REFERENCE_DATA.value:
        return None
    if claim.evidence_strength not in REFERENCE_ROLE_STRENGTHS:
        return None

    eligible_sources = [
        _source_view(link, source)
        for link, source in paths
        if source_path_is_public_knowledge(
            link,
            source,
            allowed_source_types=REFERENCE_ROLE_SOURCE_TYPES,
        )
    ]
    if not eligible_sources:
        return None
    eligible_sources.sort(key=lambda row: (row.source_key, row.locator or "", str(row.source_id)))
    return SubstanceCategoryInterpretationClaim(
        claim_id=claim.id,
        claim_key=claim.claim_key,
        claim_version=claim.claim_version,
        summary=claim.summary,
        scope=claim.scope,
        evidence_strength=claim.evidence_strength,
        evidence_tier=claim.evidence_tier,
        sources=tuple(eligible_sources),
    )


async def interpret_formula_projection(
    session: AsyncSession,
    projection: LabelSnapshotFormulaProjection,
    *,
    category: InterpretationCategory,
) -> LabelSnapshotFormulaInterpretation:
    """Attach eligible live evidence to one immutable formula observation."""
    if not isinstance(category, InterpretationCategory):
        raise ValueError("category must be an InterpretationCategory")
    formula_status = projection.formula.status.value
    if not projection.formula.ok:
        return LabelSnapshotFormulaInterpretation(
            provenance=projection.provenance,
            category=category,
            formula_status=formula_status,
            ingredients=(),
        )

    identity_rows = [
        (ingredient, ProjectedIdentityStatus(ingredient.status.value))
        for ingredient in projection.formula.ingredients
    ]
    resolved_keys = sorted({
        ingredient.substance_key
        for ingredient, status in identity_rows
        if status is ProjectedIdentityStatus.RESOLVED and ingredient.substance_key is not None
    })

    claims_by_key: dict[str, tuple[SubstanceCategoryInterpretationClaim, ...]] = {}
    if resolved_keys:
        evidence_domain = _EVIDENCE_DOMAIN_BY_CATEGORY[category]
        candidates = list((await session.execute(
            select(EvidenceClaim).where(
                EvidenceClaim.domain == evidence_domain.value,
                EvidenceClaim.subject_type == "substance",
                EvidenceClaim.subject_key.in_(resolved_keys),
                EvidenceClaim.claim_type == ClaimType.SUBSTANCE_CATEGORY_INTERPRETATION.value,
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

        accepted: dict[str, list[SubstanceCategoryInterpretationClaim]] = defaultdict(list)
        for claim in candidates:
            projected = _eligible_claim(
                claim,
                paths_by_claim[claim.id],
                category=category,
                evidence_domain=evidence_domain,
            )
            if projected is not None:
                accepted[claim.subject_key].append(projected)
        for key, rows in accepted.items():
            rows.sort(key=lambda row: (row.claim_key, row.claim_version, str(row.claim_id)))
            claims_by_key[key] = tuple(rows)

    results: list[FormulaIngredientInterpretation] = []
    for ingredient, identity_status in identity_rows:
        if identity_status is ProjectedIdentityStatus.UNRESOLVED:
            status = InterpretationStatus.IDENTITY_UNRESOLVED
            claims = ()
        elif identity_status is ProjectedIdentityStatus.AMBIGUOUS:
            status = InterpretationStatus.IDENTITY_AMBIGUOUS
            claims = ()
        else:
            claims = claims_by_key.get(ingredient.substance_key or "", ())
            status = (
                InterpretationStatus.EVIDENCE_AVAILABLE
                if claims
                else InterpretationStatus.NOT_ENOUGH_INFORMATION
            )
        results.append(FormulaIngredientInterpretation(
            position=ingredient.position,
            raw_name=ingredient.raw_name,
            normalized_name=ingredient.normalized_name,
            identity_status=identity_status,
            substance_key=ingredient.substance_key,
            entity_kind=ingredient.entity_kind,
            candidate_substance_keys=ingredient.candidate_substance_keys,
            interpretation_status=status,
            claims=claims,
        ))

    return LabelSnapshotFormulaInterpretation(
        provenance=projection.provenance,
        category=category,
        formula_status=formula_status,
        ingredients=tuple(results),
    )


async def interpret_label_snapshot(
    session: AsyncSession,
    snapshot: LabelSnapshot,
    *,
    category: InterpretationCategory,
) -> LabelSnapshotFormulaInterpretation:
    """Project and interpret the exact supplied snapshot, without selecting one."""
    projection = await project_formula_from_label_snapshot(session, snapshot)
    return await interpret_formula_projection(session, projection, category=category)
