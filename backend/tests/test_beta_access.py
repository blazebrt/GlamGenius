"""Beta access domain: invite CRUD + atomic redemption + usage limiter."""
from __future__ import annotations

import uuid

import pytest

from app.domains.beta_access import service as beta
from app.domains.beta_access.models import Invite, InviteRedemption
from app.domains.identity import service as identity
from app.shared.database.sql import get_sessionmaker


@pytest.mark.asyncio
async def test_create_and_redeem_invite_happy_path(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, label="pytest", max_uses=1)
        await session.commit()
        code = invite.code

        acc_id = uuid.uuid4()
        await identity.get_or_create_account(session, acc_id)
        await session.commit()

        redeemed = await beta.redeem_invite(session, code=code, account_id=acc_id)
        await session.commit()

        assert redeemed.uses_count == 1


@pytest.mark.asyncio
async def test_redemption_over_limit_is_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, max_uses=1)
        await session.commit()
        code = invite.code

        a = uuid.uuid4()
        b = uuid.uuid4()
        await identity.get_or_create_account(session, a)
        await identity.get_or_create_account(session, b)
        await session.commit()

        await beta.redeem_invite(session, code=code, account_id=a)
        await session.commit()

        with pytest.raises(ValueError):
            await beta.redeem_invite(session, code=code, account_id=b)


@pytest.mark.asyncio
async def test_same_account_cannot_double_redeem(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, max_uses=5)
        await session.commit()
        code = invite.code

        acc = uuid.uuid4()
        await identity.get_or_create_account(session, acc)
        await session.commit()

        await beta.redeem_invite(session, code=code, account_id=acc)
        await session.commit()

        with pytest.raises(ValueError):
            await beta.redeem_invite(session, code=code, account_id=acc)


@pytest.mark.asyncio
async def test_unknown_invite_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValueError):
            await beta.redeem_invite(session, code="NOPE", account_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_deactivated_invite_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, max_uses=1)
        await session.commit()
        await beta.deactivate_invite(session, invite.id)
        await session.commit()

        acc = uuid.uuid4()
        await identity.get_or_create_account(session, acc)
        await session.commit()

        with pytest.raises(ValueError):
            await beta.redeem_invite(session, code=invite.code, account_id=acc)


@pytest.mark.asyncio
async def test_usage_limit_blocks_after_reaching_cap(db_clean, monkeypatch):
    # Force a tiny scan limit so the test finishes in three iterations.
    from app.domains.beta_access import service as beta_svc

    monkeypatch.setitem(beta_svc._MONTH_FEATURES, beta_svc.FEATURE_SCAN, 2)

    factory = get_sessionmaker()
    async with factory() as session:
        acc = uuid.uuid4()
        await identity.get_or_create_account(session, acc)
        await session.commit()

        for _ in range(2):
            await beta.check_limit(session, account_id=acc, feature=beta.FEATURE_SCAN)
            await beta.record_usage(session, account_id=acc, feature=beta.FEATURE_SCAN)
            await session.commit()

        with pytest.raises(beta.UsageExceeded):
            await beta.check_limit(session, account_id=acc, feature=beta.FEATURE_SCAN)


@pytest.mark.asyncio
async def test_idempotent_usage_key_not_double_counted(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        acc = uuid.uuid4()
        await identity.get_or_create_account(session, acc)
        await session.commit()

        for _ in range(3):
            await beta.record_usage(
                session,
                account_id=acc,
                feature=beta.FEATURE_SCAN,
                idempotency_key="scan-abc",
            )
            await session.commit()

        summary = await beta.usage_summary(session, account_id=acc)
        scan_row = next(f for f in summary["features"] if f["feature"] == beta.FEATURE_SCAN)
        assert scan_row["used"] == 1
