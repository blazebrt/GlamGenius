"""The worker-health endpoint, with a worker that has actually reported.

``system_worker_status`` is the only table in the schema whose timestamps are
``TIMESTAMP WITHOUT TIME ZONE`` — the other 362 datetime columns are
``timestamptz``. Rows read back from those five columns are therefore naive,
while ``utcnow()`` is aware, and subtracting one from the other is a
``TypeError`` rather than a wrong answer.

``/api/v2/admin/workers`` did exactly that subtraction, so it answered 200 only
while no worker had ever reported a heartbeat — which is the state every test
left the table in, and the opposite of the state production is in. The readiness
endpoint had already met this trap and normalises the value before comparing
(``app/api/v2/config.py``); this endpoint did not.

The tests below insert the heartbeat the way the worker does — ``NOW()``
evaluated by PostgreSQL, not a Python datetime — because a Python-side aware
value would paper over the very mismatch being tested.
"""
from __future__ import annotations

import uuid

import pytest
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import text

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


async def _report_heartbeat(worker_name: str, *, age_seconds: int = 0) -> None:
    """One worker-status row, written the way the worker writes it."""
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO system_worker_status "
                "(id, worker_name, last_heartbeat_at, started_at, last_attempted_job_at, "
                " last_successful_job_at, created_at, updated_at) "
                "VALUES (:id, :name, NOW() - make_interval(secs => :age), NOW() - make_interval(secs => :age), "
                "        NOW() - make_interval(secs => :age), NOW() - make_interval(secs => :age), NOW(), NOW()) "
                "ON CONFLICT (worker_name) DO UPDATE SET "
                "  last_heartbeat_at = EXCLUDED.last_heartbeat_at"
            ),
            {"id": str(uuid.uuid4()), "name": worker_name, "age": age_seconds},
        )
        await session.commit()


async def _pending_deletion_job() -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        account_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO accounts (id, status, created_at, updated_at) "
                "VALUES (:id, 'deletion_requested', NOW(), NOW())"
            ),
            {"id": str(account_id)},
        )
        await session.execute(
            text(
                "INSERT INTO account_deletion_jobs "
                "(id, account_id, state, attempt_count, requested_at, created_at, updated_at) "
                "VALUES (:job, :account, 'requested', 0, NOW(), NOW(), NOW())"
            ),
            {"job": str(uuid.uuid4()), "account": str(account_id)},
        )
        await session.commit()


async def test_the_endpoint_answers_once_a_worker_has_reported(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user(admin=True)
    await _report_heartbeat("account_deletion_worker")

    resp = await app_client.get("/api/v2/admin/workers", headers=auth(token))

    assert resp.status_code == 200, resp.text


async def test_the_reported_worker_carries_a_real_heartbeat_age(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user(admin=True)
    await _report_heartbeat("account_deletion_worker", age_seconds=120)

    body = (await app_client.get("/api/v2/admin/workers", headers=auth(token))).json()

    rows = {row["worker_name"]: row for row in body["workers"]}
    assert "account_deletion_worker" in rows, body
    age = rows["account_deletion_worker"]["last_heartbeat_age_seconds"]
    assert isinstance(age, int), f"age was {age!r}"
    assert 100 <= age <= 200, f"a heartbeat 120s old was reported as {age}s"


async def test_a_stale_worker_is_reported_as_missed_not_as_an_error(
    app_client, db_clean, registered_supabase_user
):
    """The state machine has to be reachable at all, which it was not."""
    token, _ = await registered_supabase_user(admin=True)
    await _report_heartbeat("account_deletion_worker", age_seconds=60 * 60 * 24)

    body = (await app_client.get("/api/v2/admin/workers", headers=auth(token))).json()

    scheduled = {row["worker_name"]: row for row in body["scheduled_workers"]}
    assert scheduled["account_deletion_worker"]["state"] == "missed", scheduled


async def test_a_fresh_worker_is_reported_as_healthy(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user(admin=True)
    await _report_heartbeat("account_deletion_worker", age_seconds=5)

    body = (await app_client.get("/api/v2/admin/workers", headers=auth(token))).json()

    scheduled = {row["worker_name"]: row for row in body["scheduled_workers"]}
    assert scheduled["account_deletion_worker"]["state"] == "healthy", scheduled


async def test_pending_jobs_are_still_counted_alongside_a_reported_worker(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user(admin=True)
    await _pending_deletion_job()
    await _report_heartbeat("account_deletion_worker")

    body = (await app_client.get("/api/v2/admin/workers", headers=auth(token))).json()

    assert body["job_metrics"]["pending_jobs"] >= 1


# ---------------------------------------------------------------------------
# The schema fact underneath all of the above
# ---------------------------------------------------------------------------

# The only columns in the schema that are TIMESTAMP WITHOUT TIME ZONE. Every
# other datetime is timestamptz, so any code that compares one of these against
# ``utcnow()`` has to normalise it first. Adding a column to this list is a
# decision, not an accident — which is the point of pinning it here.
KNOWN_NAIVE_DATETIME_COLUMNS = frozenset({
    "system_worker_status.last_heartbeat_at",
    "system_worker_status.started_at",
    "system_worker_status.last_successful_job_at",
    "system_worker_status.last_attempted_job_at",
    "system_worker_status.last_error_at",
})


def test_no_new_naive_datetime_column_appears_without_a_decision():
    """A new naive column is a new instance of this bug waiting to happen."""
    from app.shared.database.registry import Base
    from sqlalchemy import DateTime

    naive = {
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    }
    added = naive - KNOWN_NAIVE_DATETIME_COLUMNS
    assert not added, (
        "these datetime columns are TIMESTAMP WITHOUT TIME ZONE while the rest "
        "of the schema is timestamptz; comparing one against utcnow() raises "
        f"TypeError: {sorted(added)}"
    )
    removed = KNOWN_NAIVE_DATETIME_COLUMNS - naive
    assert not removed, (
        "these columns are no longer naive — good; remove them from "
        f"KNOWN_NAIVE_DATETIME_COLUMNS: {sorted(removed)}"
    )
