"""Store A's own connection.

``OFF_DATABASE_URL`` is what makes the separation physical rather than
notional. Point it at a different server and the two stores are genuinely
apart; leave it unset and it falls back to the application database, which is
convenient for development and is the one case where they share hardware.

Even in the shared case the wall holds, because it is enforced by the models,
the allowlist and the write guard rather than by the two being far apart.
Separate hardware is a deployment decision; not combining the databases is an
architectural one, and only the second is a licence question.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateColumn, CreateIndex

from app import config
from app.domains.off.models import OFF_SCHEMA, OffBase
from app.domains.off.wall import (
    assert_no_cross_store_foreign_keys,
    assert_no_proprietary_fields,
    guard_off_session,
)

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def off_database_url() -> str:
    if config.APP_ENV in {"production", "staging"} and not is_separate_store():
        raise RuntimeError("OFF_DATABASE_URL must be configured separately from POSTGRES_URL outside development/test.")
    return config.OFF_DATABASE_URL or config.POSTGRES_URL


def is_separate_store() -> bool:
    """True when Store A really is a different database."""
    return bool(config.OFF_DATABASE_URL) and config.OFF_DATABASE_URL != config.POSTGRES_URL


def get_off_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(off_database_url(), pool_pre_ping=True)
        if not is_separate_store():
            logger.warning(
                "off_store_shares_the_application_database "
                "hint=set OFF_DATABASE_URL to keep the two stores physically apart",
            )
    return _engine


def get_off_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        class GuardedOffSession(AsyncSession):
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
                super().__init__(*args, **kwargs)
                guard_off_session(self.sync_session)

        _sessionmaker = async_sessionmaker(
            bind=get_off_engine(), expire_on_commit=False, class_=GuardedOffSession,
        )
    return _sessionmaker


async def dispose_off_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _evolve_off_tables(connection: Connection) -> None:
    """Bring an existing Store A table up to the model, additively.

    ``create_all`` creates what is missing and then leaves an existing table
    completely alone, so a table created by an earlier release never grows the
    columns or indexes a later one declares. Store A is deliberately outside
    the application's Alembic chain — a migration written for the product must
    not be able to reach in here — so this is where its schema moves forward.

    Three rules, and they are what make it safe to run on every startup:

    * **Additive only.** Columns and indexes are added; nothing is dropped,
      renamed, retyped or rewritten. A value Open Food Facts gave us is never
      edited by a deployment.
    * **Idempotent.** Every statement is conditional, so a fresh database, a
      half-migrated one and an already-current one all converge, and two
      workers starting together cannot fight.
    * **Backfill nothing.** A row copied before a column existed keeps NULL.
      For the canonical taxonomy columns that is the entire point: they can
      only be computed from the taxonomy arrays, and a row fetched before we
      requested those arrays does not have them. Deriving them from the raw
      ``categories`` text to fill the gap would reintroduce the defect the
      canonical columns exist to remove, so such a row stays ineligible until
      an ordinary refresh re-fetches it.
    """
    inspector = inspect(connection)
    for table in OffBase.metadata.sorted_tables:
        if not inspector.has_table(table.name, schema=table.schema):
            continue  # create_all has just made it, in full.
        present = {column["name"] for column in inspector.get_columns(table.name, schema=table.schema)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.server_default is None:
                # Adding one to a populated table would fail, and inventing a
                # value for rows Open Food Facts never gave one for is worse.
                raise RuntimeError(
                    f"{table.name}.{column.name} must be nullable or carry a server_default: "
                    f"Store A evolves additively and never fabricates a value for an existing row.",
                )
            definition = CreateColumn(column).compile(dialect=connection.dialect)
            connection.execute(text(
                f'ALTER TABLE "{table.schema}"."{table.name}" ADD COLUMN IF NOT EXISTS {definition}',
            ))
        known = {index["name"] for index in inspector.get_indexes(table.name, schema=table.schema)}
        for index in table.indexes:
            if index.name not in known:
                connection.execute(CreateIndex(index, if_not_exists=True))


async def create_off_schema() -> None:
    """Create Store A's tables, after checking they are still only OFF fields.

    Store A is not managed by the application's Alembic chain, on purpose: a
    migration written for the product must not be able to reach into it.
    """
    assert_no_proprietary_fields()
    assert_no_cross_store_foreign_keys()
    engine = get_off_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{OFF_SCHEMA}"'))
        await conn.run_sync(OffBase.metadata.create_all)
        await conn.run_sync(_evolve_off_tables)
    logger.info("off_store_ready separate=%s", is_separate_store())
