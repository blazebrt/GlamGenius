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


async def test_the_face_image_truncation_rule_still_holds(
    app_client, make_user, fake_provider
):
    """CLAUDE.md: stored image_base64 is 83 characters and must never grow."""
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
    assert len(scan["image_base64"]) == 83
