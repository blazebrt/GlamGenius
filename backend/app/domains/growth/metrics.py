"""Admin-only growth aggregates, each with a written definition.

Every number here is counted from rows the product already writes for its own
reasons. Nothing is a per-person row, nothing carries an account, recipient,
invite, code or product identifier, and no number is called a "conversion"
unless its numerator and denominator are both stated beside it.

The definitions below are the authority; ``docs/architecture/CONSUMER_GROWTH.md``
repeats them in prose and the tests pin them.

Time
----
``as_of`` is the end of the window, exclusive. The window is
``[as_of - window_days, as_of)``. Every row is placed by its server-written
``created_at``; a client-supplied scan time is never used, because an offline
queue can replay a scan days later with whatever clock the phone had.

Useful scan
-----------
An account-linked ``ScanEvent`` whose outcome is one of
:data:`~app.domains.growth.activation.USEFUL_SCAN_OUTCOMES`. ``not_found`` and
anonymous (unclaimed) scans are never counted toward any account metric.

Maturity
--------
A repeat metric with horizon *H* days only admits accounts whose first useful
scan is at least *H* days before ``as_of``, so every account in its denominator
has had the whole horizon to come back. Its cohort is therefore the window
shifted back by *H*: ``[as_of - H - window_days, as_of - H)``. A three-day-old
activation is never in a seven-day denominator, and a twenty-day-old one is
never in a thirty-day denominator.

No commercial numbers
---------------------
Commercial access is not active, so there is no premium, subscription or
revenue conversion, no ARPU and no LTV. Feature adoption is reported under the
feature's own name.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Float, and_, cast, exists, func, literal, select, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domains.analytics.models import AppEvent
from app.domains.beta_access.models import InviteRedemption, InviteRegistrationReservation
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.growth.activation import USEFUL_SCAN_OUTCOMES, useful_scan_filter
from app.domains.growth.analytics import EVENT_PRODUCT_RESULT_SHARE, EVENT_SCAN_AGAIN
from app.domains.growth.models import ConsumerReferralInvite
from app.domains.identity.models import Account
from app.domains.product.models import ProductWatch, ScanDecisionEvent, ScanEvent
from app.domains.recommendation.models import PurchaseDecisionEvent
from app.shared.database.base import utcnow

METRICS_VERSION = "growth-metrics-v1"
DEFAULT_WINDOW_DAYS = 30
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 90
#: A return is a useful scan at least this long after the first one, so a
#: second product checked in the same shopping trip is not called a return.
RETURN_MIN_GAP = timedelta(hours=24)
#: Repeat horizons, in days, keyed by metric name.
REPEAT_HORIZONS: dict[str, int] = {"repeat_useful_scan_7d": 7, "repeat_useful_scan_30d": 30}

#: Every metric key the response may carry, grouped. Stable machine keys.
METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "activation": (
        "accounts_created",
        "signup_cohort_activated",
        "time_to_first_useful_scan_hours",
        "accounts_first_useful_scan_in_window",
    ),
    "habit": (
        "repeat_useful_scan_7d",
        "repeat_useful_scan_30d",
        "useful_scans_per_active_scanner",
        "accounts_recording_decision",
        "scan_again_taps",
    ),
    "sharing": (
        "share_sheet_results",
        "accounts_shared",
        "shares_with_referral_code",
    ),
    "referral": (
        "invites_issued",
        "accounts_issued_invite",
        "reservations",
        "registrations_completed",
    ),
    "adoption": (
        "household",
        "product_watch",
    ),
}


class InvalidMetricsWindow(ValueError):
    pass


def _ratio(numerator: int, denominator: int, *, key: str = "rate") -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        key: round(numerator / denominator, 4) if denominator else None,
    }


def _first_useful_scans(end: datetime):
    """One row per account: when its first useful scan was recorded, before ``end``."""
    return (
        select(
            ScanEvent.account_id.label("account_id"),
            func.min(ScanEvent.created_at).label("first_at"),
        )
        .where(useful_scan_filter(), ScanEvent.created_at < end)
        .group_by(ScanEvent.account_id)
        .subquery("first_useful")
    )


async def _count(session: AsyncSession, stmt) -> int:
    return int(await session.scalar(stmt) or 0)


async def _activation(session: AsyncSession, start: datetime, end: datetime) -> dict[str, Any]:
    first = _first_useful_scans(end)
    in_window = and_(Account.created_at >= start, Account.created_at < end)
    created = await _count(session, select(func.count()).select_from(Account).where(in_window))
    activated = await _count(
        session,
        select(func.count())
        .select_from(Account)
        .join(first, first.c.account_id == Account.id)
        .where(in_window),
    )
    # A scan made on the phone before sign-up is attached when the device is
    # claimed, so it can precede the account. That is zero hours, not negative.
    hours = func.greatest(
        literal(0.0),
        cast(func.extract("epoch", first.c.first_at - Account.created_at), Float) / 3600.0,
    )
    timing = (await session.execute(
        select(
            func.count(),
            func.percentile_cont(0.5).within_group(hours),
            func.percentile_cont(0.75).within_group(hours),
        )
        .select_from(Account)
        .join(first, first.c.account_id == Account.id)
        .where(in_window)
    )).one()
    first_in_window = await _count(
        session,
        select(func.count()).select_from(first).where(first.c.first_at >= start, first.c.first_at < end),
    )
    return {
        "useful_scan_outcomes": list(USEFUL_SCAN_OUTCOMES),
        "accounts_created": created,
        "signup_cohort_activated": _ratio(activated, created),
        "time_to_first_useful_scan_hours": {
            "accounts": int(timing[0] or 0),
            "median": round(float(timing[1]), 1) if timing[1] is not None else None,
            "p75": round(float(timing[2]), 1) if timing[2] is not None else None,
        },
        "accounts_first_useful_scan_in_window": first_in_window,
    }


async def _repeat(
    session: AsyncSession, *, end: datetime, window: timedelta, horizon_days: int,
) -> dict[str, Any]:
    horizon = timedelta(days=horizon_days)
    cohort_end = end - horizon
    cohort_start = cohort_end - window
    first = _first_useful_scans(end)
    later = aliased(ScanEvent)
    returned = exists().where(
        later.account_id == first.c.account_id,
        later.outcome.in_(USEFUL_SCAN_OUTCOMES),
        later.created_at >= first.c.first_at + RETURN_MIN_GAP,
        later.created_at <= first.c.first_at + horizon,
    )
    cohort = and_(first.c.first_at >= cohort_start, first.c.first_at < cohort_end)
    denominator = await _count(session, select(func.count()).select_from(first).where(cohort))
    numerator = await _count(session, select(func.count()).select_from(first).where(cohort, returned))
    return {
        "horizon_days": horizon_days,
        "cohort_start": cohort_start.isoformat(),
        "cohort_end": cohort_end.isoformat(),
        **_ratio(numerator, denominator),
    }


async def _habit(session: AsyncSession, start: datetime, end: datetime) -> dict[str, Any]:
    window = end - start
    payload: dict[str, Any] = {}
    for key, days in REPEAT_HORIZONS.items():
        payload[key] = await _repeat(session, end=end, window=window, horizon_days=days)
    scans_in_window = and_(useful_scan_filter(), ScanEvent.created_at >= start, ScanEvent.created_at < end)
    scans = await _count(session, select(func.count()).select_from(ScanEvent).where(scans_in_window))
    scanners = await _count(
        session, select(func.count(func.distinct(ScanEvent.account_id))).where(scans_in_window),
    )
    payload["useful_scans_per_active_scanner"] = _ratio(scans, scanners, key="ratio")
    deciders = union(
        select(ScanDecisionEvent.account_id.label("account_id"))
        .where(ScanDecisionEvent.created_at >= start, ScanDecisionEvent.created_at < end),
        select(PurchaseDecisionEvent.account_id.label("account_id"))
        .where(PurchaseDecisionEvent.created_at >= start, PurchaseDecisionEvent.created_at < end),
    ).subquery("deciders")
    payload["accounts_recording_decision"] = await _count(
        session, select(func.count()).select_from(deciders),
    )
    payload["scan_again_taps"] = await _count(
        session,
        select(func.count()).select_from(AppEvent).where(
            AppEvent.name == EVENT_SCAN_AGAIN, AppEvent.created_at >= start, AppEvent.created_at < end,
        ),
    )
    return payload


async def _sharing(session: AsyncSession, start: datetime, end: datetime) -> dict[str, Any]:
    shares = and_(
        AppEvent.name == EVENT_PRODUCT_RESULT_SHARE,
        AppEvent.created_at >= start,
        AppEvent.created_at < end,
    )
    result = AppEvent.properties["result"].astext
    rows = (await session.execute(
        select(result, func.count()).where(shares).group_by(result)
    )).all()
    by_result = {"shared": 0, "dismissed": 0, "failed": 0}
    for value, count in rows:
        if value in by_result:
            by_result[value] = int(count)
    shared = and_(shares, result == "shared")
    return {
        "share_sheet_results": by_result,
        "accounts_shared": await _count(
            session, select(func.count(func.distinct(AppEvent.account_id))).where(shared),
        ),
        "shares_with_referral_code": await _count(
            session,
            select(func.count()).select_from(AppEvent).where(
                shared, AppEvent.properties["referral_included"].as_boolean().is_(True),
            ),
        ),
    }


async def _referral(session: AsyncSession, start: datetime, end: datetime) -> dict[str, Any]:
    issued = and_(ConsumerReferralInvite.created_at >= start, ConsumerReferralInvite.created_at < end)
    return {
        "invites_issued": await _count(
            session, select(func.count()).select_from(ConsumerReferralInvite).where(issued),
        ),
        "accounts_issued_invite": await _count(
            session,
            select(func.count(func.distinct(ConsumerReferralInvite.inviter_account_id))).where(issued),
        ),
        "reservations": await _count(
            session,
            select(func.count())
            .select_from(InviteRegistrationReservation)
            .join(
                ConsumerReferralInvite,
                ConsumerReferralInvite.invite_id == InviteRegistrationReservation.invite_id,
            )
            .where(
                InviteRegistrationReservation.created_at >= start,
                InviteRegistrationReservation.created_at < end,
            ),
        ),
        "registrations_completed": await _count(
            session,
            select(func.count())
            .select_from(InviteRedemption)
            .join(ConsumerReferralInvite, ConsumerReferralInvite.invite_id == InviteRedemption.invite_id)
            .where(InviteRedemption.created_at >= start, InviteRedemption.created_at < end),
        ),
    }


async def _adoption(session: AsyncSession, start: datetime, end: datetime) -> dict[str, Any]:
    members = and_(FamilyProfile.relation != "self", FamilyProfile.created_at < end)
    household_now = await _count(
        session,
        select(func.count(func.distinct(FamilyCircle.account_id)))
        .select_from(FamilyCircle)
        .join(FamilyProfile, FamilyProfile.circle_id == FamilyCircle.id)
        .where(members, FamilyCircle.active.is_(True), FamilyProfile.active.is_(True)),
    )
    first_member = (
        select(func.min(FamilyProfile.created_at).label("first_at"))
        .select_from(FamilyCircle)
        .join(FamilyProfile, FamilyProfile.circle_id == FamilyCircle.id)
        .where(members)
        .group_by(FamilyCircle.account_id)
        .subquery("first_member")
    )
    household_new = await _count(
        session,
        select(func.count()).select_from(first_member).where(
            first_member.c.first_at >= start, first_member.c.first_at < end,
        ),
    )
    watch_now = await _count(
        session,
        select(func.count(func.distinct(ProductWatch.account_id))).where(
            ProductWatch.active.is_(True), ProductWatch.created_at < end,
        ),
    )
    first_watch = (
        select(func.min(ProductWatch.created_at).label("first_at"))
        .where(ProductWatch.created_at < end)
        .group_by(ProductWatch.account_id)
        .subquery("first_watch")
    )
    watch_new = await _count(
        session,
        select(func.count()).select_from(first_watch).where(
            first_watch.c.first_at >= start, first_watch.c.first_at < end,
        ),
    )
    return {
        "household": {
            "accounts_with_household_member_now": household_now,
            "accounts_first_household_member_in_window": household_new,
        },
        "product_watch": {
            "accounts_with_active_watch_now": watch_now,
            "accounts_first_watch_in_window": watch_new,
        },
    }


async def growth_metrics(
    session: AsyncSession, *, now: datetime | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Every growth aggregate for one window. Reads only."""
    if not isinstance(window_days, int) or not MIN_WINDOW_DAYS <= window_days <= MAX_WINDOW_DAYS:
        raise InvalidMetricsWindow("window_days_out_of_range")
    end = now or utcnow()
    start = end - timedelta(days=window_days)
    return {
        "metrics_version": METRICS_VERSION,
        "as_of": end.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": window_days},
        "activation": await _activation(session, start, end),
        "habit": await _habit(session, start, end),
        "sharing": await _sharing(session, start, end),
        "referral": await _referral(session, start, end),
        "adoption": await _adoption(session, start, end),
    }


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "InvalidMetricsWindow",
    "MAX_WINDOW_DAYS",
    "METRICS_VERSION",
    "METRIC_KEYS",
    "MIN_WINDOW_DAYS",
    "REPEAT_HORIZONS",
    "RETURN_MIN_GAP",
    "growth_metrics",
]
