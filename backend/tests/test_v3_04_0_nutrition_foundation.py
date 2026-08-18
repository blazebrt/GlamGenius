"""V3-04.0 authority, rights, and empty-composition foundation tests."""
from __future__ import annotations

import importlib
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from app.bootstrap import run as run_seed
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.nutrition_seed import (
    DIETARY_GUIDELINES_SOURCE_KEY,
    IFCT_SOURCE_IDENTIFIER,
    RDA_EAR_SOURCE_KEY,
)
from app.domains.evidence.nutrition_seed import run as run_nutrition_authority_seed
from app.domains.identity import service as identity
from app.domains.nutrition import (
    FOOD_COMPOSITION_METADATA_SEED_VERSION,
    FOOD_COMPOSITION_SCHEMA_VERSION,
    NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
)
from app.domains.nutrition import service as nutrition_service
from app.domains.nutrition.models import FoodCompositionDataset, FoodNutrientValue, FoodReferenceItem
from app.domains.nutrition.rights import (
    FoodCompositionImportNotAllowed,
    assert_composition_import_allowed,
    composition_import_allowed,
)
from app.domains.nutrition.seed import run as run_food_composition_seed
from app.domains.reference import SeedVersionRecord
from app.domains.routines.models import HydrationPreference, NutritionPreference
from app.shared.database.registry import Base
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.asyncio

SOURCE_KEYS = {IFCT_SOURCE_IDENTIFIER, DIETARY_GUIDELINES_SOURCE_KEY, RDA_EAR_SOURCE_KEY}


async def _seed(session):
    return await run_seed(session)


async def test_bootstrap_seeds_exact_authority_and_empty_composition(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        first = await _seed(session)
        second = await _seed(session)
        sources = (await session.execute(
            select(EvidenceSource).where(EvidenceSource.source_key.in_(SOURCE_KEYS))
        )).scalars().all()
        datasets = (await session.execute(select(FoodCompositionDataset))).scalars().all()
        items = (await session.execute(select(FoodReferenceItem))).scalars().all()
        values = (await session.execute(select(FoodNutrientValue))).scalars().all()
        audits = (await session.execute(
            select(SeedVersionRecord).where(
                SeedVersionRecord.seed_version.in_((
                    NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
                    FOOD_COMPOSITION_METADATA_SEED_VERSION,
                ))
            )
        )).scalars().all()
    assert first["nutrition_authority_evidence"] == {
        "seed_version": NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
        "sources": 3, "claims": 0, "claim_source_links": 0, "rule_links": 0, "rows_written": 3,
    }
    assert second["nutrition_authority_evidence"] == first["nutrition_authority_evidence"]
    assert second["food_composition"] == {
        "seed_version": FOOD_COMPOSITION_METADATA_SEED_VERSION,
        "datasets": 1, "food_items": 0, "nutrient_values": 0, "rows_written": 1,
    }
    assert len(sources) == 3
    assert len(datasets) == 1
    assert items == [] and values == []
    assert {(row.seed_domain, row.rows_written) for row in audits} == {
        ("evidence_nutrition_authority", 3), ("nutrition_food_composition", 1),
    }


async def test_nutrition_authority_seed_alone_creates_no_claims_or_links(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        result = await run_nutrition_authority_seed(session)
        sources = (await session.execute(
            select(EvidenceSource).where(EvidenceSource.source_key.in_(SOURCE_KEYS))
        )).scalars().all()
        claim_count = await session.scalar(select(func.count()).select_from(EvidenceClaim))
        claim_source_count = await session.scalar(select(func.count()).select_from(EvidenceClaimSource))
        rule_link_count = await session.scalar(select(func.count()).select_from(RuleEvidenceLink))

    assert result == {
        "seed_version": NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
        "sources": 3, "claims": 0, "claim_source_links": 0,
        "rule_links": 0, "rows_written": 3,
    }
    assert {row.source_key for row in sources} == SOURCE_KEYS
    assert claim_count == claim_source_count == rule_link_count == 0


async def test_authority_source_metadata_is_exact_and_ifct_is_restricted(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _seed(session)
        rows = {
            row.source_key: row for row in (await session.execute(
                select(EvidenceSource).where(EvidenceSource.source_key.in_(SOURCE_KEYS))
            )).scalars().all()
        }
        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
    assert set(rows) == SOURCE_KEYS
    assert all(row.publisher == "ICMR-National Institute of Nutrition" for row in rows.values())
    assert all(row.jurisdiction == "India" for row in rows.values())
    assert rows[IFCT_SOURCE_IDENTIFIER].source_type == "government_reference"
    assert rows[DIETARY_GUIDELINES_SOURCE_KEY].source_type == "official_guideline"
    assert rows[RDA_EAR_SOURCE_KEY].source_type == "government_reference"
    assert "metadata/provenance only" in rows[IFCT_SOURCE_IDENTIFIER].license_or_use_note.lower()
    assert "permission" in rows[IFCT_SOURCE_IDENTIFIER].license_or_use_note.lower()
    assert rows[IFCT_SOURCE_IDENTIFIER].accessed_at.isoformat() == "2026-08-17T00:00:00+00:00"
    assert rows[IFCT_SOURCE_IDENTIFIER].last_reviewed_at.isoformat() == "2026-08-17T00:00:00+00:00"
    assert dataset.dataset_key == IFCT_SOURCE_IDENTIFIER
    assert dataset.source_id == rows[IFCT_SOURCE_IDENTIFIER].id
    assert dataset.schema_version == FOOD_COMPOSITION_SCHEMA_VERSION == "v3-04.0"
    assert dataset.dataset_version == "2017"
    assert dataset.jurisdiction == "India"
    assert dataset.rights_status == "restricted_reference"
    assert dataset.import_status == "metadata_only"
    assert dataset.status == "active"


async def test_rights_gate_is_explicit_and_fail_closed():
    def dataset(rights: str, status: str) -> FoodCompositionDataset:
        return FoodCompositionDataset(dataset_key=f"test-{rights}-{status}", rights_status=rights, import_status=status)

    assert composition_import_allowed(dataset("restricted_reference", "metadata_only")) is False
    assert composition_import_allowed(dataset("permission_granted", "metadata_only")) is False
    assert composition_import_allowed(dataset("permission_granted", "ready_for_import")) is True
    assert composition_import_allowed(dataset("open_licensed", "ready_for_import")) is True
    assert composition_import_allowed(dataset("open_licensed", "imported")) is True
    assert composition_import_allowed(dataset("permission_granted", "retired")) is False
    with pytest.raises(FoodCompositionImportNotAllowed):
        assert_composition_import_allowed(dataset("restricted_reference", "metadata_only"))


async def test_missing_ifct_source_fails_closed(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValueError, match="required nutrition authority source is missing"):
            await run_food_composition_seed(session)


async def test_database_rejects_restricted_import_statuses(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _seed(session)
        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
        dataset.import_status = "ready_for_import"
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
        dataset.import_status = "imported"
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_model_identities_and_numeric_constraint_are_database_enforced(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _seed(session)
        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
        item = FoodReferenceItem(dataset_id=dataset.id, source_food_code="TEST-1", canonical_name="Test food")
        session.add(item)
        await session.commit()
        session.add(FoodReferenceItem(dataset_id=dataset.id, source_food_code="TEST-1", canonical_name="Duplicate"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        item = (await session.execute(select(FoodReferenceItem))).scalar_one()
        session.add(FoodNutrientValue(food_id=item.id, nutrient_key="protein", amount=Decimal("1.25"), unit="g", basis="per_100g"))
        await session.commit()
        item = (await session.execute(select(FoodReferenceItem))).scalar_one()
        session.add(FoodNutrientValue(food_id=item.id, nutrient_key="protein", amount=Decimal("2"), unit="g", basis="per_100g"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        item = (await session.execute(select(FoodReferenceItem))).scalar_one()
        session.add(FoodNutrientValue(food_id=item.id, nutrient_key="iron", amount=Decimal("-0.1"), unit="mg", basis="per_100g"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_v3_04_seed_result_stays_zero_after_future_composition_rows(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _seed(session)
        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
        item = FoodReferenceItem(dataset_id=dataset.id, source_food_code="FUTURE-1", canonical_name="Future test food")
        session.add(item)
        await session.flush()
        session.add(FoodNutrientValue(food_id=item.id, nutrient_key="protein", amount=Decimal("3"), unit="g", basis="per_100g"))
        await session.commit()

        result = await run_food_composition_seed(session)
    assert result == {
        "seed_version": FOOD_COMPOSITION_METADATA_SEED_VERSION,
        "datasets": 1, "food_items": 0, "nutrient_values": 0, "rows_written": 1,
    }


async def test_foreign_key_deletes_are_restricted_and_values_cascade(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _seed(session)
        source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == IFCT_SOURCE_IDENTIFIER))).scalar_one()
        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
        await session.delete(source)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        dataset = (await session.execute(select(FoodCompositionDataset))).scalar_one()
        item = FoodReferenceItem(dataset_id=dataset.id, source_food_code="CASCADE-1", canonical_name="Cascade food")
        session.add(item)
        await session.flush()
        value = FoodNutrientValue(food_id=item.id, nutrient_key="protein", amount=Decimal("1"), unit="g", basis="per_100g")
        session.add(value)
        await session.flush()
        value_id = value.id
        await session.commit()
        await session.delete(dataset)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.delete(item)
        await session.commit()
        async with factory() as check:
            assert await check.scalar(select(func.count()).select_from(FoodNutrientValue).where(FoodNutrientValue.id == value_id)) == 0


async def test_new_reference_tables_are_global_and_no_food_dump_is_committed():
    forbidden = {"account_id", "user_id", "email", "profile_id", "ai_run_id"}
    for name in ("food_composition_datasets", "food_reference_items", "food_nutrient_values"):
        assert forbidden.isdisjoint(Base.metadata.tables[name].columns.keys())
    nutrition_dir = Path(__file__).parents[1] / "app" / "domains" / "nutrition"
    assert not any(path.suffix.lower() in {".csv", ".json", ".sql"} for path in nutrition_dir.rglob("*"))


async def test_legacy_nutrition_module_is_removed_and_first_class_path_is_authority(db_clean, app_client, fake_supabase_user):
    source = Path(__file__).parents[1] / "app" / "domains" / "routines" / "nutrition.py"
    assert not source.exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.domains.routines.nutrition")
    assert callable(nutrition_service.nutrition_suggestions)

    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        session.add(NutritionPreference(account_id=account_id))
        session.add(HydrationPreference(account_id=account_id))
        await session.commit()
    token, _ = fake_supabase_user(user_id=account_id)
    response = await app_client.get(
        "/api/v2/nutrition/appearance-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["suggestions"] == []
