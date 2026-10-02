"""Audit lane 1, F13: one claim of an unclaimed phone wins. Against PostgreSQL 16.

Claiming read ``claimed_by_account_id`` and then wrote it. Two accounts
claiming one unclaimed phone at the same moment could both read NULL, both
write, and both be told they had claimed it; the second write silently
replaced the first, and whichever account lost still believed it owned the
phone. Now one conditional UPDATE decides it (``devices.claim``): unclaimed or
already this account's, or no row matches and the claim is refused.

* unclaimed + A: A owns it, and the scans from before sign-up follow A in;
* A + A: a replay, the same success;
* A + B: a plain, non-retryable 409, and nothing moves.

The losing side used to raise ``ConflictError`` without the
``current_version`` that error requires, a ``TypeError`` that surfaced as a
500; the proof-less re-registration refusal had the same defect. Both are now
an ordinary 409.

The race is real: two sessions, the second blocked on the first's row lock
as PostgreSQL reports it (``pg_blocking_pids``, ``pg_locks``).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.product import devices
from app.domains.product import service as product_service
from app.domains.product.models import LabelErrorReport, ScanDevice, ScanEvent
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import AppError
from sqlalchemy import select, text

from tests.conftest import auth
from tests.test_scan_physical_pack_integrity import (
    no_external_product_data,  # noqa: F401 - re-exported autouse fixture
)

pytestmark = pytest.mark.asyncio

BARCODE = "8901030865278"
REPORT = "/api/v2/reports/label-error"


def _factory():
    return get_sessionmaker()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _phone(app_client) -> dict[str, str]:
    registered = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert registered.status_code == 201, registered.text
    return {"X-Device-Token": registered.json()["token"]}


async def _device(phone) -> ScanDevice:
    async with _factory()() as session:
        return (await session.execute(
            select(ScanDevice).where(ScanDevice.token_hash == devices._hash(phone["X-Device-Token"]))
        )).scalar_one()


async def _claim(app_client, phone, token):
    return await app_client.post("/api/v2/scan/device/claim", headers={**phone, **auth(token)})


async def _scan(app_client, phone, *, token: str | None = None, client_scan_id: str | None = None, **body):
    client_scan_id = client_scan_id or uuid.uuid4().hex
    response = await app_client.post(
        "/api/v2/scan/events",
        headers={**phone, **(auth(token) if token else {})},
        json={"barcode": BARCODE, "client_scan_id": client_scan_id, **body},
    )
    assert response.status_code == 201, response.text
    return client_scan_id


async def _report(app_client, phone, *, token: str | None = None, client_report_id: str | None = None):
    client_report_id = client_report_id or uuid.uuid4().hex
    response = await app_client.post(
        REPORT, headers={**phone, **(auth(token) if token else {})},
        data={"client_report_id": client_report_id, "subject": "Sugar", "reason": "wrong_number", "barcode": BARCODE},
    )
    assert response.status_code == 201, response.text
    return client_report_id


async def _event(client_scan_id: str) -> ScanEvent:
    async with _factory()() as session:
        return (await session.execute(
            select(ScanEvent).where(ScanEvent.client_scan_id == client_scan_id)
        )).scalar_one()


async def _report_row(client_report_id: str) -> LabelErrorReport:
    async with _factory()() as session:
        return (await session.execute(
            select(LabelErrorReport).where(LabelErrorReport.client_report_id == client_report_id)
        )).scalar_one()


async def _exported(app_client, token) -> tuple[set[str], set[str]]:
    response = await app_client.get("/api/v2/privacy/export", headers=auth(token))
    assert response.status_code == 200, response.text
    domain = response.json()["domains"]["product_scans"]
    return (
        {row["client_scan_id"] for row in domain["scans"]},
        {row["client_report_id"] for row in domain["label_error_reports"]},
    )


async def _pid(session) -> int:
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _until_blocked_on(pid: int, relation: str) -> None:
    """Poll until backend ``pid`` waits for a row lock on ``relation``."""
    for _ in range(3000):
        async with _factory()() as watcher:
            blockers = list(await watcher.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}) or [])
            relations = set((await watcher.execute(text(
                "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.pid = :pid AND l.locktype = 'tuple'"
            ), {"pid": pid})).scalars().all())
        if blockers and relation in relations:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never waited on a {relation} row")


async def _until_any_backend_blocked_on(relation: str) -> None:
    """Poll until some backend waits for a row lock on ``relation``."""
    for _ in range(3000):
        async with _factory()() as watcher:
            pids = list((await watcher.execute(text(
                "SELECT l.pid FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.locktype = 'tuple' AND c.relname = :relation "
                "AND cardinality(pg_blocking_pids(l.pid)) > 0"
            ), {"relation": relation})).scalars().all())
        if pids:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"no backend ever waited on a {relation} row")



async def test_f13_re_registering_a_known_device_without_its_token_is_a_plain_409(app_client, db_clean):
    key = uuid.uuid4().hex
    first = await app_client.post("/api/v2/scan/device", json={"device_key": key, "platform": "android"})
    assert first.status_code == 201
    again = await app_client.post("/api/v2/scan/device", json={"device_key": key, "platform": "android"})
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["retryable"] is False


async def test_f13_claim_semantics_unclaimed_then_replay_then_another_account(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, _account_b = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _claim(app_client, phone, token_a)).status_code == 200
    assert (await _claim(app_client, phone, token_a)).status_code == 200
    refused = await _claim(app_client, phone, token_b)
    assert refused.status_code == 409
    assert refused.json()["detail"]["retryable"] is False
    assert (await _device(phone)).claimed_by_account_id == account_a


async def test_f13_two_sessions_racing_for_one_unclaimed_phone_exactly_one_wins(
    app_client, db_clean, registered_supabase_user,
):
    """Both have read the phone unclaimed before either writes: the old race."""
    _token_a, account_a = await registered_supabase_user()
    _token_b, account_b = await registered_supabase_user()
    phone = await _phone(app_client)
    before_signup = await _scan(app_client, phone)
    token = phone["X-Device-Token"]

    first, second = _factory()(), _factory()()
    try:
        device_1 = await devices.resolve(first, token)
        device_2 = await devices.resolve(second, token)
        # The barrier: each transaction has seen the phone unclaimed.
        assert device_1.claimed_by_account_id is None and device_2.claimed_by_account_id is None

        await devices.claim(first, device=device_1, account_id=account_a)
        moved = await product_service.attach_scans_to_account(first, device_id=device_1.id, account_id=account_a)
        assert moved == 1

        second_pid = await _pid(second)
        contender = asyncio.create_task(devices.claim(second, device=device_2, account_id=account_b))
        await _until_blocked_on(second_pid, "scan_devices")
        assert not contender.done()

        await first.commit()
        with pytest.raises(AppError) as refused:
            await asyncio.wait_for(contender, timeout=30)
        assert refused.value.status_code == 409
        await second.rollback()
    finally:
        await first.close()
        await second.close()

    assert (await _device(phone)).claimed_by_account_id == account_a
    assert (await _event(before_signup)).account_id == account_a


async def test_f13_two_concurrent_http_claims_never_both_succeed(app_client, db_clean, registered_supabase_user):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    for _ in range(6):
        phone = await _phone(app_client)
        first, second = await asyncio.gather(_claim(app_client, phone, token_a), _claim(app_client, phone, token_b))
        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [200, 409], (first.text, second.text)
        winner = account_a if first.status_code == 200 else account_b
        assert (await _device(phone)).claimed_by_account_id == winner
