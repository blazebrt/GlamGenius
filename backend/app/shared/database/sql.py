"""Async SQLAlchemy 2.x engine, session factory and FastAPI dependency."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import (
    POSTGRES_ECHO,
    POSTGRES_MAX_OVERFLOW,
    POSTGRES_POOL_SIZE,
    POSTGRES_URL,
)

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazily built so importing this module never opens a connection.

    Tests and Alembic both import it before a database necessarily exists.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            POSTGRES_URL,
            echo=POSTGRES_ECHO,
            pool_size=POSTGRES_POOL_SIZE,
            max_overflow=POSTGRES_MAX_OVERFLOW,
            pool_pre_ping=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request, rolled back on failure."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    """True when PostgreSQL answers. Used by the health route."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — health must never raise
        logger.warning("postgres_ping_failed type=%s", type(exc).__name__)
        return False


async def dispose_engine() -> None:
    """Close the pool on shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
