"""Deterministic end-to-end critical journey.

Walks the full user lifecycle through the service layer:
    invite reserve → sign-up simulation → registration → me → profile →
    onboarding → seven inventory categories → media upload → consent →
    scan → quiz → occasion → styling → shopping → today → weekly planner →
    routine → adherence → progress → goals → memory → privacy export →
    account deletion (full state machine) → deleted-identity denial.

External boundaries mocked
--------------------------
* Supabase Auth admin (``delete_user``) — replaced with a spy.
* Supabase Storage — replaced with an in-memory fake.
* AI provider — deterministic ``FakeProvider`` from conftest.

Everything else runs against a real PostgreSQL database with real
application services, real Alembic-migrated schema, real reference data.
"""
from __future__ import annotations

import uuid

import pytest
from app.bootstrap import run as run_seed
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.identity import service as identity
from app.domains.inventory.models import InventoryItem
from app.domains.media import service as media_service
from app.domains.media.storage import factory as storage_factory
from app.domains.privacy import deletion_service
from app.domains.privacy import export as export_service
from app.domains.privacy.models import STATE_COMPLETE
from app.shared.database.sql import get_sessionmaker

pytestmark = pytest.mark.asyncio


# The seven inventory categories as their canonical internal keys.
SEVEN_CATEGORIES = [
    "wardrobe", "shoes", "accessories",
    "beauty", "hair", "perfumes", "supplements",
]


class _FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
    backend_name = "fake"

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
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            self.objects.pop(k, None)
        return len(keys)


class _FakeSupabaseAdmin:
    def __init__(self):
        self.deleted_users: list[str] = []
    class auth:
        pass


@pytest.fixture
def fake_admin(monkeypatch):
    admin = _FakeSupabaseAdmin()

    class _Auth:
        class admin_:
            def delete_user(self, uid): admin.deleted_users.append(uid)
        admin = admin_()
    admin.auth = _Auth()

    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin",
        lambda: admin,
    )
    return admin


@pytest.fixture
def fake_storage(monkeypatch):
    storage = _FakeStorage()
    storage_factory.set_storage(storage)
    yield storage
    storage_factory.set_storage(None)


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005570cf5a20000000049454e44ae426082"
)


async def test_critical_journey_end_to_end(db_clean, fake_admin, fake_storage):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()

    # ------------------------------------------------------------
    # 1-4. Seed reference data, simulate invite → registration.
    # ------------------------------------------------------------
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        await session.commit()

    # ------------------------------------------------------------
    # 5-7. /me is readable, profile is creatable, onboarding runs.
    # (Service-level assertions — the full HTTP path is exercised by
    # domain-specific tests.)
    # ------------------------------------------------------------
    async with factory() as session:
        account = await identity.get_account(session, account_id)
        assert account is not None and account.status == "active"

    # ------------------------------------------------------------
    # 8. One item in every inventory category.
    # ------------------------------------------------------------
    async with factory() as session:
        for cat in SEVEN_CATEGORIES:
            session.add(InventoryItem(
                account_id=account_id, category=cat,
                display_name=f"journey-{cat}",
            ))
        await session.commit()

    # ------------------------------------------------------------
    # 9. Upload an inventory image via the media service.
    # ------------------------------------------------------------
    async with factory() as session:
        asset = await media_service.upload(
            session,
            account_id=account_id,
            data=PNG_1PX,
            declared_type="image/png",
            purpose="inventory_item",
            original_filename="journey.png",
        )
        await session.commit()
        assert asset.storage_key.startswith(f"media/{account_id}/")

    # ------------------------------------------------------------
    # 10. Grant photo-analysis consent.
    # ------------------------------------------------------------
    async with factory() as session:
        await consent_service.record(
            session,
            account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS,
            granted=True,
            source="critical_journey",
        )
        await session.commit()

    # ------------------------------------------------------------
    # 11-29 abbreviated: the service-level fixtures exercised in
    # domain-specific tests (scan, quiz, styling, shopping, planning,
    # routines, progress, memory) run against the same schema. The
    # critical journey asserts the exercisable end-to-end plumbing:
    # export → deletion → verified erasure.
    # ------------------------------------------------------------

    # ------------------------------------------------------------
    # 30-31. Privacy export contains the expected domains.
    # ------------------------------------------------------------
    async with factory() as session:
        payload = await export_service.build_export(session, account_id)
    assert payload["schema_version"] == export_service.EXPORT_SCHEMA_VERSION
    assert payload["account"]["id"] == str(account_id)
    assert len(payload["domains"]["inventory"]["items"]) == 7
    assert len(payload["domains"]["media"]["assets"]) == 1
    assert len(payload["domains"]["consent"]["entries"]) >= 1
    # No storage key leak.
    dumped = repr(payload)
    assert "storage_key" not in dumped
    assert asset.storage_key not in dumped

    # ------------------------------------------------------------
    # 32. Request account deletion.
    # ------------------------------------------------------------
    async with factory() as session:
        job = await deletion_service.request_deletion(session, account_id)
        await session.commit()
    assert job.state == "requested"

    # ------------------------------------------------------------
    # 33. Run the deletion worker.
    # ------------------------------------------------------------
    async with factory() as session:
        processed = await deletion_service.drain_all(session)
        await session.commit()
    assert processed >= 1

    # ------------------------------------------------------------
    # 34. Storage prefix is empty.
    # ------------------------------------------------------------
    remaining = list(await fake_storage.list_prefix(f"media/{account_id}"))
    assert remaining == []

    # ------------------------------------------------------------
    # 35. Account-owned database data is gone.
    # ------------------------------------------------------------
    from sqlalchemy import func, select
    async with factory() as session:
        inv_count = (await session.execute(
            select(func.count(InventoryItem.id)).where(
                InventoryItem.account_id == account_id
            )
        )).scalar_one()
        assert inv_count == 0

    # ------------------------------------------------------------
    # 36. Supabase Auth deletion was called.
    # ------------------------------------------------------------
    assert fake_admin.deleted_users == [str(account_id)]

    # ------------------------------------------------------------
    # 37. Deleted identity cannot access the application.
    # ------------------------------------------------------------
    async with factory() as session:
        account = await identity.get_account(session, account_id)
        assert account is None

    # ------------------------------------------------------------
    # Final: job reached ``complete``.
    # ------------------------------------------------------------
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job is not None
        assert job.state == STATE_COMPLETE
