"""Account-deletion worker, in two shapes.

Job claiming is the same in both: ``SELECT … FOR UPDATE SKIP LOCKED`` with a
lease, so no two runners ever pick the same job. What differs is who decides
when to run.

:func:`run_forever` is the long-running daemon. It polls, it owns its own
loop, and it is what an always-on worker container runs. It is kept because
that is the right shape once there is a worker process to pay for.

:func:`run_cycle` is the bounded one-shot. An external scheduler calls it, it
processes **at most one** claimed job, writes a heartbeat, and returns. That is
what the pre-PMF runtime uses: there is no worker process, so Supabase Cron
POSTs to the API every five minutes and the API runs one cycle. Bounded on
purpose — an HTTP request is not a place to drain a queue, and a request that
kept claiming jobs until the queue emptied would be a request that times out
under exactly the backlog it was meant to clear.

Neither shape reimplements the deletion state machine. Both call
``deletion_service.claim_next`` and ``deletion_service.run_job``; the leases,
retries and terminal states live there and stay there.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domains.privacy import deletion_service
from app.domains.system.models import WorkerStatus
from app.shared.database.sql import get_sessionmaker
from app.workers.schedule import ACCOUNT_DELETION_WORKER_NAME, service_version

logger = logging.getLogger(__name__)

_POLL_SECONDS_IDLE = 5
_POLL_SECONDS_BUSY = 0.1


@dataclass(frozen=True)
class CycleSummary:
    """What one bounded cycle did, in terms safe to return over HTTP.

    Deliberately three fields and no more. There is no account id, no email,
    no job id, no job payload and no database error text here, because this
    object is what the scheduler endpoint serialises back to a caller that has
    a shared secret but is not a person and has no business seeing whose
    account was deleted.
    """

    processed: bool
    ok: bool
    error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"processed": self.processed, "ok": self.ok, "error_code": self.error_code}


async def _write_heartbeat(
    factory: async_sessionmaker,
    *,
    attempted: bool,
    succeeded: bool,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> None:
    """Record that the scheduled worker ran, whatever the run did.

    Never raises. A heartbeat that fails must not turn a completed deletion
    into a reported failure, and must not take the endpoint down either — the
    absence of the heartbeat is itself the signal readiness watches for.
    """
    version = service_version()
    values: dict[str, object] = {
        "last_heartbeat_at": func.now(),
        "service_version": version,
    }
    if attempted:
        values["last_attempted_job_at"] = func.now()
    if succeeded:
        values["last_successful_job_at"] = func.now()
        values["last_error_code"] = None
        values["last_error_summary"] = None
    elif error_code is not None:
        values["last_error_code"] = error_code[:64]
        values["last_error_summary"] = (error_summary or error_code)[:255]
        values["last_error_at"] = func.now()
    try:
        async with factory() as session:
            await session.execute(
                insert(WorkerStatus)
                .values(
                    worker_name=ACCOUNT_DELETION_WORKER_NAME,
                    # func.now(), not a Python datetime: system_worker_status
                    # stores naive timestamps, and asyncpg refuses to encode an
                    # aware datetime into one. Letting PostgreSQL supply the
                    # instant also keeps every column in this row on the same
                    # clock as last_heartbeat_at beside it.
                    started_at=func.now(),
                    **values,
                )
                .on_conflict_do_update(
                    index_elements=["worker_name"], set_=values
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - observability must never break the run
        # Fixed event only, for the same reason as the cycle below: this is
        # a database failure, and a database failure's text is a connection
        # string.
        logger.error("account_deletion_scheduled_heartbeat_failed")


async def run_cycle(
    *, sessionmaker: async_sessionmaker | None = None
) -> CycleSummary:
    """Process at most one claimed deletion job, then return.

    The bounded entry point the scheduler calls. Claiming and running are the
    existing service's, not reimplemented here; this function's whole job is
    to do exactly one of them, record that it ran, and describe the outcome
    without describing the person.
    """
    factory = sessionmaker or get_sessionmaker()
    processed = False
    state: str | None = None
    error: str | None = None
    try:
        async with factory() as session:
            job = await deletion_service.claim_next(session)
            if job is not None:
                processed = True
                state, error = await deletion_service.run_job(session, job)
                # The commit, and where it has to be.
                #
                # deletion_service only ever flushes -- it leaves the
                # transaction open for its caller to close, which is how the
                # daemon has always worked. Without this line the session
                # context exits, the transaction rolls back, and every
                # transition run_job just made is discarded: the claim, the
                # lease, attempt_count, the retry schedule, the terminal
                # state. The job would look untouched and be picked up again.
                #
                # That is not merely lost work. Some stages have already done
                # something irreversible by this point -- deleted the account's
                # media from Supabase Storage, revoked an integration, removed
                # the Supabase Auth user. Rolling back the record of it means
                # doing it again to an account that no longer has any of it,
                # against a state machine that thinks it never started.
                #
                # After run_job, not before: a commit between claim and run
                # would persist the claim of a job whose processing then
                # crashed. A controlled failure result is committed too --
                # run_job wrote the retry or terminal state into this session,
                # and that state is the record of the attempt.
                await session.commit()
    except Exception as exc:  # noqa: BLE001 - the cycle itself failed
        # A fixed event and nothing else. Not logger.exception, which writes
        # the traceback and the message: a database error quotes its
        # connection string, an asyncpg error quotes the row it choked on,
        # and this runs on a path an unauthenticated caller can reach. The
        # class name is the most this is allowed to say, and it says it into
        # system_worker_status rather than the log.
        logger.error("account_deletion_scheduled_cycle_failed")
        await _write_heartbeat(
            factory,
            attempted=True,
            succeeded=False,
            error_code="unexpected_worker_error",
            error_summary=type(exc).__name__,
        )
        return CycleSummary(processed=False, ok=False, error_code="unexpected_worker_error")

    # Heartbeats are written after the job session has closed, in a session of
    # their own, and _write_heartbeat never raises. A failure to record that
    # the run happened must not undo the run.
    if not processed:
        await _write_heartbeat(factory, attempted=False, succeeded=False)
        return CycleSummary(processed=False, ok=True)
    if error is None:
        await _write_heartbeat(factory, attempted=True, succeeded=True)
        return CycleSummary(processed=True, ok=True)
    await _write_heartbeat(
        factory,
        attempted=True,
        succeeded=False,
        error_code=error,
        error_summary=f"Job failed at {state}",
    )
    return CycleSummary(processed=True, ok=False, error_code=str(error)[:64])


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

    from sqlalchemy.exc import DBAPIError

    # The daemon keeps its per-host identity: two pods are two workers and an
    # operator wants to see both. The scheduled path deliberately does not —
    # see app/workers/schedule.py.
    worker_name = f"account_deletion_worker_{socket.gethostname()}"
    # Also func.now(): this used to be utcnow(), an aware datetime, which
    # asyncpg cannot encode into the naive column — so every heartbeat write
    # in this loop raised and the daemon's row was never written at all.
    started_at = func.now()
    worker_version = service_version()

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
                        service_version=worker_version,
                        last_attempted_job_at=func.now()
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_={
                            "last_heartbeat_at": func.now(),
                            "last_attempted_job_at": func.now(),
                            "service_version": worker_version
                        }
                    )
                    await session.execute(stmt)
                    await session.commit()

                    state, err = await deletion_service.run_job(session, job)
                    did_work = True

                    upd_vals = {
                        "last_heartbeat_at": func.now(),
                        "service_version": worker_version
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
                        service_version=worker_version,
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
                        service_version=worker_version
                    ).on_conflict_do_update(
                        index_elements=['worker_name'],
                        set_={"last_heartbeat_at": func.now(), "service_version": worker_version}
                    )
                    await session.execute(stmt)
                    await session.commit()
                    did_work = False

        except DBAPIError:
            logger.exception("account_deletion_worker_db_error")
            did_work = False
            # Can't write to DB if DB is down, just skip this tick
        except Exception:  # noqa: BLE001
            logger.exception("account_deletion_worker_tick_failed")
            did_work = False
            try:
                async with factory() as error_session:
                    stmt = insert(WorkerStatus).values(
                        worker_name=worker_name,
                        last_heartbeat_at=func.now(),
                        started_at=started_at,
                        service_version=worker_version,
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
