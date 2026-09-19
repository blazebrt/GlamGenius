"""The Step 11D downgrade refuses on attributed Care state, proven by running it.

Reversibility is a claim about a database, not about a file. This migration is
reversible for exactly as long as nobody has used it: dropping
``care_product_preferences`` and the two ``household_subject_id`` columns costs
nothing while no row names a person, and costs the answer itself the moment one
does.

What would be lost is not a constraint a later migration could rebuild. It is
*which human paused this product*, *whose preference this is*, and *who answered
their manager* — written down in exactly one place. A downgrade that dropped
them would leave a database that looked complete and was quietly about nobody.

So the migration checks first and refuses, and the refusal is exercised here
against a real PostgreSQL with rows the product itself wrote, rather than
reasoned about from the source.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from app.shared.database import sql
from sqlalchemy import text

from tests.conftest import auth
from tests.test_step11d_subject_care_authority import _expired_shelf, _member, _queue
from tests.test_v3_03_3_integration import _seed

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STEP_11D_REVISION = "h6i7j8k9l0"
STEP_11C_REVISION = "g5h6i7j8k9"

RESPOND_URL = "/api/v2/shelf/manager/respond"

pytestmark = pytest.mark.asyncio


async def _alembic(*arguments: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", *arguments,
        cwd=BACKEND_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode(errors="replace")


async def _answer(client, token: str, member: str | None, key: str) -> None:
    primary = (await _queue(client, token, member))["primary"]
    url = RESPOND_URL if member is None else f"{RESPOND_URL}?subject_id={member}"
    response = await client.post(url, headers=auth(token), json={
        "decision_key": primary["decision_key"],
        "decision_fingerprint": primary["decision_fingerprint"],
        "choice": "accept",
        "client_mutation_id": key,
    })
    assert response.status_code == 200, response.text


async def _counts() -> dict[str, tuple[int, int]]:
    """Total rows and attributed rows, for each thing the migration guards."""
    async with sql.get_engine().connect() as connection:
        return {
            "care_product_preferences": tuple((await connection.execute(text(
                "SELECT count(*), count(household_subject_id) "
                "FROM care_product_preferences"
            ))).one()),
            "shelf_manager_decision_events": tuple((await connection.execute(text(
                "SELECT count(*), count(household_subject_id) "
                "FROM shelf_manager_decision_events"
            ))).one()),
            "inventory_events": tuple((await connection.execute(text(
                "SELECT count(*), count(household_subject_id) FROM inventory_events"
            ))).one()),
        }


async def _current_revision() -> str:
    async with sql.get_engine().connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def _has_column(table: str) -> bool:
    async with sql.get_engine().connect() as connection:
        return bool(await connection.scalar(
            text("SELECT count(*) FROM information_schema.columns "
                 "WHERE table_name = :table AND column_name = 'household_subject_id'"),
            {"table": table},
        ))


async def _table_exists(table: str) -> bool:
    async with sql.get_engine().connect() as connection:
        return bool(await connection.scalar(
            text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table},
        ))


async def _index_definition(name: str) -> str | None:
    async with sql.get_engine().connect() as connection:
        return await connection.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": name},
        )


async def test_downgrade_refuses_once_a_care_preference_names_a_person(
    db_clean, app_client, registered_supabase_user,
):
    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    # A row the product itself wrote, through the route a customer would use.
    await _answer(app_client, token, member, "downgrade-refusal")

    before = await _counts()
    assert before["care_product_preferences"] == (1, 1)
    assert before["shelf_manager_decision_events"] == (1, 1)
    assert before["inventory_events"][1] == 1

    # The engine holds pooled connections; alembic runs in its own process and
    # must not be racing this one for the same rows.
    await sql.dispose_engine()
    restored = False
    try:
        returncode, output = await _alembic("downgrade", STEP_11C_REVISION)

        assert returncode != 0, output
        assert "Cannot downgrade h6i7j8k9l0" in output, output
        # It says what would be lost, not merely that something would be.
        assert "which person paused a product" in output, output
        assert "care_product_preferences (1 rows)" in output, output
        assert "shelf_manager_decision_events (1 rows)" in output, output
        assert "inventory_events (1 rows)" in output, output
        # And it names a forward corrective migration as the way out, rather
        # than implying the downgrade could be forced.
        assert "corrective migration" in output, output

        # Nothing was dropped, nulled or merged, and the database did not land
        # halfway through: the version row still says 11D.
        assert await _counts() == before
        assert await _current_revision() == STEP_11D_REVISION
        assert await _table_exists("care_product_preferences")
        for table in ("shelf_manager_decision_events", "inventory_events"):
            assert await _has_column(table), table
        restored = True
    finally:
        await sql.dispose_engine()
        if not restored:
            await _alembic("upgrade", "head")


async def test_the_round_trip_succeeds_while_nothing_is_attributed(
    db_clean, app_client, registered_supabase_user,
):
    """The other half of the claim: it really is reversible until it is not.

    An account with no household writes its Care state exactly where it always
    did, so this is the ordinary reversible case — and it has to keep working, or
    the migration could never be rolled back at all.

    The re-upgrade is also the only place this suite runs the migration's own
    ``upgrade()`` against a database, so it is the only place the objects it
    creates can be checked for real rather than read from the file.
    """
    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    await _answer(app_client, token, None, "downgrade-clean")

    before = await _counts()
    assert before["care_product_preferences"] == (0, 0)
    assert before["shelf_manager_decision_events"] == (1, 0)

    await sql.dispose_engine()
    try:
        returncode, output = await _alembic("downgrade", STEP_11C_REVISION)
        assert returncode == 0, output
        assert await _current_revision() == STEP_11C_REVISION
        assert not await _table_exists("care_product_preferences")
        for table in ("shelf_manager_decision_events", "inventory_events"):
            assert not await _has_column(table), table

        # The answer itself survived the downgrade untouched.
        async with sql.get_engine().connect() as connection:
            assert await connection.scalar(
                text("SELECT choice FROM shelf_manager_decision_events")
            ) == "accepted"
            assert await connection.scalar(
                text("SELECT count(*) FROM inventory_items WHERE id = :id"),
                {"id": uuid.UUID(item_id)},
            ) == 1
        await sql.dispose_engine()
    finally:
        returncode, output = await _alembic("upgrade", "head")
        assert returncode == 0, output

    assert await _current_revision() == STEP_11D_REVISION
    assert await _counts() == before

    # Asserted by definition rather than by name: an index that exists with the
    # wrong columns is the failure that looks fine in a listing.
    subject_kind = await _index_definition("ix_care_product_preferences_subject_kind")
    assert subject_kind is not None
    assert "account_id, household_subject_id, preference_kind" in subject_kind
    # The redundant one is not recreated by the re-upgrade either.
    assert await _index_definition("ix_care_product_preferences_subject_item") is None
    # The uniqueness that makes "one person, one current answer per product"
    # true is a constraint rather than a bare index, and it is back.
    unique = await _index_definition("uq_care_product_preference_subject_item_kind")
    assert unique is not None, (
        "the per-subject uniqueness was not recreated, so one person could hold "
        "two current answers about one product"
    )
    assert "UNIQUE" in unique
