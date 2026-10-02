"""How many answers a B2B client may have. Never what any answer says.

Two limits, checked in this order, both before any Product Truth work:

1. **Burst** — requests per minute, per client, in this web process. The
   shared :class:`~app.shared.security.rate_limit.FixedWindowLimiter`, keyed by
   the client's id (never by anything the caller sends, never by the secret),
   with each client's own ceiling.

2. **Daily allowance** — requests per UTC day, per client, in PostgreSQL. One
   atomic statement takes one unit or refuses::

       INSERT ... VALUES (client, today, 1)
       ON CONFLICT (client_id, usage_date) DO UPDATE
          SET request_count = request_count + 1
        WHERE request_count < (the client's current requests_per_day)

   ``ON CONFLICT DO UPDATE`` locks the row and re-evaluates the ``WHERE``
   against the newest committed version, so with one unit left two concurrent
   requests cannot both take it — the second waits for the first, sees the
   allowance spent, and is refused. There is no read-then-write in Python.
   The day is the database's own ``timezone('UTC', now())::date``, so no web
   process clock is ever compared with another. The caller commits straight
   after, so the row lock is held for one statement, not for a whole request.

Neither limit is an input to Product Truth. :mod:`app.domains.b2b.truth` has
no parameter that could carry one, and the suite proves a client allowed ten
requests a day and a client allowed a hundred thousand get byte-identical
answers.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, cast, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.b2b.models import MAX_REQUESTS_PER_MINUTE
from app.shared.security.rate_limit import FixedWindowLimiter

BURST_WINDOW_SECONDS = 60.0

#: One process-wide table of per-client buckets. ``max_per_window`` is only the
#: fallback; every hit passes the client's own ceiling.
_burst_limiter = FixedWindowLimiter(
    window_seconds=BURST_WINDOW_SECONDS,
    max_per_window=MAX_REQUESTS_PER_MINUTE,
    max_keys=10_000,
)

_TODAY_SQL = "timezone('UTC', now())::date"


def utc_today() -> ColumnElement[date]:
    """Today's UTC date by the database clock."""
    return cast(func.timezone("UTC", func.now()), Date)


def burst_retry_after(client_id: uuid.UUID, requests_per_minute: int) -> int | None:
    """``None`` if this request fits the client's per-minute ceiling, else whole seconds to wait."""
    key = str(client_id)
    if _burst_limiter.hit(key, limit=requests_per_minute):
        return _burst_limiter.retry_after_seconds(key)
    return None


def reset_burst_state() -> None:
    """For tests: forget every bucket."""
    _burst_limiter.reset()


async def acquire_daily(session: AsyncSession, client_id: uuid.UUID) -> date | None:
    """Take one unit of today's allowance. The usage date it was taken on, or ``None`` if spent.

    The ceiling is read inside the statement from the client row, so an
    administrator's change applies to the very next request.
    """
    row = (await session.execute(
        text(f"""
            INSERT INTO b2b_api_usage_daily AS usage (client_id, usage_date, request_count)
            VALUES (:client_id, {_TODAY_SQL}, 1)
            ON CONFLICT (client_id, usage_date) DO UPDATE
               SET request_count = usage.request_count + 1,
                   updated_at = now()
             WHERE usage.request_count < (
                   SELECT client.requests_per_day FROM b2b_api_clients AS client
                    WHERE client.id = usage.client_id
             )
            RETURNING usage.usage_date
        """),
        {"client_id": client_id},
    )).first()
    return row[0] if row is not None else None


async def record_rate_limited(session: AsyncSession, client_id: uuid.UUID) -> None:
    """Count one 429 against today, atomically."""
    await session.execute(
        text(f"""
            INSERT INTO b2b_api_usage_daily AS usage (client_id, usage_date, rate_limited_count)
            VALUES (:client_id, {_TODAY_SQL}, 1)
            ON CONFLICT (client_id, usage_date) DO UPDATE
               SET rate_limited_count = usage.rate_limited_count + 1,
                   updated_at = now()
        """),
        {"client_id": client_id},
    )


async def record_outcome(
    session: AsyncSession, client_id: uuid.UUID, usage_date: date, *, available: bool,
) -> None:
    """Count what a request that took an allowance was answered with.

    Written against the date the allowance was taken on, not "today": a request
    that straddles midnight is counted on the day it was admitted, which keeps
    ``successful + not_enough_information <= request_count`` true on every row.
    """
    column = "successful_count" if available else "not_enough_information_count"
    await session.execute(
        text(f"""
            UPDATE b2b_api_usage_daily
               SET {column} = {column} + 1, updated_at = now()
             WHERE client_id = :client_id AND usage_date = :usage_date
        """),
        {"client_id": client_id, "usage_date": usage_date},
    )


async def seconds_until_daily_reset(session: AsyncSession) -> int:
    """Whole seconds until the next UTC midnight, by the database clock."""
    value = (await session.execute(text(
        "SELECT ceil(extract(epoch FROM "
        "(date_trunc('day', timezone('UTC', now())) + interval '1 day') - timezone('UTC', now())))"
    ))).scalar_one()
    return max(1, int(value))


__all__ = [
    "BURST_WINDOW_SECONDS",
    "acquire_daily",
    "burst_retry_after",
    "record_outcome",
    "record_rate_limited",
    "reset_burst_state",
    "seconds_until_daily_reset",
    "utc_today",
]
