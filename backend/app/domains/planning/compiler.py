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

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care import cadence as care_cadence
from app.domains.care import decisions as care_decisions
from app.domains.care import maintenance as care_maintenance
from app.domains.care import maintenance_service as care_maintenance_service
from app.domains.care import routine_plan as care_routine_plan
from app.domains.care import service as care_service
from app.domains.care.schemas import CareContext
from app.domains.inventory import service as inventory_service
from app.domains.planning import clock
from app.domains.planning import context as context_stage
from app.domains.planning.context import DayContext
from app.domains.planning.models import (
    MODULE_HAIR,
    MODULE_HYDRATION,
    MODULE_MAINTENANCE,
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
from app.domains.routines import adherence as routine_adherence
from app.shared.database.base import utcnow


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

CARE_REFRESH_DETAIL = "Care material changed; Skin/Hair actions were refreshed while the locked day was preserved."


@dataclass(frozen=True, slots=True)
class DayCareMaterial:
    """The immutable Care material shared by Today and pinning paths."""

    care_context: CareContext
    decisions: care_decisions.CareDecisionSet
    care_plan: care_routine_plan.CareRoutinePlan
    decision_fingerprint: str
    routine_plan_fingerprint: str
    hair_wash_cadence: care_cadence.HairWashCadenceDecision
    hair_wash_cadence_fingerprint: str
    maintenance: care_maintenance.MaintenanceSet
    maintenance_fingerprint: str


@dataclass(frozen=True, slots=True)
class StoredCareFingerprints:
    """Persisted Care identity for a DailyPlan.

    Care freshness is intentionally independent from the full-day cache key so
    a locked plan can refresh Skin/Hair without claiming weather or outfit
    material is current.
    """

    decision_fingerprint: str | None
    routine_plan_fingerprint: str | None
    hair_wash_cadence_fingerprint: str | None
    maintenance_fingerprint: str | None


async def stored_care_fingerprints(
    session: AsyncSession, plan_id: uuid.UUID,
) -> StoredCareFingerprints:
    """Read the latest persisted Care fingerprints for a DailyPlan."""
    rows = (await session.execute(
        select(DailyPlanInput.input_key, DailyPlanInput.value)
        .where(
            DailyPlanInput.plan_id == plan_id,
            DailyPlanInput.input_type == "care",
            DailyPlanInput.input_key.in_((
                "care_decision_fingerprint", "care_routine_plan_fingerprint",
                "care_hair_wash_cadence_fingerprint", "care_maintenance_fingerprint",
            )),
        )
        .order_by(DailyPlanInput.created_at.desc())
    )).all()
    values: dict[str, str] = {}
    for key, value in rows:
        values.setdefault(key, value)
    return StoredCareFingerprints(
        decision_fingerprint=values.get("care_decision_fingerprint"),
        routine_plan_fingerprint=values.get("care_routine_plan_fingerprint"),
        hair_wash_cadence_fingerprint=values.get("care_hair_wash_cadence_fingerprint"),
        maintenance_fingerprint=values.get("care_maintenance_fingerprint"),
    )


def care_material_is_current(
    stored: StoredCareFingerprints, material: DayCareMaterial,
) -> bool:
    """Whether persisted Care represents the material currently being used."""
    return (
        stored.decision_fingerprint == material.decision_fingerprint
        and stored.routine_plan_fingerprint == material.routine_plan_fingerprint
        and stored.hair_wash_cadence_fingerprint == material.hair_wash_cadence_fingerprint
        and stored.maintenance_fingerprint == material.maintenance_fingerprint
    )


async def build_day_care_material(
    session: AsyncSession, context: DayContext,
) -> DayCareMaterial:
    """Build the one deterministic Care material object for a planning day."""
    care_context = await care_service.build_care_context(
        session, context.account_id, day_context=context,
    )
    decisions = care_decisions.evaluate_care_context(care_context)
    care_plan = care_routine_plan.plan_care_routine(care_context, decisions)
    last_wash_on = await routine_adherence.last_completed_wash_on(
        session, account_id=context.account_id, through=context.plan_date,
    )
    frequency_fact = care_context.hair_facts.get("care_hair_wash_frequency")
    hair_wash_cadence = care_cadence.decide_hair_wash_cadence(
        frequency_fact.value if frequency_fact is not None else None,
        plan_date=context.plan_date,
        last_wash_on=last_wash_on,
    )
    maintenance, maintenance_print = await care_maintenance_service.fingerprint_for(
        session, context.account_id, plan_date=context.plan_date,
    )
    return DayCareMaterial(
        care_context=care_context,
        decisions=decisions,
        care_plan=care_plan,
        decision_fingerprint=care_decisions.decision_fingerprint(decisions),
        routine_plan_fingerprint=care_routine_plan.routine_plan_fingerprint(care_plan),
        hair_wash_cadence=hair_wash_cadence,
        hair_wash_cadence_fingerprint=care_cadence.hair_wash_cadence_fingerprint(hair_wash_cadence),
        maintenance=maintenance,
        maintenance_fingerprint=maintenance_print,
    )


def material_cache_key(context: DayContext, material: DayCareMaterial) -> str:
    """Return the canonical Today key for context plus all Care material."""
    return context_stage.cache_key(
        context,
        material_extensions={
            "care_decision_fingerprint": material.decision_fingerprint,
            "care_routine_plan_fingerprint": material.routine_plan_fingerprint,
            "care_hair_wash_cadence_fingerprint": material.hair_wash_cadence_fingerprint,
            "care_maintenance_fingerprint": material.maintenance_fingerprint,
        },
    )


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


def _appearance_action(
    context: DayContext,
    low_use: Sequence[dict[str, Any]],
    *,
    care_context: CareContext,
    decisions: care_decisions.CareDecisionSet,
) -> list[dict[str, Any]]:
    """The single most important appearance action, if there is one.

    Deliberately one row, not a list. The brief asks for "the most important
    appearance action", and offering five is the same as offering none.
    """
    blocking = _care_blocking_action(
        care_context=care_context, decisions=decisions,
    )
    if blocking:
        return [blocking]

    if context.draft_count:
        return [{
            "module": MODULE_OUTFIT, "action_type": "confirm_drafts", "priority": 20,
            "title": f"Confirm {context.draft_count} item{'s' if context.draft_count > 1 else ''} waiting for you",
            "body": "Drafts from photos are not used in outfits until you confirm them.",
            "relevance": "You have unconfirmed inventory drafts.",
        }]

    expiring = _care_expiring_soon_action(
        care_context=care_context, decisions=decisions,
    )
    if expiring:
        return [expiring]

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


def _care_blocking_action(
    *,
    care_context: CareContext,
    decisions: care_decisions.CareDecisionSet,
) -> dict[str, Any] | None:
    """Return the highest-priority hard Care safety action, if any."""
    products = {
        product.item.id: product
        for product in (*care_context.skin_products, *care_context.hair_products)
    }
    for reason_code, title_prefix, body, relevance in (
        (
            care_decisions.CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH,
            "Keep",
            "A confirmed ingredient matches something you asked us to avoid, so GlamGenius has left it out.",
            "This follows an allergy you entered in your profile.",
        ),
        (
            care_decisions.CareDecisionReasonCode.PRODUCT_EXPIRED,
            "Set aside",
            "The date recorded for this product has passed, so GlamGenius will not include it in your Care routine.",
            "The product is past the date recorded for it.",
        ),
    ):
        for decision in decisions.product_decisions:
            if not any(reason.code == reason_code for reason in decision.blocking_reasons):
                continue
            product = products.get(decision.item_id)
            if product is None:
                continue
            module = MODULE_SKINCARE if product.item.category == "beauty" else MODULE_HAIR
            return {
                "module": module,
                "action_type": "care_safety",
                "priority": 18 if reason_code == care_decisions.CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH else 19,
                "title": f"{title_prefix} {product.item.display_name}"
                + (" out of your routine" if reason_code == care_decisions.CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH else ""),
                "body": body,
                "relevance": relevance,
                "inventory_item_id": str(product.item.id),
            }
    return None


def _care_expiring_soon_action(
    *,
    care_context: CareContext,
    decisions: care_decisions.CareDecisionSet,
) -> dict[str, Any] | None:
    """Return the current Care expiry advisory, if any."""
    products = {
        product.item.id: product
        for product in (*care_context.skin_products, *care_context.hair_products)
    }

    for decision in decisions.product_decisions:
        if not decision.eligible or not any(
            reason.code == care_decisions.CareDecisionReasonCode.PRODUCT_EXPIRING_SOON
            for reason in decision.advisory_reasons
        ):
            continue
        product = products.get(decision.item_id)
        if product is None:
            continue
        module = MODULE_SKINCARE if product.item.category == "beauty" else MODULE_HAIR
        return {
            "module": module, "action_type": "care_expiring_soon", "priority": 21,
            "title": f"Check {product.item.display_name}'s date",
            "body": "It is getting close to the date recorded for it. Keep it in rotation only if it already fits your routine.",
            "relevance": "The product is getting close to the date recorded for it.",
            "inventory_item_id": str(product.item.id),
        }
    return None


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


def _care_routine_actions(
    context: DayContext,
    beauty: Sequence[dict[str, Any]],
    hair: Sequence[dict[str, Any]],
    *,
    hair_wash_cadence: care_cadence.HairWashCadenceDecision,
) -> list[dict[str, Any]]:
    """Build the current Skin/Hair routine actions from authoritative Care rows."""
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
    if hair and hair_wash_cadence.status is care_cadence.HairWashCadenceStatus.DUE:
        first = hair[0]
        rows.append({
            "module": MODULE_HAIR, "action_type": "routine", "priority": 55,
            "title": f"Hair wash routine: {first['display_name']}",
            "body": "Your wash routine is relevant today based on the wash rhythm you recorded.",
            "relevance": (
                "Your recorded wash rhythm is daily."
                if hair_wash_cadence.reason is care_cadence.HairWashCadenceReason.DAILY_DECLARATION
                else "Your recorded wash rhythm and last completed wash make this a wash-routine day."
            ),
            "inventory_item_id": first.get("id"),
        })
    return rows


def _maintenance_action(
    maintenance: care_maintenance.MaintenanceSet,
) -> list[dict[str, Any]]:
    """At most one maintenance card, and only when something is actually due.

    Today stays quiet otherwise: "coming up" belongs on the Care screen, not
    on the day's decision list, and a kind with no recorded date is surfaced
    as a question in Care rather than as a task here.
    """
    due = maintenance.due
    if not due:
        return []
    title, body = care_maintenance.maintenance_headline(due)
    return [{
        "module": MODULE_MAINTENANCE, "action_type": "maintenance_due", "priority": 65,
        "title": title,
        "body": body,
        "relevance": "You track this and set the interval yourself.",
    }]


def _care_today_actions(
    context: DayContext,
    module_material: dict[str, list[dict[str, Any]]],
    *,
    care_context: CareContext,
    decisions: care_decisions.CareDecisionSet,
    hair_wash_cadence: care_cadence.HairWashCadenceDecision,
    maintenance: care_maintenance.MaintenanceSet,
) -> list[dict[str, Any]]:
    """The complete current Skin/Hair Today action set, and nothing else."""
    rows: list[dict[str, Any]] = []
    blocking = _care_blocking_action(care_context=care_context, decisions=decisions)
    if blocking:
        rows.append(blocking)
    else:
        expiring = _care_expiring_soon_action(care_context=care_context, decisions=decisions)
        if expiring:
            rows.append(expiring)
    rows.extend(_care_routine_actions(
        context, module_material["beauty"], module_material["hair"],
        hair_wash_cadence=hair_wash_cadence,
    ))
    rows.extend(_maintenance_action(maintenance))
    return rows


def _routine_actions(
    context: DayContext,
    beauty: Sequence[dict[str, Any]],
    hair: Sequence[dict[str, Any]],
    perfumes: Sequence[dict[str, Any]],
    *,
    hair_wash_cadence: care_cadence.HairWashCadenceDecision,
) -> list[dict[str, Any]]:
    """Skincare, hair and perfume — shown only when the user owns the products.

    Telling someone to apply a serum they do not own is noise, so these modules
    stay silent until there is something of theirs to point at.
    """
    rows: list[dict[str, Any]] = _care_routine_actions(
        context, beauty, hair, hair_wash_cadence=hair_wash_cadence,
    )
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


async def _module_material(
    session: AsyncSession,
    context: DayContext,
    *,
    care_context: CareContext,
    decisions: care_decisions.CareDecisionSet,
    care_plan: care_routine_plan.CareRoutinePlan,
) -> dict[str, list[dict[str, Any]]]:
    """Read the inventory facts the optional modules need, once."""
    account_id = context.account_id
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

    eligible = {row.item_id for row in decisions.product_decisions if row.eligible}
    products_by_id = {
        product.item.id: product
        for product in (*care_context.skin_products, *care_context.hair_products)
    }
    active_selected = {
        row.slot: row.selected_item_id
        for row in (*care_plan.skin_slots, *care_plan.hair_slots)
        if row.active and row.selected_item_id is not None
    }
    care_rows = {
        "beauty": [
            {"id": str(item_id), "display_name": products_by_id[item_id].item.display_name,
             "category": "beauty", "slot": slot}
            for slot, item_id in active_selected.items()
            if item_id in products_by_id
            and products_by_id[item_id].item.category == "beauty"
            and item_id in available and item_id in eligible
        ],
        "hair": [
            {"id": str(item_id), "display_name": products_by_id[item_id].item.display_name,
             "category": "hair", "slot": slot}
            for slot, item_id in active_selected.items()
            if item_id in products_by_id
            and products_by_id[item_id].item.category == "hair"
            and item_id in available and item_id in eligible
        ],
    }

    return {
        "low_use": [row for row in low_use if uuid.UUID(row["id"]) in available],
        "beauty": care_rows["beauty"],
        "hair": care_rows["hair"],
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
    material = await build_day_care_material(session, context)
    care_context = material.care_context
    care_decision_set = material.decisions
    care_plan = material.care_plan
    key = material_cache_key(context, material)
    existing = (await session.execute(
        select(DailyPlan).where(
            DailyPlan.account_id == context.account_id,
            DailyPlan.plan_date == context.plan_date,
        )
    )).scalar_one_or_none()

    if existing is not None and not force:
        # Care freshness is independent from the full-day cache key. Check it
        # first so a locked A -> B -> A transition cannot be hidden by an old
        # full key becoming equal again.
        if existing.locked:
            refreshed = await refresh_locked_care_if_needed(
                session, existing, context, material, key, trigger=trigger,
            )
            if refreshed:
                return existing, True
        if existing.cache_key == key and existing.status == "ready":
            existing.generated_from = PLAN_SOURCE_CACHE
            return existing, False
        if existing.locked:
            # A partial Care refresh deliberately leaves the full-day key
            # pinned to the locked computation. Once the same current key has
            # been audited by that refresh, an identical GET is stable rather
            # than producing a generic locked-context event every time.
            latest = (await session.execute(
                select(PlanRecalculationEvent).where(
                    PlanRecalculationEvent.account_id == context.account_id,
                    PlanRecalculationEvent.plan_date == context.plan_date,
                    PlanRecalculationEvent.recomputed.is_(True),
                ).order_by(PlanRecalculationEvent.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            if latest is not None and latest.detail == CARE_REFRESH_DETAIL and latest.new_cache_key == key:
                existing.generated_from = PLAN_SOURCE_CACHE
                return existing, False
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
    plan.air_quality_snapshot_id = context.air_quality_snapshot_id
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

    for key_name, value in care_input_values(material).items():
        session.add(DailyPlanInput(
            plan_id=plan.id, input_type="care", input_key=key_name,
            value=value, source="derived",
        ))

    module_material = await _module_material(
        session, context, care_context=care_context, decisions=care_decision_set,
        care_plan=care_plan,
    )
    actions: list[dict[str, Any]] = []
    actions.extend(_outfit_actions(context, ranked))
    actions.extend(_appearance_action(
        context, module_material["low_use"],
        care_context=care_context, decisions=care_decision_set,
    ))
    actions.extend(_weather_action(context))
    actions.extend(_event_action(context))
    actions.extend(_routine_actions(
        context, module_material["beauty"], module_material["hair"], module_material["perfumes"],
        hair_wash_cadence=material.hair_wash_cadence,
    ))
    actions.extend(_maintenance_action(material.maintenance))
    actions.extend(_wellbeing_actions(context))
    actions.extend(_shopping_action(context, module_material["pending_purchases"]))

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

    return plan, True


def care_input_values(material: DayCareMaterial) -> dict[str, Any]:
    """Build the canonical audited Care input payload for a planning day."""
    care_context = material.care_context
    care_decision_set = material.decisions
    care_plan = material.care_plan
    cadence = material.hair_wash_cadence
    return {
        "care_context_version": care_context.context_version,
        "care_decision_version": care_decision_set.decision_version,
        "care_decision_fingerprint": material.decision_fingerprint,
        "care_routine_plan_fingerprint": material.routine_plan_fingerprint,
        "care_cadence_version": cadence.cadence_version,
        "care_hair_wash_cadence_fingerprint": material.hair_wash_cadence_fingerprint,
        "care_maintenance_version": material.maintenance.maintenance_version,
        "care_maintenance_fingerprint": material.maintenance_fingerprint,
        "care_maintenance_due_count": len(material.maintenance.due),
        "care_maintenance_tracked_count": len(material.maintenance.tracked_decisions()),
        "care_hair_wash_status": cadence.status.value,
        "care_hair_wash_reason": cadence.reason.value,
        "care_hair_wash_frequency": cadence.declared_frequency or "",
        "care_hair_last_wash_on": cadence.last_wash_on.isoformat() if cadence.last_wash_on else "",
        "care_hair_next_due_on": cadence.next_due_on.isoformat() if cadence.next_due_on else "",
        "care_routine_plan_version": care_plan.plan_version,
        "care_routine_effort": care_plan.resolved_effort.value,
        "care_routine_effort_source": care_plan.effort_source.value,
        "care_blocked_product_count": len(care_decision_set.blocked_product_ids),
        "care_confirmation_advisory_count": sum(
            1 for row in care_decision_set.product_decisions
            if any(
                reason.code == care_decisions.CareDecisionReasonCode.INGREDIENT_CONFIRMATION_NEEDED
                for reason in row.advisory_reasons
            )
        ),
    }


async def refresh_locked_care_if_needed(
    session: AsyncSession,
    existing: DailyPlan,
    context: DayContext,
    material: DayCareMaterial,
    current_key: str,
    *,
    trigger: str,
) -> bool:
    """Refresh only Care-owned rows for a locked plan when material changed."""
    stored = await stored_care_fingerprints(session, existing.id)
    if care_material_is_current(stored, material):
        return False

    module_material = await _module_material(
        session, context, care_context=material.care_context,
        decisions=material.decisions, care_plan=material.care_plan,
    )
    completed_before = await _completed_action_marks_for_modules(
        session, existing, {MODULE_SKINCARE, MODULE_HAIR, MODULE_MAINTENANCE},
    )

    old_actions = (await session.execute(
        select(DailyPlanAction).where(
            DailyPlanAction.plan_id == existing.id,
            DailyPlanAction.module.in_((MODULE_SKINCARE, MODULE_HAIR, MODULE_MAINTENANCE)),
        )
    )).scalars().all()
    for row in old_actions:
        await session.delete(row)

    old_inputs = (await session.execute(
        select(DailyPlanInput).where(
            DailyPlanInput.plan_id == existing.id,
            DailyPlanInput.input_type == "care",
        )
    )).scalars().all()
    for row in old_inputs:
        await session.delete(row)
    await session.flush()

    for key_name, value in care_input_values(material).items():
        session.add(DailyPlanInput(
            plan_id=existing.id, input_type="care", input_key=key_name,
            value=value, source="derived",
        ))
    for row in _care_today_actions(
        context, module_material, care_context=material.care_context,
        decisions=material.decisions, hair_wash_cadence=material.hair_wash_cadence,
        maintenance=material.maintenance,
    ):
        item_id = row.pop("inventory_item_id", None)
        session.add(DailyPlanAction(
            plan_id=existing.id,
            inventory_item_id=uuid.UUID(item_id) if isinstance(item_id, str) else item_id,
            completed_at=completed_before.get(
                (row["module"], row["action_type"], row["title"])
            ),
            **row,
        ))

    existing.version += 1
    existing.generated_from = PLAN_SOURCE_CACHE
    session.add(PlanRecalculationEvent(
        account_id=context.account_id, plan_date=context.plan_date, trigger=trigger,
        detail=CARE_REFRESH_DETAIL,
        old_cache_key=existing.cache_key, new_cache_key=current_key, recomputed=True,
    ))
    await session.flush()
    return True


async def _completed_action_marks(
    session: AsyncSession, plan: DailyPlan
) -> dict[tuple[str, str, str], datetime]:
    """When each of the day's finished actions was finished.

    Keyed by what the action is — module, type and title — because the rows
    themselves are about to be replaced. Two actions that read identically to
    the user are the same action as far as "I already did that" is concerned.
    """
    return await _completed_action_marks_for_modules(session, plan, None)


async def _completed_action_marks_for_modules(
    session: AsyncSession,
    plan: DailyPlan,
    modules: set[str] | None,
) -> dict[tuple[str, str, str], datetime]:
    """Carry completion across replacement for all or selected modules."""
    filters = [
        DailyPlanAction.plan_id == plan.id,
        DailyPlanAction.completed_at.is_not(None),
    ]
    if modules is not None:
        filters.append(DailyPlanAction.module.in_(modules))
    rows = (await session.execute(select(DailyPlanAction).where(*filters))).scalars().all()
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
