"""Audit lane 1: a phone's claim is history, never authority. Against PostgreSQL 16.

Two findings from the 30 September 2026 audit, fixed together because each
decides what the other must mean. The claim race itself (F13) is proven in
``test_audit_lane1_device_claim``; this module holds the attribution rule
(F03) and the point where the two meet.

* **F03.** A scan or label-error report sent with only the device token was
  recorded as ``device.claimed_by_account_id``. So once A had claimed a
  phone, everything sent from it, after A logged out and whoever was holding
  it, went into A's history and privacy export. Now the bearer token on the
  request is the only thing that makes a row anybody's. The device token
  says which installation; the claim records who once attached it.
* **F13.** Claiming read the claim and then wrote it. Two accounts claiming
  one unclaimed phone at the same moment could both succeed, with the second
  silently replacing the first. Now one conditional UPDATE decides it.

They meet at one point: an anonymous scan may later follow its phone into an
account only if it was made before anyone claimed the phone. That decision
is read under the device row lock that a claim also takes, so a scan and a
concurrent claim are serialised one way or the other, never interleaved.

The races are real: two sessions, and every wait is on a lock PostgreSQL
reports (``pg_blocking_pids``, ``pg_locks``). No sleep decides an outcome.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.privacy import deletion_service
from app.domains.product import devices
from app.domains.product import service as product_service
from app.domains.product.models import LabelErrorReport, ScanEvent
from app.workers import account_deletion
from sqlalchemy import select

from tests.conftest import auth
from tests.test_audit_lane1_device_claim import (
    BARCODE,
    _claim,
    _device,
    _event,
    _exported,
    _factory,
    _phone,
    _report,
    _report_row,
    _scan,
    _until_any_backend_blocked_on,
)
from tests.test_scan_physical_pack_integrity import (
    deletion_boundaries,  # noqa: F401 - re-exported fixture
    no_external_product_data,  # noqa: F401 - re-exported autouse fixture
)

pytestmark = pytest.mark.asyncio


async def _erase(app_client, token) -> None:
    deleted = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert deleted.status_code == 202, deleted.text
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary


# ---------------------------------------------------------------------------
# F03 — the whole lifecycle of one phone
# ---------------------------------------------------------------------------
async def test_f03_anonymous_then_a_then_logout_then_b_attributes_every_scan_to_whoever_sent_it(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone = await _phone(app_client)

    # Fresh and anonymous: nobody's, and free to follow the phone in once.
    before_signup = await _scan(app_client, phone)
    assert ((await _event(before_signup)).account_id, (await _event(before_signup)).account_attachment_allowed) == (
        None, True,
    )

    # A signs in and claims: the scan from before follows A in, exactly once.
    claimed = await _claim(app_client, phone, token_a)
    assert claimed.status_code == 200 and claimed.json()["scans_attached"] == 1
    assert (await _event(before_signup)).account_id == account_a

    # Signed in as A: A's.
    as_a = await _scan(app_client, phone, token=token_a)
    assert ((await _event(as_a)).account_id, (await _event(as_a)).account_attachment_allowed) == (account_a, False)

    # A logs out. The phone is still claimed by A, and the device token still
    # works, but a scan sent with it alone is nobody's, now and later.
    after_logout = await _scan(app_client, phone)
    row = await _event(after_logout)
    assert (row.account_id, row.account_attachment_allowed) == (None, False)
    replay = await _claim(app_client, phone, token_a)
    assert replay.status_code == 200 and replay.json()["scans_attached"] == 0
    assert (await _event(after_logout)).account_id is None

    # B signs in on the same phone. B cannot take the claim, and nothing of A's
    # moves to B; B's own scans are B's.
    refused = await _claim(app_client, phone, token_b)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "CONFLICT"
    assert (await _device(phone)).claimed_by_account_id == account_a
    as_b = await _scan(app_client, phone, token=token_b)
    assert (await _event(as_b)).account_id == account_b

    # The exports say exactly that.
    scans_a, _ = await _exported(app_client, token_a)
    scans_b, _ = await _exported(app_client, token_b)
    assert scans_a == {before_signup, as_a}
    assert scans_b == {as_b}


async def test_f03_a_delayed_scan_is_whoever_sends_it_never_the_phones_claimant(
    app_client, db_clean, registered_supabase_user,
):
    """The server cannot know when a queued scan was made, and does not guess.

    It records who sends it. Keeping a queued scan with the identity it was
    made under is the phone's job (``productScan`` queues an owner and sends
    a scan only as that owner); here the server's half: whatever the client
    clock or the offline flag says, the claim is never used.
    """
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _claim(app_client, phone, token_a)).status_code == 200

    old = {"queued_offline": True, "scanned_at": "2026-01-01T00:00:00+00:00"}
    queued_anonymous = await _scan(app_client, phone, **old)
    queued_as_b = await _scan(app_client, phone, token=token_b, **old)
    queued_as_a = await _scan(app_client, phone, token=token_a, **old)
    assert (await _event(queued_anonymous)).account_id is None
    assert (await _event(queued_as_b)).account_id == account_b
    assert (await _event(queued_as_a)).account_id == account_a


async def test_f03_a_replayed_scan_keeps_the_identity_it_was_first_recorded_with(
    app_client, db_clean, registered_supabase_user,
):
    """Idempotency is by (device, client_scan_id), and a replay changes nothing."""
    token_a, account_a = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _claim(app_client, phone, token_a)).status_code == 200
    anonymous = await _scan(app_client, phone)
    await _scan(app_client, phone, token=token_a, client_scan_id=anonymous)
    assert (await _event(anonymous)).account_id is None
    owned = await _scan(app_client, phone, token=token_a)
    await _scan(app_client, phone, client_scan_id=owned)
    assert (await _event(owned)).account_id == account_a


async def test_f03_a_bearer_that_does_not_verify_is_refused_not_recorded_anonymously(app_client, db_clean):
    phone = await _phone(app_client)
    response = await app_client.post(
        "/api/v2/scan/events", headers={**phone, "Authorization": "Bearer not-a-real-token"},
        json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code == 401, response.text
    async with _factory()() as session:
        assert (await session.scalar(select(ScanEvent.id).limit(1))) is None


async def test_f03_reports_follow_the_same_rule(app_client, db_clean, registered_supabase_user):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _claim(app_client, phone, token_a)).status_code == 200

    by_a = await _report(app_client, phone, token=token_a)
    signed_out = await _report(app_client, phone)
    by_b = await _report(app_client, phone, token=token_b)
    assert (await _report_row(by_a)).account_id == account_a
    assert (await _report_row(signed_out)).account_id is None
    assert (await _report_row(by_b)).account_id == account_b

    _, reports_a = await _exported(app_client, token_a)
    _, reports_b = await _exported(app_client, token_b)
    assert reports_a == {by_a}
    assert reports_b == {by_b}


async def test_f03_erasing_a_takes_only_what_was_a_s_and_resurrects_nothing(
    app_client, db_clean, registered_supabase_user, deletion_boundaries,  # noqa: F811
):
    token_a, _account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _claim(app_client, phone, token_a)).status_code == 200
    as_a = await _scan(app_client, phone, token=token_a)
    report_a = await _report(app_client, phone, token=token_a)
    after_logout = await _scan(app_client, phone)
    anonymous_report = await _report(app_client, phone)
    as_b = await _scan(app_client, phone, token=token_b)

    await _erase(app_client, token_a)

    erased = await _event(as_a)
    assert (erased.account_id, erased.account_attachment_allowed) == (None, False)
    async with _factory()() as session:
        assert (await session.execute(
            select(LabelErrorReport).where(LabelErrorReport.client_report_id == report_a)
        )).scalar_one_or_none() is None
    # Not A's to begin with, so untouched by A's erasure.
    assert (await _event(after_logout)).account_id is None
    assert (await _report_row(anonymous_report)).account_id is None
    assert (await _event(as_b)).account_id == account_b

    # The phone is unclaimed again. B may claim it now, and gets only what is
    # genuinely attachable: none of A's erased history, and not the signed-out
    # scan made while A held the claim.
    claimed = await _claim(app_client, phone, token_b)
    assert claimed.status_code == 200 and claimed.json()["scans_attached"] == 0
    scans_b, _ = await _exported(app_client, token_b)
    assert scans_b == {as_b}


# ---------------------------------------------------------------------------
# F03 x F13 — an anonymous scan and a claim, at the same moment
# ---------------------------------------------------------------------------
async def test_a_claim_that_commits_first_makes_the_concurrent_anonymous_scan_unattachable(
    app_client, db_clean, registered_supabase_user,
):
    _token, account_id = await registered_supabase_user()
    phone = await _phone(app_client)
    claiming = _factory()()
    try:
        device = await devices.resolve(claiming, phone["X-Device-Token"])
        await devices.claim(claiming, device=device, account_id=account_id)  # row held, uncommitted

        scanning = asyncio.create_task(_scan(app_client, phone))
        await _until_any_backend_blocked_on("scan_devices")
        assert not scanning.done()
        await claiming.commit()
        scanned = await asyncio.wait_for(scanning, timeout=30)
    finally:
        await claiming.close()

    row = await _event(scanned)
    # Read fresh under the lock, after the claim: made on a claimed phone, signed out.
    assert (row.account_id, row.account_attachment_allowed) == (None, False)


async def test_an_anonymous_scan_that_commits_first_is_attached_by_the_claim_that_waited(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client)
    device_id = (await _device(phone)).id
    scanning = _factory()()
    try:
        event, created = await product_service.record_scan(
            scanning, barcode=BARCODE, outcome=product_service.OUTCOME_NOT_FOUND,
            client_scan_id="made-before-the-claim", device_id=device_id, account_id=None,
        )
        assert created and event.account_attachment_allowed is True  # device row now held

        claim = asyncio.create_task(_claim(app_client, phone, token))
        await _until_any_backend_blocked_on("scan_devices")
        assert not claim.done()
        await scanning.commit()
        response = await asyncio.wait_for(claim, timeout=30)
    finally:
        await scanning.close()

    assert response.status_code == 200 and response.json()["scans_attached"] == 1
    assert (await _event("made-before-the-claim")).account_id == account_id


async def test_the_claim_on_a_device_is_read_only_through_the_bearer_rule(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """Nothing on the write path consults the claim to decide ownership."""
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _claim(app_client, phone, token)).status_code == 200

    calls: list = []
    original = product_service.record_scan

    async def recording(session, **kwargs):
        calls.append(kwargs["account_id"])
        return await original(session, **kwargs)

    monkeypatch.setattr(product_service, "record_scan", recording)
    await _scan(app_client, phone)
    await _scan(app_client, phone, token=token)
    assert calls == [None, account_id]


async def test_deletion_request_racing_an_account_scan_never_leaves_an_owned_row_behind(
    app_client, db_clean, registered_supabase_user, deletion_boundaries,  # noqa: F811
):
    """An account-owned scan holds the account like a report does: deletion waits, then erases it."""
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client)
    holder = _factory()()
    try:
        await deletion_service.request_deletion(holder, account_id)  # account row held, uncommitted
        scanning = asyncio.create_task(_scan_expecting(app_client, phone, token))
        await _until_any_backend_blocked_on("accounts")
        await holder.commit()
        response = await asyncio.wait_for(scanning, timeout=30)
    finally:
        await holder.close()
    assert response.status_code == 403 and response.json()["detail"]["code"] == "ACCOUNT_INACTIVE"
    async with _factory()() as session:
        assert (await session.scalar(select(ScanEvent.id).where(ScanEvent.account_id == account_id))) is None


async def _scan_expecting(app_client, phone, token):
    return await app_client.post(
        "/api/v2/scan/events", headers={**phone, **auth(token)},
        json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
    )
