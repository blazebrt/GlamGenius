"""Every path to the provider is metered, not just the one that had a cap.

``scan.analyse`` carried a monthly beta limit from the start. The twelve other
call sites into :func:`app.domains.ai_gateway.gateway.run_structured` did not:
inventory extraction, batch shelf capture, two label transcriptions, purchase
and fragrance extraction, the profile baseline, and five written explanations
all reached Gemini with no ceiling of any kind. One signed-in account could
spend the whole provider budget in a loop.

``beta_access`` had already declared ``FEATURE_AI_REQUEST`` with an hourly
limit for exactly this, and nothing ever called it. These tests hold the gate
at the gateway — the one controlled path to the provider — so that a route
added tomorrow is covered without anyone remembering to add a gate to it.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.models import AIRun
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import BetaUsageEvent
from app.domains.identity import service as identity
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import AIRateLimitedError, AnalysisUnavailableError
from pydantic import BaseModel
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


class _Shape(BaseModel):
    observations: list[str]


async def _call(account_id_str: str | None, feature: str = "inventory_extract"):
    return await gateway.run_structured(
        feature=feature,
        prompt="p",
        system="s",
        schema=_Shape,
        prompt_version="1",
        schema_version="1",
        account_id_str=account_id_str,
    )


async def _new_account() -> uuid.UUID:
    account_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await identity.register_account(session, account_id)
        await session.commit()
    return account_id


async def _fill_hourly_budget(account_id: uuid.UUID, count: int) -> None:
    async with get_sessionmaker()() as session:
        for index in range(count):
            await beta.record_usage(
                session,
                account_id=account_id,
                feature=beta.FEATURE_AI_REQUEST,
                idempotency_key=f"seed-{index}",
            )
        await session.commit()


@pytest.fixture
def provider_result(fake_provider):
    fake_provider.text = '{"observations": ["ok"]}'
    return fake_provider


async def test_a_successful_call_counts_against_the_hourly_budget(
    db_clean, provider_result,
):
    account_id = await _new_account()

    await _call(str(account_id))

    async with get_sessionmaker()() as session:
        used = (
            await session.execute(
                select(func.coalesce(func.sum(BetaUsageEvent.quantity), 0)).where(
                    BetaUsageEvent.account_id == account_id,
                    BetaUsageEvent.feature == beta.FEATURE_AI_REQUEST,
                )
            )
        ).scalar_one()
    assert used == 1


async def test_the_provider_is_not_called_once_the_budget_is_spent(
    db_clean, provider_result,
):
    """The whole point: refused *before* anything is paid for."""
    from app.config import BETA_AI_REQUESTS_PER_HOUR

    account_id = await _new_account()
    await _fill_hourly_budget(account_id, BETA_AI_REQUESTS_PER_HOUR)

    calls_before = provider_result.calls
    with pytest.raises(AIRateLimitedError) as raised:
        await _call(str(account_id))

    assert provider_result.calls == calls_before
    assert raised.value.status_code == 429
    assert raised.value.extra["allowance_consumed"] is False
    assert raised.value.extra["period"] == "hour"


async def test_a_refused_call_writes_no_run_and_consumes_nothing(
    db_clean, provider_result,
):
    """A request that never reached the provider costs nothing, in any ledger."""
    from app.config import BETA_AI_REQUESTS_PER_HOUR

    account_id = await _new_account()
    await _fill_hourly_budget(account_id, BETA_AI_REQUESTS_PER_HOUR)

    with pytest.raises(AIRateLimitedError):
        await _call(str(account_id))

    async with get_sessionmaker()() as session:
        runs = (
            await session.execute(select(AIRun).where(AIRun.account_id == account_id))
        ).scalars().all()
        used = (
            await session.execute(
                select(func.coalesce(func.sum(BetaUsageEvent.quantity), 0)).where(
                    BetaUsageEvent.account_id == account_id,
                    BetaUsageEvent.feature == beta.FEATURE_AI_REQUEST,
                )
            )
        ).scalar_one()
    assert runs == []
    assert used == BETA_AI_REQUESTS_PER_HOUR


async def test_the_ceiling_is_per_account(db_clean, provider_result):
    """One account exhausting its hour must not shut anybody else out."""
    from app.config import BETA_AI_REQUESTS_PER_HOUR

    spent = await _new_account()
    fresh = await _new_account()
    await _fill_hourly_budget(spent, BETA_AI_REQUESTS_PER_HOUR)

    with pytest.raises(AIRateLimitedError):
        await _call(str(spent))

    result = await _call(str(fresh))
    assert result.data.observations == ["ok"]


async def test_a_failed_run_does_not_consume_the_hourly_budget(
    db_clean, fake_provider,
):
    """The rule the scan route already followed, now true for every feature."""
    from app.domains.ai_gateway.providers import gemini

    account_id = await _new_account()
    fake_provider.raises = gemini.ProviderCallFailed("upstream exploded")

    with pytest.raises(AnalysisUnavailableError):
        await _call(str(account_id))

    async with get_sessionmaker()() as session:
        used = (
            await session.execute(
                select(func.coalesce(func.sum(BetaUsageEvent.quantity), 0)).where(
                    BetaUsageEvent.account_id == account_id,
                    BetaUsageEvent.feature == beta.FEATURE_AI_REQUEST,
                )
            )
        ).scalar_one()
    assert used == 0


async def test_a_signed_out_preview_is_not_refused(db_clean, provider_result):
    """No account means no per-account budget; the route's own limits apply."""
    result = await _call(None)
    assert result.data.observations == ["ok"]


async def test_an_unknown_account_is_not_refused(db_clean, provider_result):
    """A caller with no accounts row has no budget to have exceeded.

    Whether such a caller may be here at all is the route's decision, not the
    gateway's; the cap must not become a second, accidental authorisation check
    that fails open or closed by surprise.
    """
    result = await _call(str(uuid.uuid4()))
    assert result.data.observations == ["ok"]


async def test_usage_is_keyed_by_run_so_it_cannot_double_count(
    db_clean, provider_result,
):
    account_id = await _new_account()
    await _call(str(account_id))

    async with get_sessionmaker()() as session:
        run = (
            await session.execute(select(AIRun).where(AIRun.account_id == account_id))
        ).scalars().one()
        # Replaying the same run must not add a second unit.
        await beta.record_usage(
            session,
            account_id=account_id,
            feature=beta.FEATURE_AI_REQUEST,
            idempotency_key=str(run.id),
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        used = (
            await session.execute(
                select(func.coalesce(func.sum(BetaUsageEvent.quantity), 0)).where(
                    BetaUsageEvent.account_id == account_id,
                    BetaUsageEvent.feature == beta.FEATURE_AI_REQUEST,
                )
            )
        ).scalar_one()
    assert used == 1


async def test_every_feature_that_reaches_the_provider_is_metered(
    db_clean, provider_result,
):
    """Not just the one that used to have a cap.

    These are the feature names the previously ungated call sites pass. Each
    must be counted, because the gate is at the gateway rather than per route.
    """
    features = [
        "inventory_extract",
        "inventory_batch",
        "purchase_extract",
        "fragrance_extract",
        "product_label_transcribe",
        "care_label_transcribe",
        "profile_baseline",
        "routine_explanation",
        "look_explanation",
    ]
    account_id = await _new_account()

    for feature in features:
        await _call(str(account_id), feature=feature)

    async with get_sessionmaker()() as session:
        used = (
            await session.execute(
                select(func.coalesce(func.sum(BetaUsageEvent.quantity), 0)).where(
                    BetaUsageEvent.account_id == account_id,
                    BetaUsageEvent.feature == beta.FEATURE_AI_REQUEST,
                )
            )
        ).scalar_one()
    assert used == len(features)


async def test_the_refusal_reaches_the_app_as_a_429_it_understands(
    db_clean, provider_result,
):
    """The wire contract, pinned on this side.

    ``ErrorCode`` is documented as part of the public contract because the app
    switches on these strings: ``classifyFailure`` in
    ``frontend/src/services/failure.ts`` reads ``AI_RATE_LIMITED`` to decide
    that nothing was spent and the call is worth retrying. Renaming it here
    without renaming it there would quietly change what the person is told.
    """
    from app.config import BETA_AI_REQUESTS_PER_HOUR
    from app.shared.errors.codes import ErrorCode

    account_id = await _new_account()
    await _fill_hourly_budget(account_id, BETA_AI_REQUESTS_PER_HOUR)

    with pytest.raises(AIRateLimitedError) as raised:
        await _call(str(account_id))

    detail = raised.value.to_detail()
    assert detail["code"] == "AI_RATE_LIMITED"
    assert ErrorCode.AI_RATE_LIMITED.value == "AI_RATE_LIMITED"
    assert detail["retryable"] is True
    assert detail["allowance_consumed"] is False
    assert detail["message"]
