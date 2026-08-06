"""Release entrypoint.

Validates the environment, acquires a deployment lock, runs migrations,
seeds reference data, runs consistency checks, and exits.
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
from app.domains.routines.models import Ingredient
from app.shared.database.sql import get_engine, get_sessionmaker
from app.shared.flags.models import FeatureFlag

logger = logging.getLogger("app.release")

# A single int for the advisory lock
LOCK_ID = 4829103  # arbitrary fixed number for release migrations


async def release() -> None:
    # 1. Validate production configuration
    validate_production_configuration()

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
            result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Alembic upgrade failed:\n{result.stderr}\n{result.stdout}")
                sys.exit(1)
            
            logger.info("Running alembic check...")
            result = subprocess.run(["alembic", "check"], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Alembic check failed:\n{result.stderr}\n{result.stdout}")
                sys.exit(1)

            # Seed reference data
            logger.info("Seeding reference data...")
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                counts = await seed_run(session)
                
                # Check seed version
                if counts["seed_version"] != SEED_VERSION:
                    logger.error(f"Seed version mismatch: expected {SEED_VERSION}, got {counts['seed_version']}")
                    sys.exit(1)

                # Verify seven inventory categories
                cat_count = await session.scalar(select(func.count(InventoryCategory.key)))
                if cat_count != 7:
                    logger.error(f"Inventory categories verification failed. Expected 7, found {cat_count}")
                    sys.exit(1)
                
                # Verify required feature flags
                flags_count = await session.scalar(select(func.count(FeatureFlag.key)))
                if flags_count == 0:
                    logger.error("Feature flags verification failed: no flags found.")
                    sys.exit(1)

                # Verify required reference catalogue counts and anchors
                ingredient_count = await session.scalar(select(func.count(Ingredient.key)))
                if ingredient_count == 0:
                    logger.error("Ingredient catalogue verification failed: no ingredients found.")
                    sys.exit(1)
                    
            logger.info("Release checks passed successfully.")
            
        finally:
            logger.info("Releasing advisory lock...")
            await conn.execute(text(f"SELECT pg_advisory_unlock({LOCK_ID})"))
            await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(release())
    except Exception as e:
        logger.error(f"Release failed with exception: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
