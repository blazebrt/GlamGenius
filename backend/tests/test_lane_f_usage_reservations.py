"""Lane F: an allowance unit is reserved before it is spent, against PostgreSQL 16.

The limiter used to check usage, call the provider, and record usage after
success. Every request racing for the last unit read "one left" and all of them
paid the provider. Now a unit is reserved atomically — under a transaction lock
keyed by account, feature and period — and the reservation is committed before
the provider is called. Success settles it into one usage event; a known
failure releases it; a crash leaves it to expire.

Every interleaving is built from explicit pauses and PostgreSQL's own lock
tables. The only waiting is a bounded poll of ``pg_locks``; no sleep decides an
outcome. Periods and expiry are driven by injected instants, never the clock.
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.providers import gemini
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import BetaUsageEvent, BetaUsageReservation
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.identity import service as identity
from app.domains.identity.models import Account
from app.domains.privacy import REGISTRY, Classification
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import AIRateLimitedError, AnalysisUnavailableError
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError

from tests.conftest import auth, png_bytes

pytestmark = pytest.mark.asyncio

NOON = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
CONTENDERS = 6


class _Shape(BaseModel):
    observations: list[str]


def _factory():
    return get_sessionmaker()


async def _account() -> uuid.UUID:
    account_id = uuid.uuid4()
    async with _factory()() as session:
        await identity.register_account(session, account_id)
        await session.commit()
    return account_id


async def _usage(account_id, feature=beta.FEATURE_AI_REQUEST) -> list[BetaUsageEvent]:
    async with _factory()() as session:
        return list((await session.execute(select(BetaUsageEvent).where(
            BetaUsageEvent.account_id == account_id, BetaUsageEvent.feature == feature,
        ))).scalars().all())


async def _reservations(account_id) -> list[BetaUsageReservation]:
    async with _factory()() as session:
        return list((await session.execute(select(BetaUsageReservation).where(
            BetaUsageReservation.account_id == account_id,
        ))).scalars().all())


async def _pid(session) -> int:
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _idle_in_transaction() -> int:
    """Sessions of this database holding a transaction open while doing nothing."""
    async with _factory()() as watcher:
        return int(await watcher.scalar(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND state LIKE 'idle in transaction%'"
        )))


async def _advisory_locks(*, granted: bool | None = None) -> int:
    clause = "" if granted is None else (" AND granted" if granted else " AND NOT granted")
    async with _factory()() as watcher:
        return int(await watcher.scalar(text(f"SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'{clause}")))


async def _waiting_or_done(pids: dict, index: int, task: asyncio.Task) -> str:
    """Poll PostgreSQL until contender ``index`` waits on an advisory lock, or finished."""
    for _ in range(3000):
        if task.done():
            return "done"
        pid = pids.get(index)
        if pid is not None:
            async with _factory()() as watcher:
                waiting = await watcher.scalar(text(
                    "SELECT count(*) FROM pg_locks WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"
                ), {"pid": pid})
            if waiting:
                return "waiting"
        await asyncio.sleep(0.01)
    raise AssertionError(f"contender {index} neither waited nor finished")


class _Pause:
    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self) -> None:
        self.reached.set()
        await self.release.wait()


@pytest.fixture
def one_unit_per_hour(monkeypatch):
    monkeypatch.setitem(beta._HOUR_FEATURES, beta.FEATURE_AI_REQUEST, 1)


@pytest.fixture
def one_scan_per_month(monkeypatch):
    monkeypatch.setitem(beta._MONTH_FEATURES, beta.FEATURE_SCAN, 1)


class _GatedProvider:
    """A provider that holds every caller at the door until the test opens it."""

    def __init__(self, text_: str = '{"observations": ["ok"]}') -> None:
        self.calls = 0
        self.text = text_
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.raises: BaseException | None = None

    async def generate(self, prompt, system=None, image_base64=None, **kwargs):
        self.calls += 1
        self.entered.set()
        await self.gate.wait()
        if self.raises is not None:
            raise self.raises
        return gemini.ProviderResponse(text=self.text, model="fake-model", input_tokens=10, output_tokens=5)


@pytest.fixture
def gated(monkeypatch) -> _GatedProvider:
    provider = _GatedProvider()
    monkeypatch.setattr(gemini, "generate", provider.generate)
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    return provider


async def _run(account_id):
    return await gateway.run_structured(
        feature="inventory_extract", prompt="p", system="s", schema=_Shape,
        prompt_version="1", schema_version="1", account_id_str=str(account_id),
    )


# ---------------------------------------------------------------------------
# The reservation itself: exactly one contender takes the last unit
# ---------------------------------------------------------------------------
async def test_f_g1_the_last_unit_goes_to_exactly_one_of_n_simultaneous_reservations(
    db_clean, one_unit_per_hour, monkeypatch,
):
    account_id = await _account()
    pause = _Pause()
    original = beta._held_quantity
    reads = 0

    async def held_then_pause(*args, **kwargs):
        nonlocal reads
        value = await original(*args, **kwargs)
        reads += 1
        if reads == 1:
            # The first contender has read the budget and holds the lock.
            await pause()
        return value

    monkeypatch.setattr(beta, "_held_quantity", held_then_pause)
    pids: dict[int, int] = {}

    async def contender(index: int):
        async with _factory()() as session:
            pids[index] = await _pid(session)
            try:
                taken = await beta.reserve_usage(
                    session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=NOON,
                )
            except beta.UsageExceeded:
                await session.rollback()
                return None
            await session.commit()
            return taken

    first = asyncio.create_task(contender(0))
    await asyncio.wait_for(pause.reached.wait(), timeout=30)
    others = [asyncio.create_task(contender(index)) for index in range(1, CONTENDERS)]
    try:
        # Every other contender is held at the lock, not reading a stale budget.
        states = [await _waiting_or_done(pids, index, task) for index, task in enumerate(others, start=1)]
        assert states == ["waiting"] * (CONTENDERS - 1), states
    finally:
        pause.release.set()
    results = [await first, *[await task for task in others]]

    winners = [row for row in results if row is not None]
    assert len(winners) == 1 and winners[0] == results[0]
    stored = await _reservations(account_id)
    assert [row.id for row in stored] == [winners[0].id]
    assert await _advisory_locks() == 0


async def test_f_g4_a_live_reservation_written_by_another_process_blocks_and_an_expired_one_does_not(
    db_clean, one_unit_per_hour,
):
    """Durable, not in-process: a row another worker committed is what counts."""
    account_id = await _account()
    period = beta._hour_key(NOON)
    async with _factory()() as session:
        await session.execute(text(
            "INSERT INTO beta_usage_reservations (id, account_id, feature, period_key, quantity, created_at, expires_at) "
            "VALUES (:id, :account, :feature, :period, 1, :created, :expires)"
        ), {"id": uuid.uuid4(), "account": account_id, "feature": beta.FEATURE_AI_REQUEST, "period": period,
            "created": NOON - timedelta(minutes=1), "expires": NOON + timedelta(minutes=5)})
        await session.commit()

    async with _factory()() as session:
        with pytest.raises(beta.UsageExceeded):
            await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=NOON)
        await session.rollback()
    # The other process died; its reservation expires and stops counting.
    later = NOON + timedelta(minutes=5, seconds=1)
    async with _factory()() as session:
        taken = await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=later)
        await session.commit()
    stored = await _reservations(account_id)
    assert [row.id for row in stored] == [taken.id], "the expired hold was tidied under the same lock"


async def test_f_g4_a_crashed_reservation_holds_its_unit_only_until_it_expires(db_clean, one_unit_per_hour):
    account_id = await _account()
    async with _factory()() as session:
        await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=NOON)
        await session.commit()
    # Nobody settles or releases it: the process died after reserving.
    for moment, allowed in (
        (NOON + timedelta(minutes=1), False),
        (NOON + beta.RESERVATION_TTL - timedelta(seconds=1), False),
        (NOON + beta.RESERVATION_TTL, True),
    ):
        async with _factory()() as session:
            if allowed:
                await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=moment)
                await session.commit()
            else:
                with pytest.raises(beta.UsageExceeded):
                    await beta.reserve_usage(
                        session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=moment,
                    )
                await session.rollback()


async def test_f_g_hourly_period_boundary(db_clean, one_unit_per_hour):
    account_id = await _account()
    before_the_hour = NOON + timedelta(minutes=59, seconds=50)
    after_the_hour = NOON + timedelta(hours=1, seconds=5)
    async with _factory()() as session:
        held = await beta.reserve_usage(
            session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=before_the_hour,
        )
        await session.commit()
    # Still live, but it belongs to the 12:00 hour; 13:00 has its own unit.
    async with _factory()() as session:
        await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=after_the_hour)
        await session.commit()
    # A success that settles after the hour turned counts where it began.
    async with _factory()() as session:
        await beta.settle_usage(session, held, idempotency_key="late-settle")
        await session.commit()
    [event] = await _usage(account_id)
    assert event.period_key == beta._hour_key(before_the_hour) == "2026-09-27 12"


async def test_f_g5_monthly_period_boundary(db_clean, one_scan_per_month):
    account_id = await _account()
    last_moment = datetime(2026, 9, 30, 23, 59, 30, tzinfo=UTC)
    first_moment = datetime(2026, 10, 1, 0, 0, 10, tzinfo=UTC)
    async with _factory()() as session:
        september = await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_SCAN, now=last_moment)
        await session.commit()
    async with _factory()() as session:
        with pytest.raises(beta.UsageExceeded):
            await beta.reserve_usage(
                session, account_id=account_id, feature=beta.FEATURE_SCAN, now=last_moment + timedelta(seconds=5),
            )
        await session.rollback()
    async with _factory()() as session:
        october = await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_SCAN, now=first_moment)
        await beta.settle_usage(session, september, idempotency_key="sept")
        await beta.settle_usage(session, october, idempotency_key="oct")
        await session.commit()
    assert sorted(row.period_key for row in await _usage(account_id, beta.FEATURE_SCAN)) == ["2026-09", "2026-10"]
    assert await _reservations(account_id) == []


async def test_f_g3_settling_is_idempotent_by_key_and_always_lets_go(db_clean, monkeypatch):
    monkeypatch.setitem(beta._HOUR_FEATURES, beta.FEATURE_AI_REQUEST, 5)
    account_id = await _account()
    for _ in range(2):
        async with _factory()() as session:
            taken = await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=NOON)
            await beta.settle_usage(session, taken, idempotency_key="same-run")
            await session.commit()
    assert len(await _usage(account_id)) == 1
    assert await _reservations(account_id) == []


# ---------------------------------------------------------------------------
# The AI gateway: one provider call for the last unit, and honest settlement
# ---------------------------------------------------------------------------
async def test_f_g1_n_simultaneous_gateway_calls_for_the_last_unit_reach_the_provider_once(
    db_clean, one_unit_per_hour, gated,
):
    account_id = await _account()
    tasks = [asyncio.create_task(_run(account_id)) for _ in range(CONTENDERS)]
    try:
        await asyncio.wait_for(gated.entered.wait(), timeout=30)
        # Everybody else is refused without waiting on the provider.
        for _ in range(3000):
            if sum(task.done() for task in tasks) == CONTENDERS - 1:
                break
            await asyncio.sleep(0.01)
        # While the winner is still inside the provider: its unit is a
        # committed row, and no lock or transaction is held across the call.
        [held] = await _reservations(account_id)
        assert held.feature == beta.FEATURE_AI_REQUEST
        assert await _advisory_locks() == 0 and await _idle_in_transaction() == 0
        refused = [task for task in tasks if task.done()]
        assert len(refused) == CONTENDERS - 1
        for task in refused:
            assert isinstance(task.exception(), AIRateLimitedError)
            assert task.exception().extra["allowance_consumed"] is False
    finally:
        gated.gate.set()
    [winner] = [task for task in tasks if task not in refused]
    result = await winner

    assert gated.calls == 1
    [event] = await _usage(account_id)
    assert event.idempotency_key == str(result.run_id)
    assert await _reservations(account_id) == []


async def test_f_g2_a_known_provider_failure_releases_the_unit_for_a_retry(db_clean, one_unit_per_hour, gated):
    account_id = await _account()
    gated.gate.set()
    gated.raises = gemini.ProviderCallFailed("upstream exploded")
    with pytest.raises(AnalysisUnavailableError):
        await _run(account_id)
    assert await _reservations(account_id) == [] and await _usage(account_id) == []

    gated.raises = None
    result = await _run(account_id)
    assert result.data.observations == ["ok"]
    assert len(await _usage(account_id)) == 1 and await _reservations(account_id) == []


@pytest.mark.parametrize("reply", ["", "not json", '{"unexpected": true}'])
async def test_f_g2_every_known_failure_after_the_provider_releases_the_unit(
    db_clean, one_unit_per_hour, gated, reply,
):
    account_id = await _account()
    gated.gate.set()
    gated.text = reply
    with pytest.raises(AnalysisUnavailableError):
        await _run(account_id)
    assert await _reservations(account_id) == [] and await _usage(account_id) == []


class _ProcessDied(BaseException):
    """The request's process ending mid-call. Nothing in the gateway handles it."""


async def test_f_g4_a_request_that_dies_mid_call_holds_the_unit_until_expiry(
    db_clean, one_unit_per_hour, gated, monkeypatch,
):
    account_id = await _account()
    gated.gate.set()
    gated.raises = _ProcessDied()
    monkeypatch.setattr(beta, "_now", lambda: NOON)
    with pytest.raises(_ProcessDied):
        await _run(account_id)
    assert len(await _reservations(account_id)) == 1, "an unknown outcome is never released early"

    gated.raises = None
    with pytest.raises(AIRateLimitedError):
        await _run(account_id)
    assert gated.calls == 1
    monkeypatch.setattr(beta, "_now", lambda: NOON + beta.RESERVATION_TTL + timedelta(seconds=1))
    await _run(account_id)
    assert gated.calls == 2 and len(await _usage(account_id)) == 1


async def test_f_g_a_budget_that_cannot_be_established_fails_closed_before_the_provider(
    db_clean, gated, monkeypatch,
):
    account_id = await _account()
    gated.gate.set()

    async def unavailable(*args, **kwargs):
        raise OperationalError("SELECT pg_advisory_xact_lock", {}, Exception("connection lost"))

    monkeypatch.setattr(beta, "reserve_usage", unavailable)
    with pytest.raises(OperationalError):
        await _run(account_id)
    assert gated.calls == 0


# ---------------------------------------------------------------------------
# The monthly scan allowance, through the real route
# ---------------------------------------------------------------------------
async def _consented(registered_supabase_user) -> tuple[str, uuid.UUID]:
    token, account_id = await registered_supabase_user()
    async with _factory()() as session:
        await consent_service.record(
            session, account_id=account_id, consent_type=CONSENT_PHOTO_ANALYSIS, granted=True, source="pytest",
        )
        await session.commit()
    return token, account_id


def _scan_body() -> dict:
    return {"image_base64": base64.b64encode(png_bytes()).decode("ascii"), "scan_type": "face"}


async def test_f_g5_n_simultaneous_scans_for_the_last_monthly_unit_reach_the_provider_once(
    app_client, db_clean, registered_supabase_user, one_scan_per_month, gated,
):
    gated.text = '{"observations": ["Even tone"], "colour_palette": [], "recommended_next_steps": [], "confidence": 0.8}'
    token, account_id = await _consented(registered_supabase_user)
    tasks = [
        asyncio.create_task(app_client.post("/api/v2/scan/analyse", headers=auth(token), json=_scan_body()))
        for _ in range(CONTENDERS)
    ]
    try:
        await asyncio.wait_for(gated.entered.wait(), timeout=30)
        for _ in range(3000):
            if sum(task.done() for task in tasks) == CONTENDERS - 1:
                break
            await asyncio.sleep(0.01)
        assert len(await _reservations(account_id)) == 1
        assert await _advisory_locks() == 0 and await _idle_in_transaction() == 0
    finally:
        gated.gate.set()
    responses = [await task for task in tasks]
    codes = sorted(response.status_code for response in responses)
    assert codes == [201] + [429] * (CONTENDERS - 1), [r.text for r in responses]
    assert all(r.json()["detail"]["code"] == "beta_limit_reached" for r in responses if r.status_code == 429)
    assert gated.calls == 1
    assert len(await _usage(account_id, beta.FEATURE_SCAN)) == 1
    assert await _reservations(account_id) == []


async def test_f_g2_a_failed_scan_gives_its_unit_back(
    app_client, db_clean, registered_supabase_user, one_scan_per_month, gated,
):
    token, account_id = await _consented(registered_supabase_user)
    gated.gate.set()
    gated.raises = gemini.ProviderCallFailed("upstream exploded")
    failed = await app_client.post("/api/v2/scan/analyse", headers=auth(token), json=_scan_body())
    assert failed.status_code == 502, failed.text
    assert await _reservations(account_id) == [] and await _usage(account_id, beta.FEATURE_SCAN) == []

    gated.raises = None
    gated.text = '{"observations": ["Even tone"], "confidence": 0.8}'
    worked = await app_client.post("/api/v2/scan/analyse", headers=auth(token), json=_scan_body())
    assert worked.status_code == 201, worked.text
    assert len(await _usage(account_id, beta.FEATURE_SCAN)) == 1 and await _reservations(account_id) == []


# ---------------------------------------------------------------------------
# What a reservation is, as data
# ---------------------------------------------------------------------------
async def test_f_g_a_reservation_is_operational_state_that_leaves_with_its_account(db_clean):
    assert REGISTRY["beta_usage_reservations"] == Classification.OPERATIONAL
    columns = {column.name for column in BetaUsageReservation.__table__.columns}
    # Cost control only: no prompt, output, provider data or customer text.
    # ``idempotency_key`` is the caller's logical-operation identifier.
    assert columns == {
        "id", "account_id", "feature", "period_key", "quantity", "idempotency_key", "created_at", "expires_at",
    }

    account_id = await _account()
    async with _factory()() as session:
        await beta.reserve_usage(session, account_id=account_id, feature=beta.FEATURE_AI_REQUEST, now=NOON)
        await session.commit()
    async with _factory()() as session:
        await session.execute(delete(Account).where(Account.id == account_id))
        await session.commit()
    async with _factory()() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(BetaUsageReservation).where(
                BetaUsageReservation.account_id == account_id,
            )
        )
    assert remaining == 0
