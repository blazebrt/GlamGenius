"""Small, explicit approval and lookup helpers for evidence provenance."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.applicability import parse_behavior_applicability
from app.domains.evidence.enums import (
    EVIDENCE_STRENGTHS,
    ClaimSourceRelationship,
    ClaimStatus,
    ReviewStatus,
    RuleEvidenceRelationship,
    SourceStatus,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink


class EvidenceApprovalError(ValueError):
    """Raised when a claim has not met the human approval boundary."""


class EvidenceRuleResolutionError(ValueError):
    """Raised when provenance names a rule absent from deterministic code/data."""


@dataclass(frozen=True, slots=True)
class RuleEvidenceAssessment:
    """Structured, fail-closed provenance diagnostics for one exact rule."""

    provenance_present: bool
    substantive_support_present: bool
    behavior_evidence_eligible: bool
    relationships: tuple[str, ...] = ()
    claim_ids: tuple[uuid.UUID, ...] = ()


async def assess_rule_evidence(
    session: AsyncSession,
    *,
    domain: str,
    rule_kind: str,
    rule_id: str,
    rule_version: str,
) -> RuleEvidenceAssessment:
    """Assess exact rule provenance without activating runtime behaviour.

    The assessment reuses rule identity and claim approval validation, but
    only enables ``behavior_evidence_eligible`` for a complete reviewed
    support path with validated structured applicability.
    Invalid or incomplete paths fail closed rather than raising.
    """
    links = (await session.execute(
        select(RuleEvidenceLink).where(
            RuleEvidenceLink.domain == domain,
            RuleEvidenceLink.rule_kind == rule_kind,
            RuleEvidenceLink.rule_id == rule_id,
            RuleEvidenceLink.rule_version == rule_version,
        )
    )).scalars().all()
    if not links:
        return RuleEvidenceAssessment(False, False, False)
    try:
        await assert_rule_exists(
            session,
            domain=domain,
            rule_kind=rule_kind,
            rule_id=rule_id,
            rule_version=rule_version,
        )
    except EvidenceRuleResolutionError:
        return RuleEvidenceAssessment(False, False, False)

    valid: list[tuple[RuleEvidenceLink, EvidenceClaim]] = []
    for link in links:
        if not link.reviewed_at or not link.reviewed_by:
            continue
        claim = await session.get(EvidenceClaim, link.claim_id)
        if claim is None or claim.review_status in {
            ReviewStatus.SUPERSEDED.value,
            ReviewStatus.RETIRED.value,
        }:
            continue
        try:
            await assert_claim_approvable(session, claim)
        except EvidenceApprovalError:
            continue
        valid.append((link, claim))

    relationships = tuple(sorted({link.relationship for link, _ in valid}))
    claim_ids = tuple(sorted({claim.id for _, claim in valid}, key=str))
    substantive = any(
        link.relationship == RuleEvidenceRelationship.SUPPORTS.value
        and claim.claim_status == ClaimStatus.SUPPORTED.value
        for link, claim in valid
    )
    behavior_eligible = False
    for link, claim in valid:
        if link.relationship != RuleEvidenceRelationship.SUPPORTS.value:
            continue
        if claim.claim_status != ClaimStatus.SUPPORTED.value:
            continue
        if claim.evidence_strength not in EVIDENCE_STRENGTHS:
            continue
        if not claim.strength_rationale or not claim.strength_rationale.strip():
            continue
        source_paths = (await session.execute(
            select(EvidenceClaimSource, EvidenceSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaimSource.claim_id == claim.id)
        )).all()
        if not any(
            claim_source.relationship != ClaimSourceRelationship.BACKGROUND.value
            and claim_source.reviewed_at
            and claim_source.reviewed_by
            and source.status == SourceStatus.ACTIVE.value
            for claim_source, source in source_paths
        ):
            continue
        if parse_behavior_applicability(claim).valid:
            behavior_eligible = True
            break

    return RuleEvidenceAssessment(
        provenance_present=bool(valid),
        substantive_support_present=substantive,
        behavior_evidence_eligible=behavior_eligible,
        relationships=relationships,
        claim_ids=claim_ids,
    )


rule_evidence_assessment = assess_rule_evidence


async def assert_rule_exists(
    session: AsyncSession,
    *,
    domain: str,
    rule_kind: str,
    rule_id: str,
    rule_version: str,
) -> None:
    """Resolve one exact domain rule using the same path seed validation uses."""
    if rule_kind == "ingredient_compatibility":
        from app.domains.routines.ontology import COMPATIBILITY_RULES, ONTOLOGY_VERSION

        if domain != "skin_care" or rule_version != ONTOLOGY_VERSION or not any(
            row.rule_id == rule_id for row in COMPATIBILITY_RULES
        ):
            raise EvidenceRuleResolutionError(f"unknown compatibility rule {rule_id}/{rule_version}")
        return
    if rule_kind == "ingredient_contraindication":
        from app.domains.reference import IngredientContraindicationRule

        row = (await session.execute(
            select(IngredientContraindicationRule).where(
                IngredientContraindicationRule.rule_id == rule_id,
                IngredientContraindicationRule.version == rule_version,
            )
        )).scalar_one_or_none()
        if domain != "skin_care" or row is None:
            raise EvidenceRuleResolutionError(f"unknown contraindication rule {rule_id}/{rule_version}")
        return
    raise EvidenceRuleResolutionError(f"unsupported evidence rule kind {rule_kind}")


async def assert_claim_approvable(session: AsyncSession, claim: EvidenceClaim) -> None:
    """Validate the complete human approval and provenance invariant.

    This is intentionally a service assertion rather than a public endpoint.
    Draft records can exist freely; only an explicit human review can pass it.
    """
    if claim.review_status != ReviewStatus.APPROVED.value:
        raise EvidenceApprovalError("claim review_status must be approved")
    if not claim.reviewed_by or not claim.reviewed_at:
        raise EvidenceApprovalError("approved claims require reviewer and reviewed_at")
    if not claim.claim_status:
        raise EvidenceApprovalError("approved claims require claim_status")
    if not claim.evidence_strength:
        raise EvidenceApprovalError("approved claims require evidence_strength")
    if not claim.strength_rationale or not claim.strength_rationale.strip():
        raise EvidenceApprovalError("approved claims require a non-empty strength_rationale")

    links = (await session.execute(
        select(EvidenceClaimSource, EvidenceSource)
        .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
        .where(EvidenceClaimSource.claim_id == claim.id)
    )).all()
    eligible = [
        (link, source) for link, source in links
        if link.relationship != ClaimSourceRelationship.BACKGROUND.value
    ]
    if not eligible:
        raise EvidenceApprovalError("approved claims require a non-background source")
    for link, source in eligible:
        if source.status != SourceStatus.ACTIVE.value:
            raise EvidenceApprovalError("approved claims require active sources")
        if not link.reviewed_at or not link.reviewed_by:
            raise EvidenceApprovalError("every non-background source link must be reviewed")


async def evidence_state_for_rule(
    session: AsyncSession,
    *,
    domain: str,
    rule_kind: str,
    rule_id: str,
    rule_version: str,
) -> str:
    """Return ``evidence_linked`` only for a fully reviewed exact match.

    Any mismatch, draft link, inactive source, or superseded/retired claim is
    deliberately reported as ``legacy_curated`` so current runtime behaviour
    remains unchanged until a future release consumes this state.
    """
    links = (await session.execute(
        select(RuleEvidenceLink).where(
            RuleEvidenceLink.domain == domain,
            RuleEvidenceLink.rule_kind == rule_kind,
            RuleEvidenceLink.rule_id == rule_id,
            RuleEvidenceLink.rule_version == rule_version,
        )
    )).scalars().all()
    if not links:
        return "legacy_curated"
    await assert_rule_exists(
        session,
        domain=domain,
        rule_kind=rule_kind,
        rule_id=rule_id,
        rule_version=rule_version,
    )
    claim_ids: list = []
    for link in links:
        if not link.reviewed_at or not link.reviewed_by:
            continue
        claim = await session.get(EvidenceClaim, link.claim_id)
        if claim is None or claim.review_status in {ReviewStatus.SUPERSEDED.value, ReviewStatus.RETIRED.value}:
            continue
        try:
            await assert_claim_approvable(session, claim)
        except EvidenceApprovalError:
            continue
        claim_ids.append(claim.id)
    if claim_ids:
        return "evidence_linked"
    return "legacy_curated"
