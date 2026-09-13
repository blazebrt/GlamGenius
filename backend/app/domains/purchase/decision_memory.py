"""Shared, strategy-neutral purchase decision memory."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.purchase.contract import (
    CARE_PURCHASE_VERDICT_VERSION,
    FRAGRANCE_PURCHASE_VERDICT_VERSION,
    PURCHASE_DECISION_MEMORY_VERSION,
    PURCHASE_GUARD_VERSION,
    is_active_care_category,
    is_active_fragrance_category,
    resolve_purchase_strategy,
)
from app.domains.purchase.identity import identity_for_candidate
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    PurchaseEvaluation,
    ShoppingCandidate,
)
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError


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


async def record_decision_event(
    session: AsyncSession, *, row: PurchaseDecision, candidate: ShoppingCandidate,
) -> PurchaseDecisionEvent:
    """Append only a meaningful current-state transition in this transaction."""
    identity = identity_for_candidate(candidate)
    previous = (await session.execute(
        select(PurchaseDecisionEvent).where(PurchaseDecisionEvent.decision_id == row.id).order_by(
            PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc()).limit(1)
    )).scalar_one_or_none()
    if previous is not None and all((
        previous.category == candidate.category,
        previous.strategy_key == row.strategy_key,
        previous.identity_version == identity["version"],
        previous.identity_state == identity["state"],
        previous.identity_fingerprint == identity["fingerprint"],
        previous.recommendation_verdict == row.recommendation_verdict,
        previous.recommendation_version == row.recommendation_version,
        previous.recommendation_fingerprint == row.recommendation_fingerprint,
        previous.recommendation_snapshot == row.recommendation_snapshot,
        previous.decision == row.decision,
        previous.followed_recommendation == row.followed_recommendation,
    )):
        return previous
    event = PurchaseDecisionEvent(
        account_id=row.account_id, candidate_id=candidate.id, decision_id=row.id,
        category=candidate.category, strategy_key=row.strategy_key,
        candidate_display_name=candidate.display_name,
        identity_version=identity["version"], identity_state=identity["state"],
        identity_fingerprint=identity["fingerprint"],
        recommendation_verdict=row.recommendation_verdict,
        recommendation_version=row.recommendation_version,
        recommendation_fingerprint=row.recommendation_fingerprint,
        recommendation_snapshot=row.recommendation_snapshot,
        decision=row.decision, followed_recommendation=row.followed_recommendation,
    )
    session.add(event)
    await session.flush()
    return event


def serialize_decision_event(row: PurchaseDecisionEvent) -> dict[str, Any]:
    return {
        "id": str(row.id), "candidate_id": str(row.candidate_id), "category": row.category,
        "strategy": row.strategy_key, "candidate_display_name": row.candidate_display_name,
        "identity": {"version": row.identity_version, "state": row.identity_state,
                     "fingerprint": row.identity_fingerprint},
        "recommendation_at_decision": {"verdict": row.recommendation_verdict,
            "version": row.recommendation_version, "fingerprint": row.recommendation_fingerprint},
        "decision": row.decision, "followed_recommendation": row.followed_recommendation,
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
    }


async def decision_history(session: AsyncSession, *, account_id: uuid.UUID, limit: int, before: uuid.UUID | None = None) -> list[PurchaseDecisionEvent]:
    statement = select(PurchaseDecisionEvent).where(PurchaseDecisionEvent.account_id == account_id)
    if before is not None:
        cursor = await session.scalar(select(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.id == before, PurchaseDecisionEvent.account_id == account_id,
        ))
        if cursor is None:
            raise NotFoundError("We could not find that decision history cursor.")
        statement = statement.where(or_(
            PurchaseDecisionEvent.created_at < cursor.created_at,
            and_(PurchaseDecisionEvent.created_at == cursor.created_at, PurchaseDecisionEvent.id < cursor.id),
        ))
    return (await session.execute(statement.order_by(
        PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc()).limit(limit))).scalars().all()


async def purchase_guard(session: AsyncSession, *, account_id: uuid.UUID, candidate: ShoppingCandidate) -> dict[str, Any]:
    """Project exact prior facts without recalculating a purchase verdict."""
    identity = identity_for_candidate(candidate)
    base = {"purchase_guard_version": PURCHASE_GUARD_VERSION, "candidate_id": str(candidate.id), "identity": identity,
            "history_coverage": {"state": "step_9a_events_only", "legacy_current_decisions_included": False},
            "prior_consideration_count": 0, "most_recent": None, "guard_state": "no_step9a_prior_event",
            "owned_redundancy": None}
    if identity["state"] != "exact":
        base["guard_state"] = "identity_insufficient"
        return base
    legacy_incomplete = await has_incomplete_legacy_context(
        session, account_id=account_id, candidate_id=candidate.id,
    )
    where = (PurchaseDecisionEvent.account_id == account_id,
             PurchaseDecisionEvent.category == candidate.category,
             PurchaseDecisionEvent.strategy_key == resolve_purchase_strategy(candidate.category).key,
             PurchaseDecisionEvent.identity_version == identity["version"],
             PurchaseDecisionEvent.identity_fingerprint == identity["fingerprint"],
             PurchaseDecisionEvent.identity_state == "exact")
    count = await session.scalar(select(func.count(func.distinct(PurchaseDecisionEvent.candidate_id))).where(*where))
    event = (await session.execute(select(PurchaseDecisionEvent).where(*where).order_by(
        PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc()).limit(1))).scalar_one_or_none()
    base["prior_consideration_count"] = int(count or 0)
    if event is None:
        if legacy_incomplete:
            base["guard_state"] = "historical_context_incomplete"
        return base
    base["most_recent"] = serialize_decision_event(event)
    state = {"bought": "exact_prior_bought", "waiting": "exact_prior_waiting", "skipped": "exact_prior_skipped"}.get(event.decision, "exact_prior_consideration")
    base["guard_state"] = state
    # Historical snapshots remain historical provenance only. They never prove
    # something is owned now; current strategy-owned context is unavailable
    # here until the strategy exposes its own current projection.
    return base


async def has_incomplete_legacy_context(
    session: AsyncSession, *, account_id: uuid.UUID, candidate_id: uuid.UUID,
) -> bool:
    """A pre-Step-9 current row is not evidence that no history exists."""
    decision = await session.scalar(select(PurchaseDecision.id).where(
        PurchaseDecision.account_id == account_id, PurchaseDecision.candidate_id == candidate_id,
    ).limit(1))
    if decision is None:
        return False
    event = await session.scalar(select(PurchaseDecisionEvent.id).where(PurchaseDecisionEvent.decision_id == decision))
    return event is None


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
    candidate = (await session.execute(
        select(ShoppingCandidate)
        .where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        )
        .with_for_update()
    )).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError("We could not find that shopping item.")
    if not is_active_care_category(candidate.category):
        raise ValidationFailedError(
            "This candidate is not eligible for the active Care purchase strategy.",
            field="category",
        )
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
        "environment": verdict.get("environment"),
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
    await record_decision_event(session, row=row, candidate=candidate)
    return row


async def save_fragrance_decision(
    session: AsyncSession,
    *, account_id: uuid.UUID, candidate_id: uuid.UUID,
    check: dict[str, Any], decision: str, note: str | None,
) -> PurchaseDecision:
    """Upsert the shared candidate-backed memory for a Fragrance check."""
    candidate = (await session.execute(
        select(ShoppingCandidate).where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError("We could not find that shopping item.")
    if not is_active_fragrance_category(candidate.category):
        raise ValidationFailedError("This candidate is not eligible for the active Fragrance purchase strategy.", field="category")
    verdict = check["verdict"]
    verdict_key = verdict["verdict"]
    row = await current_purchase_decision(session, account_id=account_id, candidate_id=candidate_id, strategy_key="fragrance_purchase")
    followed = {"buy": "bought", "wait": "waiting", "skip": "skipped"}.get(verdict_key) == decision
    snapshot = {
        "strategy": "fragrance_purchase",
        "fragrance_purchase_verdict_version": FRAGRANCE_PURCHASE_VERDICT_VERSION,
        "verdict": verdict_key,
        "primary_reason_code": verdict.get("primary_reason_code"),
        "supporting_reason_codes": verdict.get("supporting_reason_codes", []),
        "decision_fingerprint": verdict.get("decision_fingerprint"),
        "candidate_id": str(candidate.id),
        "owned_perfume_count": check.get("collection_context", {}).get("owned_perfume_count"),
        "exact_owned_count": len(check.get("collection_context", {}).get("exact_owned", [])),
        "covered_contexts": check.get("collection_context", {}).get("coverage", {}).get("covered", []),
        "unknown_contexts": check.get("collection_context", {}).get("coverage", {}).get("unknown", []),
        "uncovered_contexts": check.get("collection_context", {}).get("coverage", {}).get("uncovered", []),
    }
    if row is None:
        row = PurchaseDecision(
            evaluation_id=None, account_id=account_id, candidate_id=candidate_id,
            strategy_key="fragrance_purchase", recommendation_verdict=verdict_key,
            recommendation_version=FRAGRANCE_PURCHASE_VERDICT_VERSION,
            recommendation_fingerprint=verdict.get("decision_fingerprint"),
            recommendation_snapshot=snapshot, decision=decision, note=note,
            followed_recommendation=followed,
        )
        session.add(row)
    else:
        row.recommendation_verdict = verdict_key
        row.recommendation_version = FRAGRANCE_PURCHASE_VERDICT_VERSION
        row.recommendation_fingerprint = verdict.get("decision_fingerprint")
        row.recommendation_snapshot = snapshot
        row.decision = decision
        row.note = note
        row.followed_recommendation = followed
    await session.flush()
    await session.refresh(row)
    await record_decision_event(session, row=row, candidate=candidate)
    return row


__all__ = [
    "current_purchase_decision",
    "has_incomplete_legacy_context",
    "decision_history",
    "purchase_guard",
    "record_decision_event",
    "serialize_decision_event",
    "save_care_decision",
    "save_fragrance_decision",
    "serialize_purchase_decision",
    "style_recommendation_snapshot",
]
