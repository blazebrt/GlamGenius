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
    
    import uuid
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
    
    # db_clean truncates seed_version_records, so we must insert it to pass the ready check.
    # We DO NOT insert into alembic_version, because db_clean does not truncate it,
    # and modifying it will break subsequent tests that run alembic.
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(
            text("INSERT INTO seed_version_records (id, seed_domain, seed_version, rows_written, applied_at) VALUES (:id, 'core', '2026.02.16', 1, NOW())"),
            {"id": str(uuid.uuid4())}
        )
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
    import uuid

    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
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
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        version_num = result.scalar()
        await session.execute(text("DELETE FROM alembic_version"))
        await session.commit()
    
    try:
        resp = await app_client.get("/api/v2/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert resp.json()["components"]["alembic_status"] == "missing"
    finally:
        if version_num:
            async with factory() as session:
                await session.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version_num})
                await session.commit()


async def test_ready_fails_on_stale_worker_heartbeat(app_client: AsyncClient, db_clean, monkeypatch):
    # Create a pending account deletion job
    import uuid
    import datetime

    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        job_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO account_deletion_jobs (id, account_id, state, attempt_count, requested_at, created_at, updated_at) VALUES (:job_id, :id, 'requested', 0, NOW(), NOW(), NOW())"),
            {"job_id": str(job_id), "id": str(account_id)}
        )
        
        stale_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=400)
        await session.execute(
            text("INSERT INTO system_worker_status (id, worker_name, last_heartbeat_at, created_at, updated_at) VALUES (:wid, 'account_deletion_worker_1', :hb, NOW(), NOW())"),
            {"wid": str(uuid.uuid4()), "hb": stale_time}
        )
        await session.commit()
    
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["worker_heartbeat"] == "stale"


async def test_sentry_debug_returns_404(app_client: AsyncClient):
    resp = await app_client.get("/api/v2/sentry-debug")
    assert resp.status_code == 404
