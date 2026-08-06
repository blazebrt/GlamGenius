"""Invite-bypass regression.

A Supabase user with a fully valid access token must be refused
by every protected V2 route until they successfully register with an
invite. The former ``get_or_create_account`` behaviour was an invite
bypass: any valid Supabase user auto-provisioned themselves an
``accounts`` row on the first product-domain request. That path is gone;
this suite proves it.
"""
from __future__ import annotations

import pytest

from tests.conftest import auth

PROTECTED_GETS = [
    "/api/v2/me",
    "/api/v2/consent",
    "/api/v2/profile",
    "/api/v2/inventory/items",
    "/api/v2/scan/history",
    "/api/v2/today",
    "/api/v2/planner/week",
    "/api/v2/privacy/export",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
@pytest.mark.asyncio
async def test_authenticated_but_unregistered_user_is_refused(
    app_client, db_clean, fake_supabase_user, path
):
    """Valid Supabase JWT, no ``accounts`` row → 403 REGISTRATION_REQUIRED.

    ``fake_supabase_user`` (not ``registered_supabase_user``) mints a token
    without creating the ``accounts`` row.
    """
    token, _ = fake_supabase_user()
    resp = await app_client.get(path, headers=auth(token))
    assert resp.status_code == 403, (path, resp.status_code, resp.text)
    detail = resp.json().get("detail", {})
    assert detail.get("code") == "REGISTRATION_REQUIRED", (path, detail)


@pytest.mark.asyncio
async def test_registered_user_reaches_me(
    app_client, db_clean, registered_supabase_user
):
    """Sanity check: once registered, ``/api/v2/me`` works normally."""
    token, uid = await registered_supabase_user()
    resp = await app_client.get("/api/v2/me", headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"]["id"] == str(uid)


@pytest.mark.asyncio
async def test_register_then_repeat_is_idempotent(
    app_client, db_clean, fake_supabase_user
):
    """A repeated finalisation call for the same Supabase user is idempotent.

    The first call redeems the invite and creates the account; the second
    call is a no-op and still returns 200. It does **not** redeem a second
    invite and does **not** duplicate the ``accounts`` row.
    """
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, label="regression", max_uses=1)
        await session.commit()
        code = invite.code

    email = "idem-user@example.com"
    # Step 1: reserve.
    resv = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": email},
    )
    assert resv.status_code == 200, resv.text
    challenge = resv.json()["challenge"]

    # Step 2: register with the challenge.
    token, uid = fake_supabase_user(email=email)
    r1 = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["invite_redeemed"] is True

    # Second call: account already exists, no challenge required.
    r2 = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["account"]["id"] == str(uid)
    assert r2.json()["invite_redeemed"] is False
