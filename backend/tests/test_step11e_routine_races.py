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


async def _generate_self(client, token: str):
    return await client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )


async def test_two_self_writes_adopt_the_legacy_routine_exactly_once(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    """Adoption is a move, and a move that happens twice is a second routine.

    The account holder's pre-household routine is adopted in place on their
    first household-era write: the row keeps its id, its steps and every
    completion recorded against it. Two writes arriving together must still
    produce one adoption. If both read the legacy row and both then decided to
    create a routine of their own instead, the account would end up holding a
    legacy morning routine *and* an explicit one — the exact dual state the read
    path refuses to interpret, manufactured by the product itself.
    """
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    # A pre-household routine, written the only way one can exist: before the
    # household does.
    assert (await _generate_self(app_client, token)).status_code == 200
    async with get_sessionmaker()() as session:
        legacy_id = await session.scalar(
            select(Routine.id).where(
                Routine.account_id == account_id,
                Routine.kind == "morning",
                Routine.household_subject_id.is_(None),
            )
        )
    assert legacy_id is not None
    await _member(app_client, token)

    _gate_first_write_authority(monkeypatch, 2)
    first, second = await asyncio.gather(
        _generate_self(app_client, token),
        _generate_self(app_client, token),
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
    assert len(rows) == 1, "the legacy routine was cloned rather than adopted"
    assert rows[0].id == legacy_id, "adoption replaced the row instead of moving it"
    assert rows[0].household_subject_id is not None


async def test_a_household_appearing_cannot_strand_a_self_routine(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """One order or the other, and both leave the routine attributable.

    A self write takes the account row ``FOR UPDATE``; creating a circle takes
    the same row ``FOR KEY SHARE`` through its foreign key. They conflict, so
    PostgreSQL picks an order rather than interleaving. Either outcome is
    honest: a routine written before the household is a legacy row, which is
    structurally the account holder's, and one written after names them.

    What must never happen is a row landing in neither state — and the check
    below is deliberately the one a reader performs, so a routine the product
    could not attribute would fail here rather than sit quietly in the table.
    """
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    started = asyncio.Event()
    release = asyncio.Event()

    async def open_the_household() -> None:
        from app.domains.family import service as family_service

        async with get_sessionmaker()() as session:
            await _bounded(session, PATIENT_TIMEOUT_MS)
            await family_service.add_profile(session, account_id, relation="adult")
            started.set()
            await release.wait()
            await session.commit()

    household = asyncio.create_task(open_the_household())
    await started.wait()
    request = asyncio.create_task(_generate_self(app_client, token))
    await _wait_for_lock_wait(request)
    release.set()
    await household

    response = await request
    assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "morning",
            )
        )).scalars().all()
    assert len(rows) == 1
    # The reader's own question, asked of the result.
    today = await app_client.get("/api/v2/routines/today", headers=auth(token))
    assert today.status_code == 200, today.text
    assert today.json()["subject"]["is_account_holder"] is True


async def test_a_member_write_does_not_queue_behind_an_exclusive_account_lock(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """Members share the account row; they do not take turns on it.

    Writing about one member locks that member's own household row and takes
    only ``FOR KEY SHARE`` on the account, which is what keeps two people in one
    household from serialising behind each other for no reason. The account
    holder's own path takes ``FOR UPDATE`` on that row, because their write is
    about the account's own identity.

    Both halves are read from PostgreSQL rather than asserted: a held
    ``FOR KEY SHARE`` lets a member write straight through, and the same held
    lock stops a self write. A member path that had been "simplified" to take
    ``FOR UPDATE`` would fail the first half.
    """
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)

    async with get_sessionmaker()() as holder:
        await _bounded(holder, PATIENT_TIMEOUT_MS)
        await holder.execute(
            text("SELECT id FROM accounts WHERE id = :id FOR KEY SHARE"),
            {"id": account_id},
        )
        member_write = await asyncio.wait_for(
            _generate(app_client, token, member), timeout=30,
        )
        assert member_write.status_code == 200, member_write.text

        self_write = asyncio.create_task(_generate_self(app_client, token))
        await _wait_for_lock_wait(self_write)
        assert not self_write.done(), (
            "the account holder's own write did not wait for the account row"
        )
        await holder.rollback()

    assert (await self_write).status_code == 200
