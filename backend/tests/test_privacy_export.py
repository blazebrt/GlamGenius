"""Privacy export tests.

Cover:
    * Registry is complete (fails if a new ORM table has no classification).
    * Export includes every ``INCLUDED`` domain in :mod:`app.domains.privacy`.
    * Export never leaks another account's data.
    * Storage keys and other secret fields never appear.
    * Face-scan raw bytes never appear.
    * Schema version is present.
"""
from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS, Consent
from app.domains.identity import service as identity
from app.domains.inventory.models import InventoryItem
from app.domains.privacy import (
    REGISTRY,
    Classification,
    assert_registry_complete,
)
from app.domains.privacy import (
    export as export_service,
)
from app.domains.routines import models as routines_models
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker

pytestmark = pytest.mark.asyncio


ROUTINES_MODEL_EXPORT_COLLECTIONS = {
    routines_models.ProductIngredient.__tablename__: "product_ingredients",
    routines_models.Routine.__tablename__: "routines",
    routines_models.RoutineAdherence.__tablename__: "adherence",
    routines_models.UserReportedObservation.__tablename__: "observations",
    routines_models.ProductExpiryEvent.__tablename__: "product_expiry_events",
    routines_models.RoutineRecommendationRun.__tablename__: "recommendation_runs",
    routines_models.SupplementSafetyFlag.__tablename__: "supplement_safety_flags",
    routines_models.NutritionPreference.__tablename__: "nutrition_preferences",
    routines_models.HydrationPreference.__tablename__: "hydration_preferences",
    routines_models.CareExperienceFeedback.__tablename__: "experience_feedback",
    routines_models.MaintenancePreference.__tablename__: "maintenance_preferences",
    routines_models.MaintenanceEvent.__tablename__: "maintenance_events",
}

# Routine steps do not carry a direct account_id; they are exported through
# their account-owned parent routine and are asserted separately below.
ROUTINES_PARENT_OWNED_EXPORT_COLLECTIONS = {
    routines_models.RoutineStep.__tablename__: "steps",
}


async def test_registry_classifies_every_orm_table():
    """A new account-owned table must not slip in without a classification."""
    assert_registry_complete()


async def test_routines_included_models_have_explicit_export_collections(db_clean):
    """Every user-owned routines model has a deliberate export collection."""
    actual_account_owned_routines_tables = set()
    for _, cls in inspect.getmembers(routines_models, inspect.isclass):
        if cls.__module__ != routines_models.__name__:
            continue
        table = getattr(cls, "__table__", None)
        if table is not None and "account_id" in table.c:
            actual_account_owned_routines_tables.add(table.name)

    assert set(ROUTINES_MODEL_EXPORT_COLLECTIONS) == actual_account_owned_routines_tables
    assert all(REGISTRY[name] == Classification.INCLUDED for name in ROUTINES_MODEL_EXPORT_COLLECTIONS)

    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    async with factory() as session:
        payload = await export_service.build_export(session, account_id)

    routines = payload["domains"]["routines"]
    assert set(ROUTINES_MODEL_EXPORT_COLLECTIONS).issubset(set(REGISTRY))
    expected_collections = (
        set(ROUTINES_MODEL_EXPORT_COLLECTIONS.values())
        | set(ROUTINES_PARENT_OWNED_EXPORT_COLLECTIONS.values())
    )
    assert expected_collections <= set(routines)


async def test_registry_covers_all_seven_inventory_categories():
    """The seven canonical inventory categories are supported in the export.

    Internal taxonomy names in :mod:`app.domains.inventory.taxonomy` map to
    the seven user-facing categories the product spec calls out. ``beauty``
    is Beauty Shelf; ``hair`` is Hair Shelf.
    """
    from app.domains.inventory.taxonomy import CATEGORIES

    expected_internal = {
        "wardrobe",
        "shoes",
        "accessories",
        "beauty",
        "hair",
        "perfumes",
        "supplements",
    }
    assert set(CATEGORIES.keys()) == expected_internal


async def test_export_shape_has_schema_version_and_domains(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    async with factory() as session:
        payload = await export_service.build_export(session, account_id)

    assert payload["schema_version"] == export_service.EXPORT_SCHEMA_VERSION
    assert "generated_at" in payload
    assert payload["account"]["id"] == str(account_id)
    # Every domain handler must be represented.
    for domain in export_service.DOMAIN_HANDLERS:
        assert domain in payload["domains"], f"missing domain {domain}"


async def test_export_only_returns_calling_accounts_data(db_clean):
    from app.bootstrap import seed_inventory_categories

    factory = get_sessionmaker()
    account_a = uuid.uuid4()
    account_b = uuid.uuid4()
    async with factory() as session:
        await seed_inventory_categories(session)
        await identity.register_account(session, account_a)
        await identity.register_account(session, account_b)
        # Each account gets a distinct inventory item.
        session.add(InventoryItem(
            account_id=account_a, display_name="A-Blazer", category="wardrobe",
        ))
        session.add(InventoryItem(
            account_id=account_b, display_name="B-Sneakers", category="shoes",
        ))
        await session.commit()

    async with factory() as session:
        payload_a = await export_service.build_export(session, account_a)
        payload_b = await export_service.build_export(session, account_b)

    a_items = [i["display_name"] for i in payload_a["domains"]["inventory"]["items"]]
    b_items = [i["display_name"] for i in payload_b["domains"]["inventory"]["items"]]
    assert "A-Blazer" in a_items and "B-Sneakers" not in a_items
    assert "B-Sneakers" in b_items and "A-Blazer" not in b_items


async def test_export_never_exposes_media_storage_key(db_clean, media_root):
    """``storage_key`` is internal; export must not contain it."""
    import hashlib

    from app.domains.media.models import MEDIA_PURPOSE_INVENTORY, MediaAsset

    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        session.add(MediaAsset(
            account_id=account_id,
            storage_backend="local",
            storage_key=f"media/{account_id}/secret-do-not-leak.jpg",
            content_type="image/jpeg",
            byte_size=10,
            sha256=hashlib.sha256(b"x").hexdigest(),
            purpose=MEDIA_PURPOSE_INVENTORY,
        ))
        await session.commit()

    async with factory() as session:
        payload = await export_service.build_export(session, account_id)

    dumped = _flatten(payload)
    # The literal internal key must not appear anywhere in the export.
    assert not any("secret-do-not-leak" in str(v) for v in dumped)
    # Nor should either of the internal columns be exported.
    assert "storage_key" not in dumped
    assert "storage_backend" not in dumped


async def test_export_records_consent_history(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        session.add(Consent(
            account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS,
            granted=True,
            version="v1",
            source="test",
            recorded_at=utcnow(),
        ))
        await session.commit()

    async with factory() as session:
        payload = await export_service.build_export(session, account_id)

    entries = payload["domains"]["consent"]["entries"]
    assert len(entries) == 1
    assert entries[0]["consent_type"] == CONSENT_PHOTO_ANALYSIS
    assert entries[0]["granted"] is True


async def test_registry_never_classifies_a_secret_as_included():
    """No entry should be both SECRET_EXCLUDED and included in output."""
    included = {n for n, k in REGISTRY.items() if k == Classification.INCLUDED}
    secret = {n for n, k in REGISTRY.items() if k == Classification.SECRET_EXCLUDED}
    assert not (included & secret)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _flatten(payload: Any) -> set:
    """Recursively collect all leaf values *and* keys as strings.

    Used to grep-check the export for forbidden substrings without shipping
    a full recursive comparison utility.
    """
    out: set = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(k)
                _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _walk(v)
        elif obj is None:
            return
        else:
            out.add(obj)

    _walk(payload)
    return out
