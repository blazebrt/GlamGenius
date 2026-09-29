"""Lane F correction: one idempotency key is one paid operation, against PostgreSQL 16.

``POST /api/v2/scan/analyse`` accepted an ``idempotency_key`` but reserved its
allowance without it, and only deduplicated the usage event after the provider
had answered. On the reviewed head, the same key sent twice paid Gemini twice
(two successful scans, one usage row), sequentially and concurrently.

Now the reservation carries the key, under a period-free lock of its own:

* the first request for a key is the only one that reaches the provider;
* a duplicate while it runs is ``409 scan_in_progress`` and never reaches it;
* a retry after success replays the stored result (``Idempotent-Replayed``);
* a known failure leaves the key free, and an expired reservation may be retried;
* no provider call may outlive its reservation (Q10).

Interleavings come from a gated provider and PostgreSQL's own lock tables; time
comes from injected instants. No sleep decides an outcome.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

import pytest
from app import config
from app.domains.ai_gateway.providers import gemini
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import IDEMPOTENCY_KEY_MAX_LENGTH, BetaUsageReservation
from app.domains.privacy import REGISTRY, Classification
from app.domains.privacy import export as export_mod
from app.domains.privacy.coverage import EXPORT_COVERAGE
from app.domains.scan.models import Scan
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import auth, png_bytes
from tests.test_lane_f_usage_reservations import (
    NOON,
    _account,
    _advisory_locks,
    _consented,
    _factory,
    _idle_in_transaction,
    _pid,
    _reservations,
    _usage,
    _waiting_or_done,
    gated,  # noqa: F401 - fixture
    one_scan_per_month,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.asyncio

OK = '{"observations": ["Even tone"], "colour_palette": [], "recommended_next_steps": [], "confidence": 0.8}'
ROUTE = "/api/v2/scan/analyse"
REPLAYED = "Idempotent-Replayed"
CONTENDERS = 6
IMAGE = base64.b64encode(png_bytes()).decode("ascii")


def _body(key: str | None = "K1", **extra) -> dict:
    body = {"image_base64": IMAGE, "scan_type": "face", **extra}
    if key is not None:
        body["idempotency_key"] = key
    return body


async def _scans(account_id) -> list[Scan]:
    async with _factory()() as session:
        return list((await session.execute(
            select(Scan).where(Scan.account_id == account_id).order_by(Scan.created_at)
        )).scalars().all())


def _ok(scans: list[Scan]) -> list[Scan]:
    return [row for row in scans if row.status == "ok"]


@pytest.fixture
def at(monkeypatch):
    """Pin the allowance clock to an instant the test chooses."""
    def pin(instant: datetime) -> None:
        monkeypatch.setattr(beta, "_now", lambda: instant)
    return pin


# ---------------------------------------------------------------------------
# Q1 — sequential: the same key twice is one provider call, one result, one unit
# ---------------------------------------------------------------------------
async def test_q1_a_repeated_key_replays_the_first_result_without_calling_the_provider(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)

    first = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    second = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    # A reused key replays the operation; it does not reinterpret new input.
    third = await app_client.post(ROUTE, headers=auth(token), json=_body("K1", scan_type="hair"))

    assert first.status_code == 201, first.text
    assert REPLAYED not in first.headers
    for replay in (second, third):
        assert replay.status_code == 201, replay.text
        assert replay.headers[REPLAYED] == "true"
        assert replay.json() == first.json()
    assert gated.calls == 1
    usage = await _usage(account_id, beta.FEATURE_SCAN)
    assert [row.idempotency_key for row in usage] == ["K1"]
    scans = await _scans(account_id)
    assert [(row.status, row.idempotency_key, str(row.id)) for row in scans] == [("ok", "K1", first.json()["id"])]
    assert await _reservations(account_id) == []


# ---------------------------------------------------------------------------
# Q2 — concurrent: a duplicate while the first is in the provider never reaches it
# ---------------------------------------------------------------------------
async def test_q2_a_duplicate_while_the_first_is_in_the_provider_is_in_progress_and_never_calls_it(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    token, account_id = await _consented(registered_supabase_user)
    one = asyncio.create_task(app_client.post(ROUTE, headers=auth(token), json=_body("K1")))
    try:
        await asyncio.wait_for(gated.entered.wait(), timeout=30)
        # The first request is inside the provider. The duplicate finishes on
        # its own, without the first letting go of anything.
        two = await asyncio.wait_for(app_client.post(ROUTE, headers=auth(token), json=_body("K1")), timeout=30)
        assert two.status_code == 409, two.text
        assert two.json()["detail"]["code"] == "scan_in_progress"
        assert two.json()["detail"]["allowance_consumed"] is False
        assert gated.calls == 1
        held = await _reservations(account_id)
        assert [row.idempotency_key for row in held] == ["K1"]
        # The reservation is the authority, not a lock or an open transaction.
        assert await _advisory_locks() == 0 and await _idle_in_transaction() == 0
    finally:
        gated.gate.set()
    first = await one
    assert first.status_code == 201, first.text
    assert gated.calls == 1
    assert len(await _usage(account_id, beta.FEATURE_SCAN)) == 1
    assert len(_ok(await _scans(account_id))) == 1
    replay = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert replay.headers[REPLAYED] == "true" and replay.json() == first.json() and gated.calls == 1


async def test_q2_n_simultaneous_requests_for_one_key_reach_the_provider_once(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    token, account_id = await _consented(registered_supabase_user)
    tasks = [
        asyncio.create_task(app_client.post(ROUTE, headers=auth(token), json=_body("K1")))
        for _ in range(CONTENDERS)
    ]
    try:
        await asyncio.wait_for(gated.entered.wait(), timeout=30)
        for _ in range(3000):
            if sum(task.done() for task in tasks) == CONTENDERS - 1:
                break
            await asyncio.sleep(0.01)
        assert sum(task.done() for task in tasks) == CONTENDERS - 1
        assert gated.calls == 1
        assert await _advisory_locks() == 0 and await _idle_in_transaction() == 0
    finally:
        gated.gate.set()
    responses = [await task for task in tasks]
    assert sorted(r.status_code for r in responses) == [201] + [409] * (CONTENDERS - 1), [r.text for r in responses]
    assert all(r.json()["detail"]["code"] == "scan_in_progress" for r in responses if r.status_code == 409)
    assert gated.calls == 1
    assert len(await _usage(account_id, beta.FEATURE_SCAN)) == 1
    assert len(_ok(await _scans(account_id))) == 1 and await _reservations(account_id) == []


async def test_q2_the_operation_lock_is_period_free_so_a_boundary_cannot_split_one_key(db_clean):
    """Two requests for one key either side of a month boundary take different
    budget locks. The operation lock is taken first and has no period in it, so
    the second waits for the first and then sees its live reservation."""
    account_id = await _account()
    september = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
    october = datetime(2026, 10, 1, 0, 0, 1, tzinfo=UTC)
    pids: dict[int, int] = {}
    first_reserved, first_release = asyncio.Event(), asyncio.Event()

    async def first():
        async with _factory()() as session:
            pids[0] = await _pid(session)
            await beta.reserve_usage(
                session, account_id=account_id, feature=beta.FEATURE_SCAN, idempotency_key="K1", now=september,
            )
            first_reserved.set()
            await first_release.wait()
            await session.commit()

    async def second():
        async with _factory()() as session:
            pids[1] = await _pid(session)
            await beta.reserve_usage(
                session, account_id=account_id, feature=beta.FEATURE_SCAN, idempotency_key="K1", now=october,
            )

    one = asyncio.create_task(first())
    await asyncio.wait_for(first_reserved.wait(), timeout=30)
    two = asyncio.create_task(second())
    assert await _waiting_or_done(pids, 1, two) == "waiting"
    first_release.set()
    await one
    with pytest.raises(beta.UsageOperationInProgress):
        await two
    assert [row.period_key for row in await _reservations(account_id)] == ["2026-09"]


async def test_q2_the_database_refuses_a_second_reservation_row_for_one_operation(db_clean):
    account_id = await _account()
    async with _factory()() as session:
        for feature, key in ((beta.FEATURE_SCAN, "K1"), (beta.FEATURE_SCAN, None), (beta.FEATURE_SCAN, None)):
            session.add(BetaUsageReservation(
                account_id=account_id, feature=feature, period_key="2026-09", quantity=1,
                idempotency_key=key, created_at=NOON, expires_at=NOON + beta.RESERVATION_TTL,
            ))
        await session.commit()  # NULL keys are independent requests
    async with _factory()() as session:
        session.add(BetaUsageReservation(
            account_id=account_id, feature=beta.FEATURE_SCAN, period_key="2026-10", quantity=1,
            idempotency_key="K1", created_at=NOON, expires_at=NOON + timedelta(seconds=1),
        ))
        with pytest.raises(IntegrityError, match="uq_beta_usage_reservations_operation"):
            await session.commit()


# ---------------------------------------------------------------------------
# Q3 — different keys are different operations
# ---------------------------------------------------------------------------
async def test_q3_different_keys_are_independent_operations_each_consuming_a_unit(
    app_client, db_clean, registered_supabase_user, gated, one_scan_per_month,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    first = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    second = await app_client.post(ROUTE, headers=auth(token), json=_body("K2"))
    assert first.status_code == 201, first.text
    # One unit a month: K2 is a new operation and needs its own unit.
    assert second.status_code == 429 and second.json()["detail"]["code"] == "beta_limit_reached"
    assert gated.calls == 1

    beta._MONTH_FEATURES[beta.FEATURE_SCAN] = 2
    second = await app_client.post(ROUTE, headers=auth(token), json=_body("K2"))
    assert second.status_code == 201 and REPLAYED not in second.headers
    assert second.json()["id"] != first.json()["id"]
    assert gated.calls == 2
    assert sorted(row.idempotency_key for row in await _usage(account_id, beta.FEATURE_SCAN)) == ["K1", "K2"]
    # A completed operation still replays when the budget is exhausted.
    replay = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert replay.status_code == 201 and replay.headers[REPLAYED] == "true" and gated.calls == 2


# ---------------------------------------------------------------------------
# Q4 — a key is scoped to its account
# ---------------------------------------------------------------------------
async def test_q4_the_same_key_in_two_accounts_is_two_operations_with_no_cross_replay(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token_a, account_a = await _consented(registered_supabase_user)
    token_b, account_b = await _consented(registered_supabase_user)
    a1 = await app_client.post(ROUTE, headers=auth(token_a), json=_body("K1"))
    b1 = await app_client.post(ROUTE, headers=auth(token_b), json=_body("K1"))
    assert a1.status_code == b1.status_code == 201
    assert REPLAYED not in b1.headers and b1.json()["id"] != a1.json()["id"]
    assert gated.calls == 2
    a2 = await app_client.post(ROUTE, headers=auth(token_a), json=_body("K1"))
    b2 = await app_client.post(ROUTE, headers=auth(token_b), json=_body("K1"))
    assert a2.json() == a1.json() and b2.json() == b1.json() and gated.calls == 2
    for account_id in (account_a, account_b):
        assert [row.idempotency_key for row in await _usage(account_id, beta.FEATURE_SCAN)] == ["K1"]
        assert len(_ok(await _scans(account_id))) == 1


# ---------------------------------------------------------------------------
# Q5 — a known failure never uses the key up
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("failure", ["provider_failure", "invalid_json"])
async def test_q5_a_known_failure_releases_the_key_and_a_retry_settles_once(
    app_client, db_clean, registered_supabase_user, gated, failure,  # noqa: F811 - shared fixture
):
    gated.gate.set()
    if failure == "provider_failure":
        gated.raises = gemini.ProviderCallFailed("upstream exploded")
    else:
        gated.text = "not json at all"
    token, account_id = await _consented(registered_supabase_user)

    failed = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert failed.status_code == 502, failed.text
    assert await _reservations(account_id) == [] and await _usage(account_id, beta.FEATURE_SCAN) == []
    # The failure is on record, without the key: it never blocks a retry.
    assert [(row.status, row.idempotency_key) for row in await _scans(account_id)] == [("provider_failure", None)]

    gated.raises, gated.text = None, OK
    retried = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert retried.status_code == 201 and REPLAYED not in retried.headers
    assert gated.calls == 2
    again = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert again.headers[REPLAYED] == "true" and again.json() == retried.json() and gated.calls == 2
    assert [row.idempotency_key for row in await _usage(account_id, beta.FEATURE_SCAN)] == ["K1"]
    assert [row.idempotency_key for row in _ok(await _scans(account_id))] == ["K1"]


async def test_q5_a_failed_row_can_never_carry_the_key(db_clean):
    account_id = await _account()
    async with _factory()() as session:
        session.add(Scan(
            account_id=account_id, scan_type="face", status="provider_failure", analysis={}, idempotency_key="K1",
        ))
        with pytest.raises(IntegrityError, match="ck_scans_idempotency_key_successful_only"):
            await session.commit()


# ---------------------------------------------------------------------------
# Q6 — an unsettled reservation blocks its key until it genuinely expires
# ---------------------------------------------------------------------------
async def test_q6_a_crashed_attempt_blocks_its_key_until_expiry_then_a_retry_runs_once(
    app_client, db_clean, registered_supabase_user, gated, at,  # noqa: F811 - shared fixture
):
    """A process reserved K and died: it never settled or released. Whether the
    provider answered is unknown; nothing was counted and no result stored. A
    live reservation blocks K. Once it expires, a retry may run — once."""
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    started = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    async with _factory()() as session:
        await beta.reserve_usage(
            session, account_id=account_id, feature=beta.FEATURE_SCAN, idempotency_key="K1", now=started,
        )
        await session.commit()

    at(started + beta.RESERVATION_TTL - timedelta(seconds=1))
    blocked = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "scan_in_progress"
    assert gated.calls == 0

    at(started + beta.RESERVATION_TTL)
    retried = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert retried.status_code == 201, retried.text
    assert gated.calls == 1
    assert await _reservations(account_id) == []
    assert [row.idempotency_key for row in await _usage(account_id, beta.FEATURE_SCAN)] == ["K1"]
    replay = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert replay.headers[REPLAYED] == "true" and gated.calls == 1


async def test_q6_the_service_distinguishes_new_in_progress_expired_and_completed(db_clean):
    account_id = await _account()
    feature = beta.FEATURE_AI_REQUEST
    async with _factory()() as session:
        held = await beta.reserve_usage(session, account_id=account_id, feature=feature, idempotency_key="run-1", now=NOON)
        await session.commit()
    async with _factory()() as session:
        with pytest.raises(beta.UsageOperationInProgress):
            await beta.reserve_usage(
                session, account_id=account_id, feature=feature, idempotency_key="run-1",
                now=NOON + beta.RESERVATION_TTL - timedelta(microseconds=1),
            )
    async with _factory()() as session:
        retry = await beta.reserve_usage(
            session, account_id=account_id, feature=feature, idempotency_key="run-1",
            now=NOON + beta.RESERVATION_TTL,
        )
        await session.commit()
    assert retry.id != held.id
    assert [row.id for row in await _reservations(account_id)] == [retry.id]
    async with _factory()() as session:
        # A system-generated key settles under the key it was reserved for.
        with pytest.raises(ValueError):
            await beta.settle_usage(session, retry, idempotency_key="something-else")
    async with _factory()() as session:
        await beta.settle_usage(session, retry)
        await session.commit()
    async with _factory()() as session:
        with pytest.raises(beta.UsageOperationCompleted):
            await beta.reserve_usage(
                session, account_id=account_id, feature=feature, idempotency_key="run-1", now=NOON + timedelta(days=40),
            )
    assert [row.idempotency_key for row in await _usage(account_id, feature)] == ["run-1"]


# ---------------------------------------------------------------------------
# Q7 — a key names an operation, not a period
# ---------------------------------------------------------------------------
async def test_q7_a_september_success_retried_in_october_replays_without_spending(
    app_client, db_clean, registered_supabase_user, gated, at,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    at(datetime(2026, 9, 30, 23, 59, 50, tzinfo=UTC))
    first = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    at(datetime(2026, 10, 1, 0, 0, 10, tzinfo=UTC))
    retried = await app_client.post(ROUTE, headers=auth(token), json=_body("K1"))
    assert first.status_code == retried.status_code == 201
    assert retried.headers[REPLAYED] == "true" and retried.json() == first.json()
    assert gated.calls == 1
    assert [(row.idempotency_key, row.period_key) for row in await _usage(account_id, beta.FEATURE_SCAN)] == [
        ("K1", "2026-09"),
    ]


async def test_q7_an_hourly_operation_is_complete_in_every_later_hour(db_clean):
    account_id = await _account()
    feature = beta.FEATURE_AI_REQUEST
    async with _factory()() as session:
        reservation = await beta.reserve_usage(
            session, account_id=account_id, feature=feature, idempotency_key="op-1",
            now=datetime(2026, 9, 27, 12, 59, 50, tzinfo=UTC),
        )
        await beta.settle_usage(session, reservation)
        await session.commit()
    for later in (datetime(2026, 9, 27, 13, 0, 5, tzinfo=UTC), datetime(2027, 1, 1, 0, 0, tzinfo=UTC)):
        async with _factory()() as session:
            with pytest.raises(beta.UsageOperationCompleted):
                await beta.reserve_usage(session, account_id=account_id, feature=feature, idempotency_key="op-1", now=later)
    assert await _reservations(account_id) == []


# ---------------------------------------------------------------------------
# Q8 — an unusable key is refused before anything is reserved or paid for
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", [
    "K" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1),
    "K" * 10_000,
    "",
    " ",
    "two words",
    "line\nbreak",
    "clé",
])
async def test_q8_an_unusable_key_is_a_request_error_before_any_spend(
    app_client, db_clean, registered_supabase_user, gated, key,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    response = await app_client.post(ROUTE, headers=auth(token), json=_body(key))
    assert response.status_code == 422, response.text
    assert gated.calls == 0
    assert await _reservations(account_id) == []
    assert await _usage(account_id, beta.FEATURE_SCAN) == [] and await _scans(account_id) == []


async def test_q8_the_widest_storable_key_is_accepted(app_client, db_clean, registered_supabase_user, gated):  # noqa: F811 - shared fixture
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    key = "k" * IDEMPOTENCY_KEY_MAX_LENGTH
    first = await app_client.post(ROUTE, headers=auth(token), json=_body(key))
    again = await app_client.post(ROUTE, headers=auth(token), json=_body(key))
    assert first.status_code == 201 and again.headers[REPLAYED] == "true" and gated.calls == 1
    # The request bound is the storage bound, everywhere the key is kept.
    for column in (Scan.__table__.c.idempotency_key, BetaUsageReservation.__table__.c.idempotency_key):
        assert column.type.length == IDEMPOTENCY_KEY_MAX_LENGTH


# ---------------------------------------------------------------------------
# Q9 — no key: every request is its own operation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("omitted", [True, False])
async def test_q9_requests_without_a_key_are_independent_operations(
    app_client, db_clean, registered_supabase_user, gated, omitted,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    body = _body(None) if omitted else {**_body(None), "idempotency_key": None}
    first = await app_client.post(ROUTE, headers=auth(token), json=body)
    second = await app_client.post(ROUTE, headers=auth(token), json=body)
    assert first.status_code == second.status_code == 201
    assert REPLAYED not in first.headers and REPLAYED not in second.headers
    assert first.json()["id"] != second.json()["id"]
    assert gated.calls == 2
    assert [row.idempotency_key for row in await _usage(account_id, beta.FEATURE_SCAN)] == [None, None]
    assert [row.idempotency_key for row in await _scans(account_id)] == [None, None]


# ---------------------------------------------------------------------------
# Q10 — no provider call can outlive its reservation
# ---------------------------------------------------------------------------
async def test_q10_the_configured_provider_window_ends_before_a_reservation_can_expire():
    window = gemini.max_execution_seconds()
    assert window == config.AI_TIMEOUT_SECONDS * len(gemini._models_to_try())
    assert timedelta(seconds=config.USAGE_RESERVATION_TTL_SECONDS) == beta.RESERVATION_TTL
    assert config.usage_reservation_errors() == []
    assert window + config.USAGE_RESERVATION_MARGIN_SECONDS < beta.RESERVATION_TTL.total_seconds()
    config.validate_production_configuration()  # the running configuration passes the gate


@pytest.mark.parametrize("models", [1, 2, 3, 5, 8])
async def test_q10_every_permitted_configuration_finishes_before_expiry(models):
    ttl = config.USAGE_RESERVATION_TTL_SECONDS
    margin = config.USAGE_RESERVATION_MARGIN_SECONDS
    chain = [f"model-{index}" for index in range(models)]
    boundary = (ttl - margin) / models
    for timeout in (0.001, 1, 30, 45, 90, boundary - 0.001, boundary, boundary + 0.001, ttl, 10 * ttl):
        permitted = config.usage_reservation_errors(timeout_seconds=timeout, models=chain) == []
        window = config.ai_provider_window_seconds(timeout, chain)
        assert permitted == (window + margin < ttl), (models, timeout)
        if permitted:
            assert window < ttl - margin
    for unusable in (0, -1, math.nan, math.inf):
        assert config.usage_reservation_errors(timeout_seconds=unusable, models=chain) != []
    assert config.usage_reservation_errors(models=[]) != []


async def test_q10_an_unsafe_configuration_is_refused_at_startup_in_every_environment(monkeypatch):
    monkeypatch.setattr(config, "AI_TIMEOUT_SECONDS", 400.0)
    for app_env in ("development", "test", "staging", "production"):
        monkeypatch.setattr(config, "APP_ENV", app_env)
        with pytest.raises(RuntimeError, match="reservation"):
            config.validate_production_configuration()


async def test_q10_the_provider_refuses_to_start_a_call_that_could_outlive_its_reservation(monkeypatch):
    monkeypatch.setattr(gemini, "AI_TIMEOUT_SECONDS", 400.0)

    def no_client():
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(gemini, "get_client", no_client)
    with pytest.raises(gemini.ProviderNotConfigured, match="reservation"):
        await gemini.generate("p", "s")


async def test_q10_each_attempt_is_bounded_and_the_chain_fits_the_window(monkeypatch):
    timeouts: list[float] = []

    async def attempt(run, timeout):
        timeouts.append(timeout)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")  # the "try the next model" answer

    monkeypatch.setattr(gemini, "get_client", lambda: object())
    monkeypatch.setattr(gemini, "_attempt", attempt)
    with pytest.raises(gemini.ProviderCallFailed):
        await gemini.generate("p", "s")
    assert timeouts == [gemini.AI_TIMEOUT_SECONDS] * len(gemini._models_to_try())
    assert sum(timeouts) == gemini.max_execution_seconds()
    assert sum(timeouts) + config.USAGE_RESERVATION_MARGIN_SECONDS < beta.RESERVATION_TTL.total_seconds()


async def test_q10_the_sdk_request_itself_carries_the_attempt_timeout(monkeypatch):
    """``asyncio.wait_for`` stops waiting; it cannot stop the worker thread. The
    SDK's own HTTP timeout (milliseconds) ends the request itself."""
    captured: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(gemini, "GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(gemini, "HAS_GOOGLE_GENAI", True)
    monkeypatch.setattr(gemini.google_genai, "Client", _Client)
    gemini.reset_client()
    try:
        gemini.get_client()
    finally:
        gemini.reset_client()
    assert captured["http_options"].timeout == math.ceil(gemini.AI_TIMEOUT_SECONDS * 1000)


# ---------------------------------------------------------------------------
# Privacy: the key is request control — kept out of the export; no image kept
# ---------------------------------------------------------------------------
async def test_the_key_is_withheld_from_the_export_and_no_image_or_fingerprint_is_stored(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    key = "export-probe-key-7f3a"
    assert (await app_client.post(ROUTE, headers=auth(token), json=_body(key))).status_code == 201

    assert REGISTRY["beta_usage_reservations"] == Classification.OPERATIONAL
    assert REGISTRY["scans"] == Classification.INCLUDED
    assert EXPORT_COVERAGE["scans"].withheld == ("idempotency_key",)
    async with _factory()() as session:
        payload = await export_mod.build_export(session, account_id)
    exported_scans = payload["domains"]["scans"]["scans"]
    assert len(exported_scans) == 1 and "idempotency_key" not in exported_scans[0]
    assert key not in json.dumps(payload["domains"]["scans"], default=str)
    # ``beta_usage_events`` already exported its own ``idempotency_key`` before
    # this correction (the Lane E contract, schema 1.5); that is unchanged.
    usage_rows = payload["domains"]["ai_and_ops"]["beta_usage_events"]
    assert [row["idempotency_key"] for row in usage_rows] == [key]

    # Nothing derived from the image is stored anywhere the operation wrote.
    raw = png_bytes()
    fingerprints = {IMAGE, hashlib.sha256(raw).hexdigest(), hashlib.sha256(IMAGE.encode()).hexdigest()}
    async with _factory()() as session:
        for table in ("scans", "beta_usage_events", "beta_usage_reservations"):
            rows = (await session.execute(text(
                f"SELECT row_to_json(t)::text FROM {table} t WHERE account_id = :a"  # noqa: S608 - fixed names
            ), {"a": account_id})).scalars().all()
            for row in rows:
                assert not any(fingerprint in row for fingerprint in fingerprints), table
    assert {column.name for column in Scan.__table__.columns} == {
        "id", "account_id", "scan_type", "status", "provider", "model", "prompt_version", "schema_version",
        "latency_ms", "analysis", "failure_reason", "idempotency_key", "created_at", "updated_at",
    }
