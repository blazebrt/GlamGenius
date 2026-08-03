"""V1 must still work exactly as before.

The acceptance criteria require existing functionality to keep starting and
serving. These are the guardrails against a V2 change quietly breaking it.
"""
from __future__ import annotations

import pytest

from tests.conftest import auth, create_invite

# Every route that must reject an anonymous caller. Mirrors the table in
# backend_test.py so the two suites cannot drift apart.
PROTECTED_ROUTES = [
    ("GET", "/api/users/me", None),
    ("PUT", "/api/users/me", {"city": "Mumbai"}),
    ("POST", "/api/scan/analyze", {"image_base64": "x", "scan_type": "face"}),
    ("GET", "/api/scan/history", None),
    ("GET", "/api/scan/trends", None),
    ("POST", "/api/quiz/submit", {"answers": []}),
    ("POST", "/api/plans/style", {"occasion": "everyday"}),
    ("POST", "/api/recommendations/advice", {"occasion": "everyday"}),
    ("GET", "/api/recommendations/history", None),
    ("POST", "/api/subscription/create-order", {"plan": "plus_monthly"}),
    ("GET", "/api/subscription/status", None),
    # V2 routes are held to the same standard.
    ("GET", "/api/v2/me", None),
    ("GET", "/api/v2/consent", None),
    ("GET", "/api/v2/privacy/export", None),
    ("DELETE", "/api/v2/account", None),
]


@pytest.mark.parametrize("method,path,body", PROTECTED_ROUTES)
async def test_protected_routes_reject_anonymous_callers(app_client, method, path, body):
    response = await app_client.request(method, path, json=body)
    assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


async def test_health_still_answers(app_client):
    body = (await app_client.get("/api/health")).json()
    assert body["status"] == "healthy"
    assert "gemini_ready" in body
    # A boolean, never the key itself.
    assert isinstance(body["gemini_ready"], bool)


async def test_root_still_answers(app_client):
    assert (await app_client.get("/api/")).status_code == 200


async def test_registration_and_login_still_work(app_client):
    import uuid

    invite = await create_invite()
    email = f"regression.{uuid.uuid4().hex[:8]}@example.com"

    registered = await app_client.post(
        "/api/auth/register",
        json={
            "name": "Regression User",
            "email": email,
            "password": "correct-horse-battery",
            "invite_code": invite,
        },
    )
    assert registered.status_code == 200
    assert registered.json()["token"]
    assert "password_hash" not in registered.json()["user"]

    logged_in = await app_client.post(
        "/api/auth/login",
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["token"]


async def test_wrong_password_is_rejected(app_client, make_user):
    user, _ = await make_user()
    response = await app_client.post(
        "/api/auth/login", json={"email": user["email"], "password": "wrong"}
    )
    assert response.status_code == 401


async def test_signup_still_requires_an_invite(app_client):
    import uuid

    response = await app_client.post(
        "/api/auth/register",
        json={
            "name": "No Invite",
            "email": f"no.invite.{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-battery",
            "invite_code": "",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVITE_REQUIRED"


async def test_profile_read_and_update_still_work(app_client, make_user):
    _, token = await make_user()

    me = await app_client.get("/api/users/me", headers=auth(token))
    assert me.status_code == 200
    assert "password_hash" not in me.json()

    updated = await app_client.put(
        "/api/users/me", json={"city": "Jaipur"}, headers=auth(token)
    )
    assert updated.status_code == 200
    assert updated.json()["city"] == "Jaipur"


async def test_one_user_cannot_read_another_by_id(app_client, make_user):
    user_a, _ = await make_user()
    _, token_b = await make_user()

    response = await app_client.get(
        f"/api/users/{user_a['id']}", headers=auth(token_b)
    )
    assert response.status_code == 403


async def test_static_catalogue_routes_still_answer(app_client):
    for path in ("/api/services", "/api/salon-ideas", "/api/quiz/questions"):
        response = await app_client.get(path)
        assert response.status_code == 200, path


async def test_scan_history_and_trends_are_empty_but_valid(app_client, make_user):
    _, token = await make_user()

    history = await app_client.get("/api/scan/history", headers=auth(token))
    trends = await app_client.get("/api/scan/trends", headers=auth(token))

    assert history.status_code == 200 and history.json() == []
    assert trends.status_code == 200 and trends.json() == {"points": []}


async def test_new_scans_store_no_image_fragment(
    app_client, make_user, fake_provider
):
    """Fix 12: V1 previously stored the first 80 characters of the base64
    payload as a "receipt" (`image_base64[:80] + "..."`). That receipt was a
    slice of the user's photo bytes and rode along in every future history /
    trends response. New scans must not store any fragment of the image."""
    import base64
    import json

    from database import db
    from tests.conftest import png_bytes, valid_analysis_payload

    fake_provider.text = json.dumps(valid_analysis_payload())
    user, token = await make_user()

    long_image = base64.b64encode(png_bytes() * 40).decode()
    assert len(long_image) > 200

    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": long_image, "scan_type": "face"},
        headers=auth(token),
    )
    assert response.status_code == 200

    scan = await db.scans.find_one({"user_id": user["id"]})
    # The bytes themselves must not appear in the persisted record — no full
    # copy, no prefix, no "..." marker, no derivative slice. `None` is the
    # correct value; older records may still have a truncated string until the
    # cleanup script runs (see scripts/cleanup_v1_scan_image_prefixes.py and
    # docs/stabilisation/HISTORICAL_IMAGE_CLEANUP.md).
    assert scan.get("image_base64") is None, (
        "V1 scan record still stores a fragment of the image; Fix 12 requires it to be None."
    )
    # Belt and braces: even a shortened prefix would fail the following, so a
    # future regression that stores a truncated 'safe-looking' string is caught.
    for surface in (scan.get("image_base64"), scan.get("image_preview"), scan.get("image_thumb")):
        if surface is None:
            continue
        assert long_image[:20] not in surface, "Scan record contains bytes from the user's photo."
