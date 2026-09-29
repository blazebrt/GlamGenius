"""Growth telemetry: a closed whitelist over the existing ``app_events`` table.

Most of what Step 15 measures is already written down by the product doing its
job — scans, decisions, invites, reservations, redemptions, households, watches
— and is counted from those rows (:mod:`app.domains.growth.metrics`). A client
is never asked to report a fact the server already holds, because a second
account of the same thing is a second number that can be wrong.

This module exists only for the interactions that otherwise vanish: whether a
Product Result was handed to the system share sheet, and whether somebody went
straight back to scan another product. For those it is deliberately narrow:

* **Every event name is listed here.** Anything else is refused.
* **Every property is listed, required and an enum.** There is no string a
  client chooses, so there is nowhere to put a barcode, a product name, an
  email, an invite code, a household or subject id, a media id, an AI run id,
  or free text of any kind. An unknown key, a missing key, a value outside the
  enum, or a value of the wrong type fails closed.
* **Nothing about where it went.** Not the recipient, not which app was
  chosen — the platform does not tell us, and we would not keep it if it did.
* **No IP address, no device identifier.** ``client_event_id`` is a random
  operation UUID minted once per interaction so a retry is not counted twice.
* **Disposable.** Rows older than :data:`RETENTION` are pruned — see
  :func:`prune_opportunistically` — and the account's rows leave with it.

Telemetry failing never fails anything a person asked for: the only caller is
its own endpoint, which the app fires and forgets.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.analytics.models import AppEvent
from app.shared.database.base import new_uuid, utcnow

logger = logging.getLogger(__name__)

#: How long a telemetry row is kept.
RETENTION = timedelta(days=90)
#: At most one prune attempt per process in this many seconds.
PRUNE_INTERVAL_SECONDS = 3600.0
#: At most this many rows removed per prune. Bounded so a write that happens to
#: trigger it never turns into a long delete.
PRUNE_BATCH = 500
#: Transaction-scoped advisory lock key: at most one pruner at a time across
#: every process. Arbitrary, fixed, and used for nothing else.
PRUNE_LOCK_KEY = 0x6772_6F77_7468_0015  # "growth" + step 15

EVENT_PRODUCT_RESULT_SHARE = "growth.product_result_share"
EVENT_SCAN_AGAIN = "growth.scan_again"

#: name -> property -> the only values it may take. Every property is required.
EVENT_SCHEMAS: dict[str, dict[str, tuple[Any, ...]]] = {
    EVENT_PRODUCT_RESULT_SHARE: {
        "surface": ("product_result",),
        # What the system share sheet reported. ``shared`` means the platform
        # said so — on Android that is every time the sheet opened, because
        # Android does not report the choice. Never a destination.
        "result": ("shared", "dismissed", "failed"),
        "referral_included": (True, False),
    },
    EVENT_SCAN_AGAIN: {
        "surface": ("product_result",),
    },
}

EVENT_NAMES: tuple[str, ...] = tuple(EVENT_SCHEMAS)


class InvalidGrowthEvent(ValueError):
    """The event is not one this module records. Carries a code, never input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _value_allowed(value: Any, allowed: tuple[Any, ...]) -> bool:
    # ``True == 1`` in Python, so membership alone would let an integer through
    # a boolean enum. The type has to match exactly as well.
    return any(type(value) is type(option) and value == option for option in allowed)


def validate_event(name: Any, properties: Any) -> dict[str, Any]:
    """The canonical property bag for a whitelisted event, or a refusal."""
    if not isinstance(name, str) or name not in EVENT_SCHEMAS:
        raise InvalidGrowthEvent("event_not_allowed")
    if not isinstance(properties, dict):
        raise InvalidGrowthEvent("properties_invalid")
    schema = EVENT_SCHEMAS[name]
    if set(properties) != set(schema):
        raise InvalidGrowthEvent("properties_invalid")
    for key, allowed in schema.items():
        if not _value_allowed(properties[key], allowed):
            raise InvalidGrowthEvent("properties_invalid")
    return {key: properties[key] for key in schema}


async def record_event(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    name: str,
    client_event_id: uuid.UUID,
    properties: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Write one whitelisted event. ``False`` when it was a retry already held.

    The caller commits. Refuses with :class:`InvalidGrowthEvent` before any
    write if the event is not exactly one this module knows.
    """
    clean = validate_event(name, properties)
    if not isinstance(client_event_id, uuid.UUID):
        raise InvalidGrowthEvent("client_event_id_invalid")
    values: dict[str, Any] = {
        "id": new_uuid(),
        "account_id": account_id,
        "name": name,
        "properties": clean,
        "client_event_id": client_event_id,
    }
    if now is not None:
        values["created_at"] = now
        values["updated_at"] = now
    stmt = (
        pg_insert(AppEvent)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["account_id", "name", "client_event_id"],
            index_where=AppEvent.client_event_id.is_not(None),
        )
        .returning(AppEvent.id)
    )
    return (await session.execute(stmt)).first() is not None


async def prune_expired_events(
    session: AsyncSession, *, now: datetime | None = None, batch: int = PRUNE_BATCH,
) -> int:
    """Remove at most ``batch`` telemetry rows older than :data:`RETENTION`.

    Only ``app_events``. Audit events, decision memory, official records and
    evidence are other tables with other rules, and nothing here names them.
    Race-safe: one pruner at a time by advisory lock, and rows another
    transaction holds are skipped rather than waited for. The caller commits.
    """
    cutoff = (now or utcnow()) - RETENTION
    if not await session.scalar(select(func.pg_try_advisory_xact_lock(PRUNE_LOCK_KEY))):
        return 0
    victims = (
        select(AppEvent.id)
        .where(AppEvent.created_at < cutoff)
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        delete(AppEvent)
        .where(AppEvent.id.in_(victims))
        .returning(AppEvent.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())


_last_prune_attempt: float | None = None


def _prune_due() -> bool:
    """Throttle: at most one attempt per process per interval.

    The clock is taken before trying, so a prune that fails is not retried on
    every following write.
    """
    global _last_prune_attempt
    now = time.monotonic()
    if _last_prune_attempt is not None and now - _last_prune_attempt < PRUNE_INTERVAL_SECONDS:
        return False
    _last_prune_attempt = now
    return True


def reset_prune_throttle() -> None:
    """For tests: forget when this process last pruned."""
    global _last_prune_attempt
    _last_prune_attempt = None


async def prune_opportunistically(session: AsyncSession) -> int:
    """Run a bounded prune in its own transaction, if one is due. Never raises.

    Called after an event has been committed, so the event never depends on
    the prune. No worker, cron or scheduler is involved.
    """
    if not _prune_due():
        return 0
    try:
        removed = await prune_expired_events(session)
        await session.commit()
        return removed
    except Exception as exc:  # noqa: BLE001 — telemetry housekeeping is disposable
        logger.warning("growth_analytics_prune_failed type=%s", type(exc).__name__)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 — a session we cannot reset is already lost
            logger.warning("growth_analytics_prune_rollback_failed")
        return 0


__all__ = [
    "EVENT_NAMES",
    "EVENT_PRODUCT_RESULT_SHARE",
    "EVENT_SCAN_AGAIN",
    "EVENT_SCHEMAS",
    "InvalidGrowthEvent",
    "PRUNE_BATCH",
    "PRUNE_INTERVAL_SECONDS",
    "RETENTION",
    "prune_expired_events",
    "prune_opportunistically",
    "record_event",
    "reset_prune_throttle",
    "validate_event",
]
