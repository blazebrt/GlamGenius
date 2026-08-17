"""V3-04.0 metadata-only food-composition seed."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceSource
from app.domains.nutrition import FOOD_COMPOSITION_METADATA_SEED_VERSION, FOOD_COMPOSITION_SCHEMA_VERSION
from app.domains.nutrition.models import FoodCompositionDataset
from app.domains.reference import SeedVersionRecord

FOOD_COMPOSITION_SEED_DOMAIN = "nutrition_food_composition"
FOOD_COMPOSITION_SEED_NOTE = "V3-04.0 IFCT metadata-only composition catalogue"
IFCT_DATASET_IDENTIFIER = "icmr_nin.ifct.2017"
COMPOSITION_SEEDED_AT = datetime(2026, 8, 17, tzinfo=UTC)


async def run(session: AsyncSession) -> dict[str, int | str]:
    """Create one restricted dataset metadata row and no food/value rows."""
    source = (await session.execute(
        select(EvidenceSource).where(EvidenceSource.source_key == IFCT_DATASET_IDENTIFIER)
    )).scalar_one_or_none()
    if source is None:
        raise ValueError(f"required nutrition authority source is missing: {IFCT_DATASET_IDENTIFIER}")

    expected = {
        "dataset_key": IFCT_DATASET_IDENTIFIER, "source_id": source.id,
        "schema_version": FOOD_COMPOSITION_SCHEMA_VERSION, "dataset_version": "2017",
        "jurisdiction": "India", "rights_status": "restricted_reference",
        "import_status": "metadata_only", "status": "active",
        "rights_note": (
            "Reference metadata only. No IFCT food or nutrient rows may be imported until "
            "the dataset's usage basis has been explicitly reviewed and rights_status is "
            "changed through a separately reviewed repository change."
        ),
    }
    dataset = (await session.execute(
        select(FoodCompositionDataset).where(FoodCompositionDataset.dataset_key == IFCT_DATASET_IDENTIFIER)
    )).scalar_one_or_none()
    if dataset is None:
        session.add(FoodCompositionDataset(**expected))
        await session.flush()
    else:
        mismatch = [key for key, value in expected.items() if getattr(dataset, key) != value]
        if mismatch:
            raise ValueError(f"food-composition metadata drift: {', '.join(mismatch)}")

    audit = (await session.execute(
        select(SeedVersionRecord).where(
            SeedVersionRecord.seed_domain == FOOD_COMPOSITION_SEED_DOMAIN,
            SeedVersionRecord.seed_version == FOOD_COMPOSITION_METADATA_SEED_VERSION,
        )
    )).scalar_one_or_none()
    if audit is None:
        session.add(SeedVersionRecord(
            seed_domain=FOOD_COMPOSITION_SEED_DOMAIN,
            seed_version=FOOD_COMPOSITION_METADATA_SEED_VERSION,
            applied_at=COMPOSITION_SEEDED_AT, rows_written=1,
            note=FOOD_COMPOSITION_SEED_NOTE,
        ))
    elif audit.rows_written != 1 or audit.note != FOOD_COMPOSITION_SEED_NOTE:
        raise ValueError("food-composition metadata seed audit drift")
    return {
        "seed_version": FOOD_COMPOSITION_METADATA_SEED_VERSION,
        "datasets": 1, "food_items": 0, "nutrient_values": 0,
        "rows_written": 1,
    }
