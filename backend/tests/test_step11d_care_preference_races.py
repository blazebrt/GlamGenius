"""Step 11D against the things that can remove a subject mid-write.

A Care preference and a Manager answer are both written *about a person*, and
three other operations can take that person away while the writing happens: a
household appearing, a member being switched off, and the whole account being
deleted. Step 11C established the orders that make those safe for Decision
Memory. None of it is automatically true here, because these are different
tables reached through a different route.

So the same questions are asked again of this path, and the answers come from
PostgreSQL rather than from reading the code: one transaction is held open and
the other is watched in ``pg_locks`` until it is genuinely waiting. Every wait is
bounded by ``lock_timeout``, because a test that demonstrates a deadlock by
hanging forever is not a proof, it is a broken build.

The order this path takes, and the reason for each step::

    self:   Account FOR UPDATE      -> canonical self re-resolution
            -> InventoryItem FOR UPDATE -> preference -> event
    member: Account FOR KEY SHARE   -> FamilyProfile FOR UPDATE
            -> InventoryItem FOR UPDATE -> preference -> event

The account comes first in both, because every child row here has an
``account_id`` foreign key and PostgreSQL therefore takes its own lock on the
account row as part of the insert. Taking it last would mean taking it in the
opposite direction from account deletion, which goes account-first.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from app.domains.care.models import CareProductPreference
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.inventory.models import InventoryEvent
from app.domains.routines.models import ShelfManagerDecisionEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, text

from tests.conftest import auth
from tests.test_step11b_lock_order import (
    LOCK_TIMEOUT_MS,
    PATIENT_TIMEOUT_MS,
    _backend_pid,
    _bounded,
)
from tests.test_step11d_subject_care_authority import (
    _expired_shelf,
    _member,
    _queue,
    _self_subject_id,
)
from tests.test_v3_03_3_integration import _seed

pytestmark = pytest.mark.asyncio

TODAY = date.today()
RESPOND_URL = "/api/v2/shelf/manager/respond"


def _body(primary: dict, *, choice: str = "accept", key: str) -> dict:
    return {
        "decision_key": primary["decision_key"],
        "decision_fingerprint": primary["decision_fingerprint"],
        "choice": choice,
        "client_mutation_id": key,
    }


def _url(subject_id: str | None) -> str:
    return RESPOND_URL if subject_id is None else f"{RESPOND_URL}?subject_id={subject_id}"


def _gate_before_authority(monkeypatch, barrier: asyncio.Barrier) -> None:
    """Hold each racing answer just before it takes any lock, then release both.

    Establishing write authority is the first thing an answer does and the first
    thing that locks anything, so this is the last point two requests can still
    reach independently. Past it they race for real.
    """
    from app.domains.family import decision_subject as authority

    original = authority.canonicalize_decision_subject_for_write
    gated: set[object] = set()

    async def _authorise(session, **kwargs):
        task = asyncio.current_task()
        if len(gated) < 2 and task not in gated:
            gated.add(task)
            await asyncio.wait_for(barrier.wait(), timeout=60)
        return await original(session, **kwargs)

    monkeypatch.setattr(
        authority, "canonicalize_decision_subject_for_write", _authorise,
    )


async def _announce_when_waiting(pid: int) -> None:
    """Poll ``pg_stat_activity`` until this backend is waiting on a lock.

    ``pg_locks`` answers "waiting on which relation", which is right for a row
    lock and blind to a unique-index conflict, where the waiter is parked on the
    inserter's transaction id and the relation column is NULL. This only needs to
    know that the wait is real before the other side is allowed to commit.
    """
    for _ in range(600):
        async with get_sessionmaker()() as watcher:
            waiting = await watcher.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE pid = :pid AND wait_event_type = 'Lock'"
                ),
                {"pid": pid},
            )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never waited on a lock")


async def _counts(account_id: uuid.UUID) -> dict[str, int]:
    async with get_sessionmaker()() as session:
        return {
            "preferences": await session.scalar(
                select(func.count(CareProductPreference.id))
                .where(CareProductPreference.account_id == account_id)
            ),
            "manager_events": await session.scalar(
                select(func.count(ShelfManagerDecisionEvent.id))
                .where(ShelfManagerDecisionEvent.account_id == account_id)
            ),
            "care_events": await session.scalar(
                select(func.count(InventoryEvent.id)).where(
                    InventoryEvent.account_id == account_id,
                    InventoryEvent.event_type == "care_routine_paused",
                )
            ),
        }


# ---------------------------------------------------------------------------
# A. Two answers, one member, one submission key
# ---------------------------------------------------------------------------


async def test_a_retry_that_overlapped_the_original_costs_a_member_nothing(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    primary = (await _queue(app_client, token, member))["primary"]
    body = _body(primary, key="race-member-identical")

    _gate_before_authority(monkeypatch, asyncio.Barrier(2))
    first, second = await asyncio.gather(
        app_client.post(_url(member), headers=auth(token), json=body),
        app_client.post(_url(member), headers=auth(token), json=body),
    )

    for response in (first, second):
        assert response.status_code == 200, response.text
        assert "IntegrityError" not in response.text
    applied = [response.json()["applied"] for response in (first, second)]
    assert sorted(row["replayed"] for row in applied) == [False, True]
    assert sorted(row["action_applied"] for row in applied) == [False, True]

    assert await _counts(account_id) == {
        "preferences": 1, "manager_events": 1, "care_events": 1,
    }
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(CareProductPreference))).scalar_one()
    assert str(row.household_subject_id) == member
    assert str(row.inventory_item_id) == item_id
    assert row.authority_source == "shelf_manager"


# ---------------------------------------------------------------------------
# B. Two people, one bottle, at the same instant
# ---------------------------------------------------------------------------


async def test_two_members_pausing_the_same_bottle_at_once_both_land(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """The shared product serialises them; their decisions do not merge.

    This is the race the whole slice exists for. Both answers touch the same
    physical row, so one waits for the other — and what each of them writes is
    their own, which is why the end state has two preferences rather than one
    overwritten by the other.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    first_member = await _member(app_client, token)
    second_member = await _member(app_client, token)
    first_primary = (await _queue(app_client, token, first_member))["primary"]
    second_primary = (await _queue(app_client, token, second_member))["primary"]
    assert first_primary["decision_key"] == second_primary["decision_key"]

    _gate_before_authority(monkeypatch, asyncio.Barrier(2))
    first, second = await asyncio.gather(
        app_client.post(
            _url(first_member), headers=auth(token),
            json=_body(first_primary, key="race-two-members-1"),
        ),
        app_client.post(
            _url(second_member), headers=auth(token),
            json=_body(second_primary, key="race-two-members-2"),
        ),
    )

    for response in (first, second):
        assert response.status_code == 200, response.text
        assert response.json()["applied"]["action_applied"] is True

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(CareProductPreference).where(
                CareProductPreference.account_id == account_id,
            )
        )).scalars().all()
    assert {str(row.household_subject_id) for row in rows} == {first_member, second_member}
    assert {str(row.inventory_item_id) for row in rows} == {item_id}
    assert await _counts(account_id) == {
        "preferences": 2, "manager_events": 2, "care_events": 2,
    }


# ---------------------------------------------------------------------------
# C. "Me", while a household is being created
# ---------------------------------------------------------------------------


async def test_an_unnamed_answer_cannot_be_written_into_the_gap_a_new_household_opens(
    app_client, db_clean, registered_supabase_user,
):
    """Omitting the subject means "me", and "me" must stay attributable.

    An answer that names nobody is written subject-less, and a subject-less row
    belongs to the account holder only while it provably predates the household.
    If a household could appear between an answer being authorised and being
    written, that answer would land on the far side of the boundary belonging to
    nobody — a record of a decision this person made, permanently unattributable
    to them.

    It cannot, and the reason is structural rather than lucky: the answer takes
    the account row ``FOR UPDATE`` before anything else, and creating a circle
    takes the same row ``FOR KEY SHARE`` through its foreign key. The two
    conflict, so PostgreSQL picks an order and the loser waits.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    primary = (await _queue(app_client, token))["primary"]

    started: asyncio.Queue = asyncio.Queue()
    release = asyncio.Event()

    async def _open_the_household() -> None:
        from app.domains.family import service as family_service

        async with get_sessionmaker()() as session:
            await _bounded(session, PATIENT_TIMEOUT_MS)
            await family_service.add_profile(session, account_id, relation="adult")
            await started.put(await _backend_pid(session))
            await release.wait()
            await session.commit()

    household = asyncio.create_task(_open_the_household())
    await started.get()

    answer = asyncio.create_task(
        app_client.post(RESPOND_URL, headers=auth(token), json=_body(primary, key="race-self-household"))
    )
    # The answer is genuinely waiting for the household transaction, not merely
    # slow: it is parked on the account row the circle's foreign key holds.
    await _announce_when_waiting_for(answer)
    release.set()
    await household

    response = await answer
    assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        event = (await session.execute(select(ShelfManagerDecisionEvent))).scalar_one()
        circle_created_at = await session.scalar(
            select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
        )
    assert circle_created_at is not None, "the household really was created"
    if event.household_subject_id is None:
        assert event.created_at < circle_created_at, (
            "an unnamed answer landed at or after the household was created, "
            "which makes it attributable to nobody"
        )
    else:
        assert event.household_subject_id == await _self_subject_id(account_id)


async def _announce_when_waiting_for(task: asyncio.Task) -> None:
    """Wait until *some* backend other than the watchers is blocked on a lock.

    The request runs inside the application's own session, whose backend pid the
    test never sees. Asking the database which backends are waiting is the
    honest substitute for guessing at a sleep.
    """
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
    raise AssertionError("nothing ever waited on a lock")


# ---------------------------------------------------------------------------
# D. A member switched off underneath their own answer
# ---------------------------------------------------------------------------


async def test_a_member_cannot_be_deactivated_underneath_their_own_answer(
    app_client, db_clean, registered_supabase_user,
):
    """One order or the other, never a write about somebody already removed.

    The member row is taken ``FOR UPDATE`` and re-read under the lock, and
    deactivation is an update of that same row. So either the answer commits and
    the deactivation follows it, or the deactivation wins and the answer is
    refused — and a refused member answer never falls back to storing the row
    subject-less, which the account holder would then read as their own.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    primary = (await _queue(app_client, token, member))["primary"]

    started: asyncio.Queue = asyncio.Queue()
    release = asyncio.Event()

    async def _switch_them_off() -> None:
        async with get_sessionmaker()() as session:
            await _bounded(session, PATIENT_TIMEOUT_MS)
            await session.execute(
                text("UPDATE family_profiles SET active = false WHERE id = :id"),
                {"id": uuid.UUID(member)},
            )
            await started.put(await _backend_pid(session))
            await release.wait()
            await session.commit()

    deactivation = asyncio.create_task(_switch_them_off())
    await started.get()

    answer = asyncio.create_task(
        app_client.post(
            _url(member), headers=auth(token),
            json=_body(primary, key="race-deactivation"),
        )
    )
    await _announce_when_waiting_for(answer)
    release.set()
    await deactivation

    response = await answer
    assert response.status_code == 404, response.text
    # Nothing at all was written — not for them, and not subject-less either.
    assert await _counts(account_id) == {
        "preferences": 0, "manager_events": 0, "care_events": 0,
    }


# ---------------------------------------------------------------------------
# E. The account deleted underneath a member's answer
# ---------------------------------------------------------------------------


async def test_no_care_preference_outlives_the_account_it_belongs_to(
    app_client, db_clean, registered_supabase_user,
):
    """The deletion takes the account row; the answer needs it to exist.

    Account deletion goes account-first and cascades outwards. An answer that
    locked a child row first and asked for the account afterwards would be
    taking the same two locks in the opposite order, which is a deadlock rather
    than a wait. It asks for the account first, so it waits — and finds the
    account gone.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    primary = (await _queue(app_client, token, member))["primary"]

    started: asyncio.Queue = asyncio.Queue()
    release = asyncio.Event()

    async def _delete_the_account() -> None:
        async with get_sessionmaker()() as session:
            await _bounded(session, PATIENT_TIMEOUT_MS)
            await session.execute(
                text("DELETE FROM accounts WHERE id = :id"), {"id": account_id},
            )
            await started.put(await _backend_pid(session))
            await release.wait()
            await session.commit()

    deletion = asyncio.create_task(_delete_the_account())
    await started.get()

    answer = asyncio.create_task(
        app_client.post(
            _url(member), headers=auth(token),
            json=_body(primary, key="race-deletion"),
        )
    )
    await _announce_when_waiting_for(answer)
    release.set()
    await deletion

    response = await answer
    assert response.status_code >= 400, response.text
    async with get_sessionmaker()() as session:
        assert await session.scalar(
            select(func.count(CareProductPreference.id))
            .where(CareProductPreference.account_id == account_id)
        ) == 0
        assert await session.scalar(
            select(func.count(ShelfManagerDecisionEvent.id))
            .where(ShelfManagerDecisionEvent.account_id == account_id)
        ) == 0


# ---------------------------------------------------------------------------
# F. The order itself, read from the database
# ---------------------------------------------------------------------------


async def test_the_account_is_locked_before_the_member_and_the_product(
    app_client, db_clean, registered_supabase_user,
):
    """The order is observed, not asserted in prose.

    Each of the three rows is held from a separate transaction in turn, and the
    answer is watched to see which one it stops at. An implementation that took
    them in another order would stop somewhere else.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    primary = (await _queue(app_client, token, member))["primary"]

    async with get_sessionmaker()() as blocker:
        await _bounded(blocker, PATIENT_TIMEOUT_MS)
        # The account row, taken the way deletion takes it.
        await blocker.execute(
            text("SELECT id FROM accounts WHERE id = :id FOR UPDATE"),
            {"id": account_id},
        )
        answer = asyncio.create_task(
            app_client.post(
                _url(member), headers=auth(token),
                json=_body(primary, key="race-order"),
            )
        )
        await _announce_when_waiting_for(answer)
        # Nothing further along has been taken: the member row and the product
        # row are both still free while the answer waits for the account.
        async with get_sessionmaker()() as prober:
            await _bounded(prober, LOCK_TIMEOUT_MS)
            assert await prober.scalar(
                select(FamilyProfile.id).where(FamilyProfile.id == uuid.UUID(member))
                .with_for_update(nowait=True)
            ) is not None
            assert await prober.execute(text(
                "SELECT id FROM inventory_items WHERE id = :id FOR UPDATE NOWAIT"
            ), {"id": uuid.UUID(item_id)})
        await blocker.rollback()

    response = await answer
    assert response.status_code == 200, response.text
    assert await _counts(account_id) == {
        "preferences": 1, "manager_events": 1, "care_events": 1,
    }


# ---------------------------------------------------------------------------
# The member row cannot be deleted out from under a preference
# ---------------------------------------------------------------------------


async def test_deleting_a_member_row_that_owns_a_preference_is_refused(
    app_client, db_clean, registered_supabase_user,
):
    """``NO ACTION`` on purpose, so nobody's Care state leaves silently.

    No route deletes a member today. When one is built it has to make an
    explicit decision about that person's stored Care preferences rather than
    have them disappear as a side effect of a cascade nobody remembered was
    there.
    """
    from sqlalchemy.exc import IntegrityError

    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    primary = (await _queue(app_client, token, member))["primary"]
    assert (await app_client.post(
        _url(member), headers=auth(token), json=_body(primary, key="fk-refusal"),
    )).status_code == 200

    async with get_sessionmaker()() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("DELETE FROM family_profiles WHERE id = :id"),
                {"id": uuid.UUID(member)},
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Erasure still erases everybody
# ---------------------------------------------------------------------------


async def test_deleting_the_account_erases_every_subjects_care_state(
    app_client, db_clean, registered_supabase_user,
):
    """A household does not create a corner erasure forgets.

    Each member's preferences and Manager answers are the customer's data and go
    with the account. The subject rows are children of the circle, which is a
    child of the account, so the cascade reaches all of it — and this proves it
    rather than assuming the foreign keys were declared the way they were meant
    to be.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    first = await _member(app_client, token)
    second = await _member(app_client, token)
    for index, member in enumerate((first, second)):
        primary = (await _queue(app_client, token, member))["primary"]
        assert (await app_client.post(
            _url(member), headers=auth(token), json=_body(primary, key=f"erase-{index}"),
        )).status_code == 200
    assert (await _counts(account_id))["preferences"] == 2

    async with get_sessionmaker()() as session:
        await session.execute(
            text("DELETE FROM accounts WHERE id = :id"), {"id": account_id},
        )
        await session.commit()

    assert await _counts(account_id) == {
        "preferences": 0, "manager_events": 0, "care_events": 0,
    }
    async with get_sessionmaker()() as session:
        assert await session.scalar(
            select(func.count(FamilyProfile.id))
            .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
            .where(FamilyCircle.account_id == account_id)
        ) == 0
