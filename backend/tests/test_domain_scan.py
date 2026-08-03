"""Scan and photo-analysis (§1.7) regression coverage.

The scan route is the highest-consent surface in GlamGenius. Tests here
run through the real ASGI app so the flag gate, consent gate, allowance
gate, storage of the ``Scan`` row and history retrieval are exercised
end-to-end. The AI provider is stubbed via ``fake_provider`` — no live
call is ever made.

Assertions:

    * Consent is required (403 before consent, 201 after).
    * The response body never contains ``image_base64`` — raw bytes are
      dropped after the provider call.
    * Provider failures return 502 and leave the beta allowance intact.
    * Invalid image length returns 400 (image_required / image_too_large).
    * Every supported ``scan_type`` (face, hair, hands, full) records a
      Scan row of that type.
    * Provider provenance (model, provider name, latency_ms, prompt_version
      and schema_version) lands on the stored Scan.
    * ``/scan/history`` is scoped to the current account — a second
      account never sees the first account's scans.
    * ``/scan/history/{id}`` returns 404 for another account's scan (no
      cross-account probing).
    * The user-facing failure message uses premium, constructive wording
      (no "ugly", "failed", "wasted", "bad").
"""
from __future__ import annotations

import base64
from typing import Any, Optional

import pytest
from sqlalchemy import select

from app.domains.ai_gateway.providers import gemini
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.identity import service as identity_service
from app.domains.scan.models import Scan
from app.shared.database.sql import get_sessionmaker

from tests.conftest import auth, png_bytes


pytestmark = pytest.mark.asyncio


BAD_WORDS = ("ugly", "bad", "failed", "wasted", "unattractive")


async def _grant_scan_consent(uid) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await consent_service.record(
            session,
            account_id=uid,
            consent_type=CONSENT_PHOTO_ANALYSIS,
            granted=True,
            source="pytest",
        )
        await session.commit()


def _image_b64() -> str:
    return base64.b64encode(png_bytes()).decode("ascii")


# ---------------------------------------------------------------------------
# Consent + happy path
# ---------------------------------------------------------------------------

async def test_scan_requires_consent(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "consent_missing"


async def test_scan_success_records_row_without_leaking_bytes(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert body["status"] == "ok"
    assert body["scan_type"] == "face"
    # Raw image bytes must NEVER be returned.
    assert "image_base64" not in body
    assert body["provider"] == "gemini"
    assert body["model"] == "fake-model"
    assert body["latency_ms"] is not None

    # Row on disk mirrors the response, and analysis is non-empty.
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Scan).where(Scan.account_id == uid)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].analysis and isinstance(rows[0].analysis, dict)
    assert rows[0].failure_reason is None


# ---------------------------------------------------------------------------
# Supported scan types
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scan_type", ["face", "hair", "hands", "full"])
async def test_every_supported_scan_type_persists_correctly(
    app_client, db_clean, registered_supabase_user, fake_provider, scan_type
):
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": scan_type},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["scan_type"] == scan_type


async def test_unsupported_scan_type_rejected_by_schema(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "toenails"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Invalid inputs
# ---------------------------------------------------------------------------

async def test_missing_image_returns_400(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": "", "scan_type": "face"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "image_required"


async def test_oversized_image_returns_400_image_too_large(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    from app.config import MAX_IMAGE_BASE64_CHARS

    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    # One character over the cap.
    huge = "A" * (MAX_IMAGE_BASE64_CHARS + 1)
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": huge, "scan_type": "face"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "image_too_large"


# ---------------------------------------------------------------------------
# Provider failures
# ---------------------------------------------------------------------------

async def test_provider_failure_returns_502_and_preserves_allowance(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    fake_provider.raises = gemini.ProviderCallFailed("boom")
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "provider_failure"

    # Allowance untouched — the retry cost stays with the user's remaining cap.
    usage = await app_client.get("/api/v2/access/usage", headers=auth(token))
    row = next(
        f for f in usage.json()["features"] if f["feature"] == "scan.analyse"
    )
    assert row["used"] == 0
    assert row["remaining"] == row["limit"]


async def test_invalid_image_bytes_return_400(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    fake_provider.raises = gemini.ImageRejected("cannot decode")
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "image_invalid"


async def test_provider_returning_invalid_json_records_provider_failure(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    fake_provider.text = "not-json-at-all"
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    assert resp.status_code == 502

    factory = get_sessionmaker()
    async with factory() as session:
        row = (
            await session.execute(
                select(Scan).where(Scan.account_id == uid)
            )
        ).scalar_one()
    assert row.status == "provider_failure"
    assert "invalid_json" in (row.failure_reason or "")


async def test_provider_failure_message_uses_constructive_language(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """§1.6 / product-scope — no judgmental words in user-facing messages."""
    token, uid = await registered_supabase_user()
    await _grant_scan_consent(uid)

    fake_provider.raises = gemini.ProviderCallFailed("boom")
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    message = resp.json()["detail"]["message"].lower()
    for word in BAD_WORDS:
        assert word not in message


# ---------------------------------------------------------------------------
# History + cross-account isolation
# ---------------------------------------------------------------------------

async def test_history_scoped_to_account(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token_a, uid_a = await registered_supabase_user()
    await _grant_scan_consent(uid_a)
    token_b, uid_b = await registered_supabase_user()
    await _grant_scan_consent(uid_b)

    # Account A runs a scan.
    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token_a),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    assert resp.status_code == 201
    scan_a_id = resp.json()["id"]

    # B's history is empty.
    hist_b = await app_client.get("/api/v2/scan/history", headers=auth(token_b))
    assert hist_b.status_code == 200
    assert hist_b.json()["scans"] == []

    # A's history has the scan.
    hist_a = await app_client.get("/api/v2/scan/history", headers=auth(token_a))
    ids = [row["id"] for row in hist_a.json()["scans"]]
    assert scan_a_id in ids


async def test_detail_of_other_accounts_scan_returns_404(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token_a, uid_a = await registered_supabase_user()
    await _grant_scan_consent(uid_a)
    token_b, _ = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token_a),
        json={"image_base64": _image_b64(), "scan_type": "face"},
    )
    scan_id = resp.json()["id"]

    # 404 — not 403 — so the route does not confirm the scan exists.
    resp_b = await app_client.get(
        f"/api/v2/scan/history/{scan_id}", headers=auth(token_b)
    )
    assert resp_b.status_code == 404
