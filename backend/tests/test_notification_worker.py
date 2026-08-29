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


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.rollbacks = 0

    async def execute(self, _statement):
        return _Result(self.rows)

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
