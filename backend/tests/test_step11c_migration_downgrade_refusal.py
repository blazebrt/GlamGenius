"""The Step 11C downgrade refuses on attributed decision memory, proven by running it.

Reversibility is a claim about a database, not about a file. This migration is
reversible for exactly as long as nobody has used it: dropping
``household_subject_id`` costs nothing while every row is NULL, and costs the
answer itself the moment a row names a person.

That difference matters more here than it did for a constraint. A unique index
removed by a downgrade can be rebuilt by the next upgrade. *Which human made this
decision* cannot — the column is the only place it is written down, and once the
rows are gone they would sit in the database looking complete and be quietly
about nobody.

So the migration checks first and refuses. That refusal is the safety property,
so it is exercised here against a real PostgreSQL with rows the product itself
wrote, rather than reasoned about.
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
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STEP_11C_REVISION = "g5h6i7j8k9"
STEP_11B_REVISION = "f4g5h6i7j8"

PROFILES_URL = "/api/v2/family-circle/profiles"

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


async def _candidate(account_id: uuid.UUID) -> uuid.UUID:
    from app.domains.recommendation.models import ShoppingCandidate

    candidate_id = await _seed_db_candidate(account_id)
    async with sql.get_sessionmaker()() as session:
        row = await session.get(ShoppingCandidate, candidate_id)
        row.brand = "Example Labs"
        row.display_name = "Gentle Cleanser"
        row.details = {
            "product_type": "cleanser", "purpose": "cleanse",
            "active_ingredients": ["Niacinamide"],
        }
        await session.commit()
    return candidate_id


async def _decide(client, token, candidate_id, decision, *, subject_id=None):
    suffix = f"&subject_id={subject_id}" if subject_id else ""
    response = await client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20{suffix}",
        headers=auth(token), json={"decision": decision},
    )
    assert response.status_code == 200, response.text
    return response


async def _counts() -> dict[str, tuple[int, int]]:
    """Total rows and attributed rows, per table the migration guards."""
    tables = ("scan_decision_events", "purchase_decisions", "purchase_decision_events")
    async with sql.get_engine().connect() as connection:
        return {
            table: tuple(
                (await connection.execute(text(
                    f"SELECT count(*), count(household_subject_id) FROM {table}"
                ))).one()
            )
            for table in tables
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


async def _index_predicate(name: str) -> str | None:
    async with sql.get_engine().connect() as connection:
        return await connection.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": name},
        )


async def test_downgrade_refuses_once_a_decision_names_a_person(
    db_clean, app_client, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    member = (await app_client.post(
        PROFILES_URL, headers=auth(token), json={"relation": "adult"},
    )).json()["id"]
    candidate_id = await _candidate(account_id)

    # Rows the product itself writes, through the route a customer would use.
    await _decide(app_client, token, candidate_id, "bought", subject_id=member)
    before = await _counts()
    assert before["purchase_decisions"] == (1, 1)
    assert before["purchase_decision_events"] == (1, 1)

    # The engine holds pooled connections; alembic runs in its own process and
    # must not be racing this one for the same rows.
    await sql.dispose_engine()
    restored = False
    try:
        returncode, output = await _alembic("downgrade", STEP_11B_REVISION)

        assert returncode != 0, output
        assert "Cannot downgrade g5h6i7j8k9" in output, output
        # It said what was lost, not merely that something was.
        assert "which person made each decision" in output, output
        assert "purchase_decisions (1 rows)" in output, output
        assert "purchase_decision_events (1 rows)" in output, output
        # And it named a forward corrective migration as the way out, rather
        # than implying the downgrade could be forced.
        assert "forward corrective migration" in output, output

        # Nothing was dropped, nulled, or merged, and the database did not land
        # halfway through: the version row still says 11C.
        assert await _counts() == before
        assert await _current_revision() == STEP_11C_REVISION
        for table in before:
            assert await _has_column(table), table
        assert await _index_predicate(
            "uq_purchase_decision_subject_candidate_strategy"
        ) is not None
        restored = True
    finally:
        await sql.dispose_engine()
        if not restored:
            await _alembic("upgrade", "head")


async def test_downgrade_and_re_upgrade_succeed_while_nothing_is_attributed(
    db_clean, app_client, registered_supabase_user,
):
    """The other half of the claim: it really is reversible until it is not.

    An account with no household writes subject-less decision memory exactly as
    it always did, so this is the ordinary reversible case — and it has to keep
    working, or the migration could never be rolled back at all.
    """
    token, account_id = await registered_supabase_user()
    candidate_id = await _candidate(account_id)
    await _decide(app_client, token, candidate_id, "waiting")
    before = await _counts()
    assert before["purchase_decisions"] == (1, 0)

    await sql.dispose_engine()
    try:
        returncode, output = await _alembic("downgrade", STEP_11B_REVISION)
        assert returncode == 0, output
        assert await _current_revision() == STEP_11B_REVISION

        # The column is gone and the account-wide uniqueness is back to what it
        # was — no household predicate, because there is no household column.
        for table in before:
            assert not await _has_column(table), table
        legacy = await _index_predicate("uq_purchase_decision_candidate_strategy")
        assert legacy is not None
        assert "household_subject_id" not in legacy, legacy
        assert await _index_predicate(
            "uq_purchase_decision_subject_candidate_strategy"
        ) is None

        # The decision itself survived the downgrade untouched.
        async with sql.get_engine().connect() as connection:
            assert await connection.scalar(
                text("SELECT decision FROM purchase_decisions")
            ) == "waiting"
        await sql.dispose_engine()
    finally:
        returncode, output = await _alembic("upgrade", "head")
        assert returncode == 0, output

    assert await _current_revision() == STEP_11C_REVISION
    assert await _counts() == before

    # The re-upgrade is the only place the migration's own ``upgrade()`` runs
    # against a database in this suite, so it is also the only place the index
    # definitions it creates can be checked. Asserted by predicate rather than
    # by name: an index that exists with the wrong ``WHERE`` clause is the
    # failure mode that looks fine in a listing and lets one account hold two
    # current decisions for one candidate.
    for table in before:
        assert await _has_column(table), table
    subject_unique = await _index_predicate(
        "uq_purchase_decision_subject_candidate_strategy"
    )
    assert subject_unique is not None, (
        "the subject-scoped unique index was not recreated, so two members "
        "could each end up with two current decisions for one candidate"
    )
    assert "household_subject_id IS NOT NULL" in subject_unique, subject_unique
    restored_legacy = await _index_predicate("uq_purchase_decision_candidate_strategy")
    assert restored_legacy is not None
    assert "household_subject_id IS NULL" in restored_legacy, (
        "the account-wide unique index still spans attributed rows, so one "
        "member's decision would block another's for the same candidate: "
        f"{restored_legacy}"
    )


async def test_the_schema_itself_prevents_the_duplicate_the_second_guard_describes(
    db_clean, app_client, registered_supabase_user,
):
    """Why the downgrade's second check cannot fire, and is kept anyway.

    The downgrade refuses twice: once for any attributed row, and once for
    current purchase decisions that would collide when the account-wide unique
    index is restored. The second can never be reached while the first stands —
    a collision needs two current rows for one account, candidate and strategy,
    and either at least one of them names a subject (caught by the first check)
    or they are both subject-less, which the partial unique index below already
    forbids.

    That is asserted here rather than left implied, so the second guard is
    understood as defence against the first being removed, and nobody deletes it
    later believing it was dead code that had simply never run.
    """
    from sqlalchemy.exc import IntegrityError

    token, account_id = await registered_supabase_user()
    candidate_id = await _candidate(account_id)
    await _decide(app_client, token, candidate_id, "waiting")

    async with sql.get_sessionmaker()() as session:
        original = (await session.execute(text(
            "SELECT candidate_id, strategy_key, decision, recommendation_verdict, "
            "recommendation_version FROM purchase_decisions"
        ))).one()
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO purchase_decisions "
                    "(id, account_id, candidate_id, strategy_key, decision, "
                    " recommendation_verdict, recommendation_version, "
                    " followed_recommendation, created_at, updated_at) "
                    "VALUES (:id, :account, :candidate, :strategy, :decision, "
                    " :verdict, :version, false, now(), now())"
                ),
                {
                    "id": uuid.uuid4(), "account": account_id,
                    "candidate": original.candidate_id,
                    "strategy": original.strategy_key,
                    "decision": original.decision,
                    "verdict": original.recommendation_verdict,
                    "version": original.recommendation_version,
                },
            )
            await session.flush()
        await session.rollback()
