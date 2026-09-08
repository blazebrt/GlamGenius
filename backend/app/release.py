"""Release entrypoint.

Validates the environment, acquires a deployment lock, runs migrations,
seeds reference data, runs consistency checks, and exits.

**What this process is allowed to say.** It runs against production, and its
output lands in a deployment log an operator reads and often pastes elsewhere.
So the failure boundary is deliberately narrow: a fixed stage name, a fixed
failure classification, a subprocess return code, and governed non-secret
identifiers such as the expected seed version or a category count.

It must never emit a database URL, a password, a token, a Supabase key, a
connection string, an arbitrary driver exception message, arbitrary subprocess
stdout or stderr, or a traceback. Alembic's stderr in particular is not safe to
forward: when a migration cannot connect, the driver's message is the
connection string, credentials included, and a deployment log is not the place
to publish it.

Losing that text costs some convenience when debugging a failed release. The
answer is to reproduce it against a non-production database with the same
migration chain, where the same message is harmless — not to print production
credentials into a log and hope nobody forwards it.

None of this hides the failure. Every path below still logs that the release
failed, at which stage, and exits non-zero, so a failed release stops a
deployment rather than letting it proceed.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from sqlalchemy import func, select, text

from app.bootstrap import SEED_VERSION
from app.bootstrap import run as seed_run
from app.config import validate_production_configuration
from app.domains.inventory.models import InventoryCategory
from app.domains.off.store import create_off_schema, dispose_off_engine
from app.domains.routines.models import Ingredient
from app.shared.database.sql import get_engine, get_sessionmaker
from app.shared.flags.models import FeatureFlag

logger = logging.getLogger("app.release")

# A single int for the advisory lock
LOCK_ID = 4829103  # arbitrary fixed number for release migrations


def _fail(stage: str, classification: str, **safe: object) -> None:
    """Report a release failure without repeating anything the failure said.

    ``stage`` and ``classification`` are fixed strings chosen at the call site,
    never derived from an exception, a driver, or a subprocess. ``safe`` carries
    governed non-secret detail — a return code, an expected seed version, a
    count — and callers must pass only values they know cannot hold a
    credential.
    """
    detail = " ".join(f"{key}={value}" for key, value in sorted(safe.items()))
    logger.error(
        "Release failed. stage=%s classification=%s%s",
        stage,
        classification,
        f" {detail}" if detail else "",
    )
    sys.exit(1)


async def release() -> None:
    # 1. Validate production configuration
    try:
        validate_production_configuration()
    except RuntimeError:
        # The validator's own messages name the offending key, and some of them
        # quote the hostname or scheme they rejected. That is fine for the
        # readiness report, which is built to be safe to paste; it is not fine
        # here, where it would sit in a deployment log next to everything else.
        # The operator gets the classification and the tool that will tell them
        # exactly which key is wrong without printing any value.
        _fail(
            "validate_production_configuration",
            "production_configuration_invalid",
            remediation="python -m app.release_readiness --json",
        )

    engine = get_engine()
    
    # Acquire advisory lock using a dedicated connection
    async with engine.connect() as conn:
        logger.info(f"Acquiring advisory lock {LOCK_ID}...")
        # pg_advisory_lock blocks until it gets the lock
        await conn.execute(text(f"SELECT pg_advisory_lock({LOCK_ID})"))
        logger.info("Advisory lock acquired.")
        
        try:
            logger.info("Running alembic upgrade head...")
            # We run Alembic as a subprocess to keep its env.py logic separate
            # and avoid async engine sharing complexities.
            # capture_output keeps Alembic's streams out of this process's own
            # stdout/stderr. They are captured and then deliberately dropped:
            # a failing migration's stderr is usually the driver's connection
            # string, credentials and all.
            result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True, text=True)
            if result.returncode != 0:
                _fail("alembic_upgrade", "migration_failed", returncode=result.returncode)
            
            logger.info("Running alembic check...")
            result = subprocess.run([sys.executable, "-m", "alembic", "check"], capture_output=True, text=True)
            if result.returncode != 0:
                _fail("alembic_check", "schema_drift_detected", returncode=result.returncode)

            # Provision Store A. It is deliberately outside the Alembic chain
            # (a migration written for the product must not be able to reach
            # into it), so nothing else creates it — without this step the
            # first barcode lookup on a fresh database hits a missing schema.
            # create_off_schema() re-checks the ODbL wall before it writes, so
            # a broken wall stops the release here rather than in production.
            # get_off_engine() already warns when the two stores share a
            # database, so this does not repeat it.
            logger.info("Provisioning the Open Food Facts store...")
            await create_off_schema()

            # Seed reference data
            logger.info("Seeding reference data...")
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                counts = await seed_run(session)
                
                # Check seed version
                if counts["seed_version"] != SEED_VERSION:
                    # Both values are governed constants from the repository's
                    # own seed catalogue, so naming them is safe and useful.
                    _fail(
                        "seed_reference_data",
                        "seed_version_mismatch",
                        expected=SEED_VERSION,
                        found=counts["seed_version"],
                    )

                # Verify seven inventory categories
                cat_count = await session.scalar(select(func.count(InventoryCategory.key)))
                if cat_count != 7:
                    _fail(
                        "verify_inventory_categories",
                        "category_count_mismatch",
                        expected=7,
                        found=cat_count,
                    )
                
                # Verify required feature flags
                flags_count = await session.scalar(select(func.count(FeatureFlag.key)))
                if flags_count == 0:
                    _fail("verify_feature_flags", "no_feature_flags_found")

                # Verify required reference catalogue counts and anchors
                ingredient_count = await session.scalar(select(func.count(Ingredient.key)))
                if ingredient_count == 0:
                    _fail("verify_ingredient_catalogue", "no_ingredients_found")
                    
            logger.info("Release checks passed successfully.")
            
        finally:
            logger.info("Releasing advisory lock...")
            await conn.execute(text(f"SELECT pg_advisory_unlock({LOCK_ID})"))
            await dispose_off_engine()
            await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(release())
    except SystemExit:
        # _fail() already said what happened, safely. Re-raising keeps its
        # exit code instead of relabelling a classified failure as unexpected.
        raise
    except Exception:
        # Deliberately not `except Exception as e` followed by str(e). An
        # unexpected failure here is almost always the database driver, and
        # asyncpg puts the connection string — user, password, host, database —
        # into the message. The exception type is not logged either: it is
        # attacker-influenced only in narrow cases, but it buys little and the
        # stage already localises the failure. Reproduce against a non-production
        # database to see the message.
        _fail("release", "unexpected_error")


if __name__ == "__main__":
    main()
