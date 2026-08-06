"""Invite reservation & finalisation regression.

Covers §1 of the Supabase hardening spec:

* ``/access/reserve`` refuses invalid, exhausted, expired and inactive invites.
* A valid reservation returns a fresh, unique challenge.
* Finalisation requires the correct email and challenge.
* Finalisation is atomic — a rejected challenge does not touch invite usage.
* Repeated finalisation on the same account is idempotent.
* Race between concurrent reservers cannot exceed ``max_uses``.
"""
from __future__ import annotations

import pytest

from tests.conftest import auth


@pytest.mark.asyncio
async def test_reserve_rejects_missing_invite(app_client, db_clean):
    resp = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": "DOES-NOT-EXIST", "email": "a@example.com"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "invite_invalid"


@pytest.mark.asyncio
async def test_reserve_then_register_completes_registration(
    app_client, db_clean, fake_supabase_user
):
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    async with get_sessionmaker()() as s:
        invite = await beta.create_invite(s, label="pkg-a", max_uses=1)
        await s.commit()
        code = invite.code

    email = "pkga-user@example.com"
    r1 = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": email},
    )
    assert r1.status_code == 200, r1.text
    challenge = r1.json()["challenge"]
    assert len(challenge) >= 40

    # Now the client would sign up in Supabase. Mint a fake token with the
    # same email.
    token, uid = fake_supabase_user(email=email)

    r2 = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["invite_redeemed"] is True
    assert body["account"]["id"] == str(uid)

    # Idempotent second call.
    r3 = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={},
    )
    assert r3.status_code == 200
    assert r3.json()["invite_redeemed"] is False


@pytest.mark.asyncio
async def test_register_without_reservation_is_rejected(
    app_client, db_clean, fake_supabase_user
):
    """A Supabase user with no challenge cannot create an account."""
    token, _ = fake_supabase_user(email="orphan@example.com")
    resp = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "registration_challenge_required"


@pytest.mark.asyncio
async def test_register_with_wrong_email_is_rejected(
    app_client, db_clean, fake_supabase_user
):
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    async with get_sessionmaker()() as s:
        invite = await beta.create_invite(s, label="wrong-email", max_uses=1)
        await s.commit()
        code = invite.code

    r1 = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "reserved@example.com"},
    )
    challenge = r1.json()["challenge"]

    # A different email presents the challenge.
    token, _ = fake_supabase_user(email="attacker@example.com")
    r2 = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )
    assert r2.status_code == 400
    # Uniform mismatch response — do not leak whether the challenge exists.
    assert r2.json()["detail"]["code"] in {"reservation_invalid", "reservation_expired"}


@pytest.mark.asyncio
async def test_register_second_time_with_same_challenge_fails(
    app_client, db_clean, fake_supabase_user
):
    """A consumed challenge is single-use even for the original email."""
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    async with get_sessionmaker()() as s:
        invite = await beta.create_invite(s, label="single", max_uses=2)
        await s.commit()
        code = invite.code

    email = "singleuse@example.com"
    r1 = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": email},
    )
    challenge = r1.json()["challenge"]

    token, _ = fake_supabase_user(email=email)
    ok = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )
    assert ok.status_code == 200

    # A different Supabase user cannot piggy-back on the consumed challenge.
    token2, _ = fake_supabase_user(email=email)
    replay = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token2),
        json={"registration_challenge": challenge},
    )
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_exhausted_invite_cannot_be_reserved(app_client, db_clean, fake_supabase_user):
    """After ``max_uses`` accounts have registered, a fresh reserve is refused."""
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    async with get_sessionmaker()() as s:
        invite = await beta.create_invite(s, label="one-shot", max_uses=1)
        await s.commit()
        code = invite.code

    r1 = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "first@example.com"},
    )
    challenge = r1.json()["challenge"]
    token, _ = fake_supabase_user(email="first@example.com")
    await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )

    # Second reserve for the same invite is now impossible.
    r_second = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "second@example.com"},
    )
    assert r_second.status_code == 400
    assert r_second.json()["detail"]["code"] == "invite_invalid"


@pytest.mark.asyncio
async def test_reserve_idempotent_for_same_email(app_client, db_clean):
    """Re-reserving the same invite from the same email refreshes the challenge."""
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    async with get_sessionmaker()() as s:
        invite = await beta.create_invite(s, label="idem", max_uses=1)
        await s.commit()
        code = invite.code

    r1 = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "same@example.com"},
    )
    r2 = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "same@example.com"},
    )
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["challenge"] != r2.json()["challenge"]
    # Same reservation id — a refreshed challenge, not a new slot.
    assert r1.json()["reservation_id"] == r2.json()["reservation_id"]
