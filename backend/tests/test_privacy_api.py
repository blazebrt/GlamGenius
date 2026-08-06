"""API-level tests for /api/v2/privacy/* routes."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_privacy_export_returns_versioned_snapshot(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.get("/api/v2/privacy/export", headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == "1.0"
    assert "domains" in body
    assert "identity" in body["domains"]
    # No storage-key leak
    assert "storage_key" not in resp.text


async def test_privacy_delete_returns_202_and_creates_job(
    app_client, db_clean, registered_supabase_user, monkeypatch
):
    # Stub the Supabase Auth deletion so the worker completes locally.

    class _FakeAdmin:
        class auth:
            class admin:
                @staticmethod
                def delete_user(uid): pass
    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin",
        lambda: _FakeAdmin,
    )

    token, _ = await registered_supabase_user()
    resp = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "requested"
    assert body["attempts"] == 0

    # Status endpoint reflects the job.
    resp2 = await app_client.get(
        "/api/v2/privacy/account-deletion", headers=auth(token)
    )
    assert resp2.status_code == 200
    assert resp2.json()["state"] in {"requested", "complete"}


async def test_privacy_delete_status_denied_across_accounts(
    app_client, db_clean, registered_supabase_user
):
    """Account B cannot read Account A's deletion state (there is nothing to read)."""
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()

    # A requests deletion.
    await app_client.delete("/api/v2/privacy/account", headers=auth(token_a))
    # B checks — sees nothing.
    resp = await app_client.get(
        "/api/v2/privacy/account-deletion", headers=auth(token_b)
    )
    assert resp.status_code == 404


async def test_privacy_delete_is_idempotent(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    r1 = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    r2 = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert r1.status_code == 202
    assert r2.status_code == 202
    # Same account id in both responses; requesting twice is a no-op.
    assert r1.json()["account_id"] == r2.json()["account_id"]
