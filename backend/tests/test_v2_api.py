"""End-to-end V2 tests through the ASGI app.

Exercises Supabase-JWT-authenticated routes: /me, /quiz/*, /scan/*, /access/*,
plus cross-account ownership assertions.
"""
from __future__ import annotations

import base64

import pytest

from tests.conftest import auth, png_bytes


@pytest.mark.asyncio
async def test_me_requires_bearer(app_client, db_clean):
    resp = await app_client.get("/api/v2/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_supabase_uuid(app_client, db_clean, registered_supabase_user):
    token, uid = await registered_supabase_user()
    resp = await app_client.get("/api/v2/me", headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account"]["id"] == str(uid)
    assert body["account"]["is_admin"] is False


@pytest.mark.asyncio
async def test_access_register_requires_invite(app_client, db_clean, fake_supabase_user):
    """Package A/B: registration requires a reservation challenge from
    ``/access/reserve``. Calling ``/access/register`` without one is a
    400 with ``registration_challenge_required``."""
    token, _ = fake_supabase_user()
    resp = await app_client.post("/api/v2/access/register", headers=auth(token), json={})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "registration_challenge_required"


@pytest.mark.asyncio
async def test_access_register_with_valid_invite(app_client, db_clean, fake_supabase_user):
    """Full two-step: reserve then register."""
    from app.domains.beta_access import service as beta
    from app.shared.database.sql import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        invite = await beta.create_invite(session, label="pytest", max_uses=1)
        await session.commit()
        code = invite.code

    # Step 1: reserve.
    email = "critical-journey@example.com"
    reserve_resp = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": email},
    )
    assert reserve_resp.status_code == 200, reserve_resp.text
    challenge = reserve_resp.json()["challenge"]

    # Step 2: register with the same email in the Supabase token.
    token, uid = fake_supabase_user(email=email)
    resp = await app_client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": challenge},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account"]["id"] == str(uid)
    assert body["invite_redeemed"] is True


@pytest.mark.asyncio
async def test_admin_only_endpoints_hide_from_regular_users(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.get("/api/v2/access/admin/invites", headers=auth(token))
    # Admin surface returns 404 (not 403) to non-admins so a switched-off
    # feature does not leak its existence.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_create_and_list_invites(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user(admin=True)
    r = await app_client.post(
        "/api/v2/access/admin/invites",
        headers=auth(token),
        json={"label": "spring", "max_uses": 3},
    )
    assert r.status_code == 200, r.text
    invite = r.json()
    assert invite["max_uses"] == 3
    assert invite["active"] is True

    lst = await app_client.get("/api/v2/access/admin/invites", headers=auth(token))
    assert lst.status_code == 200
    assert any(inv["id"] == invite["id"] for inv in lst.json()["invites"])


@pytest.mark.asyncio
async def test_quiz_submit_and_retrieve(app_client, db_clean, registered_supabase_user):
    token, uid = await registered_supabase_user()
    q = await app_client.get("/api/v2/quiz/questions", headers=auth(token))
    assert q.status_code == 200
    assert q.json()["schema_version"].startswith("quiz.")

    answers = {
        "vibe": "polished",
        "occasion_focus": "work",
        "colour_energy": "warm",
        "silhouette": "structured",
        "care_time": "under_20",
    }
    s = await app_client.post(
        "/api/v2/quiz/submit", headers=auth(token), json={"answers": answers}
    )
    assert s.status_code == 201, s.text
    submission_id = s.json()["id"]

    latest = await app_client.get("/api/v2/quiz/latest", headers=auth(token))
    assert latest.status_code == 200
    assert latest.json()["submission"]["id"] == submission_id
    assert latest.json()["submission"]["derived_style_vibe"] == "polished"


@pytest.mark.asyncio
async def test_quiz_rejects_missing_answers(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    r = await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token),
        json={"answers": {"vibe": "natural"}},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "quiz_answers_invalid"


@pytest.mark.asyncio
async def test_quiz_history_is_scoped_to_caller(
    app_client, db_clean, registered_supabase_user
):
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    answers = {
        "vibe": "polished",
        "occasion_focus": "work",
        "colour_energy": "warm",
        "silhouette": "structured",
        "care_time": "under_20",
    }
    await app_client.post(
        "/api/v2/quiz/submit", headers=auth(token_a), json={"answers": answers}
    )
    r = await app_client.get("/api/v2/quiz/history", headers=auth(token_b))
    assert r.status_code == 200
    assert r.json()["submissions"] == []


@pytest.mark.asyncio
async def test_scan_requires_consent(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    image_b64 = base64.b64encode(png_bytes()).decode("ascii")
    r = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": image_b64, "scan_type": "face"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "consent_missing"


@pytest.mark.asyncio
async def test_scan_records_history_after_consent(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    from app.domains.consent import service as consent_service
    from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
    from app.shared.database.sql import get_sessionmaker

    token, uid = await registered_supabase_user()
    # Grant consent server-side (the frontend would POST /consent).
    from app.domains.identity import service as identity_service

    factory = get_sessionmaker()
    async with factory() as session:
        await identity_service.register_account(session, uid)
        await consent_service.record(
            session,
            account_id=uid,
            consent_type=CONSENT_PHOTO_ANALYSIS,
            granted=True,
            source="pytest",
        )
        await session.commit()

    image_b64 = base64.b64encode(png_bytes()).decode("ascii")
    r = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": image_b64, "scan_type": "face"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["scan_type"] == "face"
    # No image bytes should leak into the analysis.
    assert "image_base64" not in body

    history = await app_client.get("/api/v2/scan/history", headers=auth(token))
    assert history.status_code == 200
    assert len(history.json()["scans"]) == 1


@pytest.mark.asyncio
async def test_scan_failure_preserves_allowance(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    from app.domains.ai_gateway.providers import gemini
    from app.domains.consent import service as consent_service
    from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
    from app.domains.identity import service as identity_service
    from app.shared.database.sql import get_sessionmaker

    token, uid = await registered_supabase_user()

    factory = get_sessionmaker()
    async with factory() as session:
        await identity_service.register_account(session, uid)
        await consent_service.record(
            session,
            account_id=uid,
            consent_type=CONSENT_PHOTO_ANALYSIS,
            granted=True,
            source="pytest",
        )
        await session.commit()

    fake_provider.raises = gemini.ProviderCallFailed("boom")
    image_b64 = base64.b64encode(png_bytes()).decode("ascii")
    r = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": image_b64, "scan_type": "face"},
    )
    assert r.status_code == 502
    # Allowance not consumed:
    usage = await app_client.get("/api/v2/access/usage", headers=auth(token))
    scan_row = next(
        f for f in usage.json()["features"] if f["feature"] == "scan.analyse"
    )
    assert scan_row["used"] == 0


@pytest.mark.asyncio
async def test_cross_account_scan_read_is_404(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    from app.domains.consent import service as consent_service
    from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
    from app.domains.identity import service as identity_service
    from app.shared.database.sql import get_sessionmaker

    token_a, uid_a = await registered_supabase_user()
    token_b, uid_b = await registered_supabase_user()

    factory = get_sessionmaker()
    async with factory() as session:
        for uid in (uid_a, uid_b):
            await identity_service.register_account(session, uid)
            await consent_service.record(
                session,
                account_id=uid,
                consent_type=CONSENT_PHOTO_ANALYSIS,
                granted=True,
                source="pytest",
            )
        await session.commit()

    image_b64 = base64.b64encode(png_bytes()).decode("ascii")
    r = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token_a),
        json={"image_base64": image_b64, "scan_type": "face"},
    )
    assert r.status_code == 201, r.text
    scan_id = r.json()["id"]

    # Account B tries to read A's scan
    resp = await app_client.get(
        f"/api/v2/scan/history/{scan_id}", headers=auth(token_b)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_config_returns_no_billing_block(app_client, db_clean):
    resp = await app_client.get("/api/v2/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "billing" not in body
    assert "subscriptions_available" not in str(body)
    assert body["supabase"]["configured"] is True or body["supabase"]["url"]

async def test_legacy_account_route_returns_404(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    resp = await app_client.delete('/api/v2/account', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 404, resp.text

