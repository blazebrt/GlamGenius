"""Worker-level RR-01 regression coverage."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.workers import notifications as worker


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _Session:
    """A session that models the worker's two-step read.

    The worker first selects the eligible account ids, then re-reads each
    account's row inside the loop, so that a rollback cannot leave an expired
    ORM row in the failure handler. This double answers both queries in turn.
    """

    def __init__(self, rows):
        self.rows = rows
        self.rollbacks = 0
        self._pending = None

    async def execute(self, _statement):
        if self._pending is None:
            # First call: the id sweep.
            self._pending = list(self.rows)
            return _Result([row.account_id for row in self.rows])
        # Subsequent calls: one re-read per account, in order.
        return _Result([self._pending.pop(0)] if self._pending else [])

    async def rollback(self):
        self.rollbacks += 1


class _Factory:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_process_once_isolates_one_account_failure_and_continues(monkeypatch):
    failed = SimpleNamespace(account_id="failed")
    healthy = SimpleNamespace(account_id="healthy")
    session = _Session([failed, healthy])
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: lambda: _Factory(session))

    async def process_account(_session, preference, **_kwargs):
        if preference is failed:
            raise RuntimeError("provider unavailable")
        return 1

    monkeypatch.setattr(worker, "process_account", process_account)
    assert await worker.process_once() == 1
    assert session.rollbacks == 1


# The fake-session test above pins the control flow, but a SimpleNamespace
# attribute read needs no IO, so it cannot observe the failure that actually
# occurs against a real session: rollback expires the ORM rows, and the next
# attribute read on one raises MissingGreenlet. These two exercise the worker
# against the real database for that reason.


@pytest.mark.asyncio
async def test_one_account_failure_does_not_abort_the_real_batch(
    db_clean, registered_supabase_user, monkeypatch,
):
    """The hourly worker attempts every eligible account, or it is not hourly."""
    from app.domains.planning.models import NotificationPreference
    from app.shared.database.sql import get_sessionmaker

    _, first = await registered_supabase_user()
    _, second = await registered_supabase_user()
    _, third = await registered_supabase_user()

    factory = get_sessionmaker()
    async with factory() as session:
        for account_id in (first, second, third):
            session.add(NotificationPreference(
                account_id=account_id, timezone_name="Asia/Kolkata",
                enabled=True, native_push_enabled=True,
            ))
        await session.commit()

    seen: list[str] = []

    async def flaky_process_account(_session, preference, **_kwargs):
        seen.append(str(preference.account_id))
        if preference.account_id == second:
            raise RuntimeError("provider exploded for this account only")
        return 1

    monkeypatch.setattr(worker, "process_account", flaky_process_account)

    total = await worker.process_once()

    assert len(seen) == 3, f"every eligible account must be attempted, saw {len(seen)}"
    assert {str(first), str(second), str(third)} == set(seen)
    assert total == 2, "the two healthy accounts still sent"


@pytest.mark.asyncio
async def test_the_real_batch_only_considers_accounts_that_opted_into_push(
    db_clean, registered_supabase_user, monkeypatch,
):
    from app.domains.planning.models import NotificationPreference
    from app.shared.database.sql import get_sessionmaker

    _, opted_in = await registered_supabase_user()
    _, notifications_off = await registered_supabase_user()
    _, push_off = await registered_supabase_user()

    factory = get_sessionmaker()
    async with factory() as session:
        session.add(NotificationPreference(
            account_id=opted_in, timezone_name="Asia/Kolkata",
            enabled=True, native_push_enabled=True,
        ))
        session.add(NotificationPreference(
            account_id=notifications_off, timezone_name="Asia/Kolkata",
            enabled=False, native_push_enabled=True,
        ))
        session.add(NotificationPreference(
            account_id=push_off, timezone_name="Asia/Kolkata",
            enabled=True, native_push_enabled=False,
        ))
        await session.commit()

    seen: list[str] = []

    async def record(_session, preference, **_kwargs):
        seen.append(str(preference.account_id))
        return 0

    monkeypatch.setattr(worker, "process_account", record)
    await worker.process_once()
    assert seen == [str(opted_in)]
