"""Alembic environment.

Async throughout, because the application engine is asyncpg and running
migrations through a second synchronous driver would mean a second driver
dependency for no benefit.

The database URL always comes from ``POSTGRES_URL``, never from alembic.ini.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import POSTGRES_URL

# Importing the registry is what makes every table visible to autogenerate.
from app.shared.database.registry import Base  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", POSTGRES_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Store A (Open Food Facts) lives in its own schema and is deliberately NOT
# managed by this chain — see docs/architecture/ODBL_DATA_WALL.md. Without this
# filter, autogenerate would see those tables as unmanaged and propose dropping
# them, which is how a licence boundary quietly becomes a migration.
from app.domains.off.models import OFF_SCHEMA  # noqa: E402


def include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201, ARG001
    schema = getattr(object_, "schema", None)
    if schema == OFF_SCHEMA:
        return False
    if type_ == "table" and getattr(object_, "schema", None) == OFF_SCHEMA:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=POSTGRES_URL,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
