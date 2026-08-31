"""§1.14 — privacy export and account deletion, against real product data.

``test_privacy_export.py`` and ``test_account_deletion_state_machine.py`` cover
the registry and the state machine in isolation. This file covers the thing a
regulator or a user actually cares about: an account that has really used the
product, exported and then deleted end to end.

The account is populated through the real V2 routes (``tests/journey``), so the
rows under test are the rows the product writes.

What this protects against
--------------------------
* A new domain shipping without an export handler, so a user's data is quietly
  missing from their own export.
* Secrets, storage keys or raw image bytes riding along in an export.
* One account's export containing another account's rows.
* Deletion that leaves objects in storage, or rows in tables the account owned.
* Deletion that removes the Supabase Auth identity before the data — which
  would leave orphaned rows nobody can ever reach or delete.
* A failed stage losing the job instead of retrying it.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from app.bootstrap import run as run_seed
from app.domains.media.storage import factory as storage_factory
from app.domains.media.storage.base import StorageUnavailable, account_prefix
from app.domains.privacy import REGISTRY, Classification, deletion_service
from app.domains.privacy import export as export_service
from app.domains.privacy.models import (
    STATE_COMPLETE,
    STATE_FAILED_RETRYABLE,
    AccountDeletionJob,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth, png_bytes
from tests.journey import ok, populate_every_domain

pytestmark = pytest.mark.asyncio


class _FakeStorage:
    backend_name = "fake"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.delete_prefix_error: BaseException | None = None
        self.delete_prefix_calls = 0

    async def put(self, key, data, content_type):
        self.objects[key] = data

    async def get(self, key):
        from app.domains.media.storage.base import StorageObjectMissing

        if key not in self.objects:
            raise StorageObjectMissing(key)
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)

    async def exists(self, key):
        return key in self.objects

    async def presigned_get_url(self, key, ttl):
        return f"https://signed.example/{key}"

    async def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix):
        self.delete_prefix_calls += 1
        if self.delete_prefix_error is not None:
            error, self.delete_prefix_error = self.delete_prefix_error, None
            raise error
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)


@pytest.fixture
def storage():
    adapter = _FakeStorage()
    storage_factory.set_storage(adapter)
    yield adapter
    storage_factory.set_storage(None)


@pytest.fixture
def auth_spy(monkeypatch, storage):
    """Spy on Supabase Auth deletion, recording the state it ran against."""
    calls: list[dict[str, Any]] = []

    class _AuthAdmin:
        @staticmethod
        def delete_user(uid):
            calls.append({"uid": str(uid), "objects_remaining": len(storage.objects)})

    class _Auth:
        admin = _AuthAdmin()

    class _Admin:
        auth = _Auth()

    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin", lambda: _Admin
    )
    return calls


async def _seeded_account(client, registered_supabase_user):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()
    token, account_id = await registered_supabase_user()
    created = await populate_every_domain(client, token)
    return token, account_id, created


# ---------------------------------------------------------------------------
# Export completeness
# ---------------------------------------------------------------------------

async def test_export_carries_a_record_from_every_active_domain(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    token, account_id, _ = await _seeded_account(app_client, registered_supabase_user)

    export = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token)))

    domains = export["domains"]
    assert set(export_service.DOMAIN_HANDLERS) <= set(domains)
    for name in export_service.DOMAIN_HANDLERS:
        assert domains[name] != {"error": "domain_export_failed"}, f"{name} failed to export"

    # Each of these domains was actually written to by the journey, so an empty
    # payload here means the export is not reading what the product wrote.
    assert domains["identity"]["id"] == str(account_id)
    assert domains["profile"]["attributes"]
    assert domains["consent"]["entries"]
    assert domains["inventory"]["items"]
    assert domains["media"]["assets"]
    assert domains["scans"]["scans"]
    assert domains["quiz_and_styling"]["quiz_submissions"]
    assert domains["shopping"]["candidates"]
    assert domains["planning"]["daily_plans"]
    assert domains["routines"]["routines"]
    assert domains["progress_and_memory"]["memory_facts"]


async def test_routines_export_includes_all_owned_records_and_is_account_scoped(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    """The routines domain exports every included routines-model table."""
    token_a, account_a, created = await _seeded_account(app_client, registered_supabase_user)
    beauty_item_id = created["inventory"]["beauty"][0]
    supplement_item_id = created["inventory"]["supplements"][0]
    routine_row = created["routines"]["routines"][0]
    step_row = routine_row["steps"][0]
    beauty_item_uuid = uuid.UUID(beauty_item_id)
    supplement_item_uuid = uuid.UUID(supplement_item_id)
    routine_uuid = uuid.UUID(routine_row["id"])
    step_uuid = uuid.UUID(step_row["id"])

    observation = ok(await app_client.post(
        "/api/v2/routines/observations",
        headers=auth(token_a),
        json={
            "observed_on": "2026-08-14",
            "area": "skin",
            "note": "A-owned observation must remain verbatim in the export.",
            "item_id": beauty_item_id,
        },
    ))
    ok(await app_client.patch(
        "/api/v2/nutrition/preferences",
        headers=auth(token_a),
        json={
            "diet": "vegetarian",
            "avoid_foods": ["mushrooms"],
            "focus_nutrients": ["protein"],
            "enabled": True,
        },
    ))
    ok(await app_client.patch(
        "/api/v2/nutrition/hydration",
        headers=auth(token_a),
        json={
            "enabled": True,
            "remind_in_hot_weather_only": False,
            "note": "A-owned hydration preference.",
        },
    ))

    from app.domains.routines.models import (
        ProductExpiryEvent,
        ProductIngredient,
        RoutineAdherence,
        SupplementSafetyFlag,
    )
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import select

    factory = get_sessionmaker()
    async with factory() as session:
        existing_keys = set((await session.execute(
            select(ProductIngredient.ingredient_key).where(
                ProductIngredient.account_id == account_a,
                ProductIngredient.item_id == beauty_item_uuid,
            )
        )).scalars().all())
        ingredient_key = next(
            key for key in ("fragrance", "retinol", "vitamin_c", "glycerin")
            if key not in existing_keys
        )
        session.add(ProductIngredient(
            account_id=account_a,
            item_id=beauty_item_uuid,
            ingredient_key=ingredient_key,
            matched_text="A-owned ingredient",
            position=1,
            confidence=1.0,
            source="user_declared",
            needs_confirmation=False,
        ))
        session.add(ProductExpiryEvent(
            account_id=account_a,
            item_id=beauty_item_uuid,
            rule_id="test.expiry",
            status="expiring_soon",
            effective_expiry=date(2026, 8, 20),
            days_to_expiry=5,
            detail="A-owned expiry assessment.",
        ))
        session.add(SupplementSafetyFlag(
            account_id=account_a,
            item_id=supplement_item_uuid,
            flag="test_flag",
            message="A-owned supplement safety flag.",
        ))
        session.add(RoutineAdherence(
            account_id=account_a,
            routine_id=routine_uuid,
            slot=step_row["slot"],
            step_id=step_uuid,
            done_on=date(2026, 2, 16),
            completed=True,
            note="A-owned adherence record.",
        ))
        await session.commit()

    token_b, account_b = await registered_supabase_user()
    observation_b = ok(await app_client.post(
        "/api/v2/routines/observations",
        headers=auth(token_b),
        json={
            "observed_on": "2026-08-14",
            "area": "hair",
            "note": "B-owned observation must never appear in A's export.",
        },
    ))

    export_a = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token_a)))
    routines = export_a["domains"]["routines"]

    assert routines["routines"]
    assert routines["steps"]
    assert routines["adherence"]
    assert routines["recommendation_runs"]

    exported_observation = next(row for row in routines["observations"] if row["id"] == observation["id"])
    assert exported_observation["note"] == "A-owned observation must remain verbatim in the export."
    assert exported_observation["area"] == "skin"
    assert exported_observation["observed_on"] == "2026-08-14"
    assert exported_observation["item_id"] == beauty_item_id
    assert exported_observation["routed_to_professional"] is False
    assert exported_observation["created_at"]
    assert exported_observation["updated_at"]

    assert any(row["item_id"] == beauty_item_id and row["ingredient_key"] == ingredient_key
               for row in routines["product_ingredients"])
    assert any(row["item_id"] == beauty_item_id and row["detail"] == "A-owned expiry assessment."
               for row in routines["product_expiry_events"])
    assert any(row["item_id"] == supplement_item_id and row["message"] == "A-owned supplement safety flag."
               for row in routines["supplement_safety_flags"])
    assert any(row["diet"] == "vegetarian" and row["avoid_foods"] == ["mushrooms"]
               for row in routines["nutrition_preferences"])
    assert any(row["enabled"] is True and row["note"] == "A-owned hydration preference."
               for row in routines["hydration_preferences"])

    assert observation_b["id"] not in export_a.__repr__()
    assert "B-owned observation must never appear in A's export." not in export_a.__repr__()
    assert str(account_b) not in export_a.__repr__()


async def test_export_covers_all_seven_inventory_categories(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    token, _, _ = await _seeded_account(app_client, registered_supabase_user)

    export = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token)))

    categories = {row["category"] for row in export["domains"]["inventory"]["items"]}
    assert categories == {
        "wardrobe", "shoes", "accessories", "beauty", "hair", "perfumes", "supplements",
    }


async def test_export_declares_what_it_deliberately_leaves_out(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    """An export that silently omits tables is not an honest export: the
    classification of every table is published alongside the data."""
    token, _, _ = await _seeded_account(app_client, registered_supabase_user)

    export = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token)))

    summary = export["registry_summary"]
    published = set(
        summary["included_tables"] + summary["not_user_owned"]
        + summary["secret_excluded"] + summary["operational_only"]
        + summary["legally_retained"]
    )
    assert published == set(REGISTRY)
    assert set(summary["included_tables"]) == {
        name for name, kind in REGISTRY.items() if kind == Classification.INCLUDED
    }


async def test_export_never_carries_secrets_or_raw_images(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    token, _, _ = await _seeded_account(app_client, registered_supabase_user)

    resp = await app_client.get("/api/v2/privacy/export", headers=auth(token))
    body = resp.text

    for secret in (
        "storage_key", "storage_backend", "image_base64", "service_role",
        "SUPABASE_SERVICE_ROLE_KEY", "anon_key", "jwt_secret", "Bearer ",
        "password", "access_token", "refresh_token",
    ):
        assert secret not in body, f"'{secret}' must never appear in an export"


async def test_export_is_scoped_to_the_caller(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    token_a, account_a, _ = await _seeded_account(app_client, registered_supabase_user)
    token_b, account_b = await registered_supabase_user()
    ok(await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token_b),
        json={"category": "wardrobe", "display_name": "Account B Only Jacket"},
    ))

    export_a = await app_client.get("/api/v2/privacy/export", headers=auth(token_a))

    assert export_a.json()["account"]["id"] == str(account_a)
    assert str(account_b) not in export_a.text
    assert "Account B Only Jacket" not in export_a.text


async def test_memory_history_is_exported_including_deletions(
    app_client, db_clean, registered_supabase_user, fake_provider, storage
):
    """A user is entitled to see what was remembered, corrected and deleted —
    an export that hides the tombstones hides the interesting part."""
    token, _, created = await _seeded_account(app_client, registered_supabase_user)
    fact_id = created["memory"]["learned"]["id"]

    ok(await app_client.patch(
        f"/api/v2/memory/{fact_id}",
        headers=auth(token),
        json={"fact": "Prefers deep teal in the evening."},
    ))
    ok(await app_client.delete(f"/api/v2/memory/{fact_id}", headers=auth(token)))

    export = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token)))
    memory = export["domains"]["progress_and_memory"]

    exported_facts = {row["id"]: row for row in memory["memory_facts"]}
    assert fact_id in exported_facts, "a deleted fact must still appear in the export"
    assert exported_facts[fact_id]["deletion_state"] != "active"
    assert memory["memory_revisions"], "the correction history must be exported"


# ---------------------------------------------------------------------------
# Deletion against real data
# ---------------------------------------------------------------------------

async def test_deletion_removes_every_account_owned_row_and_object(
    app_client, db_clean, registered_supabase_user, fake_provider, storage, auth_spy
):
    token, account_id, _ = await _seeded_account(app_client, registered_supabase_user)
    assert storage.objects, "the journey must have stored at least one object"

    requested = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert requested.status_code == 202, requested.text

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    assert storage.objects == {}
    assert [call["uid"] for call in auth_spy] == [str(account_id)]

    # Every INCLUDED table that has an account_id must be empty for this
    # account. This is the assertion that catches a new domain being added
    # without a cascade.
    from app.shared.database.registry import Base

    async with factory() as session:
        for table in Base.metadata.sorted_tables:
            if table.name == AccountDeletionJob.__tablename__:
                continue
            if "account_id" not in table.c:
                continue
            remaining = (await session.execute(
                select(func.count()).select_from(table).where(
                    table.c.account_id == account_id
                )
            )).scalar_one()
            assert remaining == 0, f"{table.name} still holds rows for the deleted account"


async def test_supabase_auth_is_deleted_only_after_storage_and_database(
    app_client, db_clean, registered_supabase_user, fake_provider, storage, auth_spy
):
    """Ordering is the whole point: delete the identity first and any row left
    behind becomes unreachable and undeletable."""
    token, account_id, _ = await _seeded_account(app_client, registered_supabase_user)

    await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    assert len(auth_spy) == 1
    assert auth_spy[0]["objects_remaining"] == 0, (
        "Supabase Auth was deleted while objects were still in storage"
    )

    async with factory() as session:
        from app.domains.identity.models import Account

        account = await session.get(Account, account_id)
    assert account is None, "the account row must be gone before auth deletion completes"


async def test_only_the_minimal_deletion_record_survives(
    app_client, db_clean, registered_supabase_user, fake_provider, storage, auth_spy
):
    token, account_id, _ = await _seeded_account(app_client, registered_supabase_user)
    await app_client.delete("/api/v2/privacy/account", headers=auth(token))

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        jobs = (await session.execute(
            select(AccountDeletionJob).where(AccountDeletionJob.account_id == account_id)
        )).scalars().all()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.state == STATE_COMPLETE
    assert job.completed_at is not None
    # The surviving record proves the deletion happened. It must not become a
    # back door to the data that was deleted.
    assert REGISTRY[AccountDeletionJob.__tablename__] == Classification.LEGALLY_RETAINED


async def test_storage_failure_retries_rather_than_losing_the_job(
    app_client, db_clean, registered_supabase_user, fake_provider, storage, auth_spy
):
    """A provider blip must not silently abandon a deletion the user asked
    for, and must not skip ahead to deleting the identity."""
    token, account_id, _ = await _seeded_account(app_client, registered_supabase_user)
    storage.delete_prefix_error = StorageUnavailable("provider down")

    await app_client.delete("/api/v2/privacy/account", headers=auth(token))

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.process_once(session)
        await session.commit()

    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job is not None
        assert job.state == STATE_FAILED_RETRYABLE
        assert job.last_error_code == "storage_unavailable"
        assert job.next_retry_at is not None
    assert auth_spy == [], "auth must not be touched while storage is unfinished"
    assert storage.objects, "nothing may be reported as deleted when the stage failed"

    # The retry clears the fault and finishes the job.
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        job.next_retry_at = None
        await session.commit()

    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job.state == STATE_COMPLETE
    assert storage.objects == {}
    assert [call["uid"] for call in auth_spy] == [str(account_id)]


async def test_deleted_account_cannot_use_the_product(
    app_client, db_clean, registered_supabase_user, fake_provider, storage, auth_spy
):
    token, _, _ = await _seeded_account(app_client, registered_supabase_user)
    await app_client.delete("/api/v2/privacy/account", headers=auth(token))

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    for path in ("/api/v2/me", "/api/v2/inventory/items", "/api/v2/privacy/export"):
        resp = await app_client.get(path, headers=auth(token))
        assert resp.status_code in (401, 403), f"{path} answered {resp.status_code}"


async def test_one_accounts_deletion_leaves_another_untouched(
    app_client, db_clean, registered_supabase_user, fake_provider, storage, auth_spy
):
    token_a, account_a, _ = await _seeded_account(app_client, registered_supabase_user)
    token_b, account_b = await registered_supabase_user()
    ok(await app_client.post(
        "/api/v2/media/upload",
        headers=auth(token_b),
        files={"file": ("b.png", png_bytes(), "image/png")},
    ))
    b_objects = {k for k in storage.objects if k.startswith(account_prefix(account_b))}
    assert b_objects

    await app_client.delete("/api/v2/privacy/account", headers=auth(token_a))
    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    assert set(storage.objects) == b_objects, "another account's objects were deleted"
    me_b = await app_client.get("/api/v2/me", headers=auth(token_b))
    assert me_b.status_code == 200
    assert me_b.json()["account"]["id"] == str(account_b)
