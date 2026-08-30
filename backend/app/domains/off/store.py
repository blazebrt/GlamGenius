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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app import config
from app.domains.off.models import OFF_SCHEMA, OffBase
from app.domains.off.wall import (
    assert_no_cross_store_foreign_keys,
    assert_no_proprietary_fields,
)

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def off_database_url() -> str:
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
        _sessionmaker = async_sessionmaker(
            bind=get_off_engine(), expire_on_commit=False, class_=AsyncSession,
        )
    return _sessionmaker


async def dispose_off_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


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
    logger.info("off_store_ready separate=%s", is_separate_store())
