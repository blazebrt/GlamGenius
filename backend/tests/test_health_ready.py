"""Liveness and readiness probe tests."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_health_ok_during_normal_operation(app_client: AsyncClient):
    resp = await app_client.get("/api/v2/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


async def test_ready_ok_during_normal_operation(app_client: AsyncClient, db_clean, monkeypatch):
    # In CI, GEMINI_API_KEY is empty and APP_ENV is 'test', so we mock
    # the AI provider check and production config validation.
    import app.domains.ai.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "is_configured", lambda: True)
    import app.api.v2.config as config_mod
    monkeypatch.setattr(config_mod, "validate_production_configuration", lambda: None)

    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert "postgres" in body["components"]
    assert "seed_version" in body["components"]
    
    # Ensure no secrets leaked
    text = resp.text.lower()
    assert "secret" not in text
    assert "key" not in text
    assert "password" not in text



async def test_health_ok_during_database_outage(app_client: AsyncClient, monkeypatch):
    # Mock ping to fail
    from app.shared.database import sql
    async def mock_ping():
        return False
    monkeypatch.setattr(sql, "ping", mock_ping)
    
    resp = await app_client.get("/api/v2/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


async def test_ready_fails_during_database_outage(app_client: AsyncClient, monkeypatch):
    # Mock ping to fail
    from app.shared.database import sql
    async def mock_ping():
        return False
    monkeypatch.setattr(sql, "ping", mock_ping)
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["postgres"] == "down"


async def test_ready_fails_on_seed_mismatch(app_client: AsyncClient, db_clean, session):
    # Intentionally change the seed version in the database
    from sqlalchemy import text
    await session.execute(text("UPDATE seed_version_records SET seed_version = 'invalid-version'"))
    await session.commit()
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert "mismatch" in resp.json()["components"]["seed_version_status"]


async def test_ready_fails_on_alembic_mismatch(app_client: AsyncClient, db_clean, session):
    # Intentionally break alembic head
    from sqlalchemy import text
    await session.execute(text("DELETE FROM alembic_version"))
    await session.commit()
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["alembic_status"] == "missing"


async def test_ready_fails_on_stale_worker_heartbeat(app_client: AsyncClient, db_clean, session, monkeypatch, tmp_path):
    # Create a pending account deletion job
    import uuid

    from sqlalchemy import text
    # We must seed a valid account_id.
    account_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO account_deletion_jobs (account_id, state, created_at, updated_at) VALUES (:id, 'pending', NOW(), NOW())"),
        {"id": str(account_id)}
    )
    await session.commit()
    
    # Mock the heartbeat file
    heartbeat_file = tmp_path / "worker_heartbeat"
    # Write file and set mtime to older than 300 seconds
    heartbeat_file.touch()
    import os
    import time
    old_time = time.time() - 400
    os.utime(heartbeat_file, (old_time, old_time))
    
    # Patch config to look at this file
    import app.api.v2.config
    monkeypatch.setattr(app.api.v2.config, "os", os)
    
    # Let's mock `os.path.exists` to return true for our file, and patch where it gets it?
    # It's easier to just patch the literal path in the route or monkeypatch `os` module or something.
    # Since the route hardcodes `/tmp/worker_heartbeat`, we can monkeypatch `os.path.exists` and `os.path.getmtime`.
    original_exists = os.path.exists
    def mock_exists(p):
        if p == "/tmp/worker_heartbeat":
            return True
        return original_exists(p)
    original_getmtime = os.path.getmtime
    def mock_getmtime(p):
        if p == "/tmp/worker_heartbeat":
            return old_time
        return original_getmtime(p)
        
    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("os.path.getmtime", mock_getmtime)
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["worker_heartbeat"] == "stale"


async def test_sentry_debug_returns_404(app_client: AsyncClient):
    resp = await app_client.get("/api/v2/sentry-debug")
    assert resp.status_code == 404
