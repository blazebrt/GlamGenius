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
    import app.domains.ai_gateway.providers.gemini as gemini_mod
    monkeypatch.setattr(gemini_mod, "is_configured", lambda: True)
    import app.api.v2.config as config_mod
    monkeypatch.setattr(config_mod, "validate_production_configuration", lambda: None)
    
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
    from app.bootstrap import SEED_VERSION
    import uuid
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text("INSERT INTO seed_version_records (id, seed_domain, seed_version, rows_written, applied_at) VALUES (:id, 'core', :seed, 1, NOW())"), {"id": str(uuid.uuid4()), "seed": SEED_VERSION})
        await session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('dummy_head')"))
        await session.commit()

    resp = await app_client.get("/api/v2/ready")
    if resp.status_code != 200:
        print("READY FAILURE:", resp.json())
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


async def test_ready_fails_on_seed_mismatch(app_client: AsyncClient, db_clean):
    # Intentionally insert an invalid seed version in the database
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
    import uuid
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text("INSERT INTO seed_version_records (id, seed_domain, seed_version, rows_written, applied_at) VALUES (:id, 'core', 'invalid-version', 1, NOW())"), {"id": str(uuid.uuid4())})
        await session.commit()
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert "mismatch" in resp.json()["components"]["seed_version_status"]


async def test_ready_fails_on_alembic_mismatch(app_client: AsyncClient, db_clean):
    # Intentionally break alembic head
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text("DELETE FROM alembic_version"))
        await session.commit()
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["alembic_status"] == "missing"


async def test_ready_fails_on_stale_worker_heartbeat(app_client: AsyncClient, db_clean, monkeypatch, tmp_path):
    # Create a pending account deletion job
    import uuid

    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
    # We must seed a valid account_id.
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        job_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO account_deletion_jobs (id, account_id, state, attempt_count, requested_at, created_at, updated_at) VALUES (:job_id, :id, 'pending', 0, NOW(), NOW(), NOW())"),
            {"job_id": str(job_id), "id": str(account_id)}
        )
        await session.commit()
    
    # Write directly to /tmp/worker_heartbeat
    import os
    import time
    heartbeat_file = "/tmp/worker_heartbeat"
    with open(heartbeat_file, "w") as f:
        f.write("")
    old_time = time.time() - 400
    os.utime(heartbeat_file, (old_time, old_time))
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["worker_heartbeat"] == "stale"


async def test_sentry_debug_returns_404(app_client: AsyncClient):
    resp = await app_client.get("/api/v2/sentry-debug")
    assert resp.status_code == 404
