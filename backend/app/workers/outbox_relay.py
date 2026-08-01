"""Outbox relay.

Polls ``domain_outbox`` for undispatched events and hands each to a registered
handler. There are no handlers in Phase 1 — an event with no handler is marked
dispatched and dropped, which is correct: nothing is waiting for it.

Run with::

    python -m app.workers.outbox_relay

Polling rather than LISTEN/NOTIFY on purpose: the volume is tiny, polling
survives a connection drop without extra code, and there is no correctness
difference at this scale.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Awaitable, Callable, Dict

from sqlalchemy import select

from app.shared.database.base import utcnow
from app.shared.database.sql import dispose_engine, get_sessionmaker
from app.shared.events.models import OutboxEvent
from app.shared.observability.logging import configure_logging

logger = logging.getLogger(__name__)

Handler = Callable[[OutboxEvent], Awaitable[None]]

HANDLERS: Dict[str, Handler] = {}

POLL_SECONDS = 2.0
BATCH_SIZE = 50
MAX_ATTEMPTS = 5


def register(event_type: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        HANDLERS[event_type] = fn
        return fn

    return decorator


async def process_batch() -> int:
    """Dispatch one batch. Returns how many events were handled."""
    factory = get_sessionmaker()
    async with factory() as session:
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.dispatched_at.is_(None))
            .where(OutboxEvent.attempts < MAX_ATTEMPTS)
            .order_by(OutboxEvent.created_at)
            .limit(BATCH_SIZE)
            # Several relays can run at once without handling the same event
            # twice. Skipping locked rows means neither one waits.
            .with_for_update(skip_locked=True)
        )
        events = (await session.execute(stmt)).scalars().all()

        handled = 0
        for event in events:
            handler = HANDLERS.get(event.event_type)
            if handler is None:
                event.dispatched_at = utcnow()
                handled += 1
                continue
            try:
                await handler(event)
                event.dispatched_at = utcnow()
                event.last_error = None
                handled += 1
            except Exception as exc:  # noqa: BLE001 — one bad event must not stop the relay
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                logger.exception(
                    "outbox_handler_failed event_id=%s type=%s",
                    event.id,
                    event.event_type,
                )
        await session.commit()
        return handled


async def run() -> None:
    configure_logging()
    stop = asyncio.Event()

    def _request_stop(*_: Any) -> None:
        logger.info("outbox_relay_stopping")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, _request_stop)

    logger.info("outbox_relay_started poll=%ss", POLL_SECONDS)
    while not stop.is_set():
        try:
            count = await process_batch()
            if count:
                logger.info("outbox_dispatched count=%s", count)
        except Exception:  # noqa: BLE001 — the relay must outlive a database blip
            logger.exception("outbox_batch_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass

    await dispose_engine()
    logger.info("outbox_relay_stopped")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run())
