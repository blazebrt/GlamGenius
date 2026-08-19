"""Read-only resolver for the V3-05.5 Care purchase verdict."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.purchase import service as purchase_service
from app.domains.purchase.candidate_truth import build_care_candidate_truth
from app.domains.purchase.care_verdict import project_care_purchase_verdict
from app.domains.purchase.evidence_service import resolve_care_purchase_evidence
from app.domains.purchase.value_service import resolve_care_purchase_value
from app.shared.errors.exceptions import ValidationFailedError


async def resolve_care_purchase_verdict(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None,
    assessment: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    value: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one current-context Care verdict without creating any rows."""
    candidate = await purchase_service.owned_purchase_candidate(
        session, account_id, candidate_id
    )
    purchase_service._require_care(candidate.category)
    truth = build_care_candidate_truth(candidate)
    if not truth.facts_trusted:
        raise ValidationFailedError(
            "Review and confirm the product details first so GlamGenius does not act on an unverified label read.",
            field="verification_state",
        )

    if assessment is None:
        assessment = await purchase_service.care_purchase_assessment(
            session,
            account_id=account_id,
            account_id_str=account_id_str,
            candidate_id=candidate_id,
            plan_date=plan_date,
        )
    canonical_date = assessment["plan_date"]
    if isinstance(canonical_date, str):
        canonical_date = date.fromisoformat(canonical_date)
    if evidence is None:
        evidence = await resolve_care_purchase_evidence(
            session,
            account_id=account_id,
            account_id_str=account_id_str,
            candidate_id=candidate_id,
            plan_date=canonical_date,
            assessment=assessment,
        )
    if value is None:
        value = await resolve_care_purchase_value(
            session,
            account_id=account_id,
            account_id_str=account_id_str,
            candidate_id=candidate_id,
            plan_date=canonical_date,
            assessment=assessment,
        )
    return project_care_purchase_verdict(assessment, evidence, value).as_dict()


__all__ = ["resolve_care_purchase_verdict"]
