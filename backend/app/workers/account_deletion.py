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
    
    import socket

    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert

    from app.domains.system.models import WorkerStatus

    worker_name = f"account_deletion_worker_{socket.gethostname()}"
    
    while not stop.is_set():
        did_work = False
        try:
            async with factory() as session:
                did_work = await deletion_service.process_once(session)
                
                stmt = insert(WorkerStatus).values(
                    worker_name=worker_name,
                    last_heartbeat_at=func.now(),
                    last_error_summary=None
                ).on_conflict_do_update(
                    index_elements=['worker_name'],
                    set_={"last_heartbeat_at": func.now(), "last_error_summary": None}
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.exception("account_deletion_worker_tick_failed")
            did_work = False
            try:
                async with factory() as error_session:
                    stmt = insert(WorkerStatus).values(
                        worker_name=worker_name,
                        last_heartbeat_at=func.now(),
                        last_error_summary=str(e)[:255]
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_={"last_heartbeat_at": func.now(), "last_error_summary": str(e)[:255]}
                    )
                    await error_session.execute(stmt)
                    await error_session.commit()
            except Exception:
                pass

            did_work = False
        await asyncio.wait(
            [asyncio.create_task(stop.wait())],
            timeout=_POLL_SECONDS_BUSY if did_work else _POLL_SECONDS_IDLE,
        )
    logger.info("account_deletion_worker_stopped")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
