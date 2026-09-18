"""Which row the append-only ledger takes first, and why it has to be the account.

Step 11C's last identity correction made the public event authority load and
lock the decision row itself. That was right about *what* it may trust and
wrong about *when* it takes what, because it started at a child row.

``purchase_decision_events.account_id`` is an immediate foreign key. Inserting
an event therefore makes PostgreSQL check the parent and take ``FOR KEY SHARE``
on the account row by itself — so the account was always in this transaction's
lock set, and the only open question was whether it arrived before or after
``purchase_decisions``.

After is the reverse of account deletion, which takes the account and then
cascades down. Two transactions going opposite ways round the same pair
deadlock: one holds the decision and waits for the account, the other holds the
account and waits for the decision. PostgreSQL would notice and abort one of
them, which is a database rescuing an application from an ordering the
application chose.

These tests hold one transaction open and watch what the other one waits on, so
the claim is checked against ``pg_locks`` rather than against the comment. Every
wait is bounded by ``lock_timeout``: a test that proves a deadlock by hanging is
not a proof, it is an outage in the suite. Nothing sleeps on a guess.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.identity.models import Account
from app.domains.purchase import decision_memory
from app.domains.recommendation import service as recommendation_service
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    PurchaseEvaluation,
    RecommendationRun,
    ShoppingCandidate,
)
from app.shared.database.sql import get_engine, get_sessionmaker
from app.shared.errors.exceptions import NotFoundError
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, text

from tests.test_step11b_lock_order import (
    LOCK_TIMEOUT_MS,
    PATIENT_TIMEOUT_MS,
    _backend_pid,
    _bounded,
    _until_blocked,
)
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate

pytestmark = pytest.mark.asyncio


async def _candidate(account_id: uuid.UUID) -> uuid.UUID:
    candidate_id = await _seed_db_candidate(account_id)
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


async def _decision(account_id: uuid.UUID, candidate_id: uuid.UUID) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        row = PurchaseDecision(
            account_id=account_id, household_subject_id=None,
            candidate_id=candidate_id, strategy_key="care_purchase",
            recommendation_verdict="wait", recommendation_version="v",
            recommendation_snapshot={}, decision="waiting",
            followed_recommendation=True,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _evaluation(account_id: uuid.UUID, candidate_id: uuid.UUID) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        run = RecommendationRun(account_id=account_id, kind="purchase", status="complete")
        session.add(run)
        await session.flush()
        evaluation = PurchaseEvaluation(
            account_id=account_id, candidate_id=candidate_id, run_id=run.id,
            verdict="buy", roi_score=0.5, roi_version="roi-v", summary="s",
        )
        session.add(evaluation)
        await session.commit()
        return evaluation.id


def _capture_sql() -> tuple[list[str], callable]:
    """Record every statement this engine emits, until the caller stops it."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    engine = get_engine().sync_engine
    sa_event.listen(engine, "before_cursor_execute", record)
    return statements, lambda: sa_event.remove(engine, "before_cursor_execute", record)


async def _delete_account_in_own_session(
    account_id: uuid.UUID, announce: asyncio.Queue, *, milliseconds: int = LOCK_TIMEOUT_MS,
) -> str:
    """Delete the account in a session of its own and report why it could not.

    Its own session created inside the task, because one asyncpg connection
    cannot carry two coroutines — watching a statement block means the watcher
    has to be on a different backend. The pid goes out before the statement that
    will wait, so the caller knows where to look in ``pg_locks``.
    """
    async with get_sessionmaker()() as session:
        await _bounded(session, milliseconds)
        await announce.put(await _backend_pid(session))
        try:
            await session.execute(
                text("DELETE FROM accounts WHERE id = :id"), {"id": account_id},
            )
        except Exception as exc:  # noqa: BLE001 — the message is the result
            await session.rollback()
            return str(exc).lower()
        await session.commit()
        return ""


# ---------------------------------------------------------------------------
# 1. The order, read off the wire
# ---------------------------------------------------------------------------
class TestTheAccountIsTakenFirst:
    async def test_the_first_row_lock_is_the_account_in_key_share(
        self, db_clean, registered_supabase_user,
    ):
        """Statement order, captured as it leaves, not inferred from call order.

        Two things are asserted and both matter. The account lock is the
        *first* row lock this authority takes, ahead of the decision. And it is
        ``FOR KEY SHARE`` — three of SQLAlchemy's four row-lock spellings would
        be wrong here, and ``FOR UPDATE`` in particular would serialise every
        append on one account against every other for no benefit.
        """
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id)

        statements, stop = _capture_sql()
        try:
            async with get_sessionmaker()() as session:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=account_id, decision_id=decision_id,
                )
                await session.commit()
        finally:
            stop()

        locking = [s for s in statements if " FOR " in s and "SELECT" in s]
        assert locking, statements
        first = locking[0]
        assert "FROM accounts" in first, first
        assert "FOR KEY SHARE" in first, first
        assert "FOR UPDATE" not in first, first

        decision_locks = [
            index for index, s in enumerate(locking)
            if "FROM purchase_decisions" in s and "FOR UPDATE" in s
        ]
        assert decision_locks, locking
        assert decision_locks[0] > 0, "the decision was locked before the account"

    async def test_the_style_path_takes_the_account_before_the_evaluation(
        self, db_clean, registered_supabase_user,
    ):
        """The same proof for the dormant Style writer this PR made authoritative."""
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        evaluation_id = await _evaluation(account_id, candidate_id)

        statements, stop = _capture_sql()
        try:
            async with get_sessionmaker()() as session:
                evaluation = await session.get(PurchaseEvaluation, evaluation_id)
                await recommendation_service.save_decision(
                    session, principal_account_id=account_id,
                    evaluation=evaluation, decision="bought", note=None,
                )
                await session.commit()
        finally:
            stop()

        locking = [s for s in statements if " FOR " in s and "SELECT" in s]
        assert "FROM accounts" in locking[0], locking[0]
        assert "FOR KEY SHARE" in locking[0], locking[0]
        evaluation_locks = [
            index for index, s in enumerate(locking)
            if "FROM purchase_evaluations" in s and "FOR UPDATE" in s
        ]
        assert evaluation_locks, locking
        assert evaluation_locks[0] > 0, "the evaluation was locked before the account"

    async def test_two_appends_on_one_account_do_not_serialise(
        self, db_clean, registered_supabase_user,
    ):
        """Why ``FOR KEY SHARE`` rather than ``FOR UPDATE``, proved by behaviour.

        The weaker lock blocks a deletion and nothing else, so two appends for
        different decisions on the same account proceed at once. ``FOR UPDATE``
        would make one wait for the other for no reason — correct, and a
        needless queue on a shared row.
        """
        _, account_id = await registered_supabase_user()
        first_candidate = await _candidate(account_id)
        first_decision = await _decision(account_id, first_candidate)

        async with get_sessionmaker()() as holder:
            await _bounded(holder, PATIENT_TIMEOUT_MS)
            await decision_memory.record_decision_event_for_account(
                holder, principal_account_id=account_id, decision_id=first_decision,
            )

            # A second append, on a different decision, while the first still
            # holds its account lock. It must not wait.
            second_candidate = await _candidate(account_id)
            second_decision = await _decision(account_id, second_candidate)
            async with get_sessionmaker()() as other:
                await _bounded(other, LOCK_TIMEOUT_MS)
                event = await decision_memory.record_decision_event_for_account(
                    other, principal_account_id=account_id, decision_id=second_decision,
                )
                await other.commit()
            assert event is not None
            await holder.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 2


# ---------------------------------------------------------------------------
# 2. The append and the deletion, both ways round
# ---------------------------------------------------------------------------
class TestEventAppendVersusAccountDeletion:
    async def test_case_a_the_append_holds_the_account_and_deletion_waits(
        self, db_clean, registered_supabase_user,
    ):
        """The append gets there first. Deletion waits on ``accounts``.

        The important part is *where* it waits. Blocked on the account row means
        the two transactions are going the same way round, and the append can
        finish without ever being asked to give anything up. Blocked further
        down — on ``purchase_decisions``, through the cascade — would mean
        deletion already held the account while the append still needed it, and
        the pair would be a cycle.
        """
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id)

        async with get_sessionmaker()() as appender:
            await _bounded(appender, PATIENT_TIMEOUT_MS)
            event = await decision_memory.record_decision_event_for_account(
                appender, principal_account_id=account_id, decision_id=decision_id,
            )
            assert event is not None

            announce: asyncio.Queue = asyncio.Queue()
            deletion = asyncio.create_task(
                _delete_account_in_own_session(account_id, announce)
            )
            blocked_on = await _until_blocked(await announce.get())
            assert "accounts" in blocked_on, blocked_on
            assert "purchase_decisions" not in blocked_on, blocked_on

            # It waited rather than being let through, and gave up at the bound.
            assert "lock timeout" in (await deletion), "deletion was not blocked"
            # The append itself is untouched by the attempt: no deadlock, no
            # rollback, no raw database error.
            await appender.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 1
            assert await session.scalar(select(func.count(Account.id))) == 1

    async def test_case_a_deletion_then_completes_and_takes_the_history(
        self, db_clean, off_clean, registered_supabase_user, monkeypatch,
    ):
        """Once the append commits, the real deletion runs and sweeps it up.

        The append winning the race does not leave anything behind: the event it
        wrote is this customer's data and goes with the rest of it. Run through
        the real worker so the tombstone and the job state are the real ones.
        """
        from app.domains.privacy import deletion_service
        from app.domains.privacy.models import AccountDeletionJob

        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id)

        async with get_sessionmaker()() as session:
            await decision_memory.record_decision_event_for_account(
                session, principal_account_id=account_id, decision_id=decision_id,
            )
            await session.commit()

        async def no_external(*_args, **_kwargs):
            return None
        monkeypatch.setattr(deletion_service, "_delete_supabase_identity", no_external)
        monkeypatch.setattr(deletion_service, "_remove_external_integrations", no_external)

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()
        async with get_sessionmaker()() as session:
            assert await deletion_service.drain_all(session) >= 1
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(Account.id))) == 0
            job = (await session.execute(
                select(AccountDeletionJob)
                .where(AccountDeletionJob.account_id == account_id)
            )).scalar_one()
        assert job.state == "complete"

    async def test_case_b_deletion_holds_the_account_and_the_append_stops_there(
        self, db_clean, registered_supabase_user,
    ):
        """Deletion gets there first. The append blocks on the account.

        Not on ``purchase_decisions`` — that is the whole correction. An append
        that reached for the decision row first would be holding it while
        deletion's cascade came for the same row, and the two would be a cycle
        rather than a queue.
        """
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id)

        async with get_sessionmaker()() as deleter:
            await _bounded(deleter, PATIENT_TIMEOUT_MS)
            await deleter.execute(
                text("SELECT id FROM accounts WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            )

            announce: asyncio.Queue = asyncio.Queue()

            async def append() -> str:
                async with get_sessionmaker()() as session:
                    await _bounded(session, LOCK_TIMEOUT_MS)
                    await announce.put(await _backend_pid(session))
                    try:
                        await decision_memory.record_decision_event_for_account(
                            session, principal_account_id=account_id,
                            decision_id=decision_id,
                        )
                    except Exception as exc:  # noqa: BLE001 — the message is the result
                        await session.rollback()
                        return str(exc).lower()
                    await session.commit()
                    return ""

            task = asyncio.create_task(append())
            blocked_on = await _until_blocked(await announce.get())
            assert "accounts" in blocked_on, blocked_on
            assert "purchase_decisions" not in blocked_on, (
                "the append locked the decision before protecting the account"
            )
            assert "lock timeout" in (await task)
            await deleter.rollback()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0

    async def test_case_b_after_deletion_commits_the_append_refuses_cleanly(
        self, db_clean, registered_supabase_user,
    ):
        """No event, no integrity error, no deadlock, no uncontrolled failure.

        The account is gone by the time the append runs, so there is nothing to
        append to. It says so in the same sentence a foreign or invented
        decision id gets, rather than surfacing a foreign-key violation from
        whichever statement happened to notice first.
        """
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id)

        announce: asyncio.Queue = asyncio.Queue()
        assert await _delete_account_in_own_session(account_id, announce) == ""

        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError) as gone:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=account_id, decision_id=decision_id,
                )
            with pytest.raises(NotFoundError) as invented:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=uuid.uuid4(),
                    decision_id=uuid.uuid4(),
                )
            await session.commit()
        assert str(gone.value) == str(invented.value)
        assert str(account_id) not in str(gone.value)
        assert str(decision_id) not in str(gone.value)

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0


# ---------------------------------------------------------------------------
# 3. The dormant Style writer, against the same deletion
# ---------------------------------------------------------------------------
class TestStyleVersusAccountDeletion:
    async def test_deletion_first_stops_style_at_the_account(
        self, db_clean, registered_supabase_user,
    ):
        """Style waits on ``accounts``, never on ``purchase_evaluations``.

        ``purchase_evaluations.account_id`` cascades from the account too, so a
        writer that locked the evaluation first would be holding exactly what
        deletion's cascade is coming for. Retired or not, this function was made
        authoritative by this PR, so it is finished properly.
        """
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        evaluation_id = await _evaluation(account_id, candidate_id)

        async with get_sessionmaker()() as deleter:
            await _bounded(deleter, PATIENT_TIMEOUT_MS)
            await deleter.execute(
                text("SELECT id FROM accounts WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            )

            announce: asyncio.Queue = asyncio.Queue()

            async def style_write() -> str:
                async with get_sessionmaker()() as session:
                    await _bounded(session, LOCK_TIMEOUT_MS)
                    await announce.put(await _backend_pid(session))
                    evaluation = await session.get(PurchaseEvaluation, evaluation_id)
                    try:
                        await recommendation_service.save_decision(
                            session, principal_account_id=account_id,
                            evaluation=evaluation, decision="bought", note=None,
                        )
                    except Exception as exc:  # noqa: BLE001 — the message is the result
                        await session.rollback()
                        return str(exc).lower()
                    await session.commit()
                    return ""

            task = asyncio.create_task(style_write())
            blocked_on = await _until_blocked(await announce.get())
            assert "accounts" in blocked_on, blocked_on
            assert "purchase_evaluations" not in blocked_on, (
                "Style locked the evaluation before protecting the account"
            )
            assert "lock timeout" in (await task)
            await deleter.rollback()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0

    async def test_style_first_makes_deletion_wait_on_the_account(
        self, db_clean, registered_supabase_user,
    ):
        """And the other way round: deletion queues rather than cycling."""
        _, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        evaluation_id = await _evaluation(account_id, candidate_id)

        async with get_sessionmaker()() as writer:
            await _bounded(writer, PATIENT_TIMEOUT_MS)
            evaluation = await writer.get(PurchaseEvaluation, evaluation_id)
            await recommendation_service.save_decision(
                writer, principal_account_id=account_id,
                evaluation=evaluation, decision="bought", note=None,
            )

            announce: asyncio.Queue = asyncio.Queue()
            deletion = asyncio.create_task(
                _delete_account_in_own_session(account_id, announce)
            )
            blocked_on = await _until_blocked(await announce.get())
            assert "accounts" in blocked_on, blocked_on
            assert "purchase_evaluations" not in blocked_on, blocked_on
            assert "lock timeout" in (await deletion)
            await writer.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 1


# ---------------------------------------------------------------------------
# 4. One emitter, so there is one order
# ---------------------------------------------------------------------------
class TestOneSharedLock:
    async def test_the_account_protection_is_emitted_in_exactly_one_place(self, db_clean):
        """Two places emitting almost the same lock is how an order stops being one."""
        import pathlib

        from app.domains.identity import service as identity_service

        backend = pathlib.Path(identity_service.__file__).resolve().parents[3]
        offenders = [
            str(path.relative_to(backend))
            for path in (backend / "app").rglob("*.py")
            if path.name != "service.py" or "identity" not in str(path)
            if "key_share=True" in path.read_text()
        ]
        assert offenders == [], offenders

    async def test_the_identity_boundary_still_speaks_its_own_language(
        self, db_clean, registered_supabase_user,
    ):
        """Sharing the lock must not share one boundary's vocabulary with another.

        The profile identity path answers a vanished account as an unknown
        subject; the purchase path answers it as a missing decision. Both are
        right for their own caller, which is why the shared helper returns
        rather than raising.
        """
        from app.domains.family.subject import SubjectNotFound
        from app.domains.profile.identity import protect_account_from_delete

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await protect_account_from_delete(session, uuid.uuid4())
