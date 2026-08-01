"""V2 database connectivity and migration state."""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.shared.database import sql
from app.shared.database.registry import Base

EXPECTED_TABLES = {
    "account_links",
    "consents",
    "media_assets",
    "ai_runs",
    "ai_run_outputs",
    "feature_flags",
    "audit_events",
    "usage_ledger",
    "domain_outbox",
    "app_events",
}


async def test_postgres_is_reachable():
    assert await sql.ping() is True


async def test_migration_created_every_expected_table():
    """The tables the brief asked for exist, and Alembic knows it ran."""

    def _tables(connection):
        return set(inspect(connection).get_table_names())

    async with sql.get_engine().connect() as conn:
        tables = await conn.run_sync(_tables)

    missing = EXPECTED_TABLES - tables
    assert not missing, f"migration did not create: {sorted(missing)}"
    assert "alembic_version" in tables


async def test_models_and_migration_agree():
    """Every mapped table is a real table.

    ``alembic check`` in the test container catches drift the other way (a model
    changed without a migration). This catches a model that was never added to
    the registry, which is the failure that otherwise passes silently.
    """

    def _tables(connection):
        return set(inspect(connection).get_table_names())

    async with sql.get_engine().connect() as conn:
        tables = await conn.run_sync(_tables)

    mapped = set(Base.metadata.tables.keys())
    assert mapped <= tables, f"mapped but not created: {sorted(mapped - tables)}"


async def test_session_can_read_and_write():
    from app.shared.flags.models import FeatureFlag

    factory = sql.get_sessionmaker()
    async with factory() as session:
        session.add(FeatureFlag(key="test_probe", enabled=True, description="probe"))
        await session.commit()

    async with factory() as session:
        row = await session.get(FeatureFlag, "test_probe")
        assert row is not None
        assert row.enabled is True
        await session.delete(row)
        await session.commit()


async def test_v2_health_route_reports_postgres(app_client):
    response = await app_client.get("/api/v2/health")
    assert response.status_code == 200
    body = response.json()
    assert body["postgres"] == "up"
    assert body["status"] == "healthy"
