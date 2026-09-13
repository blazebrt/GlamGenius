"""Erasure has to reach everything the export calls the account holder's own.

``assert_registry_complete`` already stops a new table from being introduced
without deciding whether it goes *into* an export. Nothing made the matching
promise in the other direction: a table could be classified ``INCLUDED`` —
handed to the person as their data — and still be missed when that same person
asks to be deleted. That is the silent omission this module exists to catch.

Erasure reaches a table one of two ways:

* the database cascades to it from ``accounts``, which is how nearly every
  account-owned table is covered; or
* the deletion worker deletes from it by hand, because no cascade path exists.

The second list is deliberately tiny and every entry on it is proved
behaviourally below, not merely declared. A structural test that only read a
constant would pass even if the worker stopped running.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.identity import service as identity
from app.domains.privacy import deletion_service, included_tables
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import select, text

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles. Deliberately local: the deletion worker reaches Supabase Auth
# and object storage, and neither may be touched for real by a test.
# ---------------------------------------------------------------------------


class _FakeSupabaseAdmin:
    class _AuthAdmin:
        def __init__(self, outer):
            self._outer = outer

        def delete_user(self, user_id: str) -> None:
            self._outer.deleted_users.append(user_id)

    class _Auth:
        def __init__(self, outer):
            self.admin = _FakeSupabaseAdmin._AuthAdmin(outer)

    def __init__(self) -> None:
        self.deleted_users: list[str] = []
        self.auth = _FakeSupabaseAdmin._Auth(self)


@pytest.fixture
def fake_admin(monkeypatch):
    admin = _FakeSupabaseAdmin()
    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin", lambda: admin
    )
    return admin


class _FakeStorage:
    backend_name = "fake"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key, data, content_type):
        self.objects[key] = data

    async def get(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)

    async def exists(self, key):
        return key in self.objects

    async def presigned_get_url(self, key, ttl):
        return None

    async def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix):
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)


@pytest.fixture
def fake_storage():
    from app.domains.media.storage import factory as storage_factory

    storage = _FakeStorage()
    storage_factory.set_storage(storage)
    yield storage
    storage_factory.set_storage(None)



# Tables the cascade cannot reach, which the deletion worker therefore empties
# itself. Adding a name here is a claim that must be backed by a test in this
# file that actually runs a deletion and watches the rows go.
EXPLICITLY_ERASED = {
    # ``ai_runs.account_id`` is ON DELETE SET NULL so the spend and provenance
    # ledger survives the person (see ``_delete_ai_outputs``). The payload —
    # what the model said about them — must not.
    "ai_run_outputs",
    # Free-form ``properties`` JSONB; nothing about it can be shown to be
    # impersonal, so the rows go rather than being anonymised in place.
    "app_events",
}

# Tables whose rows outlive the person on purpose, because the row is worth
# keeping and carries nothing that identifies anybody once the deletion worker
# has been over it. This is the weakest of the three routes and the easiest to
# get wrong — "we set account_id to NULL" is not by itself anonymisation if a
# second column still holds an identifier — so every entry here is proved
# column by column below, against a row seeded with real identifiers.
ANONYMISED_IN_PLACE = {
    "ai_runs",
    "audit_events",
}


# A cascade closure over the live catalogue: which tables does
# ``DELETE FROM accounts`` actually empty? The deployed schema is the truth
# here, not the ORM's ``ondelete=`` strings, because it is the schema that
# will run in production.
_CASCADE_CLOSURE = text(
    """
    WITH RECURSIVE fk AS (
        SELECT con.conrelid AS child, con.confrelid AS parent, con.confdeltype AS rule
        FROM pg_constraint con
        JOIN pg_class cc ON cc.oid = con.conrelid
        JOIN pg_namespace nc ON nc.oid = cc.relnamespace AND nc.nspname = 'public'
        WHERE con.contype = 'f'
    ),
    reach AS (
        SELECT c.oid AS t
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'accounts'
        UNION
        SELECT fk.child FROM fk JOIN reach ON fk.parent = reach.t WHERE fk.rule = 'c'
    )
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = 'public' AND c.oid IN (SELECT t FROM reach)
    """
)


async def _cascade_closure() -> set[str]:
    async with get_engine().connect() as conn:
        rows = await conn.execute(_CASCADE_CLOSURE)
        return {row[0] for row in rows}


async def test_every_exported_table_is_reached_by_erasure(db_clean):
    """Nothing we call the account holder's own may outlive their deletion."""
    closure = await _cascade_closure()
    unreachable = included_tables() - closure - EXPLICITLY_ERASED - ANONYMISED_IN_PLACE
    assert unreachable == set(), (
        "These tables are classified INCLUDED — we export them to the account "
        "holder as their own data — but account deletion neither cascades to "
        "them nor deletes them explicitly, so the data outlives the person: "
        + ", ".join(sorted(unreachable))
        + ". Give the table an ON DELETE CASCADE path to accounts; or delete "
        "it in app/domains/privacy/deletion_service.py and add it to "
        "EXPLICITLY_ERASED; or, if the row itself must survive, scrub its "
        "identifying columns there and add it to ANONYMISED_IN_PLACE. Each of "
        "the last two needs a test here that proves what it claims."
    )


async def test_the_explicit_list_has_no_stale_entries(db_clean):
    """A name on the hand-erased list that the cascade now covers is noise."""
    closure = await _cascade_closure()
    redundant = (EXPLICITLY_ERASED | ANONYMISED_IN_PLACE) & closure
    assert redundant == set(), (
        "These tables are listed as hand-erased or anonymised in place, but "
        "the cascade already reaches them and will delete the rows outright; "
        "drop them from the list: " + ", ".join(sorted(redundant))
    )


async def _seed_account_with_ai_output(account_id: uuid.UUID) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await identity.register_account(session, account_id)
        run = AIRun(
            account_id=account_id,
            feature="scan_analyse",
            provider="gemini",
            model="gemini-2.0-flash",
            prompt_version="1",
            schema_version="1",
            status=AI_STATUS_SUCCEEDED,
            estimated_cost_usd=0.0004,
        )
        session.add(run)
        await session.flush()
        session.add(
            AIRunOutput(
                ai_run_id=run.id,
                schema_version="1",
                payload={"observations": ["the skin around the eyes looks dry"]},
            )
        )
        await session.commit()


async def _run_deletion(account_id: uuid.UUID) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()


async def test_ai_output_about_the_person_does_not_survive_them(
    db_clean, fake_admin, fake_storage,
):
    """The model's reading of somebody's face is erased with them."""
    account_id = uuid.uuid4()
    await _seed_account_with_ai_output(account_id)

    await _run_deletion(account_id)

    async with get_sessionmaker()() as session:
        surviving = (await session.execute(select(AIRunOutput))).scalars().all()
    assert [row.payload for row in surviving] == []


async def test_the_spend_ledger_survives_the_person_anonymised(
    db_clean, fake_admin, fake_storage,
):
    """Deleting the content must not delete the record that we paid for a call.

    Audit finding F24 exists because no AI call was recorded anywhere. The run
    row carries provider, model, latency and cost — none of which describe a
    person — and it has to remain, with its ``account_id`` severed.
    """
    account_id = uuid.uuid4()
    await _seed_account_with_ai_output(account_id)

    await _run_deletion(account_id)

    async with get_sessionmaker()() as session:
        runs = (await session.execute(select(AIRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].account_id is None
    assert runs[0].provider == "gemini"
    assert float(runs[0].estimated_cost_usd) == pytest.approx(0.0004)


async def test_ai_output_erasure_leaves_other_accounts_alone(
    db_clean, fake_admin, fake_storage,
):
    """One person's deletion must not take another person's outputs with it."""
    leaving = uuid.uuid4()
    staying = uuid.uuid4()
    await _seed_account_with_ai_output(leaving)
    await _seed_account_with_ai_output(staying)

    await _run_deletion(leaving)

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AIRun.account_id, AIRunOutput.payload).join(
                    AIRunOutput, AIRunOutput.ai_run_id == AIRun.id
                )
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == staying


async def test_ai_output_erasure_is_idempotent(db_clean, fake_admin, fake_storage):
    """A resumed job re-runs the stage; the second pass must be harmless."""
    account_id = uuid.uuid4()
    await _seed_account_with_ai_output(account_id)

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service._delete_ai_outputs(session, account_id)
        await deletion_service._delete_ai_outputs(session, account_id)
        await session.commit()

    async with factory() as session:
        assert (await session.execute(select(AIRunOutput))).scalars().all() == []
        # The run row is untouched by this stage; only the cascade/SET NULL
        # that follows the account row touches it.
        runs = (await session.execute(select(AIRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].account_id == account_id


async def test_an_account_with_no_ai_runs_deletes_cleanly(
    db_clean, fake_admin, fake_storage,
):
    """The new stage must not blow up on the common case of nothing to erase."""
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    await _run_deletion(account_id)

    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
    assert job is not None
    assert job.state == "complete"


async def test_signed_out_preview_outputs_are_not_collateral(
    db_clean, fake_admin, fake_storage,
):
    """A run with no account belongs to nobody; a deletion must not sweep it.

    ``ai_runs.account_id`` is nullable because the signed-out preview still
    costs money and still gets recorded. ``IN (SELECT ... WHERE account_id = x)``
    must not match those rows.
    """
    account_id = uuid.uuid4()
    await _seed_account_with_ai_output(account_id)

    factory = get_sessionmaker()
    async with factory() as session:
        anonymous = AIRun(
            account_id=None,
            feature="scan_preview",
            provider="gemini",
            model="gemini-2.0-flash",
            prompt_version="1",
            schema_version="1",
            status=AI_STATUS_SUCCEEDED,
        )
        session.add(anonymous)
        await session.flush()
        session.add(
            AIRunOutput(ai_run_id=anonymous.id, schema_version="1", payload={"preview": True})
        )
        await session.commit()

    await _run_deletion(account_id)

    async with factory() as session:
        surviving = (await session.execute(select(AIRunOutput))).scalars().all()
    assert [row.payload for row in surviving] == [{"preview": True}]


# ---------------------------------------------------------------------------
# app_events: deleted outright
# ---------------------------------------------------------------------------


async def test_analytics_events_do_not_survive_the_person(
    db_clean, fake_admin, fake_storage,
):
    """A free-form property bag cannot be anonymised, so it goes."""
    from app.domains.analytics.models import AppEvent

    leaving = uuid.uuid4()
    staying = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await identity.register_account(session, leaving)
        await identity.register_account(session, staying)
        session.add(AppEvent(account_id=leaving, name="scan_opened", properties={"tab": "care"}))
        session.add(AppEvent(account_id=staying, name="scan_opened", properties={"tab": "care"}))
        await session.commit()

    await _run_deletion(leaving)

    async with factory() as session:
        rows = (await session.execute(select(AppEvent))).scalars().all()
    assert [row.account_id for row in rows] == [staying]


# ---------------------------------------------------------------------------
# audit_events: kept, but stripped of every identifier
# ---------------------------------------------------------------------------


async def _seed_audit_row(account_id: uuid.UUID) -> None:
    from app.domains.audit import service as audit

    async with get_sessionmaker()() as session:
        await identity.register_account(session, account_id)
        await audit.record(
            session,
            action="privacy.exported",
            account_id=account_id,
            subject_type="account",
            # The privacy actions write the account's own UUID here. This is
            # the column that defeats ON DELETE SET NULL.
            subject_id=str(account_id),
            context={"schema_version": "1.0"},
            client_ip="203.0.113.9",
        )
        await session.commit()


async def test_the_audit_trail_keeps_what_happened_and_when(
    db_clean, fake_admin, fake_storage,
):
    """Scrubbing must not destroy the trail — only the identity in it."""
    from app.domains.audit.models import AuditEvent

    account_id = uuid.uuid4()
    await _seed_audit_row(account_id)

    await _run_deletion(account_id)

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditEvent))).scalars().all()
    exported = [row for row in rows if row.action == "privacy.exported"]
    assert len(exported) == 1
    assert exported[0].created_at is not None
    assert exported[0].context == {"schema_version": "1.0"}


async def test_no_audit_column_still_identifies_the_deleted_person(
    db_clean, fake_admin, fake_storage,
):
    """The whole point of ANONYMISED_IN_PLACE, checked column by column.

    Not "account_id is NULL" — every column, against the identifiers the row
    was seeded with. ``subject_id`` held the account UUID and ``ip_hash`` a
    stable keyed pseudonym of the person's address; neither may remain.
    """
    from app.domains.audit.models import AuditEvent
    from app.domains.audit.service import hash_ip

    account_id = uuid.uuid4()
    await _seed_audit_row(account_id)

    await _run_deletion(account_id)

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditEvent))).scalars().all()

    haystack = {
        str(getattr(row, column.name))
        for row in rows
        for column in AuditEvent.__table__.columns
    }
    assert str(account_id) not in haystack, "the account UUID survived in an audit column"
    assert hash_ip("203.0.113.9") not in haystack, "the IP pseudonym survived"
    for row in rows:
        assert row.account_id is None
        assert row.subject_id is None
        assert row.ip_hash is None


async def test_audit_scrub_leaves_other_accounts_alone(
    db_clean, fake_admin, fake_storage,
):
    """One person's erasure must not blank another person's audit trail."""
    from app.domains.audit.models import AuditEvent

    leaving = uuid.uuid4()
    staying = uuid.uuid4()
    await _seed_audit_row(leaving)
    await _seed_audit_row(staying)

    await _run_deletion(leaving)

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuditEvent).where(AuditEvent.account_id == staying)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].subject_id == str(staying)
    assert rows[0].ip_hash is not None


async def test_no_ai_run_column_still_identifies_the_deleted_person(
    db_clean, fake_admin, fake_storage,
):
    """The other ANONYMISED_IN_PLACE entry, held to the same standard."""
    account_id = uuid.uuid4()
    await _seed_account_with_ai_output(account_id)

    await _run_deletion(account_id)

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AIRun))).scalars().all()

    haystack = {
        str(getattr(row, column.name))
        for row in rows
        for column in AIRun.__table__.columns
    }
    assert str(account_id) not in haystack


async def test_audit_scrub_is_idempotent(db_clean, fake_admin, fake_storage):
    """A resumed job re-runs the stage; the second pass changes nothing."""
    from app.domains.audit.models import AuditEvent

    account_id = uuid.uuid4()
    await _seed_audit_row(account_id)

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service._scrub_audit_events(session, account_id)
        await deletion_service._scrub_audit_events(session, account_id)
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(select(AuditEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].subject_id is None
    assert rows[0].ip_hash is None
    assert rows[0].action == "privacy.exported"
