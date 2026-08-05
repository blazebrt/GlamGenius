"""Account-deletion worker.

Long-running loop that polls :func:`process_once` and processes deletion
jobs one at a time. Two workers on different pods can safely run against
the same database — job claiming uses ``SELECT … FOR UPDATE SKIP LOCKED``
and a lease so no two workers pick the same job.

In tests we call the service functions directly. This module exists so a
production deployment can run a dedicated worker container.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path

from app.domains.privacy import deletion_service
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)

_POLL_SECONDS_IDLE = 5
_POLL_SECONDS_BUSY = 0.1


async def run_forever() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover — platform-specific
            pass

    factory = get_sessionmaker()
    logger.info("account_deletion_worker_started")
    heartbeat_file = Path("/tmp/worker_heartbeat")
    
    while not stop.is_set():
        try:
            async with factory() as session:
                did_work = await deletion_service.process_once(session)
                await session.commit()
            heartbeat_file.touch()
        except Exception:  # noqa: BLE001
            logger.exception("account_deletion_worker_tick_failed")
            did_work = False
        await asyncio.wait(
            [asyncio.create_task(stop.wait())],
            timeout=_POLL_SECONDS_BUSY if did_work else _POLL_SECONDS_IDLE,
        )
    logger.info("account_deletion_worker_stopped")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
