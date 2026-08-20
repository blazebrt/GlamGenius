"""Shared, strategy-neutral purchase decision memory."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.purchase.contract import (
    CARE_PURCHASE_VERDICT_VERSION,
    PURCHASE_DECISION_MEMORY_VERSION,
)
from app.domains.recommendation.models import PurchaseDecision, PurchaseEvaluation


def serialize_purchase_decision(row: PurchaseDecision) -> dict[str, Any]:
    """Return the stable customer/read-model shape for either strategy."""
    return {
        "purchase_decision_memory_version": PURCHASE_DECISION_MEMORY_VERSION,
        "id": str(row.id),
        "candidate_id": str(row.candidate_id),
        "strategy": row.strategy_key,
        "evaluation_id": str(row.evaluation_id) if row.evaluation_id else None,
        "recommendation_at_decision": {
            "verdict": row.recommendation_verdict,
            "version": row.recommendation_version,
            "fingerprint": row.recommendation_fingerprint,
        },
        "decision": row.decision,
        "note": row.note,
        "followed_recommendation": row.followed_recommendation,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def current_purchase_decision(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    candidate_id: uuid.UUID,
    strategy_key: str | None = None,
) -> PurchaseDecision | None:
    statement = select(PurchaseDecision).where(
        PurchaseDecision.account_id == account_id,
        PurchaseDecision.candidate_id == candidate_id,
    )
    if strategy_key is not None:
        statement = statement.where(PurchaseDecision.strategy_key == strategy_key)
    return (
        await session.execute(
            statement.order_by(PurchaseDecision.updated_at.desc(), PurchaseDecision.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


def style_recommendation_snapshot(evaluation: PurchaseEvaluation) -> dict[str, Any]:
    return {
        "strategy": "style_purchase",
        "evaluation_id": str(evaluation.id),
        "verdict": evaluation.verdict,
        "roi_version": evaluation.roi_version,
        "roi_score": evaluation.roi_score,
    }


async def save_care_decision(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    candidate_id: uuid.UUID,
    check: dict[str, Any],
    decision: str,
    note: str | None,
) -> PurchaseDecision:
    """Upsert one current Care memory row from one canonical check."""
    verdict = check["verdict"]
    verdict_key = verdict["verdict"]
    row = await current_purchase_decision(
        session,
        account_id=account_id,
        candidate_id=candidate_id,
        strategy_key="care_purchase",
    )
    followed = {"buy": "bought", "wait": "waiting", "skip": "skipped"}.get(verdict_key) == decision
    snapshot = {
        "strategy": "care_purchase",
        "care_purchase_verdict_version": CARE_PURCHASE_VERDICT_VERSION,
        "plan_date": str(check["assessment"]["plan_date"]),
        "verdict": verdict_key,
        "headline": verdict.get("headline"),
        "primary_reason_code": verdict.get("primary_reason_code"),
        "reason_codes": verdict.get("reason_codes", []),
        "supporting_reason_codes": verdict.get("supporting_reason_codes", []),
        "decision_fingerprint": verdict.get("decision_fingerprint"),
        "assessment_fingerprint": verdict.get("assessment_fingerprint"),
        "evidence_projection_fingerprint": verdict.get("evidence_projection_fingerprint"),
        "value_fingerprint": verdict.get("value_fingerprint"),
    }
    if row is None:
        row = PurchaseDecision(
            evaluation_id=None,
            account_id=account_id,
            candidate_id=candidate_id,
            strategy_key="care_purchase",
            recommendation_verdict=verdict_key,
            recommendation_version=CARE_PURCHASE_VERDICT_VERSION,
            recommendation_fingerprint=verdict.get("decision_fingerprint"),
            recommendation_snapshot=snapshot,
            decision=decision,
            note=note,
            followed_recommendation=followed,
        )
        session.add(row)
    else:
        row.recommendation_verdict = verdict_key
        row.recommendation_version = CARE_PURCHASE_VERDICT_VERSION
        row.recommendation_fingerprint = verdict.get("decision_fingerprint")
        row.recommendation_snapshot = snapshot
        row.decision = decision
        row.note = note
        row.followed_recommendation = followed
    await session.flush()
    await session.refresh(row)
    return row


__all__ = [
    "current_purchase_decision",
    "save_care_decision",
    "serialize_purchase_decision",
    "style_recommendation_snapshot",
]
