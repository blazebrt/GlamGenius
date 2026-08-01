"""Consent, export, account isolation and account deletion."""
from __future__ import annotations

from tests.conftest import auth, png_bytes


async def _upload(client, token):
    return await client.post(
        "/api/v2/media/upload",
        files={"file": ("photo.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"},
        headers=auth(token),
    )


# --- Consent ----------------------------------------------------------------


async def test_consent_starts_ungranted_and_can_be_given(app_client, make_user):
    _, token = await make_user()

    before = await app_client.get("/api/v2/consent", headers=auth(token))
    assert before.status_code == 200
    assert before.json()["photo_analysis"]["granted"] is False

    granted = await app_client.post(
        "/api/v2/consent",
        json={"consent_type": "photo_analysis", "granted": True},
        headers=auth(token),
    )
    assert granted.status_code == 200
    assert granted.json()["photo_analysis"]["granted"] is True
    assert granted.json()["photo_analysis"]["recorded_at"]


async def test_consent_can_be_withdrawn(app_client, make_user):
    _, token = await make_user()

    await app_client.post(
        "/api/v2/consent",
        json={"consent_type": "photo_analysis", "granted": True},
        headers=auth(token),
    )
    revoked = await app_client.post(
        "/api/v2/consent",
        json={"consent_type": "photo_analysis", "granted": False},
        headers=auth(token),
    )

    assert revoked.json()["photo_analysis"]["granted"] is False


async def test_unknown_consent_type_is_rejected(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/consent",
        json={"consent_type": "sell_my_data", "granted": True},
        headers=auth(token),
    )
    assert response.status_code == 422


async def test_scan_analysis_requires_consent_when_enforcement_is_on(
    app_client, make_user, fake_provider, monkeypatch
):
    """The real analysis route, not media storage, owns the consent boundary."""
    import base64
    import json

    import app.domains.consent.service as consent_service
    from tests.conftest import valid_analysis_payload

    monkeypatch.setattr(consent_service, "REQUIRE_ANALYSIS_CONSENT", True)
    _, token = await make_user()
    image = base64.b64encode(png_bytes()).decode()
    fake_provider.text = json.dumps(valid_analysis_payload())

    refused = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": image, "scan_type": "face"},
        headers=auth(token),
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "CONSENT_REQUIRED"

    await app_client.post(
        "/api/v2/consent",
        json={"consent_type": "photo_analysis", "granted": True},
        headers=auth(token),
    )

    allowed = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": image, "scan_type": "face"},
        headers=auth(token),
    )
    assert allowed.status_code == 200


async def test_signed_out_preview_requires_explicit_consent(app_client, fake_provider):
    import base64
    import json

    from tests.conftest import create_invite, valid_analysis_payload

    invite = await create_invite()
    image = base64.b64encode(png_bytes()).decode()
    fake_provider.text = json.dumps(valid_analysis_payload())
    payload = {
        "image_base64": image,
        "scan_type": "face",
        "invite_code": invite,
    }

    refused = await app_client.post("/api/scan/preview", json=payload)
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "CONSENT_REQUIRED"

    allowed = await app_client.post(
        "/api/scan/preview",
        json={**payload, "photo_analysis_consent": True},
    )
    assert allowed.status_code == 200


# --- Account isolation ------------------------------------------------------


async def test_each_user_gets_their_own_account_link(app_client, make_user):
    user_a, token_a = await make_user()
    user_b, token_b = await make_user()

    me_a = (await app_client.get("/api/v2/me", headers=auth(token_a))).json()
    me_b = (await app_client.get("/api/v2/me", headers=auth(token_b))).json()

    assert me_a["account"]["id"] != me_b["account"]["id"]
    assert me_a["profile"]["email"] == user_a["email"]
    assert me_b["profile"]["email"] == user_b["email"]


async def test_export_contains_only_your_own_data(app_client, make_user, media_root):
    _, token_a = await make_user()
    _, token_b = await make_user()

    a_asset = (await _upload(app_client, token_a)).json()["id"]
    b_asset = (await _upload(app_client, token_b)).json()["id"]

    export = (
        await app_client.get("/api/v2/privacy/export", headers=auth(token_a))
    ).json()

    media_ids = {m["id"] for m in export["media"]}
    assert a_asset in media_ids
    assert b_asset not in media_ids


async def test_jobs_route_will_not_show_another_users_run(
    app_client, make_user, fake_provider
):
    import base64
    import json

    from tests.conftest import valid_analysis_payload

    fake_provider.text = json.dumps(valid_analysis_payload())
    image = base64.b64encode(png_bytes()).decode()

    _, token_a = await make_user()
    _, token_b = await make_user()

    result = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": image, "scan_type": "face"},
        headers=auth(token_a),
    )
    run_id = result.json()["analysis"]["meta"]["provenance"]["ai_run_id"]

    mine = await app_client.get(f"/api/v2/jobs/{run_id}", headers=auth(token_a))
    theirs = await app_client.get(f"/api/v2/jobs/{run_id}", headers=auth(token_b))

    assert mine.status_code == 200
    assert mine.json()["status"] == "succeeded"
    assert theirs.status_code == 404


# --- Export -----------------------------------------------------------------


async def test_export_covers_both_databases(app_client, make_user, media_root):
    _, token = await make_user()
    await _upload(app_client, token)
    await app_client.post(
        "/api/v2/consent",
        json={"consent_type": "photo_analysis", "granted": True},
        headers=auth(token),
    )

    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()

    for key in (
        "account",
        "profile",
        "scans",
        "style_plans",
        "consents",
        "media",
        "ai_runs",
        "usage_ledger",
        "audit_events",
    ):
        assert key in export, f"export is missing {key}"

    assert export["profile"]["email"]
    # A password hash must never leave the building.
    assert "password_hash" not in export["profile"]
    assert len(export["consents"]) == 1
    assert len(export["media"]) == 1


async def test_export_requires_authentication(app_client):
    assert (await app_client.get("/api/v2/privacy/export")).status_code == 401


# --- Account deletion -------------------------------------------------------


async def test_account_deletion_erases_media_and_marks_the_account(
    app_client, make_user, media_root
):
    _, token = await make_user()
    await _upload(app_client, token)
    assert len(list(media_root.rglob("*.png"))) == 1

    response = await app_client.delete("/api/v2/account", headers=auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deletion_requested"
    assert body["completed_now"]["media_deleted"] == 1
    assert body["completed_now"]["photo_analysis_consent_withdrawn"] is True
    # It says plainly what has not happened yet, rather than implying it has.
    assert "pending" in body

    assert list(media_root.rglob("*.png")) == [], "stored photos must be erased now"

    me = (await app_client.get("/api/v2/me", headers=auth(token))).json()
    assert me["account"]["status"] == "deletion_requested"
    assert me["account"]["deletion_requested_at"]
    assert me["consent"]["photo_analysis"]["granted"] is False


async def test_account_deletion_is_audited(app_client, make_user, media_root):
    from sqlalchemy import select

    from app.domains.audit.models import (
        ACTION_ACCOUNT_DELETION_REQUESTED,
        AuditEvent,
    )
    from app.shared.database import sql

    _, token = await make_user()
    account_id = (await app_client.get("/api/v2/me", headers=auth(token))).json()[
        "account"
    ]["id"]

    await app_client.delete("/api/v2/account", headers=auth(token))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == ACTION_ACCOUNT_DELETION_REQUESTED
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(r.subject_id == account_id for r in rows)


async def test_account_deletion_requires_authentication(app_client):
    assert (await app_client.delete("/api/v2/account")).status_code == 401


async def test_deletion_does_not_touch_the_other_account(
    app_client, make_user, media_root
):
    _, token_a = await make_user()
    _, token_b = await make_user()
    await _upload(app_client, token_a)
    b_asset = (await _upload(app_client, token_b)).json()["id"]

    await app_client.delete("/api/v2/account", headers=auth(token_a))

    survived = await app_client.get(f"/api/v2/media/{b_asset}", headers=auth(token_b))
    assert survived.status_code == 200

    me_b = (await app_client.get("/api/v2/me", headers=auth(token_b))).json()
    assert me_b["account"]["status"] == "active"
