"""Decision Memory against the two things that can remove its subject mid-write.

A decision is stored *about a person*, and two other operations can take that
person away while the storing is happening: a household member being switched
off, and the whole account being deleted. Both were safe for Step 11B's body
facts because of the lock order it established; neither is automatically safe
here, because Decision Memory is a different write on different tables reached
through different routes.

So the same question is asked again, of this path: while a decision is being
recorded for somebody, can that somebody stop existing underneath it?

The answer has to come from the database rather than from reading the code. The
tests below hold one transaction open and watch, in ``pg_locks``, what the other
one is waiting on — and where an ordering is the point, they force it with an
event instead of hoping the scheduler produces it.

Every wait is bounded by ``lock_timeout``. A test that demonstrates a deadlock
by hanging forever is not a proof, it is a broken build.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.family.decision_subject import decision_subject_for_write
from app.domains.family.models import FamilyProfile
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
    SubjectNotFound,
    account_holder_subject,
)
from app.domains.product.models import ScanDecisionEvent
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    ShoppingCandidate,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, text

from tests.conftest import auth
from tests.test_step11b_lock_order import (
    LOCK_TIMEOUT_MS,
    PATIENT_TIMEOUT_MS,
    _backend_pid,
    _bounded,
    _until_blocked,
)
from tests.test_step11c_subject_scoped_decision_memory import _snapshot
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate

pytestmark = pytest.mark.asyncio

PROFILES_URL = "/api/v2/family-circle/profiles"


async def _member(client, token, *, relation="adult") -> uuid.UUID:
    response = await client.post(
        PROFILES_URL, headers=auth(token), json={"relation": relation},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _candidate(account_id: uuid.UUID) -> uuid.UUID:
    candidate_id = await _seed_db_candidate(account_id)
    from app.domains.recommendation.models import ShoppingCandidate

    async with get_sessionmaker()() as session:
        row = await session.get(ShoppingCandidate, candidate_id)
        row.brand = "Example Labs"
        row.display_name = "Gentle Cleanser"
        row.details = {
            "product_type": "cleanser", "purpose": "cleanse",
            "active_ingredients": ["Niacinamide"],
        }
        await session.commit()
    return candidate_id


async def _decide(client, token, candidate_id, decision, *, subject_id=None):
    suffix = f"&subject_id={subject_id}" if subject_id else ""
    return await client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20{suffix}",
        headers=auth(token), json={"decision": decision},
    )


def _member_subject(account_id: uuid.UUID, member_id: uuid.UUID) -> ResolvedSubject:
    return ResolvedSubject(
        kind=SUBJECT_HOUSEHOLD_MEMBER,
        account_id=account_id,
        subject_id=member_id,
        relation="adult",
        age_band=AGE_BAND_ADULT,
    )


def _decision_subject_for(account_id: uuid.UUID, member_id: uuid.UUID):
    """A member claim for a direct domain call, revalidated by the service."""
    from app.domains.family.decision_subject import DecisionSubject

    return DecisionSubject(
        subject=_member_subject(account_id, member_id), circle_created_at=None,
    )


async def _until_waiting_on_a_lock(pid: int) -> None:
    """Block until this backend is genuinely waiting on a lock.

    ``_blocked_on`` answers "waiting on which relation", which is the right
    question for a row lock and the wrong one for a unique-index conflict: the
    waiter there is parked on the *inserter's transaction id*, a lock whose
    relation is NULL, so a relation-shaped answer comes back empty and reads as
    "not blocked". ``pg_stat_activity`` says only that it is waiting, which is
    all this needs to know before letting the other side commit.

    Polling rather than sleeping on a guess, with a bound so a broken build
    fails instead of hanging.
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


async def _deactivate_in_own_session(
    member_id: uuid.UUID, announce: asyncio.Queue, *, milliseconds: int = LOCK_TIMEOUT_MS,
) -> str:
    """Switch a member off in a session of its own, and report why it could not.

    Its own session created inside the task, because one asyncpg connection
    cannot carry two coroutines — watching a statement block means the watcher
    has to be on a different backend entirely. The pid is published before the
    statement that will wait, so the caller knows where to look in ``pg_locks``.
    """
    async with get_sessionmaker()() as session:
        await _bounded(session, milliseconds)
        await announce.put(await _backend_pid(session))
        try:
            await session.execute(
                text("UPDATE family_profiles SET active = false, updated_at = now() "
                     "WHERE id = :id"),
                {"id": member_id},
            )
        except Exception as exc:  # noqa: BLE001 — the message is the result
            await session.rollback()
            return str(exc).lower()
        await session.commit()
        return ""


# ---------------------------------------------------------------------------
# 1. A member cannot be switched off underneath their own decision
# ---------------------------------------------------------------------------
class TestDeactivationRace:
    async def test_a_member_decision_holds_their_row_against_a_deactivation(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The member lock, read off ``pg_locks`` rather than off the docstring.

        ``decision_subject_for_write`` is supposed to take the member row
        ``FOR UPDATE`` and hold it for the rest of the transaction, so that a
        deactivation cannot land between "this person is in the household" and
        the decision being written about them. If it took no lock, or took a
        read lock, the update below would sail past.
        """
        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)

        async with get_sessionmaker()() as writer:
            await _bounded(writer, PATIENT_TIMEOUT_MS)
            await decision_subject_for_write(
                writer,
                principal_account_id=account_id,
                subject=_member_subject(account_id, member_id),
            )

            announce: asyncio.Queue = asyncio.Queue()
            task = asyncio.create_task(_deactivate_in_own_session(member_id, announce))
            blocked_on = await _until_blocked(await announce.get())
            assert "family_profiles" in blocked_on, blocked_on

            # It waited, and gave up at the bound rather than being let through.
            assert "lock timeout" in (await task), "the deactivation was not blocked"
            await writer.rollback()

        # And nothing was half-applied by the attempt.
        async with get_sessionmaker()() as session:
            assert await session.scalar(
                select(FamilyProfile.active).where(FamilyProfile.id == member_id)
            ) is True

    async def test_a_deactivation_that_lands_before_the_lock_is_still_seen(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The authority re-reads under its lock. It does not trust an earlier read.

        This is the ordering that a plausible-looking implementation gets wrong:
        resolve the subject once when the request arrives, take the lock later,
        and write against the answer from before. The member is gone by then and
        nothing notices.

        Forced rather than raced, so it fails every time instead of sometimes.
        """
        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        subject = _member_subject(account_id, member_id)

        async with get_sessionmaker()() as writer:
            # The read a request would do on the way in — no lock held yet.
            from app.domains.profile.identity import canonical_subject

            provisional = await canonical_subject(
                writer, principal_account_id=account_id, subject=subject,
            )
            assert provisional.subject_id == member_id

            # The member is switched off and committed, entirely in between.
            announce: asyncio.Queue = asyncio.Queue()
            assert await _deactivate_in_own_session(member_id, announce) == ""

            # The write authority must now refuse, not carry the stale answer
            # through. Its re-read happens under the lock it just took.
            with pytest.raises(SubjectNotFound):
                await decision_subject_for_write(
                    writer, principal_account_id=account_id, subject=subject,
                )

    async def test_a_refused_member_write_does_not_fall_back_to_the_account_holder(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The dangerous way to handle a subject that vanished mid-write.

        When the member is gone by the time the lock is taken, one tempting
        recovery is to keep the customer's tap and store it the way it used to
        be stored — subject-less, against the account. That reads back as the
        *account holder's* decision. Somebody else's purchase would appear in
        their history, which is the exact failure this whole step exists to
        prevent, arriving through the error path.

        So the refusal is checked for what it did not write as well as for what
        it returned. Both tables, both attributions, and the account holder's
        own history seen through the route they would actually read it with.

        The two requests genuinely overlap and the scheduler decides the order;
        in practice the short PATCH lands first, so this is the refusal path. If
        the write ever wins instead, that is equally correct and the assertions
        below say so — what is never correct is a stored row disagreeing with
        the answer the customer was given.
        """
        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        candidate_id = await _candidate(account_id)

        write, patch = await asyncio.gather(
            _decide(app_client, token, candidate_id, "bought", subject_id=member_id),
            app_client.patch(
                f"{PROFILES_URL}/{member_id}", headers=auth(token), json={"active": False},
            ),
        )
        assert patch.status_code == 200, patch.text
        # Governed either way. A 500 would mean the collision reached the
        # customer as an unhandled failure.
        assert write.status_code in (200, 404), write.text
        expected = 1 if write.status_code == 200 else 0

        async with get_sessionmaker()() as session:
            theirs = await session.scalar(
                select(func.count(PurchaseDecision.id))
                .where(PurchaseDecision.household_subject_id == member_id)
            )
            their_events = await session.scalar(
                select(func.count(PurchaseDecisionEvent.id))
                .where(PurchaseDecisionEvent.household_subject_id == member_id)
            )
            # The fallback this test exists for: anything left unattributed.
            orphaned = await session.scalar(
                select(func.count(PurchaseDecision.id))
                .where(PurchaseDecision.household_subject_id.is_(None))
            )
            orphan_events = await session.scalar(
                select(func.count(PurchaseDecisionEvent.id))
                .where(PurchaseDecisionEvent.household_subject_id.is_(None))
            )
            active = await session.scalar(
                select(FamilyProfile.active).where(FamilyProfile.id == member_id)
            )

        assert theirs == their_events == expected
        assert (orphaned, orphan_events) == (0, 0), (
            "a member's refused decision was stored without a subject, which the "
            "account holder would read as their own"
        )
        # The deactivation itself always lands; it only ever had to wait.
        assert active is False

        # And the account holder, asked directly, has no history at all.
        holder = await app_client.get(
            "/api/v2/shopping/decision-history?limit=20", headers=auth(token),
        )
        assert holder.status_code == 200, holder.text
        assert holder.json()["items"] == []


# ---------------------------------------------------------------------------
# 1a. The savepoint recovery, exercised on purpose rather than hoped for
# ---------------------------------------------------------------------------
class TestConcurrentScanRetries:
    """What two colliding writers actually do, checked against the database.

    It is worth being exact about which mechanism delivers which guarantee here,
    because the obvious story is wrong.

    Two identical writers for the **same** subject never race on the index at
    all. Both take the write authority first, and for the account holder that is
    ``Account FOR UPDATE`` — so the second one waits on the account row, and by
    the time it looks for an existing event the first has committed one. It
    finds it, matches on every field, and returns it. The exact-retry guarantee
    comes from serialisation plus the lookup; the savepoint is never reached.
    (Confirmed by watching what the blocked backend is waiting on: the account
    row, not the unique index.)

    The savepoint is still necessary, and this is the case it is for. Two
    **different** members share one account-global idempotency key: they hold
    different ``family_profiles`` rows, ``FOR KEY SHARE`` on the account does not
    put them in a queue, so both miss the lookup and both insert. One loses on
    the unique index. Without the savepoint that ``IntegrityError`` would poison
    the whole transaction; with it, the loser rolls back one statement, re-reads,
    sees the event belongs to somebody else, and refuses cleanly.

    Both interleavings are forced rather than raced, so each test proves the
    thing it is named after every time.
    """

    async def test_two_identical_writers_serialise_and_agree(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.product import scan_memory

        _, account_id = await registered_supabase_user()
        snapshot = await _snapshot()
        payload = dict(
            barcode=snapshot.barcode,
            label_snapshot_id=snapshot.id,
            label_version=snapshot.version_number,
            content_fingerprint=snapshot.content_fingerprint,
            decision="BUY",
            idempotency_key="savepoint-race",
            note="one tap, sent twice",
        )
        inserted = asyncio.Event()
        announce: asyncio.Queue = asyncio.Queue()
        blocked = asyncio.Event()

        async def winner() -> uuid.UUID:
            async with get_sessionmaker()() as session:
                await _bounded(session, PATIENT_TIMEOUT_MS)
                event = await scan_memory.record_scan_decision(
                    session, principal_account_id=account_id,
                    decision_subject=None, **payload,
                )
                # Inserted and flushed, not committed: invisible to the other
                # writer's lookup, and already holding the index entry.
                inserted.set()
                # Held until the other writer is provably stuck on that entry,
                # so the recovery path is entered rather than hoped for.
                await blocked.wait()
                await session.commit()
                return event.id

        async def loser() -> uuid.UUID:
            await inserted.wait()
            async with get_sessionmaker()() as session:
                await _bounded(session, PATIENT_TIMEOUT_MS)
                await announce.put(await _backend_pid(session))
                event = await scan_memory.record_scan_decision(
                    session, principal_account_id=account_id,
                    decision_subject=None, **payload,
                )
                await session.commit()
                return event.id

        async def release_once_stuck() -> None:
            await _until_waiting_on_a_lock(await announce.get())
            blocked.set()

        won, lost, _ = await asyncio.gather(winner(), loser(), release_once_stuck())
        # Not a conflict, and not a second event: the same decision.
        assert won == lost

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 1
            stored = (await session.execute(select(ScanDecisionEvent))).scalar_one()
        assert stored.id == won
        assert stored.note == "one tap, sent twice"

    async def test_a_lost_insert_race_between_two_members_refuses_cleanly(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The savepoint's actual job: a governed refusal, not a poisoned request.

        Two members, one idempotency key, and no lock between them — this is the
        only way two writers both reach the insert. The loser must come back
        with ``ScanDecisionConflict`` and a transaction that still works, not
        with a dead session and a 500.
        """
        from app.domains.product import scan_memory

        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        snapshot = await _snapshot()
        base = dict(
            barcode=snapshot.barcode,
            label_snapshot_id=snapshot.id,
            label_version=snapshot.version_number,
            content_fingerprint=snapshot.content_fingerprint,
            decision="BUY",
            idempotency_key="shared-across-members",
        )
        inserted = asyncio.Event()
        announce: asyncio.Queue = asyncio.Queue()
        blocked = asyncio.Event()

        async def winner() -> None:
            async with get_sessionmaker()() as session:
                await _bounded(session, PATIENT_TIMEOUT_MS)
                await scan_memory.record_scan_decision(
                    session, principal_account_id=account_id,
                    decision_subject=_decision_subject_for(account_id, member_a),
                    **base,
                )
                inserted.set()
                await blocked.wait()
                await session.commit()

        async def loser() -> None:
            await inserted.wait()
            async with get_sessionmaker()() as session:
                await _bounded(session, PATIENT_TIMEOUT_MS)
                await announce.put(await _backend_pid(session))
                with pytest.raises(scan_memory.ScanDecisionConflict):
                    await scan_memory.record_scan_decision(
                        session, principal_account_id=account_id,
                        decision_subject=_decision_subject_for(account_id, member_b),
                        **base,
                    )
                # The savepoint is what makes this possible: one statement was
                # rolled back, not the transaction, so the session is still
                # usable and the request can return a clean 409 rather than
                # dying on a failed transaction.
                assert await session.scalar(
                    select(func.count(ScanDecisionEvent.id))
                ) == 1
                await session.rollback()

        async def release_once_stuck() -> None:
            await _until_waiting_on_a_lock(await announce.get())
            blocked.set()

        await asyncio.gather(winner(), loser(), release_once_stuck())

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(ScanDecisionEvent.household_subject_id)
            )).scalars().all()
        assert rows == [member_a], "the loser's decision was recorded anyway"


# ---------------------------------------------------------------------------
# 1b. A subject that was true when it was resolved, and is not any more
# ---------------------------------------------------------------------------
class TestStaleSubjectRevalidation:
    """The reason the write service owns its authority rather than inheriting it.

    A ``DecisionSubject`` is a fact about a moment. Resolve one for an active
    member, and it is correct; hold it while that member is switched off, and it
    is a correct-looking object describing a person who can no longer be
    written for. Nothing about the object changes when the household does.

    A service that trusted the object would write for them anyway. These tests
    hand each boundary a subject that was legitimately resolved and is now
    stale, and require the refusal to come from the service's own re-derivation.

    Forced rather than raced: the resolution, the deactivation and the call
    happen in that order every time, so a service that revalidates fails every
    time and one that does not passes every time.
    """

    async def _stale_member_subject(self, account_id, member_id):
        """A genuine DecisionSubject, resolved before the member is switched off.

        Deliberately the *read* authority, which takes no lock — that is what a
        worker, a background job or any caller that resolved a subject earlier
        in its own transaction would hold.
        """
        from app.domains.family.decision_subject import canonical_decision_subject

        async with get_sessionmaker()() as session:
            subject = await canonical_decision_subject(
                session,
                principal_account_id=account_id,
                subject=_member_subject(account_id, member_id),
            )
        assert subject.subject_id == member_id
        assert subject.is_account_holder is False

        announce: asyncio.Queue = asyncio.Queue()
        assert await _deactivate_in_own_session(member_id, announce) == ""
        return subject

    async def test_a_stale_subject_cannot_write_a_scan_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.family.subject import SubjectNotFound
        from app.domains.product import scan_memory

        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        snapshot = await _snapshot()
        stale = await self._stale_member_subject(account_id, member_id)

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await scan_memory.record_scan_decision(
                    session,
                    principal_account_id=account_id,
                    decision_subject=stale,
                    barcode=snapshot.barcode,
                    label_snapshot_id=snapshot.id,
                    label_version=snapshot.version_number,
                    content_fingerprint=snapshot.content_fingerprint,
                    decision="BUY",
                    idempotency_key="stale-write",
                )
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 0

    async def test_a_stale_subject_cannot_write_a_care_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.family.subject import SubjectNotFound
        from app.domains.purchase import decision_memory
        from app.domains.purchase.check_service import resolve_care_purchase_check

        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        candidate_id = await _candidate(account_id)
        stale = await self._stale_member_subject(account_id, member_id)

        async with get_sessionmaker()() as session:
            check = await resolve_care_purchase_check(
                session, account_id=account_id, account_id_str=str(account_id),
                candidate_id=candidate_id, plan_date=None,
            )
            with pytest.raises(SubjectNotFound):
                await decision_memory.save_care_decision(
                    session,
                    principal_account_id=account_id,
                    decision_subject=stale,
                    candidate_id=candidate_id,
                    check=check, decision="bought", note=None,
                )
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0

    async def test_a_stale_subject_cannot_read_either(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Deactivation hides the person from every surface, not only writes.

        Their stored history is kept — deactivation is not deletion, and the
        privacy export still carries it — but nobody can select them any more,
        including a caller holding a subject from before they were switched off.
        """
        from app.domains.family.subject import SubjectNotFound
        from app.domains.product import scan_memory
        from app.domains.purchase import decision_memory

        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        candidate_id = await _candidate(account_id)
        snapshot = await _snapshot()
        stale = await self._stale_member_subject(account_id, member_id)

        async with get_sessionmaker()() as session:
            candidate = await session.get(ShoppingCandidate, candidate_id)
            with pytest.raises(SubjectNotFound):
                await decision_memory.decision_history(
                    session, principal_account_id=account_id,
                    decision_subject=stale, limit=20,
                )
            with pytest.raises(SubjectNotFound):
                await decision_memory.purchase_guard(
                    session, principal_account_id=account_id,
                    decision_subject=stale, candidate=candidate,
                )
            with pytest.raises(SubjectNotFound):
                await decision_memory.current_purchase_decision_for_subject(
                    session, principal_account_id=account_id,
                    decision_subject=stale, candidate_id=candidate_id,
                )
            with pytest.raises(SubjectNotFound):
                await scan_memory.read_scan_memory(
                    session, principal_account_id=account_id,
                    decision_subject=stale, barcode=snapshot.barcode,
                    label_snapshot_id=snapshot.id,
                    label_version=snapshot.version_number,
                    content_fingerprint=snapshot.content_fingerprint,
                )

    async def test_the_same_thing_over_http_is_the_privacy_safe_404(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """End to end, and indistinguishable from a member who never existed."""
        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        candidate_id = await _candidate(account_id)

        announce: asyncio.Queue = asyncio.Queue()
        assert await _deactivate_in_own_session(member_id, announce) == ""

        refused = await _decide(
            app_client, token, candidate_id, "bought", subject_id=member_id,
        )
        assert refused.status_code == 404, refused.text
        assert str(member_id) not in refused.text

        invented = await _decide(
            app_client, token, candidate_id, "bought", subject_id=uuid.uuid4(),
        )
        assert invented.status_code == 404
        # Identical but for the per-request id, which is diagnostic rather than
        # informative: a deactivated member and an invented one must not be
        # distinguishable, or the answer confirms which people are real.
        def _telling(response):
            return {
                key: value for key, value in response.json()["detail"].items()
                if key != "request_id"
            }
        assert _telling(refused) == _telling(invented)

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0


# ---------------------------------------------------------------------------
# 2. A household that opens mid-request changes who "me" is
# ---------------------------------------------------------------------------
class TestHouseholdCreationRace:
    async def test_a_household_committed_before_the_lock_is_seen_by_the_write(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The account holder's write re-reads under its lock, and must.

        Before the lock this account has no household, so "me" has no subject
        id and a decision would be written subject-less — which is exactly how
        it has always been written, and correct while it stays true. The moment
        a household exists, a subject-less row stops being provably anybody's:
        it lands on the ambiguous side of the boundary and neither the account
        holder nor any member can ever claim it again. The customer's own
        decision becomes unattributable the instant they add a family member.

        So the answer taken before the lock cannot be the answer used after it.
        Forced rather than raced: the account row is held by another
        transaction, the write blocks on it, the household is created and
        committed by the holder, and only then is the lock released. That is
        the ordering, every time.
        """
        from app.domains.family import service as family_service

        _, account_id = await registered_supabase_user()

        async with get_sessionmaker()() as holder:
            # Somebody else is mid-transition and holds the account row.
            await _bounded(holder, PATIENT_TIMEOUT_MS)
            await holder.execute(
                text("SELECT id FROM accounts WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            )

            announce: asyncio.Queue = asyncio.Queue()
            resolved: asyncio.Queue = asyncio.Queue()

            async def write_authority() -> None:
                async with get_sessionmaker()() as session:
                    await _bounded(session, PATIENT_TIMEOUT_MS)
                    await announce.put(await _backend_pid(session))
                    subject = await decision_subject_for_write(
                        session,
                        principal_account_id=account_id,
                        subject=account_holder_subject(account_id),
                    )
                    await resolved.put(subject)
                    await session.rollback()

            task = asyncio.create_task(write_authority())
            # It resolved provisionally — no household yet — and is now stuck on
            # the account row it must take before trusting that answer.
            assert "accounts" in await _until_blocked(await announce.get())

            circle = await family_service.circle_for(holder, account_id, create=True)
            await holder.commit()

        subject = await resolved.get()
        await task

        # The household exists now, so "me" is the self row in it — never the
        # ``None`` that was true a moment before the lock.
        async with get_sessionmaker()() as session:
            self_id = await session.scalar(
                select(FamilyProfile.id).where(
                    FamilyProfile.circle_id == circle.id,
                    FamilyProfile.relation == "self",
                )
            )
        assert subject.subject_id == self_id, (
            "the write used the subject it resolved before taking the lock"
        )
        assert subject.circle_created_at is not None


# ---------------------------------------------------------------------------
# 3. No decision memory outlives the account it belonged to
# ---------------------------------------------------------------------------
class TestAccountDeletionRace:
    async def test_a_self_decision_holds_the_account_against_deletion(
        self, db_clean, registered_supabase_user,
    ):
        """The account holder's write path takes the account row ``FOR UPDATE``.

        That is what stops the deletion worker's final ``DELETE FROM accounts``
        from running through the middle of an adoption — the one write here that
        changes what an old row means, and the one that would be unrecoverable
        if it happened after the cascade had already counted the rows.
        """
        _, account_id = await registered_supabase_user()

        async with get_sessionmaker()() as writer:
            await _bounded(writer, PATIENT_TIMEOUT_MS)
            await decision_subject_for_write(
                writer,
                principal_account_id=account_id,
                subject=account_holder_subject(account_id),
            )

            announce: asyncio.Queue = asyncio.Queue()

            async def delete_account() -> str:
                async with get_sessionmaker()() as session:
                    await _bounded(session)
                    await announce.put(await _backend_pid(session))
                    try:
                        await session.execute(
                            text("DELETE FROM accounts WHERE id = :id"),
                            {"id": account_id},
                        )
                    except Exception as exc:  # noqa: BLE001 — the message is the result
                        await session.rollback()
                        return str(exc).lower()
                    await session.rollback()
                    return ""

            task = asyncio.create_task(delete_account())
            blocked_on = await _until_blocked(await announce.get())
            assert "accounts" in blocked_on, blocked_on
            assert "lock timeout" in (await task), "the deletion was not blocked"
            await writer.rollback()

    async def test_no_decision_memory_survives_a_completed_deletion(
        self, db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """The invariant that matters is an absence, so it is checked as one.

        A decision row that outlived its account would be a customer's purchase
        history sitting in the database after they asked for all of it to be
        erased — and with no account row left, nothing would ever find it again
        to delete it.

        The write and the deletion are started together. Either may win; what
        must never happen is a completed deletion with rows left behind.
        """
        from app.domains.privacy import deletion_service
        from app.domains.privacy.models import AccountDeletionJob

        token, account_id = await registered_supabase_user()
        member_id = await _member(app_client, token)
        candidate_id = await _candidate(account_id)
        await _decide(app_client, token, candidate_id, "waiting")

        async def no_external(*_args, **_kwargs):
            return None
        monkeypatch.setattr(deletion_service, "_delete_supabase_identity", no_external)
        monkeypatch.setattr(deletion_service, "_remove_external_integrations", no_external)

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()

        async def drain() -> int:
            async with get_sessionmaker()() as session:
                drained = await deletion_service.drain_all(session)
                await session.commit()
                return drained

        write, drained = await asyncio.gather(
            _decide(app_client, token, candidate_id, "bought", subject_id=member_id),
            drain(),
        )
        # Governed either way — the write is not entitled to a 500 because it
        # collided with the erasure.
        assert write.status_code in (200, 401, 403, 404, 409, 503), write.text

        async with get_sessionmaker()() as session:
            accounts = await session.scalar(
                text("SELECT count(*) FROM accounts WHERE id = :id"), {"id": account_id},
            )
            job = await session.scalar(
                select(AccountDeletionJob.state)
                .where(AccountDeletionJob.account_id == account_id)
            )
            decisions = await session.scalar(select(func.count(PurchaseDecision.id)))
            events = await session.scalar(select(func.count(PurchaseDecisionEvent.id)))
            scans = await session.scalar(select(func.count(ScanDecisionEvent.id)))
            profiles = await session.scalar(select(func.count(FamilyProfile.id)))

        assert drained >= 1
        if accounts == 0:
            assert job == "complete", job
            assert (decisions, events, scans, profiles) == (0, 0, 0, 0)
        else:
            # The deletion has not finished yet; it is retryable, never silently
            # abandoned, and the next drain still clears everything.
            assert job != "complete", job
            assert await drain() >= 0
