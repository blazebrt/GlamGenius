"""The metric formulas.

Every function here computes exactly one metric from the registry and returns a
:class:`MetricResult` carrying the value, the formula version, and **the actual
inputs it consumed**. That last part is what makes a metric reproducible: given
a stored event you can redo the arithmetic by hand and get the same number.

Three rules hold throughout.

**Missing data is never zero.** A metric that cannot be computed returns
``status="unavailable"`` with the inputs it was missing named. Returning 0.0
would render as a real, bad score, and nobody reading it could tell the
difference between "you have done nothing" and "we do not know yet".

**Nothing here reads another metric.** The registry forbids it and these
functions honour it, which is what keeps a composite score unbuildable.

**Nothing here judges.** ``inventory_balance`` returns counts and refuses to
score them; ``outfit_variety`` is marked neutral because repeating a favourite
combination is a perfectly good way to dress.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem, ItemUsageEvent
from app.domains.inventory.taxonomy import CATEGORIES
from app.domains.progress import registry
from app.domains.progress.models import ProgressGoal
from app.domains.recommendation.models import (
    Look,
    LookItem,
    OccasionRecord,
    RecommendationRun,
    StyleRequest,
)
from app.domains.recommendation.occasions import OCCASIONS

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_PARTIAL = "partial"

UTILISATION_WINDOW_DAYS = 90
VARIETY_WINDOW_DAYS = 30
CONSISTENCY_WINDOW_DAYS = 14
PURCHASE_WINDOW_DAYS = 180
PURCHASE_GRACE_DAYS = 30
EXPIRY_SOON_DAYS = 60
EXPIRY_IDLE_DAYS = 30
UPCOMING_WINDOW_DAYS = 30

# Categories that make an outfit complete. A "complete outfit" means one of
# each; accessories are optional by design, because insisting on a watch to
# call somebody dressed would be silly.
REQUIRED_OUTFIT_CATEGORIES = ("wardrobe", "shoes")

# What a trip needs, as a multiple of days plus fixed items.
TRAVEL_OUTFITS_PER_DAY = 1
TRAVEL_FIXED_REQUIREMENTS = ("shoes", "beauty")


@dataclass
class MetricResult:
    """One computed metric, with everything needed to reproduce and explain it."""

    key: str
    value: Optional[float]
    status: str
    inputs: dict[str, Any] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def definition(self) -> registry.MetricDefinition:
        return registry.METRIC_BY_KEY[self.key]

    def dedup_hash(self, account_id: uuid.UUID, period: str, period_start: date) -> str:
        """Identity of this computation.

        Covers the account, the metric, its formula version, the period and the
        exact inputs. Recomputing from unchanged data produces the same hash, so
        a job that runs twice writes one row.
        """
        payload = json.dumps({
            "account": str(account_id), "metric": self.key,
            "formula_version": self.definition.formula_version,
            "period": period, "period_start": period_start.isoformat(),
            "inputs": self.inputs, "value": self.value, "status": self.status,
        }, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        definition = self.definition
        return {
            "key": self.key,
            "label": definition.label,
            "value": self.value,
            "unit": definition.unit,
            "direction": definition.direction,
            "status": self.status,
            "formula": definition.formula,
            "formula_version": definition.formula_version,
            "explanation": definition.explanation,
            "not_a_measure_of": definition.not_a_measure_of,
            "update_frequency": definition.update_frequency,
            "inputs": self.inputs,
            "missing_inputs": self.missing_inputs,
            "note": self.note,
        }


def _unavailable(key: str, missing: Sequence[str], note: str) -> MetricResult:
    return MetricResult(
        key=key, value=None, status=STATUS_UNAVAILABLE,
        missing_inputs=list(missing), note=note,
    )


# --- Shared reads --------------------------------------------------------------


async def _items(session: AsyncSession, account_id: uuid.UUID) -> list[InventoryItem]:
    rows = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.account_id == account_id,
            InventoryItem.status == "active",
            InventoryItem.verification_state == "confirmed",
        )
    )).scalars().all()
    return list(rows)


async def _usage_since(
    session: AsyncSession, account_id: uuid.UUID, since: date
) -> dict[uuid.UUID, int]:
    rows = (await session.execute(
        select(ItemUsageEvent.item_id, func.count(ItemUsageEvent.id))
        .join(InventoryItem, InventoryItem.id == ItemUsageEvent.item_id)
        .where(InventoryItem.account_id == account_id, ItemUsageEvent.used_on >= since)
        .group_by(ItemUsageEvent.item_id)
    )).all()
    return {item_id: count for item_id, count in rows}


# --- The formulas ---------------------------------------------------------------


async def wardrobe_readiness(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    occasions = (await session.execute(
        select(OccasionRecord.occasion_key)
        .where(OccasionRecord.account_id == account_id)
        .distinct()
    )).scalars().all()
    keys = [row for row in occasions if row in OCCASIONS]

    definition = registry.METRIC_BY_KEY["wardrobe_readiness"]
    if len(keys) < definition.minimum_inputs:
        return _unavailable(
            "wardrobe_readiness", ["occasions"],
            f"We need at least {definition.minimum_inputs} occasions to work this out. "
            f"So far we know about {len(keys)}.",
        )

    items = await _items(session, account_id)
    by_category: dict[str, int] = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1

    # A complete outfit needs something in every required category. This is
    # coarse on purpose — a finer check would need formality matching, and
    # a readiness metric that silently depended on tags most people never fill
    # in would read as zero for reasons the user could not see.
    complete = all(by_category.get(category, 0) > 0 for category in REQUIRED_OUTFIT_CATEGORIES)
    covered = len(keys) if complete else 0

    return MetricResult(
        key="wardrobe_readiness",
        value=round(covered / len(keys), 4) if keys else None,
        status=STATUS_OK,
        inputs={
            "occasions_known": len(keys),
            "occasions_covered": covered,
            "required_categories": list(REQUIRED_OUTFIT_CATEGORIES),
            "category_counts": {key: by_category.get(key, 0) for key in REQUIRED_OUTFIT_CATEGORIES},
        },
        note=(
            "You can put a full outfit together from what you own."
            if complete else
            "Something is missing from a category every outfit needs."
        ),
    )


async def wardrobe_utilisation(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    since = today - timedelta(days=UTILISATION_WINDOW_DAYS)
    items = await _items(session, account_id)
    # Only items owned for the whole window. A shirt bought yesterday has not
    # had a chance to be worn, and counting it would drag the number down for
    # doing nothing wrong.
    eligible = [
        item for item in items
        if item.created_at is not None and item.created_at.date() <= since
    ]
    definition = registry.METRIC_BY_KEY["wardrobe_utilisation"]
    if len(eligible) < definition.minimum_inputs:
        return _unavailable(
            "wardrobe_utilisation", ["inventory", "usage"],
            f"We look at things you have owned for {UTILISATION_WINDOW_DAYS} days or more, and "
            f"there are {len(eligible)} of those so far.",
        )

    usage = await _usage_since(session, account_id, since)
    worn = sum(
        1 for item in eligible
        if usage.get(item.id, 0) > 0
        or (item.last_used_at is not None and item.last_used_at >= since)
    )
    return MetricResult(
        key="wardrobe_utilisation",
        value=round(worn / len(eligible), 4),
        status=STATUS_OK if usage else STATUS_PARTIAL,
        inputs={
            "window_days": UTILISATION_WINDOW_DAYS,
            "eligible_items": len(eligible),
            "items_worn": worn,
            "items_excluded_as_new": len(items) - len(eligible),
        },
        missing_inputs=[] if usage else ["usage"],
        note=(
            "" if usage else
            "Nothing logged as worn yet, so this is based only on last-used dates."
        ),
    )


async def outfit_variety(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    since = today - timedelta(days=VARIETY_WINDOW_DAYS)
    rows = (await session.execute(
        select(Look.id, Look.created_at)
        .where(Look.account_id == account_id, Look.created_at >= since)
    )).all()

    definition = registry.METRIC_BY_KEY["outfit_variety"]
    if len(rows) < definition.minimum_inputs:
        return _unavailable(
            "outfit_variety", ["looks"],
            f"We need at least {definition.minimum_inputs} logged outfits in the last "
            f"{VARIETY_WINDOW_DAYS} days. There are {len(rows)}.",
        )

    look_ids = [row[0] for row in rows]
    item_rows = (await session.execute(
        select(LookItem.look_id, LookItem.inventory_item_id)
        .where(LookItem.look_id.in_(look_ids), LookItem.inventory_item_id.is_not(None))
    )).all()

    by_look: dict[uuid.UUID, list[str]] = {}
    for look_id, item_id in item_rows:
        by_look.setdefault(look_id, []).append(str(item_id))

    combinations = {tuple(sorted(values)) for values in by_look.values() if values}
    days_logged = len({row[1].date() for row in rows if row[1] is not None})

    if not combinations or not days_logged:
        return _unavailable(
            "outfit_variety", ["looks"],
            "The outfits we have do not list which of your items were in them.",
        )

    return MetricResult(
        key="outfit_variety",
        value=round(min(1.0, len(combinations) / days_logged), 4),
        status=STATUS_OK,
        inputs={
            "window_days": VARIETY_WINDOW_DAYS,
            "distinct_combinations": len(combinations),
            "days_with_an_outfit": days_logged,
            "looks_considered": len(look_ids),
        },
        note="Neither high nor low is better here. It is shown because people ask.",
    )


async def occasion_preparedness(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    horizon = today + timedelta(days=UPCOMING_WINDOW_DAYS)
    upcoming = (await session.execute(
        select(OccasionRecord).where(
            OccasionRecord.account_id == account_id,
            OccasionRecord.event_date.is_not(None),
            OccasionRecord.event_date >= today,
            OccasionRecord.event_date <= horizon,
        )
    )).scalars().all()

    if not upcoming:
        return _unavailable(
            "occasion_preparedness", ["calendar"],
            "Nothing in the next 30 days that we know about. Add an event and this starts working.",
        )

    # A look built for this occasion counts as prepared. Joined through the
    # style request rather than matched on date, because a plan made last week
    # for next Friday still counts.
    prepared_ids = set((await session.execute(
        select(StyleRequest.occasion_id)
        .join(RecommendationRun, RecommendationRun.style_request_id == StyleRequest.id)
        .join(Look, Look.run_id == RecommendationRun.id)
        .where(StyleRequest.account_id == account_id, Look.status == "active")
        .distinct()
    )).scalars().all())
    prepared = sum(1 for occasion in upcoming if occasion.id in prepared_ids)

    return MetricResult(
        key="occasion_preparedness",
        value=round(prepared / len(upcoming), 4),
        status=STATUS_OK,
        inputs={
            "window_days": UPCOMING_WINDOW_DAYS,
            "upcoming_occasions": len(upcoming),
            "with_a_planned_outfit": prepared,
        },
    )


async def routine_consistency(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    from app.domains.routines.models import RoutineAdherence

    since = today - timedelta(days=CONSISTENCY_WINDOW_DAYS - 1)
    rows = (await session.execute(
        select(RoutineAdherence.done_on).where(
            RoutineAdherence.account_id == account_id,
            RoutineAdherence.completed.is_(True),
            RoutineAdherence.done_on >= since,
            RoutineAdherence.done_on <= today,
        ).distinct()
    )).scalars().all()

    from app.domains.routines.models import Routine

    has_routine = (await session.execute(
        select(func.count(Routine.id)).where(
            Routine.account_id == account_id, Routine.status == "active",
        )
    )).scalar_one()
    if not has_routine:
        return _unavailable(
            "routine_consistency", ["routines"],
            "No routine built yet, so there is nothing to be consistent with.",
        )

    days = len(set(rows))
    return MetricResult(
        key="routine_consistency",
        value=round(days / CONSISTENCY_WINDOW_DAYS, 4),
        status=STATUS_OK,
        inputs={
            "window_days": CONSISTENCY_WINDOW_DAYS,
            "days_with_a_completed_step": days,
        },
        note="Counted as days, so a heavy day does not paper over a gap.",
    )


async def product_expiry_risk(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    items = [row for row in await _items(session, account_id) if row.category in ("beauty", "hair", "supplements")]
    if not items:
        return _unavailable(
            "product_expiry_risk", ["inventory"],
            "No beauty, hair or supplement products recorded.",
        )

    dated = 0
    at_risk = 0
    undated = 0
    for item in items:
        details = await inventory_service.details_for(session, item)
        expiry = inventory_service.effective_expiry_from_details(details)
        if expiry is None:
            undated += 1
            continue
        dated += 1
        days = (expiry - today).days
        idle = item.last_used_at is None or (today - item.last_used_at).days >= EXPIRY_IDLE_DAYS
        if days < 0 or (days <= EXPIRY_SOON_DAYS and idle):
            at_risk += 1

    if not dated:
        return _unavailable(
            "product_expiry_risk", ["expiry"],
            f"None of your {len(items)} products have a date recorded. Adding one makes this work.",
        )

    return MetricResult(
        key="product_expiry_risk",
        value=round(at_risk / dated, 4),
        status=STATUS_OK if not undated else STATUS_PARTIAL,
        inputs={
            "products_with_a_date": dated,
            "products_at_risk": at_risk,
            "products_without_a_date": undated,
            "soon_days": EXPIRY_SOON_DAYS,
            "idle_days": EXPIRY_IDLE_DAYS,
        },
        missing_inputs=["expiry"] if undated else [],
        note=(
            f"{undated} product(s) have no date recorded and are not counted either way."
            if undated else ""
        ),
    )


async def value_to_recover(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    items = await _items(session, account_id)
    if not items:
        return _unavailable("value_to_recover", ["inventory"], "Nothing catalogued yet.")

    total = Decimal("0")
    priced = 0
    unpriced = 0
    for item in items:
        details = await inventory_service.details_for(session, item)
        estimate = inventory_service.value_to_recover(item, details, today)
        if estimate["estimated_value"] is None:
            unpriced += 1
            continue
        priced += 1
        total += Decimal(str(estimate["estimated_value"]))

    if not priced:
        return _unavailable(
            "value_to_recover", ["inventory"],
            f"None of your {len(items)} items have a price recorded, so there is nothing to estimate from.",
        )

    return MetricResult(
        key="value_to_recover",
        value=float(round(total, 2)),
        status=STATUS_OK if not unpriced else STATUS_PARTIAL,
        inputs={
            "items_with_a_price": priced,
            "items_without_a_price": unpriced,
            "metric_version": "v1",
        },
        missing_inputs=["inventory"] if unpriced else [],
        note="Using these things is what brings this down. It is not money you have lost.",
    )


async def purchase_efficiency(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    window_start = today - timedelta(days=PURCHASE_WINDOW_DAYS)
    grace = today - timedelta(days=PURCHASE_GRACE_DAYS)
    items = [
        item for item in await _items(session, account_id)
        if item.purchase_date is not None
        and window_start <= item.purchase_date <= grace
    ]
    definition = registry.METRIC_BY_KEY["purchase_efficiency"]
    if len(items) < definition.minimum_inputs:
        return _unavailable(
            "purchase_efficiency", ["shopping"],
            f"We look at things bought between {PURCHASE_GRACE_DAYS} and {PURCHASE_WINDOW_DAYS} days "
            f"ago, and there are {len(items)} of those with a purchase date recorded.",
        )

    used = sum(1 for item in items if item.usage_count >= 2)
    return MetricResult(
        key="purchase_efficiency",
        value=round(used / len(items), 4),
        status=STATUS_OK,
        inputs={
            "purchases_considered": len(items),
            "worn_at_least_twice": used,
            "window_days": PURCHASE_WINDOW_DAYS,
            "grace_days": PURCHASE_GRACE_DAYS,
        },
        note="Recent purchases are left out until they have had a fair chance.",
    )


async def inventory_balance(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    items = await _items(session, account_id)
    if not items:
        return _unavailable("inventory_balance", ["inventory"], "Nothing catalogued yet.")

    counts = {key: 0 for key in CATEGORIES}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    empty = [key for key, value in counts.items() if value == 0]

    # The value is a count of categories in use, never a balance score. There is
    # no correct shape for a wardrobe and scoring one would be a judgement.
    return MetricResult(
        key="inventory_balance",
        value=float(len(counts) - len(empty)),
        status=STATUS_OK,
        inputs={"category_counts": counts, "empty_categories": empty, "total_items": len(items)},
        note="Shown as counts. There is no target shape for a wardrobe, so nothing is scored.",
    )


async def travel_readiness(
    session: AsyncSession, account_id: uuid.UUID, *, today: date, trip_days: int = 3
) -> MetricResult:
    if trip_days < 1:
        return _unavailable("travel_readiness", ["planning"], "Tell us how many days the trip is.")

    items = await _items(session, account_id)
    counts: dict[str, int] = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1

    needed = {"wardrobe": trip_days * TRAVEL_OUTFITS_PER_DAY}
    for category in TRAVEL_FIXED_REQUIREMENTS:
        needed[category] = 1

    if not items:
        return _unavailable("travel_readiness", ["inventory"], "Nothing catalogued yet.")

    met = sum(1 for category, required in needed.items() if counts.get(category, 0) >= required)
    return MetricResult(
        key="travel_readiness",
        value=round(met / len(needed), 4),
        status=STATUS_OK,
        inputs={
            "trip_days": trip_days,
            "requirements": needed,
            "you_have": {key: counts.get(key, 0) for key in needed},
            "requirements_met": met,
        },
        note=f"Worked out for a {trip_days}-day trip.",
    )


# The Indian seasons this product plans around, and which months they cover.
SEASON_MONTHS = {
    "summer": (3, 4, 5, 6), "monsoon": (7, 8, 9),
    "autumn": (10, 11), "winter": (12, 1, 2),
}


def season_for(day: date) -> str:
    for season, months in SEASON_MONTHS.items():
        if day.month in months:
            return season
    return "summer"


async def seasonal_readiness(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    season = season_for(today)
    items = [row for row in await _items(session, account_id) if row.category in ("wardrobe", "shoes")]
    if not items:
        return _unavailable("seasonal_readiness", ["inventory"], "No clothes catalogued yet.")

    suitable: list[InventoryItem] = []
    for item in items:
        details = await inventory_service.details_for(session, item)
        tags = [str(row).lower() for row in (details.get("season") or [])]
        if not tags or season in tags or "all season" in tags or "all-season" in tags:
            suitable.append(item)

    definition = registry.METRIC_BY_KEY["seasonal_readiness"]
    if len(suitable) < definition.minimum_inputs:
        return _unavailable(
            "seasonal_readiness", ["inventory"],
            f"Only {len(suitable)} item(s) suit {season}. Below {definition.minimum_inputs} a "
            "fraction would look more precise than it really is.",
        )

    available = sum(1 for item in suitable if item.condition not in ("worn", "needs_attention"))
    return MetricResult(
        key="seasonal_readiness",
        value=round(available / len(suitable), 4),
        status=STATUS_OK,
        inputs={
            "season": season,
            "items_suiting_the_season": len(suitable),
            "available_to_wear": available,
            "clothes_considered": len(items),
        },
        note=f"It is {season}. Items with no season tag are counted as suitable.",
    )


async def no_buy_progress(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    goal = (await session.execute(
        select(ProgressGoal).where(
            ProgressGoal.account_id == account_id,
            ProgressGoal.kind == "no_buy",
            ProgressGoal.status == "active",
        ).order_by(ProgressGoal.starts_on.desc())
    )).scalars().first()

    if goal is None:
        return _unavailable(
            "no_buy_progress", ["goals"],
            "No no-buy period set. You can start one whenever you want to.",
        )

    purchases = (await session.execute(
        select(InventoryItem.purchase_date)
        .where(
            InventoryItem.account_id == account_id,
            InventoryItem.purchase_date.is_not(None),
            InventoryItem.purchase_date >= goal.starts_on,
            InventoryItem.purchase_date <= today,
        )
        .order_by(InventoryItem.purchase_date.desc())
    )).scalars().all()

    last_purchase = purchases[0] if purchases else None
    counting_from = last_purchase if last_purchase is not None else goal.starts_on
    days = max(0, (today - counting_from).days)

    return MetricResult(
        key="no_buy_progress",
        value=float(days),
        status=STATUS_OK,
        inputs={
            "goal_started_on": goal.starts_on.isoformat(),
            "counting_from": counting_from.isoformat(),
            "purchases_since_start": len(purchases),
            "last_purchase_on": last_purchase.isoformat() if last_purchase else None,
        },
        note=(
            f"Counting again from {counting_from.isoformat()}."
            if last_purchase else "Counting from the day you started."
        ),
    )


async def user_reported_confidence(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> MetricResult:
    from app.domains.progress.models import FeedbackEvent

    rows = (await session.execute(
        select(FeedbackEvent.reason).where(
            FeedbackEvent.account_id == account_id,
            FeedbackEvent.subject_type == "self_report",
        )
    )).scalars().all()

    values: list[float] = []
    for row in rows:
        try:
            values.append(float(row))
        except (TypeError, ValueError):
            continue

    definition = registry.METRIC_BY_KEY["user_reported_confidence"]
    if len(values) < definition.minimum_inputs:
        return _unavailable(
            "user_reported_confidence", ["self_report"],
            f"We only show this once you have recorded it {definition.minimum_inputs} times. "
            f"So far: {len(values)}. We never guess this one.",
        )

    return MetricResult(
        key="user_reported_confidence",
        value=round(sum(values) / len(values), 2),
        status=STATUS_OK,
        inputs={"ratings_recorded": len(values), "scale": "1-5"},
        note="Your own ratings, averaged. Nothing else feeds this.",
    )


COMPUTERS = {
    "wardrobe_readiness": wardrobe_readiness,
    "wardrobe_utilisation": wardrobe_utilisation,
    "outfit_variety": outfit_variety,
    "occasion_preparedness": occasion_preparedness,
    "routine_consistency": routine_consistency,
    "product_expiry_risk": product_expiry_risk,
    "value_to_recover": value_to_recover,
    "purchase_efficiency": purchase_efficiency,
    "inventory_balance": inventory_balance,
    "travel_readiness": travel_readiness,
    "seasonal_readiness": seasonal_readiness,
    "no_buy_progress": no_buy_progress,
    "user_reported_confidence": user_reported_confidence,
}

# Every metric in the registry must have a computer, and every computer must be
# a registered metric. Checked at import so the two cannot drift.
_missing = set(registry.METRIC_KEYS) - set(COMPUTERS)
_extra = set(COMPUTERS) - set(registry.METRIC_KEYS)
if _missing or _extra:
    raise AssertionError(
        f"Metric registry and computers disagree. Missing: {sorted(_missing)}. Extra: {sorted(_extra)}."
    )


async def compute(
    session: AsyncSession, account_id: uuid.UUID, key: str, *, today: Optional[date] = None, **kwargs: Any
) -> MetricResult:
    if key not in COMPUTERS:
        raise ValueError(f"'{key}' is not a metric we compute.")
    return await COMPUTERS[key](session, account_id, today=today or date.today(), **kwargs)


async def compute_all(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> list[MetricResult]:
    day = today or date.today()
    return [await COMPUTERS[key](session, account_id, today=day) for key in registry.METRIC_KEYS]
