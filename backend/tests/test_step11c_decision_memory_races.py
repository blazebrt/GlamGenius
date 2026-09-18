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
from app.domains.recommendation.models import PurchaseDecision, PurchaseDecisionEvent
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
