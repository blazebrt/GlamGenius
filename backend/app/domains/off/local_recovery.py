"""Read-only identity authority from the actual LOCAL restored Store B.

This recovery boundary is tied to the accepted Store B d0e1f2g3h4/147-table
generation. It never uses the application's production engine or accepts a
caller-supplied system identifier. The URL is process-local operator input.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

LOCAL_STORE_B_URL_ENV = "F15_LOCAL_RESTORED_STORE_B_URL"
EXPECTED_HEAD = "d0e1f2g3h4"
EXPECTED_TABLES = tuple(json.loads((Path(__file__).resolve().parents[2] / 'operations/accepted_store_b_public_tables.json').read_text(encoding='utf-8')))


class LocalRecoveryAuthorityError(ValueError):
    """Fixed failure labels; no connection URL or credential reaches output."""


def local_url(value: str | None) -> URL:
    try:
        url = make_url(value or "")
        if (url.host not in {"localhost", "127.0.0.1", "::1"} or not url.database
                or not url.username or url.query or url.drivername not in {"postgresql", "postgresql+asyncpg"}):
            raise ValueError
        return url.set(drivername="postgresql+asyncpg")
    except Exception:
        raise LocalRecoveryAuthorityError("Recovery requires explicit loopback PostgreSQL targets") from None


async def restored_store_b_authority() -> dict[str, Any]:
    url = local_url(os.environ.get(LOCAL_STORE_B_URL_ENV))
    engine = create_async_engine(url, poolclass=NullPool,
                                 connect_args={"server_settings": {"default_transaction_read_only": "on", "statement_timeout": "10000", "search_path": "pg_catalog"}})
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            async with connection.begin():
                readonly = (await connection.execute(text("SHOW transaction_read_only"))).scalar_one()
                system_identifier = (await connection.execute(text("SELECT system_identifier::text FROM pg_catalog.pg_control_system()"))).scalar_one()
                heads = (await connection.execute(text("SELECT version_num FROM public.alembic_version ORDER BY version_num"))).scalars().all()
                tables = (await connection.execute(text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname COLLATE \"C\""
                ))).scalars().all()
                off_present = (await connection.execute(text("SELECT to_regnamespace('off_data') IS NOT NULL"))).scalar_one()
                writable = (await connection.execute(text(
                    "SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls OR "
                    "has_database_privilege(current_user,current_database(),'CREATE,TEMP') OR "
                    "EXISTS(SELECT FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' "
                    "AND has_schema_privilege(current_user,n.oid,'CREATE')) OR "
                    "EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','f') "
                    "AND (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES'))) "
                    "OR EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S' "
                    "AND n.nspname NOT LIKE 'pg_%' AND has_sequence_privilege(current_user,c.oid,'USAGE,UPDATE')) "
                    "OR EXISTS(SELECT FROM pg_auth_members m WHERE m.member=r.oid) "
                    "FROM pg_roles r WHERE r.rolname=current_user"
                ))).scalar_one()
                if (readonly != "on" or not isinstance(system_identifier, str)
                        or re.fullmatch(r"[1-9][0-9]{9,19}", system_identifier) is None
                        or list(heads) != [EXPECTED_HEAD] or tuple(tables) != EXPECTED_TABLES or off_present or writable):
                    raise LocalRecoveryAuthorityError("Restored local Store B authority mismatch")
                return {"system_identifier": system_identifier, "alembic_heads": list(heads),
                        "public_tables": list(tables), "transaction_read_only": True,
                        "default_transaction_read_only": True, "role_write_capability": False, "production_session": False}
    except LocalRecoveryAuthorityError:
        raise
    except Exception:
        raise LocalRecoveryAuthorityError("Restored local Store B authority unavailable") from None
    finally:
        await engine.dispose()
