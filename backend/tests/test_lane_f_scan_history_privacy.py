"""Lane F: erased history belongs to nobody, against PostgreSQL 16.

* E — a scan left accountless by account erasure could be given to the next
  account that claimed the same phone. ``scan_events.account_attachment_allowed``
  now says which accountless rows are genuinely anonymous, and nothing else is
  ever attached. Lane C's proof that a withdrawn confirmation still anchors the
  shared snapshot keeps working, and is made stricter by the same flag.
* F — a consumed invite reservation kept the deleted person's email and Auth
  user id for good. Deletion now minimises it before the account row goes.

Deletions run through the real route and the real worker. The migration test
runs the real Lane F revision against rows written in the pre-Lane-F shape.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.domains.ai_gateway.models import AIRun
from app.domains.beta_access.models import Invite, InviteRegistrationReservation
from app.domains.privacy import deletion_service
from app.domains.product import pack_context, withdrawn_confirmation
from app.domains.product import service as product_service
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.product.personal_decision import (
    CurrentPackSnapshotUnresolved,
    resolve_current_pack_label_snapshot,
)
from app.shared.database import sql
from app.shared.database.sql import get_sessionmaker
from app.workers import account_deletion
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from tests.conftest import auth
from tests.test_account_deletion_integrity import _make_due
from tests.test_label_report_evidence_integrity import _EvidenceStorage
from tests.test_scan_physical_pack_integrity import (
    BARCODE,
    _capture,
    _device,
    _device_row,
    _erased_original,
    _facts,
    _plain_scan,
    deletion_boundaries,  # noqa: F401 - re-exported fixture
    no_external_product_data,  # noqa: F401 - re-exported autouse fixture
)

pytestmark = pytest.mark.asyncio

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE_LANE_F = "k9l0m1n2o3"


def _factory():
    return get_sessionmaker()


async def _events(device_id: uuid.UUID) -> list[ScanEvent]:
    async with _factory()() as session:
        return list((await session.execute(
            select(ScanEvent).where(ScanEvent.device_id == device_id).order_by(ScanEvent.created_at, ScanEvent.id)
        )).scalars().all())


async def _erase(app_client, token) -> None:
    deleted = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert deleted.status_code == 202, deleted.text
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary


async def _claim(app_client, phone, token) -> int:
    claimed = await app_client.post("/api/v2/scan/device/claim", headers={**phone, **auth(token)})
    assert claimed.status_code == 200, claimed.text
    return claimed.json()["scans_attached"]


# ---------------------------------------------------------------------------
# E. Erased history can never become the next claimant's
# ---------------------------------------------------------------------------
async def test_f_e1_e2_e3_a_second_owner_of_the_phone_gets_none_of_the_erased_history(
    app_client, db_clean, registered_supabase_user, deletion_boundaries,  # noqa: F811
):
    token_a, account_a = await registered_supabase_user()
    phone = await _device(app_client)
    device = (await _device_row(phone)).id

    # Signed out, then signed up: the genuine anonymous scan follows A in.
    await _plain_scan(app_client, phone)
    assert [row.account_attachment_allowed for row in await _events(device)] == [True]
    assert await _claim(app_client, phone, token_a) == 1
    # Owned history: an ordinary scan and a confirmed label capture.
    await _plain_scan(app_client, phone)
    a1, _s1 = await _capture(phone, account_a, _facts())
    history = await _events(device)
    assert len(history) == 3
    assert all(row.account_id == account_a and row.account_attachment_allowed is False for row in history)

    await _erase(app_client, token_a)

    # E1: every row A left is accountless, withdrawn where it carried facts,
    # and permanently non-attachable. The rows themselves stay (provenance).
    erased = await _events(device)
    assert [row.id for row in erased] == [row.id for row in history]
    assert all(row.account_id is None and row.account_attachment_allowed is False for row in erased)
    assert next(row for row in erased if row.id == a1.id).label_facts is None
    assert all(row.device_id == device for row in erased), "Lane C needs the device kept"

    # E3: after erasure the phone is unclaimed; a new signed-out scan is anonymous again.
    await _plain_scan(app_client, phone)
    fresh = [row for row in await _events(device) if row.id not in {r.id for r in history}]
    assert len(fresh) == 1 and fresh[0].account_attachment_allowed is True

    # E2: B claims the same phone. Only the scan made since is B's.
    token_b, account_b = await registered_supabase_user()
    assert await _claim(app_client, phone, token_b) == 1
    after = {row.id: row for row in await _events(device)}
    assert after[fresh[0].id].account_id == account_b and after[fresh[0].id].account_attachment_allowed is False
    for old in history:
        assert after[old.id].account_id is None and after[old.id].account_attachment_allowed is False
    # And nothing of A's reaches B's own view of their data.
    exported = await app_client.get("/api/v2/privacy/export", headers=auth(token_b))
    assert exported.status_code == 200, exported.text
    scans = exported.json()["domains"]["product_scans"]["scans"]
    assert [row["id"] for row in scans] == [str(fresh[0].id)]
    assert "account_attachment_allowed" not in scans[0], "internal control state is not exported"


async def test_f_e1_the_erasure_statement_itself_marks_the_rows_non_attachable(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """Defence in depth: an owned row is already non-attachable by constraint,
    and erasure still says so explicitly, in the statement that withdraws it."""
    _token, account_id = await registered_supabase_user()
    statements: list[str] = []
    from sqlalchemy import event

    engine = sql.get_engine()

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE SCAN_EVENTS"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with _factory()() as session:
            await deletion_service._withdraw_scan_observations(session, account_id)
            await session.rollback()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "label_facts" in statements[0] and "account_attachment_allowed" in statements[0]


async def test_f_e_an_owned_row_cannot_be_marked_attachable(app_client, db_clean, registered_supabase_user):
    """The constraint that makes any account's departure privacy-safe on its own."""
    token, account_id = await registered_supabase_user()
    phone = await _device(app_client, token)
    await _plain_scan(app_client, phone)
    device = (await _device_row(phone)).id
    with pytest.raises(IntegrityError):
        async with _factory()() as session:
            await session.execute(
                update(ScanEvent).where(ScanEvent.device_id == device).values(account_attachment_allowed=True)
            )
            await session.commit()
    # So the cascade's SET NULL alone leaves a row nobody can claim.
    async with _factory()() as session:
        await session.execute(update(ScanEvent).where(ScanEvent.account_id == account_id).values(account_id=None))
        await session.commit()
    _token_b, account_b = await registered_supabase_user()
    async with _factory()() as session:
        moved = await product_service.attach_scans_to_account(session, device_id=device, account_id=account_b)
        await session.rollback()
    assert moved == 0


async def _alembic(*arguments: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", *arguments, cwd=BACKEND_ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode(errors="replace")


async def test_f_e4_the_migration_backfills_every_existing_row_as_non_attachable(
    app_client, db_clean, registered_supabase_user,
):
    """Upgrade from the exact pre-Lane-F head with rows written in its shape.

    An accountless row from before this revision is either an anonymous scan
    or a deleted person's history, and nothing stored can tell which. The
    backfill must refuse both; only scans recorded afterwards carry the truth.
    """
    _token, account_id = await registered_supabase_user()
    phone = await _device(app_client)
    device = (await _device_row(phone)).id
    owned_id, ambiguous_id = uuid.uuid4(), uuid.uuid4()

    await sql.dispose_engine()
    try:
        returncode, output = await _alembic("downgrade", BEFORE_LANE_F)
        assert returncode == 0, output
        async with sql.get_engine().begin() as connection:
            columns = set((await connection.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'scan_events'"
            ))).scalars())
            assert "account_attachment_allowed" not in columns
            for row_id, owner in ((owned_id, account_id), (ambiguous_id, None)):
                await connection.execute(text(
                    "INSERT INTO scan_events (id, created_at, updated_at, device_id, account_id, barcode, "
                    "outcome, client_scan_id, queued_offline) "
                    "VALUES (:id, now(), now(), :device, :account, :barcode, 'not_found', :client, false)"
                ), {"id": row_id, "device": device, "account": owner, "barcode": BARCODE, "client": row_id.hex})
        await sql.dispose_engine()
        returncode, output = await _alembic("upgrade", "head")
        assert returncode == 0, output

        async with _factory()() as session:
            owned = await session.get(ScanEvent, owned_id)
            ambiguous = await session.get(ScanEvent, ambiguous_id)
            assert (owned.account_id, owned.account_attachment_allowed) == (account_id, False)
            assert (ambiguous.account_id, ambiguous.account_attachment_allowed) == (None, False)
            # A scan recorded after the revision carries the real value.
            fresh, created = await product_service.record_scan(
                session, barcode=BARCODE, outcome=product_service.OUTCOME_NOT_FOUND,
                client_scan_id=uuid.uuid4().hex, device_id=device, account_id=None,
            )
            assert created and fresh.account_attachment_allowed is True
            await session.commit()

        token_b, account_b = await registered_supabase_user()
        assert await _claim(app_client, phone, token_b) == 1
        async with _factory()() as session:
            assert (await session.get(ScanEvent, ambiguous_id)).account_id is None
            assert (await session.get(ScanEvent, fresh.id)).account_id == account_b
            assert (await session.get(ScanEvent, owned_id)).account_id == account_id
    finally:
        await sql.dispose_engine()
        await _alembic("upgrade", "head")


async def test_f_e5_the_shared_snapshot_still_resolves_after_the_original_confirmer_is_erased(
    app_client, db_clean, registered_supabase_user, deletion_boundaries,  # noqa: F811
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a, phone_b = await _device(app_client, token_a), await _device(app_client, token_b)
    device_a, device_b = (await _device_row(phone_a)).id, (await _device_row(phone_b)).id
    a1, s1 = await _capture(phone_a, account_a, _facts())
    b1, reused = await _capture(phone_b, account_b, _facts())
    assert reused.id == s1.id and s1.scan_event_id == a1.id

    await _erase(app_client, token_a)

    async with _factory()() as session:
        source = await session.get(ScanEvent, a1.id)
        snapshot = await session.get(LabelSnapshot, s1.id)
        assert source.label_facts is None and source.account_id is None
        assert source.account_attachment_allowed is False
        assert source.device_id == device_a and source.ai_run_id == a1.ai_run_id
        retained = (await withdrawn_confirmation.retained_runs(session, [source.ai_run_id]))[source.ai_run_id]
        assert withdrawn_confirmation.proves_withdrawn_confirmation(
            snapshot=snapshot, source=source, retained=retained, barcode=BARCODE,
        )
        pack_b = await pack_context.current_pack(session, barcode=BARCODE, device_id=device_b)
        assert pack_b.is_proven and pack_b.scan_event.id == b1.id
        assert (await resolve_current_pack_label_snapshot(session, pack=pack_b)).id == s1.id

    # B claiming A's old phone takes none of A's provenance.
    assert await _claim(app_client, phone_a, token_b) == 0
    async with _factory()() as session:
        source = await session.get(ScanEvent, a1.id)
        assert source.account_id is None and source.label_facts is None
        assert (await session.get(LabelSnapshot, s1.id)).scan_event_id == a1.id
        assert (await session.get(AIRun, a1.ai_run_id)).account_id is None


async def test_f_e5_a_source_still_open_to_a_claim_does_not_prove_a_withdrawal(
    app_client, db_clean, registered_supabase_user,
):
    """The flag strengthens Lane C: erasure marks every row it withdraws, so an
    accountless capture still marked attachable is not an erased confirmation."""
    world = await _erased_original(app_client, registered_supabase_user)
    async with _factory()() as session:
        await session.execute(
            update(ScanEvent).where(ScanEvent.id == world["a1"].id).values(account_attachment_allowed=True)
        )
        await session.commit()
    with pytest.raises(CurrentPackSnapshotUnresolved):
        async with _factory()() as session:
            pack = await pack_context.current_pack(session, barcode=BARCODE, device_id=world["device_b"])
            await resolve_current_pack_label_snapshot(session, pack=pack)
    async with _factory()() as session:
        assert (await session.get(LabelSnapshot, world["s1"].id)).scan_event_id == world["a1"].id


# ---------------------------------------------------------------------------
# F. A consumed invite reservation keeps no identity after deletion
# ---------------------------------------------------------------------------
async def _consumed_reservation(account_id: uuid.UUID, email: str) -> uuid.UUID:
    async with _factory()() as session:
        invite = Invite(code=f"LANEF{uuid.uuid4().hex[:10].upper()}", label="lane-f", max_uses=5, uses_count=1)
        session.add(invite)
        await session.flush()
        reservation = InviteRegistrationReservation(
            invite_id=invite.id, email_normalised=email, challenge_hash=uuid.uuid4().hex * 2,
            status="consumed", expires_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
            consumed_at=datetime(2026, 9, 1, 11, 45, tzinfo=UTC), supabase_user_id=account_id,
        )
        session.add(reservation)
        await session.commit()
        return reservation.id


async def _reservation(reservation_id) -> InviteRegistrationReservation:
    async with _factory()() as session:
        return await session.get(InviteRegistrationReservation, reservation_id)


def _values(row: InviteRegistrationReservation) -> str:
    return " ".join(str(getattr(row, column.name)) for column in InviteRegistrationReservation.__table__.columns)


class _FlakyAdmin:
    """Supabase admin that fails the first identity deletion, then succeeds."""

    def __init__(self) -> None:
        self.calls = 0
        outer = self

        class _AuthAdmin:
            def delete_user(self, user_id: str) -> None:
                outer.calls += 1
                if outer.calls == 1:
                    raise RuntimeError("identity provider unavailable")

        class _Auth:
            admin = _AuthAdmin()

        self.auth = _Auth()


async def test_f_f1_f2_f3_f4_deletion_minimises_the_consumed_reservation_and_retries_cleanly(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    from app.domains.media.storage import factory

    factory.set_storage(_EvidenceStorage([]))
    admin = _FlakyAdmin()
    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: admin)
    try:
        token_a, account_a = await registered_supabase_user()
        _token_b, account_b = await registered_supabase_user()
        mine = await _consumed_reservation(account_a, "a.person@example.com")
        theirs = await _consumed_reservation(account_b, "b.person@example.com")
        before_mine, before_theirs = await _reservation(mine), await _reservation(theirs)

        deleted = await app_client.delete("/api/v2/privacy/account", headers=auth(token_a))
        assert deleted.status_code == 202, deleted.text
        first = await account_deletion.run_cycle()
        # The database stage committed; the identity provider failed; a retry is due.
        assert admin.calls == 1 and not first.ok
        minimised = await _reservation(mine)
        assert minimised.email_normalised == deletion_service.ERASED_RESERVATION_EMAIL
        assert minimised.supabase_user_id is None

        await _make_due(account_a)
        second = await account_deletion.run_cycle()
        assert second.ok and admin.calls == 2

        final = await _reservation(mine)
        # F1/F2: no email, no Auth id, and no encoding of either, anywhere in the row.
        values = _values(final)
        assert "a.person" not in values and "example.com" not in values
        assert str(account_a) not in values and account_a.hex not in values
        assert final.email_normalised == deletion_service.ERASED_RESERVATION_EMAIL
        # The operational history stays exactly as it was.
        assert (final.invite_id, final.status, final.consumed_at, final.expires_at, final.challenge_hash) == (
            before_mine.invite_id, before_mine.status, before_mine.consumed_at,
            before_mine.expires_at, before_mine.challenge_hash,
        )
        # F3: somebody else's reservation is untouched.
        assert _values(await _reservation(theirs)) == _values(before_theirs)
        # F4: minimising again changes nothing.
        async with _factory()() as session:
            await deletion_service._minimise_invite_reservations(session, account_a)
            await session.commit()
        assert _values(await _reservation(mine)) == values
    finally:
        factory.set_storage(None)


async def test_f_f_minimisation_touches_only_the_deleting_accounts_rows(db_clean, registered_supabase_user):
    _token_a, account_a = await registered_supabase_user()
    _token_b, account_b = await registered_supabase_user()
    mine = await _consumed_reservation(account_a, "a@example.com")
    theirs = await _consumed_reservation(account_b, "b@example.com")
    unclaimed = await _consumed_reservation(None, "never-registered@example.com")
    before = {rid: _values(await _reservation(rid)) for rid in (theirs, unclaimed)}
    async with _factory()() as session:
        await deletion_service._minimise_invite_reservations(session, account_a)
        await session.commit()
    assert (await _reservation(mine)).supabase_user_id is None
    assert {rid: _values(await _reservation(rid)) for rid in (theirs, unclaimed)} == before
