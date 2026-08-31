"""Scanning a packaged product: known, unknown, and offline.

The acceptance criteria are behaviours a person experiences, so these are
written against the API a phone actually calls.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.domains.ai_gateway.gateway import AIResult
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.product import service
from app.domains.product.confidence import CONFIDENCE_LEVELS, ProductConfidence
from app.domains.product.extraction import SYSTEM as LABEL_SYSTEM
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import ProductRecord, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth, png_bytes

KNOWN = "8901058000191"
UNKNOWN = "8909999999999"


@pytest_asyncio.fixture
async def off_clean():
    """Store A has its own cleanup — see the ODbL wall."""
    from sqlalchemy import text

    await create_off_schema()
    async with get_off_engine().begin() as conn:
        names = ", ".join(
            f'"{t.schema}"."{t.name}"' for t in reversed(OffBase.metadata.sorted_tables)
        )
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def device(app_client):
    """A phone that has just been launched for the first time."""
    response = await app_client.post(
        "/api/v2/scan/device",
        json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


def _fake_ai_result(data):
    """A gateway result without a provider call. Same shape the route unpacks."""
    return AIResult(
        data=data, run_id=uuid.uuid4(), provider="test", model="test-model",
        prompt_version="scan-label.v1", schema_version="scan-label.v1",
        confidence=data.confidence, latency_ms=12, estimated_cost_usd=None,
    )


async def _seed_off_product(barcode: str, **fields):
    """Seed Store A the way the cache writes it, ``fetched_at`` included.

    Every record the application caches carries the time it was fetched, and
    the freshness check reads it, so a fixture without one is not a cached
    record — it is an undated import.
    """
    fields.setdefault("fetched_at", datetime.now(UTC))
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(barcode=barcode, **fields))
        await session.commit()


# ---------------------------------------------------------------------------
# No account required
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_brand_new_phone_can_scan_without_an_account(db_clean, off_clean, app_client):
    """The acceptance criterion: camera to result, nothing set up."""
    registered = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "ios"},
    )
    assert registered.status_code == 201
    token = registered.json()["token"]
    assert token and len(token) > 20

    result = await app_client.get(
        f"/api/v2/scan/lookup/{UNKNOWN}", headers={"X-Device-Token": token},
    )
    assert result.status_code == 200, result.text


@pytest.mark.asyncio
async def test_the_device_token_reaches_product_data_and_nothing_else(db_clean, app_client, device):
    """An anonymous device must not be a way into anybody's account."""
    for path in ("/api/v2/me", "/api/v2/inventory/items", "/api/v2/today"):
        response = await app_client.get(path, headers=device)
        assert response.status_code in (401, 403), f"{path} accepted a device token"


@pytest.mark.asyncio
async def test_an_unregistered_device_is_refused(db_clean, app_client):
    response = await app_client.get(
        f"/api/v2/scan/lookup/{KNOWN}", headers={"X-Device-Token": "not-a-real-token"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# A known barcode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_known_barcode_returns_a_product(db_clean, off_clean, app_client, device):
    await _seed_off_product(
        KNOWN, product_name="Parle-G Biscuits", brands="Parle",
        ingredients_text="wheat flour, sugar, palm oil",
        nutriments={"sugars_100g": 22.5, "salt_100g": 0.7},
    )
    response = await app_client.get(f"/api/v2/scan/lookup/{KNOWN}", headers=device)
    body = response.json()

    assert response.status_code == 200
    assert body["found"] is True
    assert body["open_food_facts"]["product_name"] == "Parle-G Biscuits"
    assert body["confidence"]["level"] in CONFIDENCE_LEVELS
    # ODbL requires the attribution wherever the data is shown.
    assert "Open Food Facts" in body["attribution"]["text"]


@pytest.mark.asyncio
async def test_a_known_barcode_answers_from_the_cache_without_the_network(
    db_clean, off_clean, app_client, device, monkeypatch,
):
    """Under three seconds means not waiting on Open Food Facts."""
    from app.domains.off import client as off_client

    async def _forbidden(barcode):
        raise AssertionError("a cached product should not have called the network")

    await _seed_off_product(KNOWN, product_name="Parle-G Biscuits")
    monkeypatch.setattr(off_client, "fetch_product", _forbidden)

    body = (await app_client.get(f"/api/v2/scan/lookup/{KNOWN}", headers=device)).json()
    assert body["found"] is True
    assert body["from_network"] is False


# ---------------------------------------------------------------------------
# An unknown barcode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unknown_barcode_offers_label_capture(db_clean, off_clean, app_client, device):
    """Not found is an answer, with a way forward — not an error screen."""
    body = (await app_client.get(f"/api/v2/scan/lookup/{UNKNOWN}", headers=device)).json()

    assert body["found"] is False
    assert body["outcome"] == service.OUTCOME_NOT_FOUND
    assert body["can_capture_label"] is True
    assert body["confidence"]["level"] == ProductConfidence.NOT_ENOUGH_INFORMATION.value
    assert "label" in body["message"].lower()


@pytest.mark.asyncio
async def test_a_product_with_no_ingredients_still_offers_label_capture(
    db_clean, off_clean, app_client, device,
):
    """Incomplete counts as unknown enough to be worth photographing."""
    await _seed_off_product(UNKNOWN, product_name="Something", ingredients_text=None)
    body = (await app_client.get(f"/api/v2/scan/lookup/{UNKNOWN}", headers=device)).json()
    assert body["found"] is True
    assert body["can_capture_label"] is True


@pytest.mark.asyncio
async def test_confirming_a_label_creates_a_record_and_raises_confidence(
    db_clean, off_clean, app_client, device,
):
    """One tap to accept, the VC-07 draft-to-confirmed shape."""
    facts = {
        "product_name": "Regional namkeen",
        "ingredients_text": "besan, edible oil, salt, spices. FSSAI Lic. No. 10012345678901",
        "nutrition_per_100g": {"energy_kcal": "520", "sugars_g": "3.1"},
    }
    response = await app_client.post(
        "/api/v2/scan/label/confirm", headers=device,
        json={"barcode": UNKNOWN, "facts": facts, "client_scan_id": uuid.uuid4().hex},
    )
    body = response.json()
    assert response.status_code == 201, response.text
    assert body["confidence"]["level"] == ProductConfidence.UNVERIFIED.value
    # Read off the label rather than asked for separately.
    assert body["fssai_licence"] == "10012345678901"
    assert body["confirmations"] == 1


@pytest.mark.asyncio
async def test_a_second_person_confirming_promotes_it_to_community(db_clean, off_clean, app_client):
    factory = get_sessionmaker()
    async with factory() as session:
        for _ in range(2):
            await service.apply_confirmed_label(
                session, barcode=UNKNOWN, facts={"product_name": "Namkeen"},
            )
        await session.commit()
        record = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == UNKNOWN)
        )).scalar_one()
    assert record.confidence == ProductConfidence.COMMUNITY.value
    assert record.confirmation_count == 2


# ---------------------------------------------------------------------------
# Offline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_cached_product_works_with_the_network_disabled(db_clean, off_clean, monkeypatch):
    """Everything cached must answer with no connection at all."""
    from app.domains.off import client as off_client

    async def _no_network(barcode):
        raise AssertionError("the network was used while offline")

    monkeypatch.setattr(off_client, "fetch_product", _no_network)
    await _seed_off_product(KNOWN, product_name="Parle-G Biscuits", ingredients_text="wheat flour")

    factory = get_sessionmaker()
    async with factory() as session:
        body = await service.lookup(session, KNOWN, allow_network=False)
    assert body["found"] is True
    assert body["open_food_facts"]["product_name"] == "Parle-G Biscuits"
    assert body["confidence"]["level"] in CONFIDENCE_LEVELS


@pytest.mark.asyncio
async def test_an_uncached_product_offline_says_so_rather_than_failing(db_clean, off_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        body = await service.lookup(session, UNKNOWN, allow_network=False)
    assert body["found"] is False
    assert body["can_capture_label"] is True
    assert body["confidence"]["level"] == ProductConfidence.NOT_ENOUGH_INFORMATION.value


@pytest.mark.asyncio
async def test_a_replayed_offline_queue_does_not_double_count(db_clean, off_clean, app_client, device):
    """A phone that loses its connection mid-sync sends the same scan twice."""
    scan = {
        "barcode": KNOWN, "client_scan_id": "queued-scan-1",
        "scanned_at": datetime.now(UTC).isoformat(), "queued_offline": True,
    }
    first = await app_client.post("/api/v2/scan/events", headers=device, json=scan)
    second = await app_client.post("/api/v2/scan/events", headers=device, json=scan)

    assert first.json()["created"] is True
    assert second.json()["created"] is False, "the replayed scan was recorded again"
    assert first.json()["scan_id"] == second.json()["scan_id"]

    factory = get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(
            select(ScanEvent).where(ScanEvent.client_scan_id == "queued-scan-1")
        )).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_a_queued_scan_keeps_the_time_it_actually_happened(db_clean, off_clean, app_client, device):
    """A scan made in a basement at 9am is not a scan made at 6pm when the signal returned."""
    scanned = datetime(2026, 3, 1, 9, 15, tzinfo=UTC)
    await app_client.post("/api/v2/scan/events", headers=device, json={
        "barcode": KNOWN, "client_scan_id": "queued-scan-2",
        "scanned_at": scanned.isoformat(), "queued_offline": True,
    })
    factory = get_sessionmaker()
    async with factory() as session:
        event = (await session.execute(
            select(ScanEvent).where(ScanEvent.client_scan_id == "queued-scan-2")
        )).scalar_one()
    assert event.scanned_at.replace(tzinfo=UTC) == scanned
    assert event.queued_offline is True


# ---------------------------------------------------------------------------
# Every result carries a confidence level
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_result_is_ever_returned_without_a_confidence_level(
    db_clean, off_clean, app_client, device,
):
    """The acceptance criterion, across every shape of answer."""
    await _seed_off_product(KNOWN, product_name="Parle-G Biscuits")
    for barcode in (KNOWN, UNKNOWN, "8900000000000"):
        body = (await app_client.get(f"/api/v2/scan/lookup/{barcode}", headers=device)).json()
        assert "confidence" in body, f"{barcode} came back with no confidence"
        assert body["confidence"]["level"] in CONFIDENCE_LEVELS
        assert body["confidence"]["text"], "the confidence level has no wording for a person"


def test_a_record_defaults_to_not_enough_information():
    """Never a default that quietly means 'probably fine'."""
    assert ProductRecord.__table__.c.confidence.default.arg == (
        ProductConfidence.NOT_ENOUGH_INFORMATION.value
    )


# ---------------------------------------------------------------------------
# The wall, and the extraction boundary
# ---------------------------------------------------------------------------
def test_our_product_record_holds_no_open_food_facts_field():
    """Copying their fields here is the other direction of the ODbL breach."""
    off_fields = {"product_name", "brands", "ingredients_text", "nutriments",
                  "categories", "image_url", "quantity", "countries"}
    ours = {c.name for c in ProductRecord.__table__.columns}
    leaked = ours & off_fields
    assert not leaked, f"Store B copied Open Food Facts fields: {leaked}"


def test_the_label_prompt_forbids_diagnosis_inference_and_recommendation():
    """The extraction boundary, in the words the model actually receives."""
    lowered = LABEL_SYSTEM.lower()
    for rule in ("never diagnose", "never infer", "never recommend", "never judge"):
        assert rule in lowered, f"the label prompt does not say {rule!r}"
    assert "only text visibly printed" in lowered


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FSSAI Lic. No. 10012345678901", "10012345678901"),
        ("Lic No: 1001 2345 6789 01", "10012345678901"),
        ("Batch 20240115 MRP 45.00", None),
        ("Contact 9876543210", None),
        ("12345678901234 and 22219003000315", None),   # ambiguous, so refused
        ("00000000000000", None),                       # placeholder
    ],
)
def test_the_fssai_licence_is_read_only_when_it_is_unambiguous(text, expected):
    assert find_licence(text) == expected


def test_licence_validation_is_a_shape_check_and_says_so():
    assert is_valid_licence("10012345678901") is True
    assert is_valid_licence("1001234567890") is False    # 13 digits
    assert is_valid_licence("11111111111111") is False   # placeholder


# ---------------------------------------------------------------------------
# Reading the label
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reading_a_label_returns_the_facts_without_storing_them(
    db_clean, off_clean, app_client, registered_supabase_user, media_root, monkeypatch
):
    """The transcription is shown to the person first. Nothing is written yet."""
    token, _ = await registered_supabase_user()
    asset = await app_client.post(
        "/api/v2/media/upload", headers=auth(token),
        files={"file": ("label.png", png_bytes(), "image/png")},
    )
    assert asset.status_code in (200, 201), asset.text

    async def fake_run(**kwargs):
        return _fake_ai_result(kwargs["schema"](
            product_name="Masala Oats",
            brand="Test Brand",
            ingredients_text="Oats, salt, spices. FSSAI Lic. No. 10012345678901",
            nutrition_per_100g={"energy_kcal": "384", "sugars_g": "3.4"},
            confidence=0.82,
        ))

    monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", fake_run)
    response = await app_client.post(
        "/api/v2/scan/label/transcribe", headers=auth(token),
        json={"barcode": UNKNOWN, "media_asset_id": asset.json()["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stored"] is False
    assert body["facts"]["product_name"] == "Masala Oats"
    assert body["fssai_licence"] == "10012345678901"
    assert body["confidence"]["level"] == ProductConfidence.UNVERIFIED.value

    # Nothing written until the person confirms.
    factory = get_sessionmaker()
    async with factory() as session:
        assert (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == UNKNOWN)
        )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_reading_a_label_needs_an_account_but_scanning_does_not(
    db_clean, off_clean, app_client, device
):
    """Looking a barcode up is open to any phone; spending a model call is not."""
    lookup = await app_client.get(f"/api/v2/scan/lookup/{UNKNOWN}", headers=device)
    assert lookup.status_code == 200

    transcribe = await app_client.post(
        "/api/v2/scan/label/transcribe", headers=device,
        json={"barcode": UNKNOWN, "media_asset_id": str(uuid.uuid4())},
    )
    assert transcribe.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Signing up later
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_signing_up_later_brings_the_phones_earlier_scans_along(
    db_clean, off_clean, app_client, device, registered_supabase_user
):
    """Someone scans first and signs up afterwards. The scans follow them."""
    for barcode in (KNOWN, UNKNOWN):
        recorded = await app_client.post("/api/v2/scan/events", headers=device, json={
            "barcode": barcode, "client_scan_id": f"before-{barcode}",
        })
        assert recorded.status_code == 201, recorded.text

    factory = get_sessionmaker()
    async with factory() as session:
        before = (await session.execute(select(ScanEvent))).scalars().all()
    assert len(before) == 2
    assert all(event.account_id is None for event in before), "nobody owns these yet"

    token, account_id = await registered_supabase_user()
    claimed = await app_client.post(
        "/api/v2/scan/device/claim", headers={**device, **auth(token)},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json() == {"claimed": True, "scans_attached": 2}

    async with factory() as session:
        after = (await session.execute(select(ScanEvent))).scalars().all()
    assert {event.account_id for event in after} == {account_id}


@pytest.mark.asyncio
async def test_a_scan_nobody_claimed_is_in_nobodys_export(
    db_clean, off_clean, app_client, device, registered_supabase_user
):
    """An anonymous scan belongs to no account, so it leaves in no export."""
    from app.domains.privacy import export as export_service

    await app_client.post("/api/v2/scan/events", headers=device, json={
        "barcode": KNOWN, "client_scan_id": "anonymous-one",
    })
    _, account_id = await registered_supabase_user()

    factory = get_sessionmaker()
    async with factory() as session:
        payload = await export_service.build_export(session, account_id)
    assert payload["domains"]["product_scans"] == {"scans": [], "label_error_reports": []}


@pytest.mark.asyncio
async def test_a_claimed_scan_appears_in_that_persons_export(
    db_clean, off_clean, app_client, device, registered_supabase_user
):
    from app.domains.privacy import export as export_service

    await app_client.post("/api/v2/scan/events", headers=device, json={
        "barcode": KNOWN, "client_scan_id": "mine-one",
    })
    token, account_id = await registered_supabase_user()
    await app_client.post("/api/v2/scan/device/claim", headers={**device, **auth(token)})

    factory = get_sessionmaker()
    async with factory() as session:
        payload = await export_service.build_export(session, account_id)
    scans = payload["domains"]["product_scans"]["scans"]
    assert len(scans) == 1
    assert scans[0]["barcode"] == KNOWN


def test_the_device_token_itself_is_never_exported():
    """The token authenticates the phone, so it never leaves in an export."""
    from app.domains.privacy import REGISTRY, Classification

    assert REGISTRY["scan_devices"] == Classification.SECRET_EXCLUDED
    assert REGISTRY["scan_events"] == Classification.INCLUDED
    assert REGISTRY["product_records"] == Classification.NOT_USER_OWNED


# ---------------------------------------------------------------------------
# A photographed pack becomes an answer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_confirmed_label_answers_the_next_lookup(db_clean, off_clean, app_client, device):
    """The point of the label capture: an unknown barcode stops being unknown.

    Confirming used to keep only the FSSAI licence, leaving the ingredients and
    nutrition on a scan event nothing ever read back — so the rescan that
    follows the confirmation found the same nothing it started with.
    """
    facts = {
        "product_name": "Regional namkeen",
        "brand": "Local",
        "ingredients_text": "besan, edible oil, salt, spices. FSSAI Lic. No. 10012345678901",
        "nutrition_per_100g": {
            "energy_kcal": "520", "sugars_g": "3.1", "total_fat_g": "32",
            "saturated_fat_g": "14", "protein_g": "12", "sodium_g": "1.1",
        },
        "confidence": 0.9,
    }
    confirm = await app_client.post(
        "/api/v2/scan/label/confirm", headers=device,
        json={"barcode": UNKNOWN, "facts": facts, "client_scan_id": uuid.uuid4().hex},
    )
    assert confirm.status_code == 201, confirm.text

    found = (await app_client.get(f"/api/v2/scan/lookup/{UNKNOWN}", headers=device)).json()
    assert found["found"] is True
    assert found["label"]["ingredients_text"].startswith("besan")
    assert found["label"]["product_name"] == "Regional namkeen"
    # There is an ingredient list now, so there is nothing left to capture.
    assert found["can_capture_label"] is False

    verdict = (await app_client.get(f"/api/v2/scan/verdict/{UNKNOWN}", headers=device)).json()
    assert verdict["outcome"] != "not_enough_information", verdict
    assert verdict["grade"] is not None


@pytest.mark.asyncio
async def test_a_confirmed_label_does_not_reach_store_a(db_clean, off_clean, app_client, device):
    """Ours stays ours. Their store holds only what they published."""
    await app_client.post(
        "/api/v2/scan/label/confirm", headers=device,
        json={
            "barcode": UNKNOWN,
            "facts": {"product_name": "Regional namkeen", "ingredients_text": "besan, salt"},
            "client_scan_id": uuid.uuid4().hex,
        },
    )
    factory = get_off_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(OffProduct).where(OffProduct.barcode == UNKNOWN)
        )).scalar_one_or_none()
    assert row is None, "a confirmed label was written into Store A"


@pytest.mark.asyncio
async def test_replaying_a_confirmation_does_not_promote_the_record(
    db_clean, off_clean, app_client, device,
):
    """An offline queue sending the same tap five times is still one tap."""
    client_scan_id = uuid.uuid4().hex
    facts = {"product_name": "Namkeen", "ingredients_text": "besan, salt"}
    bodies = []
    for _ in range(5):
        response = await app_client.post(
            "/api/v2/scan/label/confirm", headers=device,
            json={"barcode": UNKNOWN, "facts": facts, "client_scan_id": client_scan_id},
        )
        assert response.status_code == 201, response.text
        bodies.append(response.json())
    assert [b["confirmations"] for b in bodies] == [1, 1, 1, 1, 1]
    assert bodies[-1]["confidence"]["level"] == ProductConfidence.UNVERIFIED.value


@pytest.mark.asyncio
async def test_a_stale_cached_product_is_revalidated(db_clean, off_clean, app_client, device, monkeypatch):
    """Formulations change. A copy kept forever pins the grade to the old pack."""
    from datetime import timedelta

    from app.domains.off import client as off_client
    from app.domains.product import service as product_service

    await _seed_off_product(
        KNOWN, product_name="Old Name", brands="Parle",
        ingredients_text="wheat flour",
        fetched_at=datetime.now(UTC) - product_service.OFF_CACHE_TTL - timedelta(days=1),
    )

    called = {"n": 0}

    async def _fetch(barcode: str):
        called["n"] += 1
        return {"product_name": "New Name", "brands": "Parle",
                "ingredients_text": "wheat flour, sugar"}

    monkeypatch.setattr(off_client, "fetch_product", _fetch)
    body = (await app_client.get(f"/api/v2/scan/lookup/{KNOWN}", headers=device)).json()
    assert called["n"] == 1, "a stale record was served without re-checking"
    assert body["open_food_facts"]["product_name"] == "New Name"


@pytest.mark.asyncio
async def test_a_stale_record_survives_a_failed_refresh(db_clean, off_clean, app_client, device, monkeypatch):
    """Their API being down is not a reason to lose the answer we have."""
    from datetime import timedelta

    from app.domains.off import client as off_client
    from app.domains.product import service as product_service

    await _seed_off_product(
        KNOWN, product_name="Parle-G Biscuits", brands="Parle",
        ingredients_text="wheat flour",
        fetched_at=datetime.now(UTC) - product_service.OFF_CACHE_TTL - timedelta(days=1),
    )

    async def _fetch(barcode: str):
        return None

    monkeypatch.setattr(off_client, "fetch_product", _fetch)
    body = (await app_client.get(f"/api/v2/scan/lookup/{KNOWN}", headers=device)).json()
    assert body["found"] is True
    assert body["open_food_facts"]["product_name"] == "Parle-G Biscuits"
