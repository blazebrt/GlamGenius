"""Step 11E PostgreSQL races for persisted routine identity and adherence."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.routines.models import Routine, RoutineAdherence
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, text

from tests.conftest import auth
from tests.test_domain_routines_api import _seeded_shelf
from tests.test_step11b_lock_order import PATIENT_TIMEOUT_MS, _bounded
from tests.test_step11d_subject_care_authority import _member

pytestmark = pytest.mark.asyncio


def _gate_first_write_authority(monkeypatch, parties: int) -> None:
    """Release the first write-authority attempt from each request together."""
    from app.domains.family import decision_subject as authority

    original = authority.canonicalize_decision_subject_for_write
    barrier = asyncio.Barrier(parties)
    gated: set[object] = set()

    async def wrapped(session, **kwargs):
        task = asyncio.current_task()
        if task not in gated and len(gated) < parties:
            gated.add(task)
            await asyncio.wait_for(barrier.wait(), timeout=60)
        return await original(session, **kwargs)

    monkeypatch.setattr(authority, "canonicalize_decision_subject_for_write", wrapped)


async def _generate(client, token: str, member: str):
    return await client.post(
        f"/api/v2/routines/generate?subject_id={member}",
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )


async def _wait_for_lock_wait(task: asyncio.Task) -> None:
    for _ in range(600):
        if task.done():
            return
        async with get_sessionmaker()() as watcher:
            waiting = await watcher.scalar(text(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
            ))
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("request never reached a PostgreSQL lock wait")


async def test_same_member_concurrent_generation_settles_on_one_routine(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)

    _gate_first_write_authority(monkeypatch, 2)
    first, second = await asyncio.gather(
        _generate(app_client, token, member),
        _generate(app_client, token, member),
    )
    for response in (first, second):
        assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == uuid.UUID(member),
                Routine.kind == "morning",
            )
        )).scalars().all()
    assert len(rows) == 1


async def test_two_members_can_generate_independently_at_the_same_time(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    first_member = await _member(app_client, token)
    second_member = await _member(app_client, token)

    _gate_first_write_authority(monkeypatch, 2)
    first, second = await asyncio.gather(
        _generate(app_client, token, first_member),
        _generate(app_client, token, second_member),
    )
    for response in (first, second):
        assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "morning",
            )
        )).scalars().all()
    assert {str(row.household_subject_id) for row in rows} == {
        first_member, second_member,
    }


async def test_same_subject_same_day_concurrent_completion_is_one_logical_row(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    generated = (await _generate(app_client, token, member)).json()
    step_id = generated["routines"][0]["steps"][0]["id"]
    url = f"/api/v2/routines/steps/{step_id}/complete?subject_id={member}"

    _gate_first_write_authority(monkeypatch, 2)
    first, second = await asyncio.gather(
        app_client.post(url, headers=auth(token), json={"completed": True}),
        app_client.post(url, headers=auth(token), json={"completed": True}),
    )
    for response in (first, second):
        assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        count = await session.scalar(
            select(func.count(RoutineAdherence.id)).where(
                RoutineAdherence.account_id == account_id,
            )
        )
    assert count == 1


async def test_deactivation_that_commits_first_refuses_member_generation(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)

    started = asyncio.Event()
    release = asyncio.Event()

    async def deactivate() -> None:
        async with get_sessionmaker()() as session:
            await _bounded(session, PATIENT_TIMEOUT_MS)
            await session.execute(
                text("UPDATE family_profiles SET active = false WHERE id = :id"),
                {"id": uuid.UUID(member)},
            )
            started.set()
            await release.wait()
            await session.commit()

    blocker = asyncio.create_task(deactivate())
    await started.wait()
    request = asyncio.create_task(_generate(app_client, token, member))
    await _wait_for_lock_wait(request)
    release.set()
    await blocker
    response = await request
    assert response.status_code == 404, response.text

    async with get_sessionmaker()() as session:
        count = await session.scalar(
            select(func.count(Routine.id)).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == uuid.UUID(member),
            )
        )
    assert count == 0


async def test_account_delete_that_commits_first_leaves_no_member_routine(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)

    started = asyncio.Event()
    release = asyncio.Event()

    async def delete_account() -> None:
        async with get_sessionmaker()() as session:
            await _bounded(session, PATIENT_TIMEOUT_MS)
            await session.execute(
                text("DELETE FROM accounts WHERE id = :id"),
                {"id": account_id},
            )
            started.set()
            await release.wait()
            await session.commit()

    deletion = asyncio.create_task(delete_account())
    await started.wait()
    request = asyncio.create_task(_generate(app_client, token, member))
    await _wait_for_lock_wait(request)
    release.set()
    await deletion

    response = await request
    assert response.status_code >= 400, response.text
    async with get_sessionmaker()() as session:
        assert await session.scalar(
            select(func.count(Routine.id)).where(Routine.account_id == account_id)
        ) == 0
