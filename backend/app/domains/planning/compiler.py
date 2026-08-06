"""The daily-plan compiler.

Three rules shape this file.

**Do not run an expensive model for every user every morning.** The outfit comes
from the Phase 4 deterministic engine, which is pure arithmetic over confirmed
inventory. A language model is reached for only when the situation is genuinely
novel *and* an operator has switched the flag on. The default path costs one set
of database reads and no AI call at all.

**Do not recompute what has not changed.** Every material input is hashed into
``daily_plans.cache_key``. Matching hash means the stored plan is returned
untouched, which is what makes Today open instantly.

**Do not show everything.** Optional modules produce a row only when they have
something relevant to say today, and each row carries the reason it appeared.
A screen that always shows eight cards is a dashboard, and the brief is explicit
that Today is not one.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import service as inventory_service
from app.domains.planning import clock, notifications
from app.domains.planning import context as context_stage
from app.domains.planning.context import DayContext
from app.domains.planning.models import (
    MODULE_HAIR,
    MODULE_HYDRATION,
    MODULE_NUTRITION,
    MODULE_OUTFIT,
    MODULE_PERFUME,
    MODULE_SHOPPING,
    MODULE_SKINCARE,
    PLAN_SOURCE_CACHE,
    PLAN_SOURCE_FRESH,
    PLANNER_VERSION,
    DailyPlan,
    DailyPlanAction,
    DailyPlanInput,
    OutfitSchedule,
    PlanRecalculationEvent,
)
from app.domains.recommendation import candidates as candidate_stage
from app.domains.recommendation import context as style_stage
from app.domains.recommendation import ranking as ranking_stage
from app.domains.recommendation import service as recommendation_service
from app.domains.recommendation.context import StyleContext
from app.domains.recommendation.models import Look, OccasionRecord
from app.domains.recommendation.occasions import OCCASIONS, get_occasion
from app.shared.database.base import utcnow

logger = logging.getLogger(__name__)

# Below this, the occasion is too uncertain to just act on, and the plan asks
# one question instead of guessing.
CLARIFY_BELOW = 0.6

# How much a recently worn item is marked down, before recency decay. A
# penalty rather than a ban: with a small wardrobe, banning everything worn
# this week leaves nothing to wear.
REPETITION_PENALTY = 0.35

# Wearing something again this soon is the thing worth avoiding. Beyond it,
# rewearing is just how clothes work.
RECENT_DAYS = 2


# --- Occasion record for the day --------------------------------------------


async def occasion_for_day(session: AsyncSession, context: DayContext) -> OccasionRecord:
    """Get or create the Phase 4 occasion row that represents this day.

    Reusing the Phase 4 shape means the day's look is a real ``looks`` row that
    can be revised, swapped and given feedback through the endpoints that
    already exist, rather than a parallel second kind of outfit.
    """
    title = f"{context.plan_date.strftime('%A')} · {get_occasion(context.occasion_key).label}"
    existing = (await session.execute(
        select(OccasionRecord).where(
            OccasionRecord.account_id == context.account_id,
            OccasionRecord.event_date == context.plan_date,
            OccasionRecord.occasion_key == context.occasion_key,
            OccasionRecord.status == "active",
        ).order_by(OccasionRecord.created_at.desc()).limit(1)
    )).scalar_one_or_none()

    weather = context.weather.condition if context.weather else None
    if existing is not None:
        existing.weather = weather
        existing.dress_code = context.dress_code or existing.dress_code
        existing.title = title
        return existing

    record = OccasionRecord(
        account_id=context.account_id, occasion_key=context.occasion_key, title=title,
        event_date=context.plan_date, weather=weather, dress_code=context.dress_code,
        setting=get_occasion(context.occasion_key).default_setting,
        notes="Created automatically for your daily plan.",
    )
    session.add(record)
    await session.flush()
    return record


def style_context_for(context: DayContext, record: OccasionRecord) -> StyleContext:
    """Translate a day into the Phase 4 styling context."""
    style = StyleContext(
        account_id=context.account_id,
        occasion_record=record,
        occasion=get_occasion(context.occasion_key),
        confirmed_attributes=context.profile,
        owned=context.available_owned(),
        draft_count=context.draft_count,
    )
    style = style_stage.resolve_constraints(style)
    style.weather = context.weather.condition if context.weather else None
    style.missing_information = list(context.missing_information)
    return style


# --- Outfit selection --------------------------------------------------------


def _apply_repetition(buckets: dict[str, list[candidate_stage.ScoredItem]], context: DayContext) -> None:
    """Mark down what was worn recently, in proportion to how recently.

    Worn yesterday is heavily penalised; worn six days ago barely at all. A flat
    penalty over a seven-day window makes the entire wardrobe "recent" by
    Thursday, at which point the signal stops meaning anything.
    """
    if not context.item_last_worn:
        return
    window = max(context.repetition_window_days, 1)
    for rows in buckets.values():
        for scored in rows:
            days = context.days_since_worn(scored.id)
            if days is None:
                continue
            decay = max(0.0, (window - days) / window)
            scored.score = round(max(0.0, scored.score - REPETITION_PENALTY * decay), 4)
        rows.sort(key=lambda row: (-row.score, row.item.display_name))


def choose_look(context: DayContext, style: StyleContext) -> ranking_stage.RankedLook | None:
    """The day's outfit, deterministically.

    Unavailable items are excluded outright — the user told us they cannot wear
    them. Recently worn items are marked down, and an exact repeat of a look
    from inside the window is skipped if any alternative exists.
    """
    buckets = candidate_stage.filter_candidates(style)
    _apply_repetition(buckets, context)
    pool = candidate_stage.build_candidates(style, buckets, exclude_ids=context.unavailable_item_ids)
    ranked = ranking_stage.rank(style, pool)
    if not ranked:
        return None

    # Only the last couple of days count as "too soon". Anything older is
    # ordinary rewearing, which is what people actually do with clothes.
    just_worn = context.worn_within(RECENT_DAYS)
    if not just_worn:
        return ranked[0]

    fresh = [row for row in ranked if not _matches_recent(row, just_worn)]
    if fresh:
        return fresh[0]

    # Every option repeats something worn in the last day or two. Try once more
    # with those items excluded outright. A small wardrobe can still end up
    # repeating, but only after we have genuinely looked for an alternative.
    blocked = list(context.unavailable_item_ids) + list(just_worn)
    retry = candidate_stage.build_candidates(style, buckets, exclude_ids=blocked)
    alternatives = ranking_stage.rank(style, retry)
    if alternatives:
        return alternatives[0]
    return ranked[0]


def _matches_recent(ranked: ranking_stage.RankedLook, just_worn: set) -> bool:
    """True when every core piece was already worn in the last day or two."""
    core = {row.id for row in ranked.candidate.clothing}
    if ranked.candidate.shoes is not None:
        core.add(ranked.candidate.shoes.id)
    if not core:
        return False
    return core.issubset(just_worn)


# --- Contextual modules ------------------------------------------------------
# Each builder returns rows only when it has something relevant to say. Returning
# an empty list is the normal, expected outcome for most of them on most days.


def _outfit_actions(context: DayContext, ranked: ranking_stage.RankedLook | None) -> list[dict[str, Any]]:
    if ranked is None:
        return [{
            "module": MODULE_OUTFIT, "action_type": "add_inventory", "priority": 10,
            "title": "Add a few things you own",
            "body": "There is not enough confirmed inventory to put an outfit together yet. We will not invent clothes you do not have.",
            "relevance": "You have no confirmed items that suit today.",
        }]
    names = ", ".join(row.item.display_name for row in ranked.candidate.owned_items())
    return [{
        "module": MODULE_OUTFIT, "action_type": "wear_outfit", "priority": 10,
        "title": ranked.title, "body": names,
        "relevance": f"Chosen for {get_occasion(context.occasion_key).label.lower()}.",
    }]


def _appearance_action(context: DayContext, expiring: Sequence[dict[str, Any]], low_use: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The single most important appearance action, if there is one.

    Deliberately one row, not a list. The brief asks for "the most important
    appearance action", and offering five is the same as offering none.
    """
    if expiring:
        first = expiring[0]
        days = first.get("days_to_expiry")
        when = "has passed its date" if isinstance(days, int) and days < 0 else f"runs out in {days} days"
        return [{
            "module": MODULE_SKINCARE if first.get("category") == "beauty" else MODULE_HAIR,
            "action_type": "use_or_replace", "priority": 20,
            "title": f"Use or replace {first['display_name']}",
            "body": f"This {when}. Using it now is better than replacing it later.",
            "relevance": "Something you own is close to its expiry date.",
            "inventory_item_id": first.get("id"),
        }]
    if context.draft_count:
        return [{
            "module": MODULE_OUTFIT, "action_type": "confirm_drafts", "priority": 20,
            "title": f"Confirm {context.draft_count} item{'s' if context.draft_count > 1 else ''} waiting for you",
            "body": "Drafts from photos are not used in outfits until you confirm them.",
            "relevance": "You have unconfirmed inventory drafts.",
        }]
    if low_use:
        first = low_use[0]
        return [{
            "module": MODULE_OUTFIT, "action_type": "wear_low_use", "priority": 22,
            "title": f"Give {first['display_name']} a wear",
            "body": "You have not worn this in a while. It still fits the kind of day you have today.",
            "relevance": "An item you own is going unused.",
            "inventory_item_id": first.get("id"),
        }]
    return []


def _weather_action(context: DayContext) -> list[dict[str, Any]]:
    """Only when the weather actually changes what someone should do."""
    if context.weather is None:
        return []
    condition = context.weather.condition
    rain = context.weather.precipitation_chance or 0
    if condition == "rainy" or rain >= 50:
        return [{
            "module": MODULE_OUTFIT, "action_type": "weather_adjustment", "priority": 30,
            "title": "Rain is likely — plan for it",
            "body": "Closed shoes you do not mind getting wet, and something to keep the rain off. Suede and silk will mark.",
            "relevance": f"Rain chance recorded at {rain}%." if rain else "Rain recorded for today.",
        }]
    if condition in ("hot", "humid"):
        return [{
            "module": MODULE_OUTFIT, "action_type": "weather_adjustment", "priority": 30,
            "title": "Dress for the heat",
            "body": "Cotton and linen over anything heavy. Loose beats tight when it is this warm.",
            "relevance": f"Today is recorded as {condition}.",
        }]
    if condition == "cold":
        return [{
            "module": MODULE_OUTFIT, "action_type": "weather_adjustment", "priority": 30,
            "title": "Add a layer you can take off",
            "body": "Indoors will be warmer than outdoors. A layer you can remove beats one you cannot.",
            "relevance": "Today is recorded as cold.",
        }]
    return []


def _event_action(context: DayContext) -> list[dict[str, Any]]:
    event = context.primary_event
    if event is None:
        return []
    when = event.local_time(context.timezone_name)
    where = f" at {event.location}" if event.location else ""
    return [{
        "module": MODULE_OUTFIT, "action_type": "upcoming_commitment", "priority": 40,
        "title": f"{event.title} · {when}" if not event.all_day else event.title,
        "body": f"Today's outfit is built around this{where}.",
        "relevance": "The most formal commitment on your calendar today.",
    }]


def _routine_actions(context: DayContext, beauty: Sequence[dict[str, Any]], hair: Sequence[dict[str, Any]], perfumes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Skincare, hair and perfume — shown only when the user owns the products.

    Telling someone to apply a serum they do not own is noise, so these modules
    stay silent until there is something of theirs to point at.
    """
    rows: list[dict[str, Any]] = []
    part = clock.part_of_day(context.now_local)
    if beauty and part in ("morning", "afternoon"):
        first = beauty[0]
        rows.append({
            "module": MODULE_SKINCARE, "action_type": "routine", "priority": 50,
            "title": f"Morning skincare: {first['display_name']}",
            "body": "Your recorded routine for the start of the day.",
            "relevance": "You have this product in your inventory.",
            "inventory_item_id": first.get("id"),
        })
    if hair and clock.is_weekend(context.plan_date):
        first = hair[0]
        rows.append({
            "module": MODULE_HAIR, "action_type": "routine", "priority": 55,
            "title": f"Weekend hair: {first['display_name']}",
            "body": "A slower day is a good time for the longer part of your hair routine.",
            "relevance": "It is the weekend and you own this product.",
            "inventory_item_id": first.get("id"),
        })
    occasion = OCCASIONS.get(context.occasion_key)
    if perfumes and occasion and occasion.formality >= 3:
        first = perfumes[0]
        rows.append({
            "module": MODULE_PERFUME, "action_type": "routine", "priority": 60,
            "title": f"Perfume: {first['display_name']}",
            "body": "Today is dressier than usual, which is when this earns its place.",
            "relevance": f"{occasion.label} is a more formal occasion.",
            "inventory_item_id": first.get("id"),
        })
    return rows


def _wellbeing_actions(context: DayContext) -> list[dict[str, Any]]:
    """Hydration and nutrition. Contextual, never a daily nag."""
    rows: list[dict[str, Any]] = []
    if context.weather and context.weather.condition in ("hot", "humid"):
        rows.append({
            "module": MODULE_HYDRATION, "action_type": "reminder", "priority": 70,
            "title": "Carry water today",
            "body": "It is hot enough that you will notice it by the afternoon.",
            "relevance": f"Today is recorded as {context.weather.condition}.",
        })
    # Once a week, on Monday, rather than every single morning.
    if context.plan_date.weekday() == 0 and context.profile.get("current_goal"):
        rows.append({
            "module": MODULE_NUTRITION, "action_type": "weekly_note", "priority": 75,
            "title": "A fresh week for your goal",
            "body": f"Your recorded goal is: {context.profile['current_goal']}.",
            "relevance": "Shown on Mondays only, because you set a goal.",
        })
    return rows


def _shopping_action(context: DayContext, pending: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not pending:
        return []
    first = pending[0]
    return [{
        "module": MODULE_SHOPPING, "action_type": "pending_decision", "priority": 80,
        "title": f"Still deciding on {first['display_name']}?",
        "body": "You checked this and we said Wait. It is still sitting undecided.",
        "relevance": "You have an unresolved shopping check.",
    }]


# --- Clarification -----------------------------------------------------------


def clarification_for(context: DayContext) -> dict[str, Any] | None:
    """One focused question, only when the answer would change the plan.

    Never asks something we already know: an event the user confirmed, or a
    dress code already recorded, produces no question.
    """
    event = context.primary_event
    if event is not None and not event.user_confirmed and context.occasion_confidence < CLARIFY_BELOW:
        occasion = get_occasion(context.occasion_key)
        return {
            "key": "occasion_key",
            "question": f"Is \"{event.title}\" a {occasion.label.lower()} kind of day?",
            "why": "Getting this right changes how formal today's outfit is.",
            "options": [
                {"value": occasion.key, "label": f"Yes, {occasion.label.lower()}"},
                {"value": "office", "label": "Office"},
                {"value": "business_meeting", "label": "Business meeting"},
                {"value": "everyday", "label": "Just a normal day"},
            ],
        }
    if event is not None and not context.dress_code and OCCASIONS[context.occasion_key].formality >= 4:
        occasion = get_occasion(context.occasion_key)
        return {
            "key": "dress_code",
            "question": "Is there a dress code for today?",
            "why": "A stated dress code always beats our guess at the formality.",
            "options": [{"value": code, "label": code.replace("_", " ").title()} for code in occasion.dress_codes],
        }
    if context.weather is None and context.events:
        return {
            "key": "weather",
            "question": "What is the weather doing today?",
            "why": "Weather changes the fabric and the layers, more than anything else does.",
            "options": [{"value": value, "label": value.title()} for value in ("hot", "humid", "rainy", "mild", "cool", "cold")],
        }
    return None


# --- Compilation -------------------------------------------------------------


async def _module_material(session: AsyncSession, context: DayContext) -> dict[str, list[dict[str, Any]]]:
    """Read the inventory facts the optional modules need, once."""
    account_id = context.account_id
    expiring = await inventory_service.expiring_items(session, account_id, days=45)
    low_use = await inventory_service.low_use_items(session, account_id)
    available = {item.id for item in context.available_owned()}

    def owned_rows(category: str) -> list[dict[str, Any]]:
        return [
            {"id": str(item.id), "display_name": item.display_name, "category": item.category}
            for item in context.available_owned() if item.category == category
        ]

    pending: list[dict[str, Any]] = []
    from app.domains.recommendation.models import PurchaseDecision, PurchaseEvaluation, ShoppingCandidate

    rows = (await session.execute(
        select(PurchaseEvaluation, ShoppingCandidate)
        .join(ShoppingCandidate, ShoppingCandidate.id == PurchaseEvaluation.candidate_id)
        .outerjoin(PurchaseDecision, PurchaseDecision.evaluation_id == PurchaseEvaluation.id)
        .where(
            PurchaseEvaluation.account_id == account_id,
            PurchaseEvaluation.verdict == "wait",
            PurchaseDecision.id.is_(None),
        )
        .order_by(PurchaseEvaluation.created_at.desc())
        .limit(3)
    )).all()
    for _, candidate in rows:
        pending.append({"display_name": candidate.display_name})

    return {
        "expiring": [row for row in expiring if uuid.UUID(row["id"]) in available],
        "low_use": [row for row in low_use if uuid.UUID(row["id"]) in available],
        "beauty": owned_rows("beauty"),
        "hair": owned_rows("hair"),
        "perfumes": owned_rows("perfumes"),
        "pending_purchases": pending,
    }


def headline_for(context: DayContext, ranked: ranking_stage.RankedLook | None) -> str:
    occasion = get_occasion(context.occasion_key)
    day = context.plan_date.strftime("%A")
    if ranked is None:
        return f"{day}: nothing to suggest yet"
    if context.primary_event is not None:
        return f"{day}: dressed for {context.primary_event.title}"
    return f"{day}: {occasion.label.lower()}"


async def compile_day(
    session: AsyncSession,
    *,
    context: DayContext,
    force: bool = False,
    trigger: str = "requested",
) -> tuple[DailyPlan, bool]:
    """Return the plan for this day, rebuilding it only if it must change.

    The second element of the tuple is True when the plan was recomputed.
    """
    key = context_stage.cache_key(context)
    existing = (await session.execute(
        select(DailyPlan).where(
            DailyPlan.account_id == context.account_id,
            DailyPlan.plan_date == context.plan_date,
        )
    )).scalar_one_or_none()

    if existing is not None and not force:
        if existing.cache_key == key and existing.status == "ready":
            existing.generated_from = PLAN_SOURCE_CACHE
            return existing, False
        if existing.locked:
            # A locked day is the user's decision. Note that context moved, but
            # do not overwrite what they chose.
            session.add(PlanRecalculationEvent(
                account_id=context.account_id, plan_date=context.plan_date, trigger=trigger,
                detail="Context changed but the day is locked, so the plan was kept.",
                old_cache_key=existing.cache_key, new_cache_key=key, recomputed=False,
            ))
            existing.generated_from = PLAN_SOURCE_CACHE
            return existing, False

    if existing is not None:
        session.add(PlanRecalculationEvent(
            account_id=context.account_id, plan_date=context.plan_date, trigger=trigger,
            detail="Material context changed, so the plan was rebuilt.",
            old_cache_key=existing.cache_key, new_cache_key=key, recomputed=True,
        ))

    record = await occasion_for_day(session, context)
    style = style_context_for(context, record)
    ranked = choose_look(context, style)

    look: Look | None = None
    if ranked is not None:
        # The run is linked to a style request so the day's look is reachable
        # by the Phase 4 revise and swap endpoints, which walk
        # look -> run -> style_request -> occasion to rebuild their context.
        request = await recommendation_service.create_style_request(
            session, context.account_id, record, [], None
        )
        run = await recommendation_service.start_run(
            session, context.account_id, kind="daily_plan", style_request_id=request.id
        )
        await recommendation_service.record_inputs(session, run, [
            {"input_type": "planner", "input_key": "plan_date", "value": context.plan_date.isoformat(), "source": "derived"},
            {"input_type": "planner", "input_key": "occasion_key", "value": context.occasion_key, "source": "calendar" if context.events else "derived"},
        ])
        look = await recommendation_service.persist_look(session, run, ranked, 1)
        await recommendation_service.finish_run(
            session, run, status="succeeded", considered=len(context.available_owned()),
            explanation_source="deterministic",
        )

    plan = existing or DailyPlan(account_id=context.account_id, plan_date=context.plan_date, cache_key=key)
    completed_before: dict[tuple[str, str, str], datetime] = {}
    if existing is None:
        session.add(plan)
    else:
        plan.version += 1
        # Rebuilding the day must not un-tick what the user already did. The
        # rows are replaced, so the completions are carried across by what the
        # action *is* rather than by row id.
        completed_before = await _completed_action_marks(session, plan)
        await _clear_children(session, plan)

    clarification = clarification_for(context)
    plan.timezone_name = context.timezone_name
    plan.status = "ready" if ranked is not None else "needs_inventory"
    plan.headline = headline_for(context, ranked)
    plan.look_id = look.id if look is not None else None
    plan.weather_snapshot_id = context.weather_snapshot_id
    plan.weather_note = ranked.weather_note if ranked else ""
    plan.event_note = (context.primary_event.title if context.primary_event else "")
    plan.confidence = round(min(ranked.confidence if ranked else 0.2, context.occasion_confidence), 4)
    plan.generated_from = PLAN_SOURCE_FRESH
    plan.engine_version = PLANNER_VERSION
    plan.cache_key = key
    plan.used_llm = False
    plan.needs_clarification = clarification is not None
    plan.clarification = clarification
    plan.missing_information = list(context.missing_information)
    plan.computed_at = utcnow()
    await session.flush()

    for row in context_stage.input_rows(context):
        session.add(DailyPlanInput(plan_id=plan.id, **row))

    material = await _module_material(session, context)
    actions: list[dict[str, Any]] = []
    actions.extend(_outfit_actions(context, ranked))
    actions.extend(_appearance_action(context, material["expiring"], material["low_use"]))
    actions.extend(_weather_action(context))
    actions.extend(_event_action(context))
    actions.extend(_routine_actions(context, material["beauty"], material["hair"], material["perfumes"]))
    actions.extend(_wellbeing_actions(context))
    actions.extend(_shopping_action(context, material["pending_purchases"]))

    for row in actions:
        item_id = row.pop("inventory_item_id", None)
        session.add(DailyPlanAction(
            plan_id=plan.id,
            inventory_item_id=uuid.UUID(item_id) if isinstance(item_id, str) else item_id,
            completed_at=completed_before.get(
                (row["module"], row["action_type"], row["title"])
            ),
            **row,
        ))

    await _schedule_outfit(session, context, plan, ranked)
    await session.flush()

    # Queue the day's single notification. The decision layer — dedup, the
    # daily cap, quiet hours — runs here so preferences actually take effect;
    # delivery to a device is a separate transport and is not wired yet, so a
    # queued row is where this stops. Never allowed to break a plan.
    try:
        await notifications.queue_for_plan(session, plan=plan, timezone_name=context.timezone_name)
    except Exception:  # noqa: BLE001 - a notification must not fail the day
        logger.exception("notification_queue_failed date=%s", context.plan_date)

    return plan, True


async def _completed_action_marks(
    session: AsyncSession, plan: DailyPlan
) -> dict[tuple[str, str, str], datetime]:
    """When each of the day's finished actions was finished.

    Keyed by what the action is — module, type and title — because the rows
    themselves are about to be replaced. Two actions that read identically to
    the user are the same action as far as "I already did that" is concerned.
    """
    rows = (await session.execute(
        select(DailyPlanAction).where(
            DailyPlanAction.plan_id == plan.id,
            DailyPlanAction.completed_at.is_not(None),
        )
    )).scalars().all()
    return {
        (row.module, row.action_type, row.title): row.completed_at for row in rows
    }


async def _clear_children(session: AsyncSession, plan: DailyPlan) -> None:
    """Wipe the previous computation's rows before writing the new one."""
    for model in (DailyPlanInput, DailyPlanAction):
        rows = (await session.execute(select(model).where(model.plan_id == plan.id))).scalars().all()
        for row in rows:
            await session.delete(row)
    await session.flush()


async def _schedule_outfit(
    session: AsyncSession, context: DayContext, plan: DailyPlan, ranked: ranking_stage.RankedLook | None
) -> None:
    """Record what is planned, so tomorrow knows what today used."""
    row = (await session.execute(
        select(OutfitSchedule).where(
            OutfitSchedule.account_id == context.account_id,
            OutfitSchedule.plan_date == context.plan_date,
        )
    )).scalar_one_or_none()
    item_ids = [str(scored.id) for scored in ranked.candidate.owned_items()] if ranked else []
    if row is None:
        session.add(OutfitSchedule(
            account_id=context.account_id, plan_date=context.plan_date,
            look_id=plan.look_id, item_ids=item_ids, status="planned",
        ))
        return
    if row.status == "worn":
        # What was actually worn is a fact. A regenerated plan does not erase it.
        return
    row.look_id = plan.look_id
    row.item_ids = item_ids
