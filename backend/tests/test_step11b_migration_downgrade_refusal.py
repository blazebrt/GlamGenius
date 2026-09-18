"""The Step 11B downgrade refuses on live household data, proven by running it.

Reversibility is a claim about a database, not about a file, and this one stops
being true the moment a second person exists. After that, restoring
``UNIQUE(account_id)`` means deleting or merging a real human's body facts.

The migration checks first and refuses. That refusal is the safety property, so
it is exercised here against a real PostgreSQL with real rows rather than
reasoned about: seed two legitimate profiles for one account, run
``alembic downgrade`` for real, and prove it stopped without touching anything.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from app.shared.database import sql
from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STEP_11B_REVISION = "f4g5h6i7j8"
PREVIOUS_REVISION = "e3f4g5h6i7"
#: Step 11C sits on top of 11B, so reaching 11B's downgrade means stepping past
#: it first. Named rather than counted: ``downgrade -2`` would silently follow
#: the chain wherever it grows next.
STEP_11C_REVISION = "g5h6i7j8k9"

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


async def _seed_two_subjects() -> dict[str, uuid.UUID]:
    """One account, one household, two people, two profiles.

    Written with raw SQL on purpose: this file is about what the *database*
    holds when the migration runs, and going through the ORM would tie the
    fixture to whatever the models happen to say at the time.
    """
    ids = {
        "account": uuid.uuid4(),
        "circle": uuid.uuid4(),
        "self_member": uuid.uuid4(),
        "other_member": uuid.uuid4(),
        "self_profile": uuid.uuid4(),
        "other_profile": uuid.uuid4(),
    }
    async with sql.get_engine().begin() as connection:
        await connection.execute(
            text("INSERT INTO accounts (id, created_at, updated_at) "
                 "VALUES (:id, now(), now())"),
            {"id": ids["account"]},
        )
        await connection.execute(
            text("INSERT INTO family_circles (id, account_id, created_at, updated_at) "
                 "VALUES (:id, :account, now(), now())"),
            {"id": ids["circle"], "account": ids["account"]},
        )
        for key, position, relation in (
            ("self_member", 1, "self"), ("other_member", 2, "adult"),
        ):
            await connection.execute(
                text("INSERT INTO family_profiles "
                     "(id, circle_id, position, relation, active, age_band, created_at, updated_at) "
                     "VALUES (:id, :circle, :position, :relation, true, 'adult_18_plus', now(), now())"),
                {
                    "id": ids[key], "circle": ids["circle"],
                    "position": position, "relation": relation,
                },
            )
        for profile_key, member_key in (
            ("self_profile", "self_member"), ("other_profile", "other_member"),
        ):
            await connection.execute(
                text("INSERT INTO appearance_profiles "
                     "(id, account_id, household_subject_id, created_at, updated_at) "
                     "VALUES (:id, :account, :subject, now(), now())"),
                {
                    "id": ids[profile_key], "account": ids["account"],
                    "subject": ids[member_key],
                },
            )
    return ids


async def _snapshot() -> list[tuple]:
    async with sql.get_engine().connect() as connection:
        rows = await connection.execute(text(
            "SELECT id, account_id, household_subject_id, created_at, updated_at, version "
            "FROM appearance_profiles ORDER BY id"
        ))
        return [tuple(row) for row in rows]


async def _current_revision() -> str:
    async with sql.get_engine().connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def test_downgrade_refuses_while_two_people_share_an_account(db_clean):
    ids = await _seed_two_subjects()
    before = await _snapshot()
    assert len(before) == 2

    # The engine holds pooled connections; alembic runs in its own process and
    # must not be racing this one for the same rows.
    await sql.dispose_engine()
    restored = False
    try:
        # Step past Step 11C first. It has no attributed decision memory here,
        # so its own refusal does not fire and this is an ordinary downgrade.
        step_down, step_output = await _alembic("downgrade", STEP_11B_REVISION)
        assert step_down == 0, step_output

        returncode, output = await _alembic("downgrade", "-1")

        # It refused, and said why in terms an operator can act on.
        assert returncode != 0, output
        assert "Cannot downgrade f4g5h6i7j8" in output, output
        assert "more than one appearance profile" in output, output
        assert str(ids["account"]) in output, output
        # And it named a forward corrective migration as the way out, rather
        # than implying the downgrade could be forced.
        assert "forward corrective migration" in output, output

        # Nothing was deleted, merged, or picked between.
        assert await _snapshot() == before
        # And the database did not land halfway through: the schema this
        # migration owns is still in place, and so is its version row. Step 11C
        # was stepped past on the way in, so 11B is where the refusal left it.
        assert await _current_revision() == STEP_11B_REVISION
        async with sql.get_engine().connect() as connection:
            indexes = (await connection.execute(text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'appearance_profiles'"
            ))).scalars().all()
            column = await connection.scalar(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'appearance_profiles' "
                "AND column_name = 'household_subject_id'"
            ))
        assert "uq_appearance_profile_household_subject" in indexes
        assert "uq_appearance_profile_account_legacy" in indexes
        assert column == 1
        restored = True
    finally:
        await sql.dispose_engine()
        if not restored:
            await _alembic("upgrade", "head")


async def test_downgrade_and_re_upgrade_succeed_once_the_data_allows_it(db_clean):
    """The other half of the claim: it really is reversible until it is not."""
    ids = await _seed_two_subjects()
    async with sql.get_engine().begin() as connection:
        await connection.execute(
            text("DELETE FROM appearance_profiles WHERE id = :id"),
            {"id": ids["other_profile"]},
        )
    await sql.dispose_engine()
    try:
        step_down, step_output = await _alembic("downgrade", STEP_11B_REVISION)
        assert step_down == 0, step_output
        returncode, output = await _alembic("downgrade", "-1")
        assert returncode == 0, output
        assert await _current_revision() == PREVIOUS_REVISION

        # The surviving profile is untouched by the downgrade itself.
        async with sql.get_engine().connect() as connection:
            remaining = (await connection.execute(text(
                "SELECT id FROM appearance_profiles"
            ))).scalars().all()
        assert remaining == [ids["self_profile"]]
        await sql.dispose_engine()
    finally:
        returncode, output = await _alembic("upgrade", "head")
        assert returncode == 0, output
    assert await _current_revision() == STEP_11C_REVISION
