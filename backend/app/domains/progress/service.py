"""Persistence and orchestration for progress, goals, memory and milestones.

The shape is the same as Phases 4-6: compute deterministically, store what was
computed along with the inputs that produced it, and only then present it.

The part specific to this phase is that **storing is what makes a metric
reproducible**. A metric event carries its formula version and the exact input
counts, so a number shown to a user last March can still be checked by hand
today, against the formula that was live in March rather than today's one.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryItem
from app.domains.media.models import MediaAsset
from app.domains.planning import clock
from app.domains.progress import comparison, memory, metrics, milestones, registry
from app.domains.progress.models import (
    ComparisonSession, FeedbackEvent, GamificationEvent, GoalUpdate, MemoryFact,
    Milestone, MetricEvent, ProgressGoal, ProgressPhoto, ProgressSnapshot,
    ScoreExplanation, Streak,
)
from app.domains.progress.schemas import (
    FeedbackInput, GoalCreate, GoalPatch, MemoryPatch, ProgressPhotoInput, SelfReport,
)
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError


# --- Metrics -----------------------------------------------------------------------


def _period_bounds(period: str, day: date) -> tuple[date, date]:
    if period == "month":
        start = day.replace(day=1)
        next_month = (start + timedelta(days=32)).replace(day=1)
        return start, next_month - timedelta(days=1)
    start = clock.week_start(day)
    return start, start + timedelta(days=6)


async def record_metric(
    session: AsyncSession,
    account_id: uuid.UUID,
    result: metrics.MetricResult,
    *,
    period: str,
    period_start: date,
) -> Optional[MetricEvent]:
    """Store one computed metric, once.

    The unique constraint on ``(account_id, dedup_hash)`` is what makes this
    idempotent, and it is enforced by the database rather than by a check here —
    two concurrent jobs cannot both pass a check and both insert.
    """
    definition = result.definition
    digest = result.dedup_hash(account_id, period, period_start)

    statement = (
        pg_insert(MetricEvent.__table__)
        .values(
            id=uuid.uuid4(), account_id=account_id, metric_key=result.key,
            formula_version=definition.formula_version, period=period,
            period_start=period_start, value=result.value, unit=definition.unit,
            status=result.status, inputs=result.inputs,
            missing_inputs=result.missing_inputs, dedup_hash=digest,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "dedup_hash"])
        .returning(MetricEvent.__table__.c.id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    if inserted is None:
        # Already recorded from identical data. Not an error — that is the point.
        return None

    session.add(ScoreExplanation(
        account_id=account_id, metric_event_id=inserted, metric_key=result.key,
        formula_version=definition.formula_version, formula=definition.formula,
        plain_english=definition.explanation,
        contributing_factors=[
            {"input": key, "value": value} for key, value in result.inputs.items()
        ],
    ))
    return await session.get(MetricEvent, inserted)


async def compute_and_record(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    period: str = "week",
    today: Optional[date] = None,
) -> List[metrics.MetricResult]:
    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    start, _ = _period_bounds(period, day)
    results = await metrics.compute_all(session, account_id, today=day)
    for result in results:
        await record_metric(session, account_id, result, period=period, period_start=start)
    return results


async def overview(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    period: str = "week",
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Everything the Progress screen shows."""
    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    start, end = _period_bounds(period, day)
    results = await compute_and_record(session, account_id, period=period, today=day)

    available = [row for row in results if row.status != metrics.STATUS_UNAVAILABLE]
    unavailable = [row for row in results if row.status == metrics.STATUS_UNAVAILABLE]

    await _upsert_snapshot(session, account_id, period, start, end, results)

    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "metrics": [row.as_dict() for row in results],
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "registry_version": registry.REGISTRY_VERSION,
        # Stated on the response, not only in the docs. A client that renders
        # this cannot accidentally imply there is a headline number.
        "no_overall_score": True,
        # Worded to avoid the phrase it is denying. A sweep over responses bans
        # "overall score" outright, and weakening that check so one sentence can
        # use the words would weaken it everywhere else.
        "no_overall_score_note": (
            "There is no single number here summing you up, and there is not going to be one. "
            "Each number below measures one specific thing and says how it was worked out."
        ),
        "goals": await list_goals(session, account_id),
        "milestones": await recent_milestones(session, account_id),
    }


async def _upsert_snapshot(
    session: AsyncSession, account_id: uuid.UUID, period: str,
    start: date, end: date, results: Sequence[metrics.MetricResult],
) -> None:
    payload = {
        row.key: {
            "value": row.value, "status": row.status,
            "formula_version": row.definition.formula_version,
            "inputs": row.inputs,
        }
        for row in results
    }
    existing = (await session.execute(
        select(ProgressSnapshot).where(
            ProgressSnapshot.account_id == account_id,
            ProgressSnapshot.period == period,
            ProgressSnapshot.period_start == start,
        )
    )).scalar_one_or_none()
    if existing is None:
        session.add(ProgressSnapshot(
            account_id=account_id, period=period, period_start=start,
            period_end=end, metrics=payload,
        ))
    else:
        existing.metrics = payload
        existing.period_end = end


async def metric_detail(
    session: AsyncSession, account_id: uuid.UUID, key: str, *, today: Optional[date] = None
) -> Dict[str, Any]:
    definition = registry.get(key)
    if definition is None:
        raise NotFoundError("We do not track a metric by that name.")

    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    result = await metrics.compute(session, account_id, key, today=day)

    history = (await session.execute(
        select(MetricEvent)
        .where(MetricEvent.account_id == account_id, MetricEvent.metric_key == key)
        .order_by(MetricEvent.period_start.desc())
        .limit(26)
    )).scalars().all()

    return {
        "definition": definition.as_dict(),
        "current": result.as_dict(),
        "history": [
            {
                "period": row.period, "period_start": row.period_start.isoformat(),
                "value": row.value, "status": row.status,
                "formula_version": row.formula_version, "inputs": row.inputs,
            }
            for row in reversed(history)
        ],
        "how_it_is_worked_out": definition.formula,
        "what_it_is_not": definition.not_a_measure_of,
    }


# --- Goals ------------------------------------------------------------------------------


async def create_goal(
    session: AsyncSession, account_id: uuid.UUID, body: GoalCreate, *, today: Optional[date] = None
) -> Dict[str, Any]:
    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    starting: Optional[float] = None
    if body.metric_key:
        result = await metrics.compute(session, account_id, body.metric_key, today=day)
        starting = result.value

    goal = ProgressGoal(
        account_id=account_id, kind=body.kind, title=body.title.strip(),
        metric_key=body.metric_key, target_value=body.target_value,
        starting_value=starting, starts_on=body.starts_on or day,
        target_date=body.target_date, note=body.note,
    )
    session.add(goal)
    await session.flush()

    session.add(GoalUpdate(
        goal_id=goal.id, account_id=account_id, value=starting,
        source="created", recorded_on=day,
        note="Where you started, so progress is measured from something real.",
    ))
    # Flushed before serialising, or the response would omit the update it
    # just created.
    await session.flush()
    return await serialize_goal(session, goal, today=day)


async def patch_goal(
    session: AsyncSession, account_id: uuid.UUID, goal_id: uuid.UUID, body: GoalPatch,
    *, today: Optional[date] = None,
) -> Dict[str, Any]:
    goal = (await session.execute(
        select(ProgressGoal).where(
            ProgressGoal.id == goal_id, ProgressGoal.account_id == account_id,
        )
    )).scalar_one_or_none()
    if goal is None:
        raise NotFoundError("We could not find that goal.")

    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    for field in ("title", "target_value", "target_date", "status", "note"):
        value = getattr(body, field)
        if value is not None:
            setattr(goal, field, value.strip() if isinstance(value, str) else value)
    if body.status == "achieved" and goal.completed_at is None:
        goal.completed_at = utcnow()
        await record_behaviour(
            session, account_id, milestones.BEHAVIOUR_REACHED_OWN_GOAL,
            occurred_on=day, subject_id=goal.id, detail={"title": goal.title},
        )

    if body.progress_value is not None or body.progress_note:
        session.add(GoalUpdate(
            goal_id=goal.id, account_id=account_id, value=body.progress_value,
            source="user", recorded_on=day, note=body.progress_note,
        ))
    await session.flush()
    return await serialize_goal(session, goal, today=day)


async def serialize_goal(
    session: AsyncSession, goal: ProgressGoal, *, today: Optional[date] = None
) -> Dict[str, Any]:
    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    current: Optional[float] = None
    metric_status = None
    if goal.metric_key:
        result = await metrics.compute(session, account_id=goal.account_id, key=goal.metric_key, today=day)
        current = result.value
        metric_status = result.status

    progress: Optional[float] = None
    if (
        goal.target_value is not None and current is not None
        and goal.starting_value is not None and goal.target_value != goal.starting_value
    ):
        span = goal.target_value - goal.starting_value
        progress = round(max(0.0, min(1.0, (current - goal.starting_value) / span)), 4)

    updates = (await session.execute(
        select(GoalUpdate).where(GoalUpdate.goal_id == goal.id)
        .order_by(GoalUpdate.recorded_on.desc()).limit(20)
    )).scalars().all()

    return {
        "id": str(goal.id),
        "kind": goal.kind,
        "title": goal.title,
        "metric_key": goal.metric_key,
        "metric_status": metric_status,
        "starting_value": goal.starting_value,
        "current_value": current,
        "target_value": goal.target_value,
        "progress": progress,
        "starts_on": goal.starts_on.isoformat(),
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "status": goal.status,
        "note": goal.note,
        "updates": [
            {
                "recorded_on": row.recorded_on.isoformat(), "value": row.value,
                "source": row.source, "note": row.note,
            }
            for row in updates
        ],
        "progress_note": (
            "We cannot show progress against this one yet — the metric it uses does not have "
            "enough data." if goal.metric_key and metric_status == metrics.STATUS_UNAVAILABLE else None
        ),
    }


async def list_goals(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> List[Dict[str, Any]]:
    rows = (await session.execute(
        select(ProgressGoal).where(ProgressGoal.account_id == account_id)
        .order_by(ProgressGoal.created_at.desc())
    )).scalars().all()
    return [await serialize_goal(session, row, today=today) for row in rows]


# --- Photos and comparison ------------------------------------------------------------------


async def add_photo(
    session: AsyncSession, account_id: uuid.UUID, body: ProgressPhotoInput,
    *, today: Optional[date] = None,
) -> Dict[str, Any]:
    """Keep a photo for comparison. Ownership of the media is checked here."""
    asset = (await session.execute(
        select(MediaAsset).where(
            MediaAsset.id == body.media_id,
            MediaAsset.account_id == account_id,
            MediaAsset.status == "active",
        )
    )).scalar_one_or_none()
    if asset is None:
        raise NotFoundError("We could not find that image.")

    day = today or clock.local_today(clock.DEFAULT_TIMEZONE)
    existing = (await session.execute(
        select(ProgressPhoto).where(ProgressPhoto.media_id == body.media_id)
    )).scalar_one_or_none()
    if existing is not None:
        raise ValidationFailedError("That image is already saved as a progress photo.", field="media_id")

    photo = ProgressPhoto(
        account_id=account_id, media_id=body.media_id, body_area=body.body_area,
        taken_on=body.taken_on or day, lighting=body.lighting, angle=body.angle,
        framing=body.framing, time_of_day=body.time_of_day, note=body.note,
    )
    session.add(photo)
    await session.flush()

    return {
        "id": str(photo.id),
        "media_id": str(photo.media_id),
        "body_area": photo.body_area,
        "taken_on": photo.taken_on.isoformat(),
        "conditions": {
            "lighting": photo.lighting, "angle": photo.angle,
            "framing": photo.framing, "time_of_day": photo.time_of_day,
        },
        "note": (
            "Saved. To compare it later, take the next one in the same light, at the same "
            "angle and from the same distance."
        ),
    }


async def _conditions_for(session: AsyncSession, photo: ProgressPhoto) -> comparison.PhotoConditions:
    asset = await session.get(MediaAsset, photo.media_id)
    return comparison.PhotoConditions(
        taken_on=photo.taken_on, body_area=photo.body_area, lighting=photo.lighting,
        angle=photo.angle, framing=photo.framing, time_of_day=photo.time_of_day,
        width=asset.width if asset else None, height=asset.height if asset else None,
    )


async def comparisons(
    session: AsyncSession, account_id: uuid.UUID, *, body_area: str = "face",
) -> Dict[str, Any]:
    """The most recent photo against the best comparable earlier one.

    Refusing is the normal outcome and it is explained, never silent.
    """
    photos = (await session.execute(
        select(ProgressPhoto)
        .where(ProgressPhoto.account_id == account_id, ProgressPhoto.body_area == body_area)
        .order_by(ProgressPhoto.taken_on.desc())
    )).scalars().all()

    if len(photos) < 2:
        return {
            "body_area": body_area,
            "comparable": False,
            "comparison": None,
            "photos_held": len(photos),
            "message": (
                "We need at least two photos of the same area, taken in the same conditions "
                "and at least a week apart."
            ),
            "guidance": comparison.guidance_for([]),
        }

    current = photos[0]
    current_conditions = await _conditions_for(session, current)

    candidates = []
    for row in photos[1:]:
        conditions = await _conditions_for(session, row)
        candidates.append((row, conditions))

    best = None
    best_result = None
    rejected: List[Dict[str, Any]] = []
    for row, conditions in candidates:
        result = comparison.compare(conditions, current_conditions)
        if result.comparable:
            if best_result is None or (result.days_apart or 0) > (best_result.days_apart or 0):
                best, best_result = row, result
        else:
            rejected.append({
                "photo_id": str(row.id),
                "taken_on": row.taken_on.isoformat(),
                "reasons": result.blocking_reasons,
            })

    if best is None or best_result is None:
        # Explain rather than show nothing. The reasons are the product here.
        first = rejected[0] if rejected else {"reasons": []}
        session.add(ComparisonSession(
            account_id=account_id, baseline_media_id=candidates[0][0].media_id,
            current_media_id=current.media_id, body_area=body_area, comparable=False,
            checks=[], blocking_reasons=first["reasons"],
            days_apart=(current.taken_on - candidates[0][0].taken_on).days,
        ))
        return {
            "body_area": body_area,
            "comparable": False,
            "comparison": None,
            "photos_held": len(photos),
            "rejected": rejected,
            "message": (
                "We are not going to show a side-by-side from these. The conditions are too "
                "different, and a comparison would show the lighting rather than you."
            ),
            "guidance": comparison.guidance_for([
                comparison.Check(key=key, passed=False, blocking=True, detail="")
                for key in {"lighting", "angle", "framing", "image_quality", "time_difference"}
            ]),
        }

    session.add(ComparisonSession(
        account_id=account_id, baseline_media_id=best.media_id,
        current_media_id=current.media_id, body_area=body_area, comparable=True,
        checks=[row.as_dict() for row in best_result.checks],
        blocking_reasons=[], days_apart=best_result.days_apart,
    ))

    payload = best_result.as_dict()
    payload.update({
        "baseline": {
            "photo_id": str(best.id), "media_id": str(best.media_id),
            "taken_on": best.taken_on.isoformat(),
        },
        "current": {
            "photo_id": str(current.id), "media_id": str(current.media_id),
            "taken_on": current.taken_on.isoformat(),
        },
    })
    return {
        "body_area": body_area,
        "comparable": True,
        "comparison": payload,
        "photos_held": len(photos),
        "rejected": rejected,
        "message": (
            "These two were taken in comparable conditions. What you see is a real difference "
            "in the photos — reading anything into it is still up to you."
        ),
    }


# --- Memory --------------------------------------------------------------------------------


async def list_memory(
    session: AsyncSession, account_id: uuid.UUID, *,
    category: Optional[str] = None, include_deleted: bool = False,
) -> Dict[str, Any]:
    """Everything the app remembers, for the Memory Control screen."""
    rows = await memory.all_facts_including_deleted(session, account_id)
    if not include_deleted:
        rows = [row for row in rows if row.deletion_state == memory.DELETION_ACTIVE]
    if category:
        rows = [row for row in rows if row.category == category]

    disabled = await memory.disabled_categories(session, account_id)
    facts = []
    for row in rows:
        payload = memory.serialize(row, await memory.sources_for(session, row.id))
        payload["why_we_remember"] = memory.why_we_remember(row)
        payload["category_enabled"] = row.category not in disabled
        facts.append(payload)

    return {
        "facts": facts,
        "categories": [
            {
                "key": key,
                "label": memory.CATEGORY_LABELS[key],
                "enabled": key not in disabled,
                "count": sum(
                    1 for row in rows
                    if row.category == key and row.deletion_state == memory.DELETION_ACTIVE
                ),
            }
            for key in memory.CATEGORIES
        ],
        "influencing_count": sum(1 for row in facts if row["influences_recommendations"]),
        "note": (
            "Anything here can be corrected or deleted. Deleted facts stop affecting what we "
            "suggest immediately, and switching a whole category off does the same without "
            "losing anything."
        ),
    }


async def _owned_fact(session: AsyncSession, account_id: uuid.UUID, fact_id: uuid.UUID) -> MemoryFact:
    row = (await session.execute(
        select(MemoryFact).where(MemoryFact.id == fact_id, MemoryFact.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that.")
    return row


async def patch_memory(
    session: AsyncSession, account_id: uuid.UUID, fact_id: uuid.UUID, body: MemoryPatch
) -> Dict[str, Any]:
    fact = await _owned_fact(session, account_id, fact_id)
    if fact.deletion_state == memory.DELETION_DELETED:
        raise ValidationFailedError("That has been deleted. There is nothing left to edit.", field="id")
    await memory.revise(
        session, fact, new_text=body.fact, verification_state=body.verification_state,
    )
    await session.flush()
    payload = memory.serialize(fact, await memory.sources_for(session, fact.id))
    payload["why_we_remember"] = memory.why_we_remember(fact)
    return payload


async def delete_memory(
    session: AsyncSession, account_id: uuid.UUID, fact_id: uuid.UUID
) -> Dict[str, Any]:
    fact = await _owned_fact(session, account_id, fact_id)
    await memory.delete(session, fact)
    await session.flush()
    return {
        "id": str(fact_id),
        "deletion_state": fact.deletion_state,
        "message": (
            "Deleted. We have stopped using it, and the wording is gone — only the record that "
            "something was deleted remains."
        ),
    }


async def export_memory(session: AsyncSession, account_id: uuid.UUID) -> Dict[str, Any]:
    """Everything we hold, including what was deleted and why.

    A deleted fact appears with empty text and its revision history, so the
    export is honest about what happened rather than pretending the fact never
    existed.
    """
    rows = await memory.all_facts_including_deleted(session, account_id)
    from app.domains.progress.models import MemoryRevision

    facts = []
    for row in rows:
        revisions = (await session.execute(
            select(MemoryRevision).where(MemoryRevision.fact_id == row.id)
            .order_by(MemoryRevision.created_at)
        )).scalars().all()
        payload = memory.serialize(row, await memory.sources_for(session, row.id))
        payload["revisions"] = [
            {
                "at": rev.created_at.isoformat() if rev.created_at else None,
                "previous_fact": rev.previous_fact,
                "previous_confidence": rev.previous_confidence,
                "previous_verification_state": rev.previous_verification_state,
                "reason": rev.reason,
            }
            for rev in revisions
        ]
        facts.append(payload)

    disabled = await memory.disabled_categories(session, account_id)
    return {
        "exported_at": utcnow().isoformat(),
        "account_id": str(account_id),
        "facts": facts,
        "total": len(facts),
        "deleted_count": sum(1 for row in rows if row.deletion_state == memory.DELETION_DELETED),
        "disabled_categories": sorted(disabled),
        "format_version": "phase7-v1",
        "note": (
            "Everything GlamGenius remembers about you, including what you deleted and when. "
            "Deleted entries have no text left — only the record that they were removed."
        ),
    }


async def set_memory_category(
    session: AsyncSession, account_id: uuid.UUID, category: str, enabled: bool
) -> Dict[str, Any]:
    row = await memory.set_category_enabled(session, account_id, category, enabled)
    return {
        "category": row.category,
        "label": memory.CATEGORY_LABELS[row.category],
        "enabled": row.enabled,
        "message": (
            f"We will use '{memory.CATEGORY_LABELS[row.category]}' again."
            if enabled else
            f"We have stopped using '{memory.CATEGORY_LABELS[row.category]}'. Nothing was deleted, "
            "so you can switch it back on whenever you like."
        ),
    }


# --- Feedback learning ---------------------------------------------------------------------


# What each signal means for memory. A rejection is worth remembering; a
# compliment the user recorded is worth more.
SIGNAL_TO_CATEGORY = {
    "rejected": memory.CATEGORY_REJECTION,
    "not_for_me": memory.CATEGORY_REJECTION,
    "liked": memory.CATEGORY_FAVOURITE,
    "wore_it": memory.CATEGORY_FAVOURITE,
    "returned": memory.CATEGORY_RETURN,
    "complimented": memory.CATEGORY_COMPLIMENT,
}

SIGNAL_CONFIDENCE = {
    "rejected": 0.7, "not_for_me": 0.7, "liked": 0.6,
    "wore_it": 0.5, "returned": 0.8, "complimented": 0.8,
}


async def record_feedback(
    session: AsyncSession, account_id: uuid.UUID, body: FeedbackInput
) -> Dict[str, Any]:
    """Learn from a reaction, and say plainly what was learned.

    Nothing is learned silently: the response names the fact that was created,
    and that fact is immediately editable and deletable on the Memory screen.
    """
    category = SIGNAL_TO_CATEGORY.get(body.signal)
    label = body.subject_label or body.subject_type.replace("_", " ")

    fact = None
    if category:
        wording = {
            memory.CATEGORY_REJECTION: f"You turned down {label}.",
            memory.CATEGORY_FAVOURITE: f"You liked {label}.",
            memory.CATEGORY_RETURN: f"You returned {label}.",
            memory.CATEGORY_COMPLIMENT: f"You were complimented on {label}.",
        }[category]
        fact = await memory.record(
            session, account_id, category=category, fact=wording,
            source=memory.SOURCE_FEEDBACK,
            confidence=SIGNAL_CONFIDENCE.get(body.signal, 0.5),
            evidence={
                "subject_type": body.subject_type,
                "subject_id": str(body.subject_id) if body.subject_id else None,
                "signal": body.signal, "reason": body.reason,
            },
        )

    event = FeedbackEvent(
        account_id=account_id, subject_type=body.subject_type, subject_id=body.subject_id,
        signal=body.signal, reason=body.reason,
        learned_fact_id=fact.id if fact else None, applied=fact is not None,
    )
    session.add(event)
    await session.flush()

    return {
        "id": str(event.id),
        "signal": body.signal,
        "learned": memory.serialize(fact) if fact else None,
        "message": (
            f"Noted. We remembered: \"{fact.fact}\" — you can change or delete that on the "
            "Memory screen." if fact else
            "Noted. This one did not add anything to what we remember."
            if category is None else
            "Noted. That category of memory is switched off, so we did not store anything."
        ),
    }


async def record_self_report(
    session: AsyncSession, account_id: uuid.UUID, body: SelfReport
) -> Dict[str, Any]:
    """The user's own 1-5 rating. The only thing that feeds that metric."""
    event = FeedbackEvent(
        account_id=account_id, subject_type="self_report", subject_id=None,
        signal="self_report", reason=str(body.rating), applied=True,
    )
    session.add(event)
    await session.flush()
    return {
        "id": str(event.id),
        "rating": body.rating,
        "recorded_on": (body.recorded_on or clock.local_today(clock.DEFAULT_TIMEZONE)).isoformat(),
        "message": "Recorded. This is yours — nothing in the app estimates it for you.",
    }


# --- Gamification and milestones ---------------------------------------------------------------


async def record_behaviour(
    session: AsyncSession,
    account_id: uuid.UUID,
    behaviour: str,
    *,
    occurred_on: date,
    subject_id: Optional[uuid.UUID] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[GamificationEvent]:
    """Record one instance of useful behaviour, once.

    Refuses anything outside :data:`milestones.REWARDABLE_BEHAVIOURS`, so an
    engagement metric cannot be slipped in later. Deduplicated by the database.
    """
    if behaviour in milestones.NEVER_REWARDABLE:
        raise ValidationFailedError(
            f"'{behaviour}' is engagement, not useful behaviour. This product does not reward it.",
            field="behaviour",
        )
    if behaviour not in milestones.REWARDABLE_BEHAVIOURS:
        raise ValidationFailedError(f"'{behaviour}' is not a behaviour we record.", field="behaviour")

    digest = milestones.dedup_hash(account_id, behaviour, occurred_on, subject_id)
    statement = (
        pg_insert(GamificationEvent.__table__)
        .values(
            id=uuid.uuid4(), account_id=account_id, behaviour=behaviour,
            occurred_on=occurred_on, subject_id=subject_id, detail=detail or {},
            dedup_hash=digest,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "dedup_hash"])
        .returning(GamificationEvent.__table__.c.id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    if inserted is None:
        return None

    await _award_milestones(session, account_id, behaviour, occurred_on)
    return await session.get(GamificationEvent, inserted)


async def _award_milestones(
    session: AsyncSession, account_id: uuid.UUID, behaviour: str, occurred_on: date
) -> None:
    count = len((await session.execute(
        select(GamificationEvent.id).where(
            GamificationEvent.account_id == account_id,
            GamificationEvent.behaviour == behaviour,
        )
    )).scalars().all())

    already = set((await session.execute(
        select(Milestone.rule_id).where(Milestone.account_id == account_id)
    )).scalars().all())

    for rule in milestones.earned(behaviour, count, already):
        statement = (
            pg_insert(Milestone.__table__)
            .values(
                id=uuid.uuid4(), account_id=account_id, rule_id=rule.rule_id,
                label=rule.label, description=rule.description, earned_on=occurred_on,
                evidence={"behaviour": behaviour, "count": count, "threshold": rule.threshold},
            )
            .on_conflict_do_nothing(
                index_elements=["account_id", "rule_id", "earned_on"]
            )
        )
        await session.execute(statement)


async def recent_milestones(
    session: AsyncSession, account_id: uuid.UUID, limit: int = 10
) -> List[Dict[str, Any]]:
    rows = (await session.execute(
        select(Milestone).where(Milestone.account_id == account_id)
        .order_by(Milestone.earned_on.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": str(row.id), "rule_id": row.rule_id, "label": row.label,
            "description": row.description, "earned_on": row.earned_on.isoformat(),
            "evidence": row.evidence,
            "acknowledged": row.acknowledged_at is not None,
        }
        for row in rows
    ]


async def acknowledge_milestone(
    session: AsyncSession, account_id: uuid.UUID, milestone_id: uuid.UUID
) -> Dict[str, Any]:
    row = (await session.execute(
        select(Milestone).where(Milestone.id == milestone_id, Milestone.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that.")
    row.acknowledged_at = utcnow()
    return {"id": str(row.id), "acknowledged": True}


async def update_streak(
    session: AsyncSession, account_id: uuid.UUID, behaviour: str, *, on: date, broke: bool = False
) -> Streak:
    """Advance or reset a run of days.

    A reset records the date and increments a count. It is information, and the
    copy around it never treats it as a failure.
    """
    row = (await session.execute(
        select(Streak).where(Streak.account_id == account_id, Streak.behaviour == behaviour)
    )).scalar_one_or_none()
    if row is None:
        row = Streak(account_id=account_id, behaviour=behaviour, current_length=0, longest_length=0)
        session.add(row)
        await session.flush()

    if broke:
        row.current_length = 0
        row.last_reset_on = on
        row.reset_count += 1
        row.started_on = on
        return row

    if row.last_counted_on == on:
        return row
    if row.last_counted_on is not None and (on - row.last_counted_on).days > 1:
        row.current_length = 1
        row.started_on = on
    else:
        row.current_length += 1
        if row.started_on is None:
            row.started_on = on
    row.last_counted_on = on
    row.longest_length = max(row.longest_length, row.current_length)
    return row


async def streaks_for(session: AsyncSession, account_id: uuid.UUID) -> List[Dict[str, Any]]:
    rows = (await session.execute(
        select(Streak).where(Streak.account_id == account_id)
    )).scalars().all()
    return [
        {
            "behaviour": row.behaviour,
            "current_length": row.current_length,
            "longest_length": row.longest_length,
            "started_on": row.started_on.isoformat() if row.started_on else None,
            "last_reset_on": row.last_reset_on.isoformat() if row.last_reset_on else None,
            "reset_count": row.reset_count,
            "note": "A break is just a break. Nothing here is lost by starting again.",
        }
        for row in rows
    ]
