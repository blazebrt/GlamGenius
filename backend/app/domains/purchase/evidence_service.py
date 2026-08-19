"""Read-only resolver for first-class Evidence used by Care Purchase."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import assert_claim_approvable, assert_rule_exists
from app.domains.purchase.candidate_truth import build_care_candidate_truth
from app.domains.purchase.care_evidence import ReviewedEvidencePath, project_care_purchase_evidence
from app.domains.routines.ontology import ONTOLOGY_VERSION
from app.shared.errors.exceptions import ValidationFailedError


async def _reviewed_rule_paths(
    session: AsyncSession,
    *,
    category: str,
    findings: list[dict[str, Any]],
) -> tuple[ReviewedEvidencePath, ...]:
    """Resolve exact reviewed RuleEvidenceLink paths; never infer a link."""
    domain = "skin_care" if category == "beauty" else "hair_care"
    paths: list[ReviewedEvidencePath] = []
    for finding in findings:
        rule_id = finding.get("rule_id")
        if not rule_id:
            continue
        rule_version = finding.get("rule_version") or ONTOLOGY_VERSION
        links = (await session.execute(
            select(RuleEvidenceLink).where(
                RuleEvidenceLink.domain == domain,
                RuleEvidenceLink.rule_kind == "ingredient_compatibility",
                RuleEvidenceLink.rule_id == rule_id,
                RuleEvidenceLink.rule_version == rule_version,
            )
        )).scalars().all()
        if not links:
            continue
        try:
            await assert_rule_exists(
                session,
                domain=domain,
                rule_kind="ingredient_compatibility",
                rule_id=rule_id,
                rule_version=rule_version,
            )
        except ValueError:
            continue
        for link in links:
            if not link.reviewed_at or not link.reviewed_by:
                continue
            claim = await session.get(EvidenceClaim, link.claim_id)
            if claim is None:
                continue
            try:
                await assert_claim_approvable(session, claim)
            except ValueError:
                continue
            source_rows = (await session.execute(
                select(EvidenceClaimSource, EvidenceSource)
                .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
                .where(EvidenceClaimSource.claim_id == claim.id)
                .order_by(
                    EvidenceSource.source_key,
                    EvidenceSource.id,
                    EvidenceClaimSource.relationship,
                    EvidenceClaimSource.locator,
                )
            )).all()
            sources = tuple(
                {
                    "source_id": source.id,
                    "source_key": source.source_key,
                    "title": source.title,
                    "publisher": source.publisher,
                    "source_type": source.source_type,
                    "publication_date": source.publication_date,
                    "canonical_url": source.canonical_url,
                    "relationship": claim_source.relationship,
                    "locator": claim_source.locator,
                }
                for claim_source, source in source_rows
                if claim_source.relationship != "background"
                and source.status == "active"
                and claim_source.reviewed_at
                and claim_source.reviewed_by
            )
            if not sources:
                continue
            paths.append(ReviewedEvidencePath(
                rule_id=link.rule_id,
                rule_kind=link.rule_kind,
                rule_version=link.rule_version,
                relationship=link.relationship,
                claim_id=claim.id,
                claim_key=claim.claim_key,
                claim_version=claim.claim_version,
                claim_summary=claim.summary,
                claim_scope=claim.scope,
                claim_type=claim.claim_type,
                evidence_strength=claim.evidence_strength,
                claim_status=claim.claim_status,
                applicability=claim.structured_value,
                sources=sources,
            ))
    return tuple(sorted(paths, key=lambda path: (str(path.rule_id), path.claim_key, str(path.claim_id))))


async def resolve_care_purchase_evidence(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date,
) -> dict[str, Any]:
    """Build the recomputable V3-05.3 projection without writes or entitlements."""
    from app.domains.purchase import service as purchase_service

    row = await purchase_service.owned_purchase_candidate(session, account_id, candidate_id)
    purchase_service._require_care(row.category)
    truth = build_care_candidate_truth(row)
    if not truth.facts_trusted:
        raise ValidationFailedError(
            "Review and confirm the product details first so GlamGenius does not act on an unverified label read.",
            field="verification_state",
        )
    assessment = await purchase_service.care_purchase_assessment(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=plan_date,
    )
    findings = list(
        assessment.get("dimensions", {}).get("compatibility", {}).get("findings", ())
    )
    paths = await _reviewed_rule_paths(
        session, category=row.category, findings=findings
    )
    projection = project_care_purchase_evidence(
        assessment,
        rule_evidence=paths,
        candidate_truth=truth,
    )
    return projection.as_dict()


__all__ = ["resolve_care_purchase_evidence"]
