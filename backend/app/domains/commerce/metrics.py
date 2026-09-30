"""Admin-only Commerce aggregates, counted from ``commerce.outbound_open`` rows.

An outbound open is exactly that: a signed-in person opened a disclosed link to
a partner's search page. It is not a sale. GlamGenius does not see what
happens on the partner's site, so there is deliberately no conversion rate, no
purchase count, no revenue, no order value, no commission and no return on
anything here — reporting any of them would be inventing a number.

Every value is an aggregate. No account, product, barcode, partner address or
person appears. Rows are placed by their server-written ``created_at`` in the
window ``[as_of - window_days, as_of)``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.analytics.models import AppEvent
from app.domains.commerce.analytics import EVENT_OUTBOUND_OPEN
from app.domains.commerce.partners import PARTNER_KEYS
from app.shared.database.base import utcnow

METRICS_VERSION = "commerce-metrics-v1"
DEFAULT_WINDOW_DAYS = 30
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 90

DECISIONS: tuple[str, ...] = ("buy", "wait", "skip")

#: Stable machine keys with their written definitions. The tests pin both.
DEFINITIONS: dict[str, str] = {
    "outbound_opens": "Count of commerce.outbound_open rows in the window.",
    "accounts_opening_commerce": "Distinct accounts with at least one commerce.outbound_open in the window.",
    "current_product_opens": "Outbound opens whose target was the scanned product (decision BUY).",
    "alternative_opens": "Outbound opens whose target was the one comparable alternative (decision WAIT or SKIP).",
    "opens_by_partner": "Outbound opens per registered partner key.",
    "opens_by_decision": "Outbound opens per canonical decision the handoff rested on.",
}

#: Numbers this surface will never report, because GlamGenius does not have them.
NOT_REPORTED: tuple[str, ...] = (
    "purchases", "conversion_rate", "revenue", "gross_merchandise_value", "commission",
    "average_order_value", "return_on_ad_spend", "order_completion",
)


class InvalidMetricsWindow(ValueError):
    pass


async def _count(session: AsyncSession, stmt) -> int:
    return int(await session.scalar(stmt) or 0)


async def commerce_metrics(
    session: AsyncSession, *, now: datetime | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Every Commerce aggregate for one window. Reads only."""
    if not isinstance(window_days, int) or not MIN_WINDOW_DAYS <= window_days <= MAX_WINDOW_DAYS:
        raise InvalidMetricsWindow("window_days_out_of_range")
    end = now or utcnow()
    start = end - timedelta(days=window_days)
    opened = (
        AppEvent.name == EVENT_OUTBOUND_OPEN,
        AppEvent.created_at >= start,
        AppEvent.created_at < end,
    )
    target = AppEvent.properties["target"].astext
    partner = AppEvent.properties["partner"].astext
    decision = AppEvent.properties["decision"].astext
    total = select(func.count()).select_from(AppEvent).where(*opened)
    by_partner = dict((await session.execute(
        select(partner, func.count()).where(*opened).group_by(partner)
    )).all())
    by_decision = dict((await session.execute(
        select(decision, func.count()).where(*opened).group_by(decision)
    )).all())
    return {
        "metrics_version": METRICS_VERSION,
        "as_of": end.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": window_days},
        "metrics": {
            "outbound_opens": await _count(session, total),
            "accounts_opening_commerce": await _count(
                session, select(func.count(func.distinct(AppEvent.account_id))).where(*opened),
            ),
            "current_product_opens": await _count(session, total.where(target == "current_product")),
            "alternative_opens": await _count(session, total.where(target == "alternative")),
            "opens_by_partner": {key: int(by_partner.get(key, 0)) for key in PARTNER_KEYS},
            "opens_by_decision": {key: int(by_decision.get(key, 0)) for key in DECISIONS},
        },
        "definitions": dict(DEFINITIONS),
        "not_reported": list(NOT_REPORTED),
    }


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "DEFINITIONS",
    "InvalidMetricsWindow",
    "MAX_WINDOW_DAYS",
    "METRICS_VERSION",
    "MIN_WINDOW_DAYS",
    "NOT_REPORTED",
    "commerce_metrics",
]
