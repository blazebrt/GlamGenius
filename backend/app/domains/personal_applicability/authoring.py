"""Governed admin authoring for exact Step 8B applicability evidence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import (
    ClaimSourceRelationship,
    ClaimType,
    EvidenceTier,
    ReviewStatus,
    SourceStatus,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.evidence.service import (
    claim_is_public_knowledge_path,
    publication_verification_complete,
    source_path_is_public_knowledge,
)
from app.domains.evidence.urls import openable_url
from app.domains.personal_applicability.enums import (
    PersonalApplicabilityCategory,
    PersonalApplicabilityOperator,
)
from app.domains.personal_applicability.schema import (
    MAX_PERSONAL_APPLICABILITY_CONDITIONS,
    PERSONAL_APPLICABILITY_PAYLOAD_KEY,
    PERSONAL_APPLICABILITY_SCHEMA_VERSION,
    parse_personal_applicability_payload,
)
from app.domains.personal_applicability.service import (
    PERSONAL_APPLICABILITY_SOURCE_TYPES,
    PERSONAL_APPLICABILITY_STRENGTHS,
    evidence_domain_for_category,
)
from app.domains.personal_lens.enums import PersonalLensCategory
from app.domains.personal_lens.service import BODY_FACT_KEYS_BY_CATEGORY
from app.domains.profile.registry import ATTRIBUTE_REGISTRY

ValidationFailedError = evidence_authoring.ValidationFailedError
ConflictError = evidence_authoring.ConflictError
NotFoundError = evidence_authoring.NotFoundError

PERSONAL_APPLICABILITY_AUTHORING_CATEGORIES: tuple[PersonalApplicabilityCategory, ...] = (
    PersonalApplicabilityCategory.SKIN_CARE,
    PersonalApplicabilityCategory.HAIR_CARE,
    PersonalApplicabilityCategory.COSMETICS,
)
MAX_PERSONAL_APPLICABILITY_SOURCES = 5


@dataclass(frozen=True, slots=True)
class AuthoringConditionInput:
    fact_key: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExistingSourceInput:
    source_key: str
    locator: str | None = None


@dataclass(frozen=True, slots=True)
class NewSourceInput:
    source_type: str
    title: str
    publisher: str
    canonical_url: str
    license_or_use_note: str
    locator: str | None = None
    publication_date: date | None = None
    version_or_revision: str | None = None
    jurisdiction: str | None = None


AuthoringSourceInput = ExistingSourceInput | NewSourceInput


@dataclass(frozen=True, slots=True)
class PersonalApplicabilityDraftInput:
    category: PersonalApplicabilityCategory
    substance_key: str
    summary: str
    scope: str
    evidence_strength: str
    strength_rationale: str
    conditions: tuple[AuthoringConditionInput, ...]
    sources: tuple[AuthoringSourceInput, ...]


@dataclass(frozen=True, slots=True)
class _PreparedDraft:
    category: PersonalApplicabilityCategory
    domain: str
    substance_key: str
    summary: str
    scope: str
    evidence_strength: str
    strength_rationale: str
    structured_value: dict[str, Any]
    sources: tuple[AuthoringSourceInput, ...]


def _required(value: str | None, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationFailedError(f"{field} is required.", field=field)
    return text


def _fact_keys(category: PersonalApplicabilityCategory) -> tuple[str, ...]:
    return BODY_FACT_KEYS_BY_CATEGORY[PersonalLensCategory(category.value)]


def _prepare(entry: PersonalApplicabilityDraftInput) -> _PreparedDraft:
    if not isinstance(entry.category, PersonalApplicabilityCategory):
        raise ValidationFailedError("Unknown personal-applicability category.", field="category")
    if entry.category not in PERSONAL_APPLICABILITY_AUTHORING_CATEGORIES:
        raise ValidationFailedError("CATEGORY_HAS_NO_PERSONAL_BODY_FACTS", field="category")

    fact_keys = _fact_keys(entry.category)
    if not fact_keys:
        raise ValidationFailedError("CATEGORY_HAS_NO_PERSONAL_BODY_FACTS", field="category")
    if not 1 <= len(entry.conditions) <= MAX_PERSONAL_APPLICABILITY_CONDITIONS:
        raise ValidationFailedError(
            f"conditions must contain 1 to {MAX_PERSONAL_APPLICABILITY_CONDITIONS} items.",
            field="conditions",
        )
    if not 1 <= len(entry.sources) <= MAX_PERSONAL_APPLICABILITY_SOURCES:
        raise ValidationFailedError(
            f"sources must contain 1 to {MAX_PERSONAL_APPLICABILITY_SOURCES} items.",
            field="sources",
        )
    if entry.evidence_strength not in PERSONAL_APPLICABILITY_STRENGTHS:
        raise ValidationFailedError(
            "evidence_strength must be strong, moderate, or limited.",
            field="evidence_strength",
        )

    conditions: list[dict[str, object]] = []
    for condition in entry.conditions:
        fact_key = _required(condition.fact_key, "fact_key")
        if fact_key not in fact_keys:
            raise ValidationFailedError(
                f"{fact_key} is not an allowed body fact for {entry.category.value}.",
                field="fact_key",
            )
        spec = ATTRIBUTE_REGISTRY.get(fact_key)
        if spec is None or not spec.choices:
            raise ValidationFailedError("The body fact has no controlled choices.", field="fact_key")
        if not isinstance(condition.values, (tuple, list)) or not condition.values:
            raise ValidationFailedError("values must not be empty.", field="values")
        operator = (
            PersonalApplicabilityOperator.CONTAINS_ANY
            if spec.kind == "list"
            else PersonalApplicabilityOperator.EQUALS_ANY
        )
        conditions.append({
            "fact_key": fact_key,
            "operator": operator.value,
            "values": list(condition.values),
        })

    structured_value: dict[str, Any] = {
        PERSONAL_APPLICABILITY_PAYLOAD_KEY: {
            "schema_version": PERSONAL_APPLICABILITY_SCHEMA_VERSION,
            "category": entry.category.value,
            "all_of": conditions,
        },
    }
    if parse_personal_applicability_payload(structured_value) is None:
        raise ValidationFailedError(
            "The conditions do not form a valid personal-applicability payload.",
            field="conditions",
        )

    return _PreparedDraft(
        category=entry.category,
        domain=evidence_domain_for_category(entry.category).value,
        substance_key=_required(entry.substance_key, "substance_key"),
        summary=_required(entry.summary, "summary"),
        scope=_required(entry.scope, "scope"),
        evidence_strength=entry.evidence_strength,
        strength_rationale=_required(entry.strength_rationale, "strength_rationale"),
        structured_value=structured_value,
        sources=entry.sources,
    )


def _assert_source_metadata(source: EvidenceSource) -> None:
    if source.status != SourceStatus.ACTIVE.value:
        raise ValidationFailedError("The source must be active.", field="source")
    if source.source_type not in PERSONAL_APPLICABILITY_SOURCE_TYPES:
        raise ValidationFailedError("The source type is not eligible for Step 8B.", field="source_type")
    _required(source.title, "source.title")
    _required(source.publisher, "source.publisher")
    if openable_url(source.canonical_url) is None:
        raise ValidationFailedError("The source URL must be openable HTTP(S).", field="canonical_url")
    _required(source.license_or_use_note, "license_or_use_note")


def _validated_locator(locator: str | None) -> str | None:
    if locator is None:
        return None
    if not isinstance(locator, str) or not locator.strip():
        raise ValidationFailedError("locator must be null or nonblank.", field="locator")
    return locator


async def _resolve_source(
    session: AsyncSession,
    source_input: AuthoringSourceInput,
) -> tuple[EvidenceSource, str | None]:
    if not isinstance(source_input, (ExistingSourceInput, NewSourceInput)):
        raise ValidationFailedError("Each source must be existing or new.", field="sources")
    locator = _validated_locator(source_input.locator)
    if isinstance(source_input, ExistingSourceInput):
        source_key = _required(source_input.source_key, "source_key")
        source = (await session.execute(
            select(EvidenceSource).where(EvidenceSource.source_key == source_key)
        )).scalar_one_or_none()
        if source is None:
            raise NotFoundError("That evidence source does not exist.")
        _assert_source_metadata(source)
        return source, locator

    if source_input.source_type not in PERSONAL_APPLICABILITY_SOURCE_TYPES:
        raise ValidationFailedError("The source type is not eligible for Step 8B.", field="source_type")
    canonical_url = openable_url(source_input.canonical_url)
    if canonical_url is None:
        raise ValidationFailedError("The source URL must be openable HTTP(S).", field="canonical_url")
    existing = (await session.execute(
        select(EvidenceSource).where(EvidenceSource.canonical_url == canonical_url).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"DUPLICATE_SOURCE_URL: use existing source {existing.source_key}.",
            current_version=1,
        )

    source_key = f"personal-applicability-source:{uuid.uuid4().hex}"
    source = EvidenceSource(
        source_key=source_key,
        source_series_key=source_key,
        source_type=source_input.source_type,
        title=_required(source_input.title, "source.title"),
        publisher=_required(source_input.publisher, "source.publisher"),
        canonical_url=canonical_url,
        license_or_use_note=_required(source_input.license_or_use_note, "license_or_use_note"),
        publication_date=source_input.publication_date,
        version_or_revision=(source_input.version_or_revision or "").strip() or None,
        jurisdiction=(source_input.jurisdiction or "").strip() or None,
        accessed_at=evidence_authoring.utcnow(),
        status=SourceStatus.ACTIVE.value,
    )
    session.add(source)
    await session.flush()
    return source, locator


async def _resolve_sources(
    session: AsyncSession,
    inputs: tuple[AuthoringSourceInput, ...],
) -> list[tuple[EvidenceSource, str | None]]:
    resolved: list[tuple[EvidenceSource, str | None]] = []
    identities: set[tuple[uuid.UUID, str]] = set()
    for source_input in inputs:
        source, locator = await _resolve_source(session, source_input)
        identity = (source.id, locator or "")
        if identity in identities:
            raise ValidationFailedError("Duplicate source path.", field="sources")
        identities.add(identity)
        resolved.append((source, locator))
    return resolved


def _new_claim(prepared: _PreparedDraft, *, claim_key: str, claim_version: int) -> EvidenceClaim:
    return EvidenceClaim(
        claim_key=claim_key,
        claim_version=claim_version,
        domain=prepared.domain,
        subject_type="substance",
        subject_key=prepared.substance_key,
        claim_type=ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value,
        summary=prepared.summary,
        scope=prepared.scope,
        evidence_strength=prepared.evidence_strength,
        strength_rationale=prepared.strength_rationale,
        claim_status=None,
        review_status=ReviewStatus.DRAFT.value,
        structured_value=prepared.structured_value,
        ai_generated=False,
        evidence_tier=EvidenceTier.CLINICALLY_STUDIED.value,
    )


async def _add_links(
    session: AsyncSession,
    claim: EvidenceClaim,
    paths: list[tuple[EvidenceSource, str | None]],
) -> None:
    session.add_all([
        EvidenceClaimSource(
            claim_id=claim.id,
            source_id=source.id,
            relationship=ClaimSourceRelationship.SUPPORTS.value,
            locator=locator,
        )
        for source, locator in paths
    ])
    await session.flush()


async def _paths_for_claim_ids(
    session: AsyncSession,
    claim_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]]:
    grouped = {claim_id: [] for claim_id in claim_ids}
    if not claim_ids:
        return grouped
    rows = (await session.execute(
        select(EvidenceClaimSource, EvidenceSource)
        .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
        .where(EvidenceClaimSource.claim_id.in_(claim_ids))
    )).all()
    for link, source in rows:
        grouped[link.claim_id].append((link, source))
    for paths in grouped.values():
        paths.sort(key=lambda pair: (pair[1].source_key, pair[0].locator or "", str(pair[1].id)))
    return grouped


def _serialize(
    claim: EvidenceClaim,
    paths: list[tuple[EvidenceClaimSource, EvidenceSource]],
) -> dict[str, Any]:
    payload = parse_personal_applicability_payload(claim.structured_value)
    verification = (claim.structured_value or {}).get("publication_verification")
    return {
        "id": str(claim.id),
        "claim_key": claim.claim_key,
        "claim_version": claim.claim_version,
        "category": payload.category.value if payload is not None else None,
        "domain": claim.domain,
        "substance_key": claim.subject_key,
        "subject_type": claim.subject_type,
        "claim_type": claim.claim_type,
        "summary": claim.summary,
        "scope": claim.scope,
        "evidence_strength": claim.evidence_strength,
        "strength_rationale": claim.strength_rationale,
        "evidence_tier": claim.evidence_tier,
        "review_status": claim.review_status,
        "claim_status": claim.claim_status,
        "ai_generated": claim.ai_generated,
        "conditions": [
            {
                "fact_key": condition.fact_key,
                "operator": condition.operator.value,
                "values": list(condition.values),
            }
            for condition in payload.all_of
        ] if payload is not None else [],
        "sources": [
            {
                "source_id": str(source.id),
                "source_key": source.source_key,
                "source_type": source.source_type,
                "title": source.title,
                "publisher": source.publisher,
                "canonical_url": source.canonical_url,
                "locator": link.locator,
                "publication_date": source.publication_date.isoformat() if source.publication_date else None,
                "version_or_revision": source.version_or_revision,
                "jurisdiction": source.jurisdiction,
                "status": source.status,
                "license_or_use_note": source.license_or_use_note,
                "reviewed_by": link.reviewed_by,
                "reviewed_at": link.reviewed_at.isoformat() if link.reviewed_at else None,
            }
            for link, source in paths
        ],
        "verification": verification,
        "reviewed_by": claim.reviewed_by,
        "reviewed_at": claim.reviewed_at.isoformat() if claim.reviewed_at else None,
        "published_by": claim.published_by,
        "published_at": claim.published_at.isoformat() if claim.published_at else None,
        "supersedes_claim_id": str(claim.supersedes_claim_id) if claim.supersedes_claim_id else None,
        "rejection_reason": claim.rejection_reason,
    }


async def _view(session: AsyncSession, claim: EvidenceClaim) -> dict[str, Any]:
    paths = await _paths_for_claim_ids(session, [claim.id])
    return _serialize(claim, paths[claim.id])


async def _specialized_claim(session: AsyncSession, entry_id: uuid.UUID) -> EvidenceClaim:
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That personal-applicability entry does not exist.")
    if (
        claim.claim_type != ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value
        and not claim.claim_key.startswith("personal-applicability:")
    ):
        raise ConflictError("SPECIALIZED_AUTHORING_REQUIRED", current_version=claim.claim_version)
    return claim


async def create_personal_applicability_draft(
    session: AsyncSession,
    entry: PersonalApplicabilityDraftInput,
    *,
    author: str,
) -> dict[str, Any]:
    del author
    prepared = _prepare(entry)
    paths = await _resolve_sources(session, prepared.sources)
    claim = _new_claim(
        prepared,
        claim_key=f"personal-applicability:{prepared.category.value}:{uuid.uuid4().hex}",
        claim_version=1,
    )
    session.add(claim)
    await session.flush()
    await _add_links(session, claim, paths)
    return await _view(session, claim)


async def get_personal_applicability_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
) -> dict[str, Any]:
    return await _view(session, await _specialized_claim(session, entry_id))


async def list_personal_applicability_entries(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    query = select(EvidenceClaim).where(
        EvidenceClaim.claim_type == ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value,
    )
    if status:
        query = query.where(EvidenceClaim.review_status == status)
    bounded_limit = min(max(limit, 1), 200)
    bounded_offset = max(offset, 0)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    claims = list((await session.execute(
        query.order_by(EvidenceClaim.created_at.desc(), EvidenceClaim.id.desc())
        .limit(bounded_limit)
        .offset(bounded_offset)
    )).scalars().all())
    paths = await _paths_for_claim_ids(session, [claim.id for claim in claims])
    return {
        "entries": [_serialize(claim, paths[claim.id]) for claim in claims],
        "total": total,
        "limit": bounded_limit,
        "offset": bounded_offset,
    }


async def versions_of_personal_applicability_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
) -> dict[str, Any]:
    claim = await _specialized_claim(session, entry_id)
    claims = list((await session.execute(
        select(EvidenceClaim)
        .where(EvidenceClaim.claim_key == claim.claim_key)
        .order_by(EvidenceClaim.claim_version.asc())
    )).scalars().all())
    paths = await _paths_for_claim_ids(session, [row.id for row in claims])
    return {
        "claim_key": claim.claim_key,
        "versions": [_serialize(row, paths[row.id]) for row in claims],
    }


async def _replace_links(
    session: AsyncSession,
    claim: EvidenceClaim,
    paths: list[tuple[EvidenceSource, str | None]],
) -> None:
    links = (await session.execute(
        select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim.id)
    )).scalars().all()
    for link in links:
        await session.delete(link)
    await session.flush()
    await _add_links(session, claim, paths)


def _apply_prepared(claim: EvidenceClaim, prepared: _PreparedDraft) -> None:
    claim.domain = prepared.domain
    claim.subject_type = "substance"
    claim.subject_key = prepared.substance_key
    claim.claim_type = ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value
    claim.summary = prepared.summary
    claim.scope = prepared.scope
    claim.evidence_strength = prepared.evidence_strength
    claim.strength_rationale = prepared.strength_rationale
    claim.claim_status = None
    claim.review_status = ReviewStatus.DRAFT.value
    claim.structured_value = prepared.structured_value
    claim.ai_generated = False
    claim.evidence_tier = EvidenceTier.CLINICALLY_STUDIED.value
    claim.rejection_reason = None
    claim.reviewed_by = None
    claim.reviewed_at = None
    claim.published_by = None
    claim.published_at = None


async def edit_personal_applicability_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    entry: PersonalApplicabilityDraftInput,
    *,
    author: str,
) -> dict[str, Any]:
    del author
    claim = await _specialized_claim(session, entry_id)
    prepared = _prepare(entry)
    paths = await _resolve_sources(session, prepared.sources)
    if claim.review_status in {ReviewStatus.DRAFT.value, ReviewStatus.REJECTED.value}:
        _apply_prepared(claim, prepared)
        await _replace_links(session, claim, paths)
        await session.flush()
        return await _view(session, claim)
    if claim.review_status not in {ReviewStatus.APPROVED.value, ReviewStatus.PUBLISHED.value}:
        raise ConflictError(
            f"A {claim.review_status} entry cannot be edited.",
            current_version=claim.claim_version,
        )

    next_version = int((await session.execute(
        select(func.max(EvidenceClaim.claim_version)).where(EvidenceClaim.claim_key == claim.claim_key)
    )).scalar_one() or claim.claim_version) + 1
    replacement = _new_claim(prepared, claim_key=claim.claim_key, claim_version=next_version)
    replacement.supersedes_claim_id = claim.id
    session.add(replacement)
    await session.flush()
    await _add_links(session, replacement, paths)
    if claim.review_status == ReviewStatus.APPROVED.value:
        claim.review_status = ReviewStatus.SUPERSEDED.value
    await session.flush()
    return await _view(session, replacement)


async def assert_personal_applicability_authoring_ready(
    session: AsyncSession,
    claim: EvidenceClaim,
) -> list[tuple[EvidenceClaimSource, EvidenceSource]]:
    if claim.claim_type != ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value:
        raise ValidationFailedError("The claim type is not personal applicability.", field="claim_type")
    if claim.subject_type != "substance" or not (claim.subject_key or "").strip():
        raise ValidationFailedError("The claim requires an exact substance identity.", field="subject_type")
    if claim.ai_generated is not False:
        raise ValidationFailedError("AI-authored evidence cannot enter this workflow.", field="ai_generated")
    if claim.evidence_tier != EvidenceTier.CLINICALLY_STUDIED.value:
        raise ValidationFailedError("The evidence tier must be clinically_studied.", field="evidence_tier")
    if claim.evidence_strength not in PERSONAL_APPLICABILITY_STRENGTHS:
        raise ValidationFailedError("The evidence strength is not eligible.", field="evidence_strength")
    _required(claim.strength_rationale, "strength_rationale")
    payload = parse_personal_applicability_payload(claim.structured_value)
    if payload is None:
        raise ValidationFailedError("The persisted applicability payload is invalid.", field="structured_value")
    if payload.category not in PERSONAL_APPLICABILITY_AUTHORING_CATEGORIES:
        raise ValidationFailedError("The persisted category is not authorable.", field="category")
    if claim.domain != evidence_domain_for_category(payload.category).value:
        raise ValidationFailedError("The persisted category and domain disagree.", field="domain")

    paths = (await session.execute(
        select(EvidenceClaimSource, EvidenceSource)
        .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
        .where(EvidenceClaimSource.claim_id == claim.id)
    )).all()
    if not 1 <= len(paths) <= MAX_PERSONAL_APPLICABILITY_SOURCES:
        raise ValidationFailedError("The claim requires 1 to 5 sources.", field="sources")
    identities: set[tuple[uuid.UUID, str]] = set()
    for link, source in paths:
        if link.relationship != ClaimSourceRelationship.SUPPORTS.value:
            raise ValidationFailedError("Every source path must support the claim.", field="relationship")
        identity = (source.id, link.locator or "")
        if identity in identities:
            raise ValidationFailedError("Duplicate source path.", field="sources")
        identities.add(identity)
        _assert_source_metadata(source)
    return list(paths)


async def approve_personal_applicability_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    reviewer: str,
) -> dict[str, Any]:
    claim = await _specialized_claim(session, entry_id)
    await assert_personal_applicability_authoring_ready(session, claim)
    await evidence_authoring.approve(session, entry_id, reviewer=reviewer)
    return await _view(session, claim)


async def record_personal_applicability_publication_verification(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    verification: evidence_authoring.VerificationInput,
    actor: str,
) -> dict[str, Any]:
    claim = await _specialized_claim(session, entry_id)
    await evidence_authoring.record_publication_verification(
        session,
        entry_id,
        verification=verification,
        actor=actor,
    )
    return await _view(session, claim)


async def publish_personal_applicability_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    publisher: str,
) -> dict[str, Any]:
    claim = await _specialized_claim(session, entry_id)
    await assert_personal_applicability_authoring_ready(session, claim)
    if not publication_verification_complete(claim):
        raise ValidationFailedError(
            "Every publication verification checkpoint must pass with no unresolved doubt.",
            field="verification",
        )
    await evidence_authoring.publish(session, entry_id, publisher=publisher)
    paths = await assert_personal_applicability_authoring_ready(session, claim)
    if not claim_is_public_knowledge_path(claim):
        raise ValidationFailedError("The published claim is not public knowledge.", field="claim")
    if not any(
        source_path_is_public_knowledge(
            link,
            source,
            allowed_source_types=PERSONAL_APPLICABILITY_SOURCE_TYPES,
        )
        for link, source in paths
    ):
        raise ValidationFailedError("No source path is eligible for Step 8B.", field="sources")
    published_predecessors = list((await session.execute(
        select(EvidenceClaim).where(
            EvidenceClaim.claim_key == claim.claim_key,
            EvidenceClaim.review_status == ReviewStatus.PUBLISHED.value,
            EvidenceClaim.id != claim.id,
        )
    )).scalars().all())
    if len(published_predecessors) > 1:
        raise ConflictError(
            "MULTIPLE_ACTIVE_PUBLISHED_VERSIONS",
            current_version=claim.claim_version,
        )
    if published_predecessors:
        published_predecessors[0].review_status = ReviewStatus.SUPERSEDED.value
        await session.flush()
    return await _view(session, claim)


async def reject_personal_applicability_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    reviewer: str,
    reason: str,
) -> dict[str, Any]:
    claim = await _specialized_claim(session, entry_id)
    await evidence_authoring.reject(session, entry_id, reviewer=reviewer, reason=reason)
    return await _view(session, claim)


def personal_applicability_vocabulary() -> dict[str, Any]:
    categories = []
    for category in PersonalApplicabilityCategory:
        definitions = []
        for fact_key in _fact_keys(category):
            spec = ATTRIBUTE_REGISTRY[fact_key]
            definitions.append({
                "fact_key": fact_key,
                "kind": spec.kind,
                "expected_operator": (
                    PersonalApplicabilityOperator.CONTAINS_ANY.value
                    if spec.kind == "list"
                    else PersonalApplicabilityOperator.EQUALS_ANY.value
                ),
                "choices": list(spec.choices),
            })
        categories.append({
            "category": category.value,
            "supported_for_personal_applicability": bool(definitions),
            "facts": definitions,
        })
    return {
        "categories": categories,
        "allowed_evidence_strengths": sorted(PERSONAL_APPLICABILITY_STRENGTHS),
        "allowed_source_types": sorted(PERSONAL_APPLICABILITY_SOURCE_TYPES),
        "max_conditions": MAX_PERSONAL_APPLICABILITY_CONDITIONS,
        "max_sources": MAX_PERSONAL_APPLICABILITY_SOURCES,
        "schema_version": PERSONAL_APPLICABILITY_SCHEMA_VERSION,
    }


__all__ = [
    "MAX_PERSONAL_APPLICABILITY_SOURCES",
    "PERSONAL_APPLICABILITY_AUTHORING_CATEGORIES",
    "AuthoringConditionInput",
    "AuthoringSourceInput",
    "ExistingSourceInput",
    "NewSourceInput",
    "PersonalApplicabilityDraftInput",
    "approve_personal_applicability_entry",
    "assert_personal_applicability_authoring_ready",
    "create_personal_applicability_draft",
    "edit_personal_applicability_entry",
    "get_personal_applicability_entry",
    "list_personal_applicability_entries",
    "personal_applicability_vocabulary",
    "publish_personal_applicability_entry",
    "record_personal_applicability_publication_verification",
    "reject_personal_applicability_entry",
    "versions_of_personal_applicability_entry",
]
