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
    
    import os
    import socket
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert
    from app.domains.system.models import WorkerStatus
    from app.shared.database.base import utcnow
    from sqlalchemy.exc import DBAPIError

    worker_name = f"account_deletion_worker_{socket.gethostname()}"
    started_at = utcnow()
    service_version = os.environ.get("COMMIT_SHA", os.environ.get("APP_VERSION", "unknown"))

    while not stop.is_set():
        did_work = False
        try:
            async with factory() as session:
                job = await deletion_service.claim_next(session)
                if job:
                    # Heartbeat + mark attempt before processing
                    stmt = insert(WorkerStatus).values(
                        worker_name=worker_name,
                        last_heartbeat_at=func.now(),
                        started_at=started_at,
                        service_version=service_version,
                        last_attempted_job_at=func.now()
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_={
                            "last_heartbeat_at": func.now(),
                            "last_attempted_job_at": func.now(),
                            "service_version": service_version
                        }
                    )
                    await session.execute(stmt)
                    await session.commit()

                    state, err = await deletion_service.run_job(session, job)
                    did_work = True

                    upd_vals = {
                        "last_heartbeat_at": func.now(),
                        "service_version": service_version
                    }
                    if err is None:
                        upd_vals["last_successful_job_at"] = func.now()
                    else:
                        upd_vals["last_error_code"] = err
                        upd_vals["last_error_summary"] = f"Job failed at {state}"
                        upd_vals["last_error_at"] = func.now()

                    stmt2 = insert(WorkerStatus).values(
                        worker_name=worker_name,
                        last_heartbeat_at=func.now(),
                        started_at=started_at,
                        service_version=service_version,
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_=upd_vals
                    )
                    await session.execute(stmt2)
                    await session.commit()
                else:
                    # No job, just heartbeat
                    stmt = insert(WorkerStatus).values(
                        worker_name=worker_name,
                        last_heartbeat_at=func.now(),
                        started_at=started_at,
                        service_version=service_version
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_={"last_heartbeat_at": func.now(), "service_version": service_version}
                    )
                    await session.execute(stmt)
                    await session.commit()
                    did_work = False

        except DBAPIError as db_exc:
            logger.exception("account_deletion_worker_db_error")
            did_work = False
            # Can't write to DB if DB is down, just skip this tick
        except Exception as e:  # noqa: BLE001
            logger.exception("account_deletion_worker_tick_failed")
            did_work = False
            try:
                async with factory() as error_session:
                    stmt = insert(WorkerStatus).values(
                        worker_name=worker_name,
                        last_heartbeat_at=func.now(),
                        started_at=started_at,
                        service_version=service_version,
                        last_error_code="unexpected_worker_error",
                        last_error_summary="Unexpected worker crash",
                        last_error_at=func.now()
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_={
                            "last_heartbeat_at": func.now(),
                            "last_error_code": "unexpected_worker_error",
                            "last_error_summary": "Unexpected worker crash",
                            "last_error_at": func.now()
                        }
                    )
                    await error_session.execute(stmt)
                    await error_session.commit()
            except Exception:
                pass

        await asyncio.wait(
            [asyncio.create_task(stop.wait())],
            timeout=_POLL_SECONDS_BUSY if did_work else _POLL_SECONDS_IDLE,
        )
    logger.info("account_deletion_worker_stopped")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
