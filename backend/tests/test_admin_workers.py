import uuid

import pytest
from app.shared.database.sql import get_sessionmaker
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

async def test_admin_workers_returns_metrics(
    app_client: AsyncClient,
    registered_supabase_user,
    db_clean,
):
    token, _ = await registered_supabase_user(admin=True)
    
    # Insert some dummy data
    factory = get_sessionmaker()
    async with factory() as session:
        # Create an account to satisfy foreign keys
        account_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO accounts (id, status, created_at, updated_at) VALUES (:id, 'deletion_requested', NOW(), NOW())"),
            {"id": str(account_id)}
        )
        
        job_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO account_deletion_jobs (id, account_id, state, attempt_count, requested_at, created_at, updated_at) VALUES (:job_id, :account_id, 'requested', 0, NOW(), NOW(), NOW())"),
            {"job_id": str(job_id), "account_id": str(account_id)}
        )
        await session.commit()
    
    resp = await app_client.get("/api/v2/admin/workers", headers=auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "job_metrics" in data
    assert data["job_metrics"]["pending_jobs"] >= 1

async def test_admin_workers_rejects_non_admin(
    app_client: AsyncClient,
    registered_supabase_user,
    db_clean,
):
    token, _ = await registered_supabase_user(admin=False)
    resp = await app_client.get("/api/v2/admin/workers", headers=auth(token))
    assert resp.status_code == 403

