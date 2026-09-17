"""Identity writes and account deletion, in the same order, against real PostgreSQL.

The member row lock that keeps a Care write and a deactivation apart introduced
a second, quieter problem: it was documented as taking "the member row and
nothing else", and that was never true. ``appearance_profiles.account_id`` is an
immediate ``ON DELETE CASCADE`` foreign key, so the first insert for a member
makes PostgreSQL check the parent with its own ``SELECT 1 FROM accounts WHERE
id = ... FOR KEY SHARE``. The real order was::

    FamilyProfile FOR UPDATE -> Account FOR KEY SHARE -> AppearanceProfile

Account deletion goes the other way — the account row first, then cascades down
into ``family_profiles`` — so the two formed a cycle. A Care write would hold a
member row and wait for the account; a deletion would hold the account and wait,
through its cascade, for that same member row. PostgreSQL would notice and abort
one of them, which is a database rescuing an application from an ordering the
application chose.

Taking the account explicitly, first, and in the weakest mode that still blocks a
``DELETE``, puts the implicit lock where the application already claimed it was.
The tests below hold one transaction open and watch what the other one waits on,
so the claim is checked against ``pg_locks`` rather than against the comment.

Every wait here is bounded by ``lock_timeout``: a test that proves a deadlock by
hanging is not a proof, it is an outage in the suite.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import (
    SubjectNotFound,
    account_holder_subject,
    resolve_subject,
)
from app.domains.privacy import deletion_service
from app.domains.privacy.models import AccountDeletionJob
from app.domains.profile import service as profile_service
from app.domains.profile.identity import (
    canonical_subject_for_write,
    protect_account_from_delete,
    resolve_self_profile_for_write,
    resolve_subject_profile_for_write,
)
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import event, func, select, text

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

SKIN = "care_skin_usual_feel"
PROFILES_URL = "/api/v2/family-circle/profiles"

#: Long enough that a lock which is genuinely free is never mistaken for one
#: that is held, short enough that a cycle fails the test in seconds instead of
#: sitting on PostgreSQL's one-second deadlock detector and then its default
#: wait. Nothing here sleeps for this; it is a ceiling, not a pace.
LOCK_TIMEOUT_MS = 1500

#: For the one session whose whole job is to wait for another transaction to
#: finish. Long enough never to fire in a healthy run, short enough that a
#: mutation which turns the wait into a cycle fails the test rather than the
#: build.
PATIENT_TIMEOUT_MS = 20_000


class LockWaitTimeout(Exception):
    """The statement gave up waiting. Deliberate, and always a real answer."""


async def _member(client, token, *, relation="adult") -> uuid.UUID:
    response = await client.post(
        PROFILES_URL, headers=auth(token), json={"relation": relation},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _backend_pid(session) -> int:
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def _bounded(session, milliseconds: int = LOCK_TIMEOUT_MS) -> None:
    """Never let a lock wait here run without an end.

    Two different bounds are used. A session that is *expected* to be blocked
    gets the short one, so the test reads the block and moves on. A session that
    is expected to wait for another transaction to commit — a legitimate wait,
    and the thing being proven — gets a long one, which is a backstop against a
    hang rather than part of the timing.
    """
    await session.execute(text(f"SET LOCAL lock_timeout = '{milliseconds}ms'"))


async def _blocked_on(pid: int) -> set[str]:
    """Which table this backend is stuck against, or an empty set if it is not.

    Reading ``pg_locks`` for a row-lock wait needs a little care. PostgreSQL does
    not implement row locks as relation locks: the waiter registers a *tuple*
    lock naming the exact relation it wants, and then waits on the holder's
    *transaction id*, whose ``relation`` column is NULL. Looking only for
    ungranted rows with a relation therefore finds nothing at all, which would
    read as "not blocked" — the opposite of the truth.

    So both are taken together: the wait is real when something is ungranted,
    and the tuple lock is what says where.
    """
    async with get_sessionmaker()() as watcher:
        rows = (await watcher.execute(
            text(
                "SELECT l.locktype, c.relname, l.granted "
                "FROM pg_locks l LEFT JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.pid = :pid AND (NOT l.granted OR l.locktype = 'tuple')"
            ),
            {"pid": pid},
        )).all()
    if not any(granted is False for _, _, granted in rows):
        return set()
    return {relname for _, relname, _ in rows if relname}


async def _until_blocked(pid: int) -> set[str]:
    """Poll until this backend is waiting on something, and say what.

    Polling rather than sleeping on a guess: it returns the moment the wait is
    registered, and the bound exists only so a broken build fails instead of
    hanging.
    """
    for _ in range(400):
        blocked = await _blocked_on(pid)
        if blocked:
            return blocked
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never blocked on anything")


async def _delete_account_row(session, account_id: uuid.UUID) -> None:
    """The deletion worker's last step, which is what takes the account row."""
    await session.execute(
        text("DELETE FROM accounts WHERE id = :id"), {"id": account_id},
    )


async def _blocked_delete(account_id: uuid.UUID, announce: asyncio.Queue) -> str:
    """Try to delete the account in a session of its own, and report why it could not.

    Its own session, created inside the task, because one asyncpg connection
    cannot carry two coroutines: watching a statement block means the watcher
    has to be somewhere else entirely. The backend pid goes out on the queue
    before the statement that will wait, so the caller knows which backend to
    look for in ``pg_locks``.
    """
    async with get_sessionmaker()() as session:
        await _bounded(session)
        await announce.put(await _backend_pid(session))
        try:
            await _delete_account_row(session, account_id)
        except Exception as exc:  # noqa: BLE001 — the message is the result
            await session.rollback()
            return str(exc).lower()
        await session.rollback()
        return ""


# ---------------------------------------------------------------------------
# The exact lock mode, read off the wire
# ---------------------------------------------------------------------------
class TestTheAccountLockIsKeyShare:
    async def test_the_helper_emits_for_key_share_and_nothing_stronger(
        self, db_clean, registered_supabase_user,
    ):
        """What PostgreSQL is actually asked for, not what a name suggests.

        SQLAlchemy spells the four row-lock modes with two independent flags,
        and three of the four combinations are wrong here. ``FOR UPDATE`` and
        ``FOR NO KEY UPDATE`` would serialise every member write in a household
        against every other; ``FOR SHARE`` is stronger than needed and blocks
        ordinary updates of the account row. So the statement is captured as it
        leaves, rather than asserted about in prose.
        """
        _, account_id = await registered_supabase_user()
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()))

        engine = get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            async with get_sessionmaker()() as session:
                await protect_account_from_delete(session, account_id)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        locking = [s for s in statements if "FROM accounts" in s]
        assert len(locking) == 1, statements
        sql = locking[0]
        assert sql.endswith("FOR KEY SHARE"), sql
        for stronger in ("FOR UPDATE", "FOR NO KEY UPDATE", "FOR SHARE"):
            # ``FOR KEY SHARE`` contains none of these as a suffix; checking the
            # ending rather than the substring keeps the assertion honest.
            assert not sql.endswith(stronger), (stronger, sql)

    async def test_it_refuses_when_the_account_has_already_gone(
        self, db_clean, registered_supabase_user,
    ):
        """There is nothing to protect, so there is nothing to write about.

        An authenticated request whose account row has been erased mid-flight
        is not a corrupt state and not a server fault — the cascades have taken
        the household and every profile with it, so there genuinely is no such
        member any more. It answers exactly as a foreign or invented id does,
        which is also the only answer that says nothing about what existed.
        """
        _, account_id = await registered_supabase_user()
        factory = get_sessionmaker()

        async with factory() as session:
            await _delete_account_row(session, account_id)
            await session.commit()

        async with factory() as session:
            with pytest.raises(SubjectNotFound):
                await protect_account_from_delete(session, account_id)
        async with factory() as session:
            with pytest.raises(SubjectNotFound):
                await protect_account_from_delete(session, uuid.uuid4())

        # And it created nothing on the way past.
        async with factory() as session:
            assert await session.scalar(
                text("SELECT count(*) FROM accounts WHERE id = :id"),
                {"id": account_id},
            ) == 0

    async def test_two_sessions_hold_it_at_once_while_a_delete_waits(
        self, db_clean, registered_supabase_user,
    ):
        """The behaviour the mode was chosen for, proven three ways at once.

        Two holders coexist — that is what keeps two members of one household
        writable at the same moment. And a ``DELETE`` of the parent still waits
        — that is the whole reason the lock is taken at all. A mode that failed
        either half would be the wrong choice, and both halves are checked here
        rather than inferred from the keyword.
        """
        _, account_id = await registered_supabase_user()
        factory = get_sessionmaker()

        first, second = factory(), factory()
        await first.__aenter__()
        await second.__aenter__()
        try:
            await _bounded(first)
            await _bounded(second)
            await protect_account_from_delete(first, account_id)
            # Compatible: this returns rather than blocking.
            await protect_account_from_delete(second, account_id)

            announce: asyncio.Queue = asyncio.Queue()
            waiter = asyncio.create_task(_blocked_delete(account_id, announce))
            pid = await announce.get()
            blocked = await _until_blocked(pid)
            assert blocked == {"accounts"}, blocked
            message = await waiter
            assert "lock timeout" in message, message
        finally:
            await first.__aexit__(None, None, None)
            await second.__aexit__(None, None, None)

        async with factory() as session:
            assert await session.scalar(
                text("SELECT count(*) FROM accounts WHERE id = :id"),
                {"id": account_id},
            ) == 1


# ---------------------------------------------------------------------------
# Care and account deletion, in both directions
# ---------------------------------------------------------------------------
class TestCareAndAccountDeletionNeverDeadlock:
    @pytest.fixture
    def fake_supabase_admin(self, monkeypatch):
        """Erasure also asks Supabase Auth to delete the identity.

        That is a live call, and this suite never makes one. Stubbed exactly as
        the account-deletion state machine's own tests stub it, so the job can
        reach its terminal state and the tombstone can be checked.
        """
        class _Admin:
            def __init__(self):
                self.deleted: list[str] = []

            @property
            def auth(self):
                return self

            @property
            def admin(self):
                return self

            def delete_user(self, user_id: str) -> None:
                self.deleted.append(user_id)

        admin = _Admin()
        monkeypatch.setattr(
            "app.domains.privacy.deletion_service.get_supabase_admin", lambda: admin,
        )
        return admin

    async def _household(self, app_client, registered_supabase_user):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        return token, account_id, member

    async def test_care_first_holds_the_account_and_deletion_waits_for_it(
        self, db_clean, app_client, registered_supabase_user, fake_supabase_admin,
    ):
        """Case A. The Care write gets there first.

        It holds the account weakly and the member row strongly. Deletion must
        therefore stop at the *account* — the first thing in the order — rather
        than getting past it and into a cascade that waits on the member row
        the Care write is holding. That second shape is the deadlock, and the
        assertion below is what tells the two apart: the blocked backend is
        waiting on ``accounts``, and on nothing else.
        """
        token, account_id, member = await self._household(
            app_client, registered_supabase_user,
        )
        factory = get_sessionmaker()

        care = factory()
        await care.__aenter__()
        try:
            await _bounded(care)
            subject = await resolve_subject(
                care, account_id=account_id, subject_id=member,
            )
            # Account FOR KEY SHARE, then FamilyProfile FOR UPDATE.
            checked = await canonical_subject_for_write(
                care, principal_account_id=account_id, subject=subject,
            )
            assert checked.subject_id == member

            announce: asyncio.Queue = asyncio.Queue()
            waiter = asyncio.create_task(_blocked_delete(account_id, announce))
            pid = await announce.get()
            blocked = await _until_blocked(pid)
            # Stopped at the top of the order, not inside the cascade.
            assert blocked == {"accounts"}, blocked
            message = await waiter
            assert "lock timeout" in message, message
            assert "deadlock" not in message, message

            # The Care write finishes normally. No database error reaches it.
            profile = await resolve_subject_profile_for_write(
                care, subject, principal_account_id=account_id,
            )
            await profile_service.apply_attributes(
                care, profile, [{"key": SKIN, "value": "comfortable"}],
            )
            await care.commit()
        finally:
            await care.__aexit__(None, None, None)

        async with factory() as session:
            assert await session.scalar(select(func.count(AppearanceProfile.id))) == 1

        # And once Care is done, deletion runs to completion as it always would.
        async with factory() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()
        async with factory() as session:
            assert await deletion_service.drain_all(session) >= 1
            await session.commit()

        async with factory() as session:
            assert await session.scalar(select(func.count(AppearanceProfile.id))) == 0
            assert await session.scalar(select(func.count(ProfileAttribute.id))) == 0
            assert await session.scalar(select(func.count(FamilyCircle.id))) == 0
            assert await session.scalar(select(func.count(FamilyProfile.id))) == 0
            job = (await session.execute(
                select(AccountDeletionJob)
                .where(AccountDeletionJob.account_id == account_id)
            )).scalar_one()
        assert job.state == "complete"
        assert job.completed_at is not None
        assert fake_supabase_admin.deleted == [str(account_id)]

    async def test_deletion_first_makes_care_wait_at_the_account_not_the_member(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Case B. Deletion gets there first.

        The Care write must stop at the account parent. If it took the member
        row first and only then reached for the account — which is what the
        implicit foreign-key check does when nothing asks explicitly — it would
        be holding exactly the row the cascade is waiting for, and the two would
        be a cycle.

        Then the deletion commits and the account is gone. The Care write has to
        answer that cleanly: the same privacy-safe refusal any unknown member
        gets, no foreign-key violation, no partial rows, and nothing about which
        account or member was involved.
        """
        token, account_id, member = await self._household(
            app_client, registered_supabase_user,
        )
        factory = get_sessionmaker()
        announce: asyncio.Queue = asyncio.Queue()

        async def care_write() -> BaseException | None:
            """The whole attempt, in a session of its own.

            Its own session because one asyncpg connection cannot carry two
            coroutines: something has to be free to watch this one block.
            """
            async with factory() as session:
                # Patient: this wait is the thing being proven, and it ends when
                # the deletion commits. The bound is only a backstop against a
                # build where it never would.
                await _bounded(session, PATIENT_TIMEOUT_MS)
                await announce.put(await _backend_pid(session))
                # Plain reads see the pre-delete rows under MVCC, so the subject
                # still resolves. The account lock is where this stops.
                subject = await resolve_subject(
                    session, account_id=account_id, subject_id=member,
                )
                try:
                    await canonical_subject_for_write(
                        session, principal_account_id=account_id, subject=subject,
                    )
                except BaseException as exc:  # noqa: BLE001 — returned, then asserted
                    await session.rollback()
                    return exc
                await session.rollback()
                return None

        async with factory() as deleter:
            await _bounded(deleter)
            await _delete_account_row(deleter, account_id)  # held, not committed

            attempt = asyncio.create_task(care_write())
            care_pid = await announce.get()
            blocked = await _until_blocked(care_pid)
            # The one assertion this whole file exists for: it is stuck at the
            # account, never holding a member row and reaching back for it.
            assert blocked == {"accounts"}, blocked
            assert "family_profiles" not in blocked, blocked

            await deleter.commit()
            refusal = await attempt

        assert refusal is not None, "the Care write neither refused nor completed"
        assert isinstance(refusal, SubjectNotFound), refusal

        async with factory() as session:
            assert await session.scalar(select(func.count(AppearanceProfile.id))) == 0
            assert await session.scalar(select(func.count(ProfileAttribute.id))) == 0

        # Through the route, a later request with the same token never reaches
        # the Care seam at all: the account row is what makes a Supabase
        # identity a registered customer, and authentication refuses first. The
        # domain-level refusal above is the one this lock ordering produces; this
        # is what the customer actually sees afterwards, and it is governed,
        # id-free and already part of the product rather than something invented
        # for a deleted account.
        response = await app_client.patch(
            f"{PROFILES_URL}/{member}/care-profile",
            headers=auth(token),
            json={"attributes": [{"key": SKIN, "value": "comfortable"}]},
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "REGISTRATION_REQUIRED"
        assert str(member) not in response.text
        assert str(account_id) not in response.text

    async def test_two_self_writes_never_upgrade_an_account_lock(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The deadlock class the account-holder path is kept away from.

        ``FOR KEY SHARE`` is shared, so two writers can both hold it, and
        ``FOR UPDATE`` conflicts with it — so if both then asked to upgrade,
        each would be waiting for the other to let go of a lock neither can.
        PostgreSQL would abort one, which is a database rescuing an application
        from a sequence the application chose.

        The account holder therefore takes no weak lock at all: self is decided
        before any lock is acquired, and that path goes straight to
        ``FOR UPDATE``. Both writers are held here until both have finished
        deciding, which is exactly the moment an upgrade path would have them
        both holding the weak lock.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token)  # a household exists, so self is stored
        factory = get_sessionmaker()
        barrier = asyncio.Barrier(2)

        async def write_self() -> uuid.UUID:
            async with factory() as session:
                await _bounded(session, PATIENT_TIMEOUT_MS)
                checked = await canonical_subject_for_write(
                    session,
                    principal_account_id=account_id,
                    subject=account_holder_subject(account_id),
                )
                assert checked.is_account_holder
                # Both writers are now past the decision. Under an upgrade path
                # both would be holding FOR KEY SHARE right here.
                await barrier.wait()
                profile = await resolve_self_profile_for_write(session, account_id)
                await session.commit()
                return profile.id

        first, second = await asyncio.gather(write_self(), write_self())
        assert first == second
        async with factory() as session:
            assert await session.scalar(select(func.count(AppearanceProfile.id))) == 1

    async def test_two_members_are_written_at_the_same_moment(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Case C. The reason the account lock is weak.

        Both writers hold the account at once, each holding only their own
        member row, and neither can finish until the other has arrived. Under a
        stronger account lock the second would still be waiting at the account
        when the first reached the barrier, and the barrier would never be
        satisfied — so the timeout below is what turns "serialised" into a
        failure rather than a hang.
        """
        token, account_id = await registered_supabase_user()
        one = await _member(app_client, token, relation="adult")
        two = await _member(app_client, token, relation="other")

        barrier = asyncio.Barrier(2)
        factory = get_sessionmaker()

        async def write(member: uuid.UUID, value: str) -> uuid.UUID:
            async with factory() as session:
                # Bounded so that a stronger account lock surfaces as a database
                # error in a second and a half rather than as two coroutines
                # waiting on each other until something gives up. A concurrency
                # test that can hang is an outage waiting to happen in CI.
                await _bounded(session)
                subject = await resolve_subject(
                    session, account_id=account_id, subject_id=member,
                )
                # Account FOR KEY SHARE + this member's FamilyProfile FOR UPDATE.
                await canonical_subject_for_write(
                    session, principal_account_id=account_id, subject=subject,
                )
                # Both writers now hold their authority simultaneously. With a
                # stronger account lock, only one of them could be here.
                await barrier.wait()
                profile = await resolve_subject_profile_for_write(
                    session, subject, principal_account_id=account_id,
                )
                await profile_service.apply_attributes(
                    session, profile, [{"key": SKIN, "value": value}],
                )
                await session.commit()
                return profile.id

        first, second = await asyncio.gather(
            write(one, "comfortable"), write(two, "often_oily"),
        )

        assert first != second
        async with factory() as session:
            rows = (await session.execute(
                select(AppearanceProfile.id, AppearanceProfile.household_subject_id)
            )).all()
            values = dict((await session.execute(
                select(ProfileAttribute.profile_id, ProfileAttribute.value)
            )).all())
        assert len(rows) == 2
        assert {str(subject_id) for _, subject_id in rows} == {str(one), str(two)}
        assert set(values.values()) == {"comfortable", "often_oily"}
