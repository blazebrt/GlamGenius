"""Step 11E migration identity-loss refusal, against real PostgreSQL."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from app.shared.database import sql
from sqlalchemy import text

from tests.conftest import auth
from tests.test_domain_routines_api import _seeded_shelf
from tests.test_step11d_subject_care_authority import _member

pytestmark = pytest.mark.asyncio

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STEP_11E_REVISION = "i7j8k9l0m1"
STEP_11D_REVISION = "h6i7j8k9l0"


async def _alembic(*arguments: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *arguments,
        cwd=BACKEND_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode(errors="replace")


async def _revision() -> str:
    async with sql.get_engine().connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def test_downgrade_refuses_once_a_persisted_routine_names_a_subject(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, _account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    response = await app_client.post(
        f"/api/v2/routines/generate?subject_id={member}",
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )
    assert response.status_code == 200, response.text

    await sql.dispose_engine()
    try:
        returncode, output = await _alembic("downgrade", STEP_11D_REVISION)
        assert returncode != 0, output
        assert "Step 11E downgrade refused" in output
        assert await _revision() == STEP_11E_REVISION
    finally:
        await sql.dispose_engine()
        await _alembic("upgrade", "head")


async def test_clean_round_trip_restores_the_old_account_kind_uniqueness(
    db_clean,
):
    await sql.dispose_engine()
    try:
        returncode, output = await _alembic("downgrade", STEP_11D_REVISION)
        assert returncode == 0, output
        assert await _revision() == STEP_11D_REVISION
        returncode, output = await _alembic("upgrade", STEP_11E_REVISION)
        assert returncode == 0, output
        assert await _revision() == STEP_11E_REVISION
    finally:
        await sql.dispose_engine()
        await _alembic("upgrade", "head")
