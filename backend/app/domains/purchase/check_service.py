"""The composed, read-only V3-05.7 Care Purchase customer read model."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.purchase import service as purchase_service
from app.domains.purchase.candidate_truth import (
    build_care_candidate_truth,
    serialize_care_candidate_truth,
)
from app.domains.purchase.contract import CARE_PURCHASE_CHECK_VERSION
from app.domains.purchase.decision_memory import current_purchase_decision, serialize_purchase_decision
from app.domains.purchase.evidence_service import resolve_care_purchase_evidence
from app.domains.purchase.value_service import resolve_care_purchase_value
from app.domains.purchase.verdict_service import resolve_care_purchase_verdict
from app.shared.errors.exceptions import ValidationFailedError


def _canonical_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _assert_projection_identity(
    projection: Mapping[str, Any],
    *,
    label: str,
    account_id: str,
    candidate_id: str,
    category: str,
    plan_date: date,
    assessment_fingerprint: str,
) -> None:
    if str(projection.get("account_id")) != account_id:
        raise RuntimeError(f"{label} account identity diverged from the Care assessment.")
    if str(projection.get("candidate_id")) != candidate_id:
        raise RuntimeError(f"{label} candidate identity diverged from the Care assessment.")
    if projection.get("category") != category:
        raise RuntimeError(f"{label} category diverged from the Care assessment.")
    if _canonical_date(projection.get("plan_date")) != plan_date:
        raise RuntimeError(f"{label} plan date diverged from the Care assessment.")
    if projection.get("assessment_fingerprint") != assessment_fingerprint:
        raise RuntimeError(f"{label} fingerprint diverged from the Care assessment.")


async def resolve_care_purchase_check(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None,
) -> dict[str, Any]:
    """Compose existing Care authorities without creating or mutating rows."""
    candidate = await purchase_service.owned_purchase_candidate(
        session, account_id, candidate_id
    )
    purchase_service._require_care(candidate.category)
    truth = build_care_candidate_truth(candidate)
    if not truth.facts_trusted:
        # Keep the explicit review gate ahead of every Care projection.
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
    canonical_date = _canonical_date(assessment["plan_date"])
    evidence = await resolve_care_purchase_evidence(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=canonical_date,
        assessment=assessment,
    )
    value = await resolve_care_purchase_value(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=canonical_date,
        assessment=assessment,
    )
    verdict = await resolve_care_purchase_verdict(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=canonical_date,
        assessment=assessment,
        evidence=evidence,
        value=value,
    )

    account_text = str(account_id)
    candidate_text = str(candidate.id)
    category = candidate.category
    assessment_fingerprint = str(assessment["assessment_fingerprint"])
    if str(assessment.get("account_id")) != account_text:
        raise RuntimeError("Care assessment account identity diverged from the request.")
    if str(assessment.get("candidate_id")) != candidate_text:
        raise RuntimeError("Care assessment candidate identity diverged from the request.")
    if assessment.get("category") != category:
        raise RuntimeError("Care assessment category diverged from the candidate.")
    truth_payload = serialize_care_candidate_truth(candidate)
    if str(truth_payload["candidate"]["id"]) != candidate_text:
        raise RuntimeError("Care candidate truth identity diverged from the candidate.")
    if truth_payload["candidate"]["category"] != category:
        raise RuntimeError("Care candidate truth category diverged from the candidate.")
    for label, projection in (("Evidence", evidence), ("Value", value), ("Verdict", verdict)):
        _assert_projection_identity(
            projection,
            label=label,
            account_id=account_text,
            candidate_id=candidate_text,
            category=category,
            plan_date=canonical_date,
            assessment_fingerprint=assessment_fingerprint,
        )
    if verdict.get("evidence_projection_fingerprint") != evidence.get("projection_fingerprint"):
        raise RuntimeError("Care verdict Evidence fingerprint diverged from the Evidence projection.")
    if verdict.get("value_fingerprint") != value.get("value_fingerprint"):
        raise RuntimeError("Care verdict Value fingerprint diverged from the Value projection.")

    decision = await current_purchase_decision(
        session,
        account_id=account_id,
        candidate_id=candidate.id,
        strategy_key="care_purchase",
    )

    return {
        "care_purchase_check_version": CARE_PURCHASE_CHECK_VERSION,
        "strategy": "care_purchase",
        "candidate_truth": truth_payload,
        "assessment": assessment,
        "evidence": evidence,
        "value": value,
        "verdict": verdict,
        "decision": serialize_purchase_decision(decision) if decision else None,
    }


__all__ = ["resolve_care_purchase_check"]
