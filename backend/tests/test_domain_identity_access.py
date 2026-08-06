"""§1.1 — the identity and access cases the existing suites do not cover.

``test_invite_reservation.py`` covers the happy path, the wrong-email refusal
and the single-use rule. This file covers what happens at the edges of *time*
and *concurrency* — expiry, and two devices racing each other — which are
exactly the conditions under which an invite-only beta leaks.

What this protects against
--------------------------
* An invite that has passed its expiry date still admitting someone.
* A reservation working forever, so a challenge captured once is a permanent
  key to the beta.
* Two concurrent reservations against a one-use invite both succeeding, which
  would let two people in through one invite.
* Two concurrent finalisations of the same reservation creating two accounts or
  redeeming the invite twice.
* A registered account consuming a second invite on a repeat call.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import Invite, InviteRedemption
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


async def _invite(**kwargs) -> str:
    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, label="pytest", **kwargs)
        await session.commit()
        return invite.code


async def _reserve(client, code: str, email: str):
    return await client.post(
        "/api/v2/access/reserve", json={"invite_code": code, "email": email}
    )


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

async def test_expired_invite_cannot_be_reserved(app_client, db_clean):
    code = await _invite(max_uses=1, expires_at=utcnow() - timedelta(days=1))

    resp = await _reserve(app_client, code, "late@example.com")

    assert resp.status_code == 400, resp.text
    # Missing, inactive, expired and exhausted all answer identically, so the
    # response cannot be used to probe which invite codes exist.
    assert resp.json()["detail"]["code"] == "invite_invalid"


async def test_invite_valid_until_the_moment_it_expires(app_client, db_clean):
    code = await _invite(max_uses=1, expires_at=utcnow() + timedelta(minutes=5))

    resp = await _reserve(app_client, code, "intime@example.com")

    assert resp.status_code == 200, resp.text
    assert resp.json()["challenge"]


async def test_expired_reservation_cannot_finalise_registration(
    app_client, db_clean, fake_supabase_user
):
    """A challenge is a short-lived permit, not a permanent key."""
    from app.domains.beta_access.models import InviteRegistrationReservation

    code = await _invite(max_uses=1)
    email = "slow@example.com"
    challenge = (await _reserve(app_client, code, email)).json()["challenge"]

    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(InviteRegistrationReservation).where(
                InviteRegistrationReservation.email_normalised == email
            )
        )).scalar_one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()

    token, _ = fake_supabase_user(email=email)
    resp = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "reservation_expired"

    # And the invite is not burned by the failed attempt — it can still be used.
    async with factory() as session:
        invite = (await session.execute(
            select(Invite).where(Invite.code == code)
        )).scalar_one()
        assert invite.uses_count == 0


async def test_expiry_sweep_marks_stale_reservations(db_clean):
    from app.domains.beta_access.models import InviteRegistrationReservation

    code = await _invite(max_uses=2)
    factory = get_sessionmaker()
    async with factory() as session:
        await beta.reserve_invite(session, code=code, email="a@example.com")
        await beta.reserve_invite(session, code=code, email="b@example.com")
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(
            select(InviteRegistrationReservation)
        )).scalars().all()
        rows[0].expires_at = utcnow() - timedelta(minutes=1)
        await session.commit()

    async with factory() as session:
        swept = await beta.expire_stale_reservations(session)
        await session.commit()
    assert swept == 1

    async with factory() as session:
        states = {
            row.email_normalised: row.status
            for row in (await session.execute(
                select(InviteRegistrationReservation)
            )).scalars().all()
        }
    assert states["a@example.com"] == beta.RESERVATION_STATUS_EXPIRED
    assert states["b@example.com"] != beta.RESERVATION_STATUS_EXPIRED


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

async def test_concurrent_reservations_cannot_oversubscribe_one_invite(db_clean):
    """Two people redeem the same code at the same instant. The invite has one
    seat, so exactly one of them may get it."""
    code = await _invite(max_uses=1)
    factory = get_sessionmaker()

    async def _attempt(email: str):
        async with factory() as session:
            try:
                reservation, challenge = await beta.reserve_invite(
                    session, code=code, email=email
                )
                await session.commit()
                return challenge
            except beta.InviteReservationError:
                return None

    results = await asyncio.gather(
        _attempt("first@example.com"),
        _attempt("second@example.com"),
        return_exceptions=True,
    )
    granted = [row for row in results if isinstance(row, str)]

    assert len(granted) == 1, f"one seat, {len(granted)} challenges issued"


async def test_concurrent_finalisation_creates_one_account_and_one_redemption(
    app_client, db_clean, fake_supabase_user
):
    """The same device retrying while the first request is still in flight."""
    code = await _invite(max_uses=1)
    email = "race@example.com"
    challenge = (await _reserve(app_client, code, email)).json()["challenge"]
    token, account_id = fake_supabase_user(email=email)

    responses = await asyncio.gather(
        *[
            app_client.post(
                "/api/v2/access/register",
                headers=auth(token),
                json={"registration_challenge": challenge},
            )
            for _ in range(3)
        ],
        return_exceptions=True,
    )
    accepted = [
        r for r in responses
        if not isinstance(r, BaseException) and r.status_code == 200
    ]
    assert accepted, "at least one finalisation must succeed"
    assert all(
        r.json()["account"]["id"] == str(account_id) for r in accepted
    ), "every successful reply must describe the same account"

    factory = get_sessionmaker()
    async with factory() as session:
        redemptions = (await session.execute(
            select(func.count(InviteRedemption.id)).where(
                InviteRedemption.account_id == account_id
            )
        )).scalar_one()
        invite = (await session.execute(
            select(Invite).where(Invite.code == code)
        )).scalar_one()

    assert redemptions == 1, "the invite must be redeemed exactly once"
    assert invite.uses_count == 1


async def test_registered_account_does_not_consume_a_second_invite(
    app_client, db_clean, fake_supabase_user
):
    first_code = await _invite(max_uses=1)
    second_code = await _invite(max_uses=1)
    email = "returning@example.com"
    challenge = (await _reserve(app_client, first_code, email)).json()["challenge"]
    token, account_id = fake_supabase_user(email=email)
    assert (await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )).status_code == 200

    # A second registration call, this time waving a different invite.
    second_challenge = (
        await _reserve(app_client, second_code, "someone-else@example.com")
    ).json()["challenge"]
    repeat = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": second_challenge},
    )

    assert repeat.status_code == 200
    assert repeat.json()["invite_redeemed"] is False

    factory = get_sessionmaker()
    async with factory() as session:
        second = (await session.execute(
            select(Invite).where(Invite.code == second_code)
        )).scalar_one()
        redemptions = (await session.execute(
            select(func.count(InviteRedemption.id)).where(
                InviteRedemption.account_id == account_id
            )
        )).scalar_one()

    assert second.uses_count == 0, "an already-registered account must not spend an invite"
    assert redemptions == 1


async def test_challenge_is_bound_to_one_account(
    app_client, db_clean, fake_supabase_user
):
    """A challenge captured from someone else's sign-up is worthless: it is
    bound to the email that reserved it."""
    code = await _invite(max_uses=1)
    challenge = (
        await _reserve(app_client, code, "owner@example.com")
    ).json()["challenge"]

    thief_token, _ = fake_supabase_user(email="thief@example.com")
    resp = await app_client.post(
        "/api/v2/access/register",
        headers=auth(thief_token),
        json={"registration_challenge": challenge},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] in {
        "reservation_email_mismatch", "reservation_invalid", "registration_challenge_invalid",
    }

    factory = get_sessionmaker()
    async with factory() as session:
        invite = (await session.execute(
            select(Invite).where(Invite.code == code)
        )).scalar_one()
    assert invite.uses_count == 0


async def test_unknown_challenge_is_refused(app_client, db_clean, fake_supabase_user):
    token, _ = fake_supabase_user(email="nobody@example.com")
    resp = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": uuid.uuid4().hex},
    )
    assert resp.status_code == 400
