"""Proactive notifications, kept rare on purpose.

The default is **one** appearance notification per day. That is a product
decision enforced in code, not a suggestion in a settings screen: an app that
tells you about your face every few hours is one people delete.

Three gates, in order, and every decision is written down — including the
suppressed ones, so "why didn't I hear about X" is answerable:

1. **Deduplication.** A stable hash of account, date and content. The same
   notification can be queued a hundred times and sends once.
2. **The daily cap.** Default 1.
3. **Quiet hours.** Default 21:00–07:00 local, which is the user's local time,
   not the server's.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.environment_decision import evaluate_environment
from app.domains.planning import clock
from app.domains.planning.models import (
    MODULE_MAINTENANCE,
    MODULE_SKINCARE,
    MODULES,
    DailyPlan,
    DailyPlanAction,
    NotificationDelivery,
    NotificationDevice,
    NotificationPreference,
)
from app.shared.database.base import utcnow

SUPPRESSED_DUPLICATE = "duplicate"
SUPPRESSED_CAP = "daily_cap_reached"
SUPPRESSED_QUIET = "quiet_hours"
SUPPRESSED_DISABLED = "disabled"
SUPPRESSED_MODULE_OFF = "module_disabled"
#: Settled by the worker's final send-authority gate: the account stopped being
#: active after this delivery was claimed and before the provider was called.
#: Nothing was sent. The row is terminal, so it is never claimed again.
SUPPRESSED_ACCOUNT_INACTIVE = "account_inactive"
#: A Product Watch delivery whose watch epoch ended before it reached the
#: provider: the customer stopped, restarted or re-anchored the watch. The
#: watch lifecycle settles it in that same transaction; recovery and the final
#: gate settle it too, if they find one. Nothing was sent, and the row is
#: terminal.
SUPPRESSED_WATCH_ENDED = "watch_ended"
STATUS_SUPPRESSED = "suppressed"
STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_PROVIDER_ACCEPTED = "provider_accepted"
STATUS_PROVIDER_FAILED = "provider_failed"
STATUS_RECEIPT_OK = "receipt_ok"
STATUS_RECEIPT_FAILED = "receipt_failed"

#: How long a ``sending`` claim is owned before another cycle may treat it as
#: abandoned. The lease is wall-clock time, so it is always read from
#: :func:`utcnow`, never from the local moment a cycle is deciding for.
CLAIM_LEASE_SECONDS = 300

#: Suppression reasons that speak for one candidate only. Each is the
#: customer's own choice about one kind of notification: that choice is
#: recorded for the candidate, and the day's single chance passes on to the
#: next candidate. Every other reason — the master switch, quiet hours, the
#: daily cap, an inactive account, and any reason not listed here — applies to
#: every notification this account could get at this moment, so the worker
#: stops there. Unknown reasons stop: the safe mistake is one missed
#: notification, never a second one.
CANDIDATE_SUPPRESSIONS = frozenset({SUPPRESSED_MODULE_OFF})


def is_candidate_opt_out(delivery: Any) -> bool:
    """Whether this decision was suppressed for this candidate alone."""
    return (
        getattr(delivery, "status", None) == STATUS_SUPPRESSED
        and getattr(delivery, "suppressed_reason", None) in CANDIDATE_SUPPRESSIONS
    )

#: Modules a new account is notified about by default. Maintenance sits here
#: like any other module: its real gate is the per-kind ``reminders_enabled``
#: choice, which defaults to off. Excluding it from this map instead would make
#: that per-kind opt-in unreachable, because nothing in the product turns the
#: generic module flag back on.
DEFAULT_MODULE_NOTIFICATIONS: dict[str, bool] = {module: True for module in MODULES}

# Customer-facing switches. These are deliberately not the Planning MODULES
# map: an unknown topic must never silently become enabled.
#
# ``product_watch`` (Step 12C) defaults on in this map because a Product Watch
# notice can only exist for a pack the customer explicitly chose to watch; the
# watch itself is the opt-in. The master switch, native push consent and the OS
# permission all remain separate and authoritative.
NOTIFICATION_TOPICS = ("today_style", "care", "event_preparation", "maintenance", "product_watch")
DEFAULT_TOPIC_NOTIFICATIONS: dict[str, bool] = {topic: True for topic in NOTIFICATION_TOPICS}
PRODUCT_WATCH_TOPIC = "product_watch"

#: The only barcode a notification may carry into ``/verdict``: a GTIN of 8 to
#: 14 digits. Deliberately narrower than what a scan accepts, because a push
#: payload is untrusted the moment it leaves the server and this is what the
#: app will route on.
VERDICT_BARCODE = re.compile(r"[0-9]{8,14}")


def topic_for_candidate(candidate: Any) -> str | None:
    """Map one agenda item to exactly one typed customer topic."""
    source_kind = getattr(candidate, "source_kind", None)
    domain = getattr(candidate, "domain", None)
    provenance = getattr(candidate, "provenance", {}) or {}
    action_key = provenance.get("action_key", "")
    if source_kind in {"event_ready_action", "event_preparation_entry"}:
        if domain == "maintenance" or action_key.startswith("maintenance:"):
            return "maintenance"
        if domain in {"event", "preparation", "style", "care"}:
            return "event_preparation"
        return None
    if source_kind != "today_action":
        return None
    if domain == "maintenance":
        return "maintenance"
    if domain in {"skincare", "hair", "care", "perfume"}:
        return "care"
    if domain in {"style", "outfit", "shopping", "wardrobe", "shoes", "accessories"}:
        return "today_style"
    return None


def _target(destination: str | None, params: dict[str, Any] | None) -> tuple[str | None, dict[str, str]]:
    """Keep only server-owned destinations and their narrow routing data."""
    allowed = {"/(tabs)/today", "/(tabs)/style", "/(tabs)/care", "/(tabs)/plan", "/event-ready", "/improve", "/(tabs)/services", "/(tabs)/inventory", "/verdict"}
    if destination not in allowed:
        return None, {}
    if destination == "/verdict":
        # Step 12C: a watched product opens on its own verdict, and nothing
        # else rides along — no source URL, no snapshot, no account, no query.
        # A missing or malformed barcode drops the destination entirely, so the
        # app falls back to a safe screen rather than routing on a guess.
        barcode = (params or {}).get("barcode")
        if not isinstance(barcode, str) or not VERDICT_BARCODE.fullmatch(barcode):
            return None, {}
        return destination, {"barcode": barcode}
    if destination == "/event-ready":
        event_id = (params or {}).get("eventId")
        if not isinstance(event_id, str) or not event_id:
            return "/(tabs)/plan", {}
        return destination, {"eventId": event_id}
    if destination == "/(tabs)/care" and (params or {}).get("routine_action") == "complete_step":
        step_id = (params or {}).get("step_id")
        done_on = (params or {}).get("done_on")
        if not isinstance(step_id, str) or not isinstance(done_on, str):
            return destination, {}
        return destination, {
            "routine_action": "complete_step", "step_id": step_id, "done_on": done_on,
            "category_id": "routine-adherence",
        }
    return destination, {}


async def maintenance_reminders_allowed(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date,
) -> Sequence[Any]:
    """The due kinds this account has explicitly asked to be reminded about.

    Consent is read from canonical maintenance state rather than inferred from
    the module appearing in a plan, and it is returned per kind rather than as
    an account-wide boolean so the notification can name only what was opted
    into. Empty means no maintenance notification is permitted at all.
    """
    from app.domains.care import maintenance as care_maintenance
    from app.domains.care import maintenance_service as care_maintenance_service

    decided = await care_maintenance_service.build_maintenance(
        session, account_id, plan_date=plan_date,
    )
    return care_maintenance.reminder_eligible(decided)


async def preferences_for(session: AsyncSession, account_id: uuid.UUID, timezone_name: str, *, lock: bool = False) -> NotificationPreference:
    statement = select(NotificationPreference).where(NotificationPreference.account_id == account_id)
    if lock:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        row = NotificationPreference(
            account_id=account_id, timezone_name=timezone_name,
            modules=dict(DEFAULT_MODULE_NOTIFICATIONS), native_push_enabled=False,
        )
        session.add(row)
        await session.flush()
    return row


def dedup_hash(account_id: uuid.UUID, plan_date: date, notification_key: str, title: str) -> str:
    """What makes two notifications the same notification.

    Content is included as well as the key, so a plan that genuinely changed can
    notify again, while a plan recomputed to the same answer cannot.
    """
    raw = f"{account_id}|{plan_date.isoformat()}|{notification_key}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Quiet hours, handling the normal case of a window crossing midnight."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def _sent_today(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date,
    *, excluding: uuid.UUID | None = None,
) -> int:
    """How many of today's deliveries count against the daily cap.

    ``excluding`` leaves one row out: a delivery being recovered must not be
    refused because it is itself one of today's ``sending`` rows.
    """
    statement = select(func.count()).select_from(NotificationDelivery).where(
        NotificationDelivery.account_id == account_id,
        NotificationDelivery.plan_date == plan_date,
        NotificationDelivery.status.in_((STATUS_QUEUED, STATUS_SENDING, STATUS_PROVIDER_ACCEPTED, STATUS_PROVIDER_FAILED)),
    )
    if excluding is not None:
        statement = statement.where(NotificationDelivery.id != excluding)
    return int((await session.execute(statement)).scalar_one())


async def cap_permits(
    session: AsyncSession, preference: NotificationPreference, plan_date: date,
    *, excluding: uuid.UUID | None = None,
) -> bool:
    """Whether the daily cap leaves room for one more delivery today."""
    return await _sent_today(session, preference.account_id, plan_date, excluding=excluding) < preference.daily_cap


async def queue(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_date: date,
    notification_key: str,
    title: str,
    body: str = "",
    module: str = "outfit",
    timezone_name: str = clock.DEFAULT_TIMEZONE,
    moment=None,
    deep_link: str | None = None,
    destination_params: dict[str, Any] | None = None,
    topic: str | None = None,
    source_kind: str | None = None,
    source_id: str | None = None,
    scheduled_for: datetime | None = None,
) -> NotificationDelivery:
    """Decide about one notification and record the decision either way."""
    preference = await preferences_for(session, account_id, timezone_name, lock=True)
    digest = dedup_hash(account_id, plan_date, notification_key, title)

    existing = (await session.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.dedup_hash == digest,
        )
    )).scalar_one_or_none()
    if existing is not None:
        # Already decided. Return the original decision rather than making a
        # second one — this is what makes queueing idempotent.
        return existing

    deep_link, destination_params = _target(deep_link, destination_params)
    row = NotificationDelivery(
        account_id=account_id, plan_date=plan_date, notification_key=notification_key,
        dedup_hash=digest, title=title, body=body, status=STATUS_QUEUED,
        deep_link=deep_link, destination_params=destination_params or {}, source_kind=source_kind, source_id=source_id,
        scheduled_for=scheduled_for,
    )

    local_hour = clock.local_now(preference.timezone_name or timezone_name, moment=moment).hour
    # Suppression is an ordered, mutually-exclusive chain.  A higher
    # authority (for example the master switch) must never be overwritten by a
    # later quiet-hours or cap check.
    if not preference.enabled:
        row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_DISABLED
    else:
        selected_topic = topic if topic in NOTIFICATION_TOPICS else None
        topic_preferences = preference.topics or {}
        if topic is not None and selected_topic is None:
            row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_MODULE_OFF
        else:
            if selected_topic is None:
                # Legacy callers are mapped conservatively; arbitrary planner
                # module names do not bypass the typed topic layer.
                selected_topic = module if module in NOTIFICATION_TOPICS else "today_style"
            if not bool(topic_preferences.get(selected_topic, DEFAULT_TOPIC_NOTIFICATIONS[selected_topic])):
                row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_MODULE_OFF
            elif module in MODULES and preference.modules is not None and module in preference.modules and not bool(preference.modules[module]):
                # Preserve old preference JSON while keeping topic semantics primary.
                row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_MODULE_OFF
            elif in_quiet_hours(local_hour, preference.quiet_hours_start, preference.quiet_hours_end):
                row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_QUIET
            elif await _sent_today(session, account_id, plan_date) >= preference.daily_cap:
                row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_CAP

    session.add(row)
    await session.flush()
    return row


async def queue_for_plan(
    session: AsyncSession, *, plan: DailyPlan, timezone_name: str, moment=None
) -> NotificationDelivery | None:
    """The single daily notification, built from the plan's top action.

    One notification carrying the most important thing, rather than one per
    module. If the plan has nothing worth saying, nothing is queued at all.
    """
    if plan.status != "ready":
        return None
    candidates = (await session.execute(
        select(DailyPlanAction)
        .where(DailyPlanAction.plan_id == plan.id)
        .order_by(DailyPlanAction.priority)
    )).scalars().all()

    action = None
    eligible_kinds: Sequence[Any] | None = None
    body: str | None = None
    for candidate in candidates:
        if candidate.module == MODULE_MAINTENANCE:
            # Maintenance reminders are a per-kind opt-in. Without one we move
            # on to the next action rather than silently sending maintenance
            # text or dropping the day's notification altogether.
            if eligible_kinds is None:
                eligible_kinds = await maintenance_reminders_allowed(
                    session, plan.account_id, plan.plan_date,
                )
            if not eligible_kinds:
                continue
            # The plan's card names every due kind. The notification must name
            # only the ones the customer asked to hear about, so the copy is
            # rebuilt from the opted-in subset rather than reused.
            from app.domains.care.maintenance import maintenance_headline

            title, card_body = maintenance_headline(eligible_kinds)
            body = f"{title}. {card_body}".strip()
        action = candidate
        break
    if action is None:
        return None
    return await queue(
        session, account_id=plan.account_id, plan_date=plan.plan_date,
        notification_key="daily_plan", title=plan.headline,
        body=body if body is not None else f"{action.title}. {action.body}".strip(),
        module=action.module, timezone_name=timezone_name, moment=moment,
    )


async def queue_for_environment_crossing(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """Notify only when the air actually crossed a band, in either direction.

    A daily "the air is bad" message trains people to ignore it. This fires on
    the day something changed — into Poor or worse, or back out of it — and
    stays silent through the middle of a long stretch.

    The crossing decides *whether* to queue. Everything about *when* a person
    is reachable — the hourly batch, the due check, quiet hours, the daily cap
    and the claim-before-send boundary — belongs to the worker and to
    :func:`queue`, and is untouched here.
    """
    from app.domains.care import environment_service
    from app.domains.planning.environment import naqi_at_least

    window = await environment_service.load_window(
        session, account_id=account_id, plan_date=plan_date,
    )
    today = window.today
    yesterday = window.history[-1] if window.history else None
    if not today.is_indian_reading or yesterday is None or not yesterday.is_indian_reading:
        return None
    today_bad = naqi_at_least(today.category, "Poor")
    yesterday_bad = naqi_at_least(yesterday.category, "Poor")
    if today_bad == yesterday_bad:
        return None
    allowed = await environment_service.allowed_environment_rule_ids(session)
    decision = evaluate_environment(window, allowed_rule_ids=allowed)
    if decision is None:
        return None
    return await queue(
        session, account_id=account_id, plan_date=plan_date,
        notification_key=f"environment_crossing:{today.category}",
        title=decision.headline, body=decision.reason,
        module=MODULE_SKINCARE, topic="care", timezone_name=timezone_name, moment=moment,
    )


async def queue_for_running_out(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """Queue one evidence-based low-supply reminder.

    A low percentage alone is not a usage-rate signal.  We require recorded
    usage events as well as a current, user-recorded remaining percentage, so
    a never-used or guessed shelf item cannot create a purchase nudge.
    """
    from app.domains.inventory.models import (
        BeautyProductDetail,
        HairProductDetail,
        InventoryItem,
        ItemUsageEvent,
    )
    from app.domains.planning import notification_strings as strings

    usage_counts = (
        select(ItemUsageEvent.item_id.label("item_id"), func.sum(ItemUsageEvent.quantity).label("uses"))
        .group_by(ItemUsageEvent.item_id)
        .subquery()
    )
    rows = (await session.execute(
        select(InventoryItem, BeautyProductDetail.remaining_percent, HairProductDetail.remaining_percent, usage_counts.c.uses)
        .outerjoin(BeautyProductDetail, BeautyProductDetail.item_id == InventoryItem.id)
        .outerjoin(HairProductDetail, HairProductDetail.item_id == InventoryItem.id)
        .join(usage_counts, usage_counts.c.item_id == InventoryItem.id)
        .where(InventoryItem.account_id == account_id, InventoryItem.status == "active")
        .order_by(usage_counts.c.uses.desc(), InventoryItem.display_name)
    )).all()
    for item, beauty_remaining, hair_remaining, uses in rows:
        remaining = beauty_remaining if beauty_remaining is not None else hair_remaining
        if remaining is None or remaining > 15 or not uses:
            continue
        return await queue(
            session, account_id=account_id, plan_date=plan_date,
            notification_key=f"running_low:{item.id}:{remaining}",
            title=strings.RUNNING_LOW_TITLE.format(name=item.display_name),
            body=strings.RUNNING_LOW_BODY.format(uses=int(uses), remaining=remaining),
            module=MODULE_SKINCARE, topic="care", timezone_name=timezone_name, moment=moment,
            deep_link="/(tabs)/care", source_kind="running_low", source_id=str(item.id),
        )
    return None


async def queue_for_protocol_day(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """Offer the first due routine step with idempotent Done / Skip actions."""
    from app.domains.planning import notification_strings as strings
    from app.domains.routines import service as routines_service

    today = await routines_service.routines_today(session, account_id=account_id, on=plan_date)
    for routine in today["routines"]:
        for step in routine["steps"]:
            if step.get("is_gap") or step.get("completed_today"):
                continue
            return await queue(
                session, account_id=account_id, plan_date=plan_date,
                notification_key=f"routine_due:{routine['id']}:{step['slot']}",
                title=strings.ROUTINE_DUE_TITLE.format(routine=routine["label"]),
                body=strings.ROUTINE_DUE_BODY.format(step=step["label"]),
                module=MODULE_SKINCARE, topic="care", timezone_name=timezone_name, moment=moment,
                deep_link="/(tabs)/care", source_kind="routine_due", source_id=step["id"],
                destination_params={
                    "routine_action": "complete_step", "step_id": step["id"],
                    "done_on": plan_date.isoformat(), "category_id": "routine-adherence",
                },
            )
    return None


async def queue_for_deferred_purchase_relevance(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """Revisit a Care purchase only after its recorded environmental wait clears."""
    from app.domains.planning import notification_strings as strings
    from app.domains.purchase import check_service
    from app.domains.recommendation.models import PurchaseDecision

    rows = (await session.execute(
        select(PurchaseDecision).where(
            PurchaseDecision.account_id == account_id,
            PurchaseDecision.strategy_key == "care_purchase",
            PurchaseDecision.decision == "waiting",
        ).order_by(PurchaseDecision.updated_at.desc())
    )).scalars().all()
    for row in rows:
        snapshot = row.recommendation_snapshot or {}
        # Only a recorded temporary environmental deferment is eligible.  A
        # generic wait is intentionally not reinterpreted by the worker.
        historical_environment = snapshot.get("environment") or {}
        if not historical_environment.get("currently_deferred"):
            continue
        try:
            current = await check_service.resolve_care_purchase_check(
                session, account_id=account_id, account_id_str=str(account_id),
                candidate_id=row.candidate_id, plan_date=plan_date,
            )
        except Exception:  # A removed/untrusted candidate is never a prompt.
            continue
        current_environment = (current.get("verdict") or {}).get("environment") or {}
        if current_environment.get("currently_deferred"):
            continue
        return await queue(
            session, account_id=account_id, plan_date=plan_date,
            notification_key=f"purchase_relevant:{row.id}",
            title=strings.PURCHASE_RELEVANT_TITLE,
            body=strings.PURCHASE_RELEVANT_BODY,
            module=MODULE_SKINCARE, topic="care", timezone_name=timezone_name, moment=moment,
            deep_link="/(tabs)/care", source_kind="purchase_relevance", source_id=str(row.candidate_id),
        )
    return None


async def queue_for_product_watch(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """At most one material Product Watch notice, decided by Step 12C.

    The facts come from Step 12A and Step 12B, the matching from the official
    records matcher, and the decision about delivery from :func:`queue` like
    every other notification here. This function is only the door.
    """
    from app.domains.product import watch as product_watch

    return await product_watch.queue_material_notice(
        session, account_id=account_id, plan_date=plan_date,
        timezone_name=timezone_name, moment=moment,
    )


async def queue_for_agenda(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """Queue the first genuinely useful item from the typed Attention Agenda."""
    from app.domains.planning.agenda import build_agenda

    agenda = await build_agenda(session, account_id, generated_for=plan_date, timezone_name=timezone_name)
    candidate = None
    preference = await preferences_for(session, account_id, timezone_name)
    eligible_kinds: Sequence[Any] | None = None
    body = None
    title = None
    for item in agenda.items:
        if not item.notification_eligible:
            continue
        topic = topic_for_candidate(item)
        if topic is None:
            continue
        topic_preferences = preference.topics or {}
        if not bool(topic_preferences.get(topic, DEFAULT_TOPIC_NOTIFICATIONS[topic])):
            # A disabled high-ranked item must not consume the day's chance;
            # continue to the next typed candidate instead.
            continue
        if item.domain in MODULES and preference.modules is not None and item.domain in preference.modules and not bool(preference.modules[item.domain]):
            continue
        if topic == "maintenance":
            if eligible_kinds is None:
                eligible_kinds = await maintenance_reminders_allowed(session, account_id, plan_date)
            if not eligible_kinds:
                continue
            from app.domains.care.maintenance import maintenance_headline
            title, card_body = maintenance_headline(eligible_kinds)
            body = f"{title}. {card_body}".strip()
        candidate = item
        break
    if candidate is None:
        return None
    topic = topic_for_candidate(candidate)
    return await queue(
        session, account_id=account_id, plan_date=plan_date,
        notification_key=candidate.key, title=title or candidate.title, body=body or candidate.body,
        module=candidate.domain, topic=topic, timezone_name=timezone_name, moment=moment,
        deep_link=candidate.destination, source_kind=candidate.source_kind,
        destination_params=candidate.destination_params, source_id=candidate.source_id,
        scheduled_for=moment,
    )


def _lease_expired_before(lease_seconds: int) -> datetime:
    now = utcnow()
    return datetime.fromtimestamp(now.timestamp() - lease_seconds, tz=now.tzinfo)


async def claim_delivery(
    session: AsyncSession, delivery_id: uuid.UUID, *, lease_seconds: int = CLAIM_LEASE_SECONDS,
) -> str | None:
    """Atomically claim an outbox row: the right to attempt it, nothing more.

    A claim is not an attempt. It sets ``status = sending``, a fresh
    ``claim_token`` and ``claimed_at``, and leaves ``attempted_at`` empty.
    ``attempted_at`` is written separately, and only by
    :func:`mark_attempt_started`, immediately before the provider is called.
    That keeps two states apart that look alike from the outside:

    * claimed, never attempted (``attempted_at IS NULL``): the provider was
      certainly not reached, so once the lease expires the row may be claimed
      again;
    * attempted (``attempted_at IS NOT NULL``): the provider may have accepted
      it. The outcome is unknown, so the row is never claimed again, however
      old its lease. A missed notification is preferred to a duplicate.
    """
    now = utcnow()
    token = uuid.uuid4().hex
    result = await session.execute(update(NotificationDelivery).where(
        NotificationDelivery.id == delivery_id,
        or_(NotificationDelivery.status == STATUS_QUEUED,
            and_(NotificationDelivery.status == STATUS_SENDING,
                 NotificationDelivery.attempted_at.is_(None),
                 NotificationDelivery.claimed_at < _lease_expired_before(lease_seconds))),
    ).values(status=STATUS_SENDING, claim_token=token, claimed_at=now))
    if not result.rowcount:
        return None
    await session.flush()
    return token


async def mark_attempt_started(session: AsyncSession, delivery_id: uuid.UUID, claim_token: str) -> bool:
    """Record that the provider is about to be called for this claim.

    The durable line between "certainly not sent" and "may have been sent".
    It succeeds only for the exact claim that owns the row, and only once:
    ``status = sending``, the same ``claim_token``, and no attempt recorded
    yet. The caller commits it before the provider request, and holds no
    transaction or lock across that request.
    """
    result = await session.execute(update(NotificationDelivery).where(
        NotificationDelivery.id == delivery_id,
        NotificationDelivery.status == STATUS_SENDING,
        NotificationDelivery.claim_token == claim_token,
        NotificationDelivery.attempted_at.is_(None),
    ).values(attempted_at=utcnow()))
    return bool(result.rowcount)


async def abandoned_claims(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date, source_kind: str,
    lease_seconds: int = CLAIM_LEASE_SECONDS,
) -> list[NotificationDelivery]:
    """Today's ``sending`` rows of one source whose claim lease has expired.

    Attempted and unattempted alike, oldest first; the caller tells them apart
    by ``attempted_at``. Only ``plan_date`` is searched, so an abandoned claim
    from an earlier day is never found here and never sent late.
    """
    return list((await session.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.plan_date == plan_date,
            NotificationDelivery.source_kind == source_kind,
            NotificationDelivery.status == STATUS_SENDING,
            NotificationDelivery.claimed_at < _lease_expired_before(lease_seconds),
        ).order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        .execution_options(populate_existing=True)
    )).scalars().all())


def _still_abandoned(row: NotificationDelivery, lease_seconds: int):
    """The exact abandoned, never-attempted claim ``row`` was read as."""
    return and_(
        NotificationDelivery.id == row.id,
        NotificationDelivery.status == STATUS_SENDING,
        NotificationDelivery.attempted_at.is_(None),
        NotificationDelivery.claim_token.is_(None) if row.claim_token is None
        else NotificationDelivery.claim_token == row.claim_token,
        NotificationDelivery.claimed_at < _lease_expired_before(lease_seconds),
    )


async def requeue_abandoned_claim(
    session: AsyncSession, row: NotificationDelivery, *, lease_seconds: int = CLAIM_LEASE_SECONDS,
) -> bool:
    """Return a re-proved, never-attempted abandoned claim to ``queued``.

    Only if it is still exactly the claim it was read as. The caller then
    claims it through :func:`claim_delivery` in the same transaction, so the
    row is never committed as ``queued`` and a crash before that commit leaves
    it abandoned and recoverable, as before. Two cycles recovering the same
    row serialise on it: the second finds the first one's fresh claim, which
    is no longer abandoned, and changes nothing.
    """
    result = await session.execute(
        update(NotificationDelivery).where(_still_abandoned(row, lease_seconds))
        .values(status=STATUS_QUEUED, claim_token=None, claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        return False
    await session.refresh(row)
    return True


async def settle_abandoned_claim(
    session: AsyncSession, row: NotificationDelivery, reason: str,
    *, lease_seconds: int = CLAIM_LEASE_SECONDS,
) -> bool:
    """Close a never-attempted abandoned claim that may no longer be sent.

    Terminal and unclaimed, with the reason written down, so it stops counting
    against the day's cap and is never looked at again. Only if it is still
    exactly the claim it was read as.
    """
    result = await session.execute(
        update(NotificationDelivery).where(_still_abandoned(row, lease_seconds))
        .values(status=STATUS_SUPPRESSED, suppressed_reason=reason, claim_token=None, claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        return False
    await session.refresh(row)
    return True


async def withdraw_unattempted(
    session: AsyncSession, account_id: uuid.UUID, *, source_kind: str, reason: str,
    source_id: str | None = None,
) -> int:
    """Settle, as not sent, every delivery of one source that has not reached the provider.

    For a customer decision that takes away the authority to send, in the same
    transaction as that decision and with the caller already holding the lock
    that decision is made under:

    * an opt-out, under the preference lock — every delivery of the source;
    * a watch leaving its epoch (stopped, reactivated, re-anchored), under the
      watch row's lock — only the deliveries for that one ``source_id``.

    A row is withdrawn only while nothing can have been sent: ``queued``, or
    ``sending`` with no attempt recorded. Its claim lease does not matter — a
    claim made a moment ago is withdrawn too, because once this commits the
    worker's final gate finds the row no longer its own and does not reach the
    provider. It becomes terminal, unclaimed, with the reason written down, so
    nothing that happens afterwards can revive it.

    An attempted row (``attempted_at IS NOT NULL``) is never touched, nor is
    any row the provider already answered for. The provider may already have
    it; a decision after that moment can stop what comes next, but it cannot
    prove this one was not sent.
    """
    conditions = [
        NotificationDelivery.account_id == account_id,
        NotificationDelivery.source_kind == source_kind,
        NotificationDelivery.status.in_((STATUS_QUEUED, STATUS_SENDING)),
        NotificationDelivery.attempted_at.is_(None),
    ]
    if source_id is not None:
        conditions.append(NotificationDelivery.source_id == source_id)
    result = await session.execute(
        update(NotificationDelivery).where(*conditions)
        .values(status=STATUS_SUPPRESSED, suppressed_reason=reason, claim_token=None, claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def withdraw_claim(
    session: AsyncSession, delivery_id: uuid.UUID, claim_token: str, reason: str,
) -> bool:
    """Settle one claim, before its attempt, as not sent.

    Only the exact claim that owns the row, still ``sending`` and never
    attempted. Terminal and unclaimed, so it is never claimed again.
    """
    result = await session.execute(
        update(NotificationDelivery).where(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.status == STATUS_SENDING,
            NotificationDelivery.claim_token == claim_token,
            NotificationDelivery.attempted_at.is_(None),
        ).values(status=STATUS_SUPPRESSED, suppressed_reason=reason, claim_token=None, claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


async def locked_preference(session: AsyncSession, account_id: uuid.UUID) -> NotificationPreference | None:
    """This account's preference row, locked, as committed now. Never creates one.

    ``populate_existing``: a row this session loaded earlier is overwritten
    with what the lock returned, so a change committed in between is what the
    caller decides on.
    """
    return (await session.execute(
        select(NotificationPreference).where(NotificationPreference.account_id == account_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def devices_for_attempt(session: AsyncSession, account_id: uuid.UUID) -> list[NotificationDevice]:
    """This account's active devices as they are now, held until the caller commits.

    ``FOR SHARE``: removing a device — an unregister, or another account taking
    over the same push token — either commits before this read, and is seen,
    or waits until the caller's transaction ends. ``populate_existing``, so a
    token rotated since this session first loaded the row is the token used.
    """
    return list((await session.execute(
        select(NotificationDevice).where(
            NotificationDevice.account_id == account_id, NotificationDevice.status == "active",
            NotificationDevice.disabled_at.is_(None),
        ).order_by(NotificationDevice.id)
        .with_for_update(read=True).execution_options(populate_existing=True)
    )).scalars().all())


async def register_device(
    session: AsyncSession, account_id: uuid.UUID, *, device_key: str,
    platform: str, expo_push_token: str,
) -> NotificationDevice:
    """Idempotently register/rotate one account-owned device token."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = utcnow()
    # A token can be registered by two accounts before either account has a
    # row to lock. A transaction-scoped advisory lock gives every handoff for
    # that token one PostgreSQL rendezvous without logging the token or key.
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(CAST(:lock_key AS text), 0))"
        ),
        {"lock_key": f"notification-token:{expo_push_token}"},
    )
    # Token ownership is account-global. The partial unique index in the
    # migration is the final concurrency guard; this update performs the
    # atomic hand-off without exposing the previous owner to the caller.
    await session.execute(update(NotificationDevice).where(
        NotificationDevice.expo_push_token == expo_push_token,
        not_(and_(NotificationDevice.account_id == account_id, NotificationDevice.device_key == device_key)),
        NotificationDevice.status == "active",
    ).values(status="disabled", disabled_at=now))
    await session.execute(pg_insert(NotificationDevice).values(
        account_id=account_id, device_key=device_key, platform=platform,
        expo_push_token=expo_push_token, status="active", last_seen_at=now,
        disabled_at=None,
    ).on_conflict_do_update(
        index_elements=["account_id", "device_key"],
        set_={"platform": platform, "expo_push_token": expo_push_token,
              "status": "active", "last_seen_at": now, "disabled_at": None},
    ))
    return (await session.execute(select(NotificationDevice).where(
        NotificationDevice.account_id == account_id, NotificationDevice.device_key == device_key,
    ))).scalar_one()


async def unregister_device(session: AsyncSession, account_id: uuid.UUID, device_key: str) -> bool:
    from sqlalchemy import delete

    result = await session.execute(delete(NotificationDevice).where(
        NotificationDevice.account_id == account_id, NotificationDevice.device_key == device_key,
    ))
    return bool(result.rowcount)


async def active_devices(session: AsyncSession, account_id: uuid.UUID) -> list[NotificationDevice]:
    return list((await session.execute(select(NotificationDevice).where(
        NotificationDevice.account_id == account_id, NotificationDevice.status == "active",
        NotificationDevice.disabled_at.is_(None),
    ).order_by(NotificationDevice.id))).scalars().all())


async def current_device_registered(
    session: AsyncSession, account_id: uuid.UUID, device_key: str,
) -> bool:
    """Return whether this account's exact installation is actively registered."""
    result = await session.execute(select(NotificationDevice.id).where(
        NotificationDevice.account_id == account_id,
        NotificationDevice.device_key == device_key,
        NotificationDevice.status == "active",
        NotificationDevice.disabled_at.is_(None),
    ).limit(1))
    return result.scalar_one_or_none() is not None


def serialize_preferences(row: NotificationPreference) -> dict[str, Any]:
    return {
        "enabled": row.enabled, "native_push_enabled": row.native_push_enabled,
        "daily_cap": row.daily_cap,
        "quiet_hours": {"start": row.quiet_hours_start, "end": row.quiet_hours_end},
        "preferred_hour": row.preferred_hour,
        "modules": {
            module: bool(row.modules.get(module, DEFAULT_MODULE_NOTIFICATIONS[module]))
            for module in MODULES
        },
        "topics": {topic: bool((row.topics or {}).get(topic, DEFAULT_TOPIC_NOTIFICATIONS[topic])) for topic in NOTIFICATION_TOPICS},
        "timezone": row.timezone_name,
        "note": "At most one proactive appearance notification a day by default. Repeats are never sent twice.",
    }


def serialize_delivery(row: NotificationDelivery) -> dict[str, Any]:
    return {
        "id": str(row.id), "plan_date": row.plan_date.isoformat(),
        "notification_key": row.notification_key, "title": row.title, "body": row.body,
        "status": row.status, "suppressed_reason": row.suppressed_reason,
        "scheduled_for": row.scheduled_for.isoformat() if row.scheduled_for else None,
        "deep_link": row.deep_link, "source_kind": row.source_kind, "source_id": row.source_id,
        "destination_params": dict(row.destination_params or {}),
        "provider_ticket_id": row.provider_ticket_id, "provider_error_code": row.provider_error_code,
        "attempted_at": row.attempted_at.isoformat() if row.attempted_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


async def recent_deliveries(
    session: AsyncSession, account_id: uuid.UUID, limit: int = 30
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(NotificationDelivery)
        .where(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [serialize_delivery(row) for row in rows]
