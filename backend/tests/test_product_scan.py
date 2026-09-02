"""Scanning a packaged product: known, unknown, and offline.

The acceptance criteria are behaviours a person experiences, so these are
written against the API a phone actually calls.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from app.domains.ai_gateway.gateway import AIResult
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.nutrition.grading.production_rules import (
    STATUS_PUBLISHED,
    ProductionRuleset,
    candidate_ruleset,
)
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.product import service
from app.domains.product.confidence import CONFIDENCE_LEVELS, ProductConfidence
from app.domains.product.extraction import SYSTEM as LABEL_SYSTEM
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanEvent
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


async def _seed_label_run(facts: dict, account_id: uuid.UUID) -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature="product_label_transcribe", provider="test",
            model="test-model", prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts))
        await session.commit()
        return run.id


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
async def test_verdict_exposes_product_result_contract_identity_and_provenance(
    db_clean, off_clean, app_client, device,
):
    """The result surface keeps the scan identity and its source footing."""
    await _seed_off_product(
        KNOWN, product_name="Parle-G Biscuits", brands="Parle",
        ingredients_text="wheat flour, sugar, palm oil",
        nutriments={"sugars_100g": 22.5, "salt_100g": 0.7},
    )

    response = await app_client.get(f"/api/v2/scan/verdict/{KNOWN}", headers=device)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_contract_version"] == "v1"
    assert body["barcode"] == KNOWN
    assert body["product_name"] == "Parle-G Biscuits"
    assert body["brand"] == "Parle"
    assert body["facts_provenance"] == "open_food_facts"
    assert "Open Food Facts" in body["attribution"]["text"]
    assert body["negatives"] == body["lowers"]
    assert body["positives"] == body["helps"]


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
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """One tap to accept, the VC-07 draft-to-confirmed shape."""
    facts = {
        "product_name": "Regional namkeen",
        "ingredients_text": "besan, edible oil, salt, spices. FSSAI Lic. No. 10012345678901",
        "nutrition_per_100g": {"energy_kcal": "520", "sugars_g": "3.1"},
        "nutrition_basis": "per_100g",
    }
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(facts, account_id)
    response = await app_client.post(
        "/api/v2/scan/label/confirm",
        json={"barcode": UNKNOWN, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
        headers={**device, **auth(token)},
    )
    body = response.json()
    assert response.status_code == 201, response.text
    assert body["confidence"]["level"] == ProductConfidence.UNVERIFIED.value
    # Read off the label rather than asked for separately.
    assert body["fssai_licence"] == "10012345678901"
    # Anonymous confirmations are kept but do not count towards a community
    # claim, so the record stays unverified and the count stays at zero until
    # an accountable reviewer promotes it.
    assert body["confirmations"] == 0


@pytest.mark.asyncio
async def test_confirmed_label_content_versions_are_deduplicated_and_linked(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Repeated observations reuse a semantic version; content gets a lineage."""
    first = {
        "product_name": "Regional namkeen",
        "brand": "Acme",
        "ingredients_text": "besan, edible oil, salt",
        "nutrition_per_100g": {"energy_kcal": "520", "sugars_g": "3.1"},
        "nutrition_basis": "per_100g",
        "batch_number": "B-1",
    }

    token, account_id = await registered_supabase_user()
    async def confirm(facts):
        run_id = await _seed_label_run(facts, account_id)
        response = await app_client.post(
            "/api/v2/scan/label/confirm", headers={**device, **auth(token)},
            json={"barcode": UNKNOWN, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
        )
        assert response.status_code == 201, response.text

    second = {
        **first,
        "nutrition_per_100g": {"energy_kcal": "520", "sugars_g": "4.2"},
    }
    await confirm(first)  # A -> V1
    await confirm({**first, "batch_number": "B-2"})  # A -> reuse V1
    await confirm(second)  # B -> V2
    await confirm({**second, "batch_number": "B-3"})  # B -> reuse V2
    await confirm({**first, "batch_number": "B-4"})  # A -> V3, not historic V1

    factory = get_sessionmaker()
    async with factory() as session:
        snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == UNKNOWN).order_by(LabelSnapshot.version_number)
        )).scalars().all()
        events = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == UNKNOWN)
        )).scalars().all()
    assert [snapshot.version_number for snapshot in snapshots] == [1, 2, 3]
    assert snapshots[0].previous_snapshot_id is None
    assert snapshots[1].previous_snapshot_id == snapshots[0].id
    assert snapshots[2].previous_snapshot_id == snapshots[1].id
    assert snapshots[1].changed_fields == ["nutrition"]
    assert snapshots[2].changed_fields == ["nutrition"]
    assert snapshots[0].content_fingerprint != snapshots[1].content_fingerprint
    assert snapshots[2].content_fingerprint == snapshots[0].content_fingerprint
    assert len(events) == 5, "same semantic content must not erase observations"

    verdict = await app_client.get(f"/api/v2/scan/verdict/{UNKNOWN}", headers=device)
    assert verdict.status_code == 200, verdict.text
    assert verdict.json()["label_version"]["version_number"] == 3


def _published_ruleset() -> ProductionRuleset:
    candidate = candidate_ruleset()
    return ProductionRuleset(provenance={
        rule_id: replace(row, status=STATUS_PUBLISHED)
        for rule_id, row in candidate.provenance.items()
    })


@pytest.mark.asyncio
async def test_unknown_confirmed_pack_facts_feed_the_real_grader_without_off_copy(
    db_clean, off_clean, app_client, device, monkeypatch, registered_supabase_user,
):
    from app.api.v2 import product as product_api

    facts = {
        "product_name": "Regional millet snack",
        "brand": "Local Foods",
        "ingredients_text": "millet flour, chickpea flour, sugar, edible oil, salt",
        "nutrition_per_100g": {
            "energy_kcal": "440",
            "protein_g": "9",
            "total_fat_g": "14",
            "saturated_fat_g": "3",
            "trans_fat_g": "0",
            "sugars_g": "12",
            "fibre_g": "6",
            "sodium_g": "0.32",
        },
        "nutrition_basis": "per_100g",
        "net_quantity": "180 g",
        "serving_size": "30 g",
    }
    captured = {}
    real_grade_product = product_api.grade_product

    def capture_product(product):
        captured["product"] = product
        return real_grade_product(product)

    async def published_rules(_session):
        return _published_ruleset()

    monkeypatch.setattr(product_api, "grade_product", capture_product)
    monkeypatch.setattr(product_api, "resolve_production_ruleset", published_rules)
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(facts, account_id)
    confirmed = await app_client.post(
        "/api/v2/scan/label/confirm",
        headers={**device, **auth(token)},
        json={"barcode": UNKNOWN, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
    )
    assert confirmed.status_code == 201, confirmed.text

    response = await app_client.get(f"/api/v2/scan/verdict/{UNKNOWN}", headers=device)
    assert response.status_code == 200, response.text
    body = response.json()
    product = captured["product"]
    assert product.total_sugar_g == Decimal("12")
    assert product.saturated_fat_g == Decimal("3")
    assert product.sodium_g == Decimal("0.32")
    assert body["outcome"] == "graded"
    assert body["facts_provenance"] == "confirmed_label_snapshot"
    assert body["label_version"]["version_number"] == 1
    assert body["label_version"]["completeness"] == "complete_for_grading"
    assert body["pack_size_g"] == 180.0

    factory = get_off_sessionmaker()
    async with factory() as session:
        assert await session.get(OffProduct, UNKNOWN) is None


@pytest.mark.asyncio
async def test_insufficient_confirmed_pack_remains_not_enough_information(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    facts = {
        "product_name": "Energy-only snack",
        "ingredients_text": "millet flour, salt",
        "nutrition_per_100g": {"energy_kcal": "440"},
    }
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(facts, account_id)
    confirmed = await app_client.post(
        "/api/v2/scan/label/confirm",
        headers={**device, **auth(token)},
        json={"barcode": UNKNOWN, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
    )
    assert confirmed.status_code == 201, confirmed.text
    body = (await app_client.get(
        f"/api/v2/scan/verdict/{UNKNOWN}", headers=device
    )).json()
    assert body["outcome"] == "not_enough_information"
    assert "nutrition panel" in body["missing"]
    assert body["label_version"]["completeness"] == "incomplete_for_grading"


async def _seed_label_events(barcode: str, count: int) -> list[uuid.UUID]:
    factory = get_sessionmaker()
    ids: list[uuid.UUID] = []
    async with factory() as session:
        for index in range(count):
            event = ScanEvent(
                barcode=barcode,
                outcome=service.OUTCOME_LABEL,
                client_scan_id=f"concurrent-{uuid.uuid4().hex}-{index}",
                scanned_at=datetime.now(UTC),
                label_facts={"observation": index},
            )
            session.add(event)
            await session.flush()
            ids.append(event.id)
        await session.commit()
    return ids


async def _store_concurrently(
    barcode: str, event_id: uuid.UUID, facts: dict, start: asyncio.Event,
) -> int:
    factory = get_sessionmaker()
    async with factory() as session:
        await start.wait()
        snapshot = await service.store_label_snapshot(
            session,
            barcode=barcode,
            facts=facts,
            device_id=None,
            scan_event_id=event_id,
        )
        await session.commit()
        return snapshot.version_number


@pytest.mark.asyncio
async def test_concurrent_confirmations_allocate_one_ordered_semantic_lineage(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8907777777777"
    first = {
        "product_name": "Concurrent snack",
        "ingredients_text": "millet, salt",
        "nutrition_per_100g": {"sugars_g": "1"},
        "nutrition_basis": "per_100g",
    }
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(first, account_id)
    same_responses = await asyncio.gather(*(
        app_client.post(
            "/api/v2/scan/label/confirm",
            headers={**device, **auth(token)},
            json={
                "barcode": barcode,
                "ai_run_id": str(run_id),
                "client_scan_id": f"same-{uuid.uuid4().hex}",
            },
        )
        for _ in range(2)
    ))
    assert [response.status_code for response in same_responses] == [201, 201], [
        response.text for response in same_responses
    ]

    factory = get_sessionmaker()
    async with factory() as session:
        same_snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == barcode)
        )).scalars().all()
        same_events = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == barcode)
        )).scalars().all()
    assert [row.version_number for row in same_snapshots] == [1]
    assert len(same_events) == 2

    event_ids = await _seed_label_events(barcode, 2)
    second = {**first, "nutrition_per_100g": {"sugars_g": "2"}}
    third = {**first, "nutrition_per_100g": {"sugars_g": "3"}}
    start = asyncio.Event()
    tasks = (
        asyncio.create_task(_store_concurrently(barcode, event_ids[0], second, start)),
        asyncio.create_task(_store_concurrently(barcode, event_ids[1], third, start)),
    )
    start.set()
    assert sorted(await asyncio.gather(*tasks)) == [2, 3]

    async with factory() as session:
        snapshots = (await session.execute(
            select(LabelSnapshot)
            .where(LabelSnapshot.barcode == barcode)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
        latest = await service.latest_label_snapshot(session, barcode)
    assert [row.version_number for row in snapshots] == [1, 2, 3]
    assert len({row.version_number for row in snapshots}) == 3
    assert snapshots[1].previous_snapshot_id == snapshots[0].id
    assert snapshots[2].previous_snapshot_id == snapshots[1].id
    assert latest is not None and latest.id == snapshots[2].id


@pytest.mark.asyncio
async def test_replayed_label_confirmation_is_idempotent_for_event_and_version(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    facts = {
        "product_name": "Replay-safe oats",
        "ingredients_text": "oats",
        "nutrition_per_100g": {"energy_kcal": "370"},
        "nutrition_basis": "per_100g",
    }
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(facts, account_id)
    client_scan_id = uuid.uuid4().hex
    payload = {"barcode": UNKNOWN, "ai_run_id": str(run_id), "client_scan_id": client_scan_id}
    for _ in range(2):
        response = await app_client.post("/api/v2/scan/label/confirm", headers={**device, **auth(token)}, json=payload)
        assert response.status_code == 201, response.text

    factory = get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(
            select(ScanEvent).where(ScanEvent.client_scan_id == client_scan_id)
        )).scalars().all()
        snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == UNKNOWN)
        )).scalars().all()
    assert len(events) == 1
    assert len(snapshots) == 1
    assert snapshots[0].version_number == 1


@pytest.mark.asyncio
async def test_repeated_anonymous_confirmations_do_not_make_a_community_claim(
    db_clean, off_clean, app_client,
):
    """A device identity is not a person identity.

    Confirmations from anonymous devices are kept — the label facts are worth
    having — but they do not accumulate into a community claim, because
    nothing here can tell two people apart from one person with two phones, or
    from one phone replaying a queue. Only an accountable reviewer promotes a
    record, and that is asserted below.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        for _ in range(3):
            await service.apply_confirmed_label(
                session, barcode=UNKNOWN, facts={"product_name": "Namkeen"},
            )
        await session.commit()
        record = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == UNKNOWN)
        )).scalar_one()
    assert record.confidence == ProductConfidence.UNVERIFIED.value
    assert record.confirmation_count == 0


@pytest.mark.asyncio
async def test_an_accountable_reviewer_is_what_promotes_a_record(db_clean, off_clean, app_client):
    """The one path to verified, and it carries a name."""
    factory = get_sessionmaker()
    async with factory() as session:
        await service.apply_confirmed_label(
            session, barcode=UNKNOWN, facts={"product_name": "Namkeen"},
            confirmed_by="reviewer@glamgenius",
        )
        await session.commit()
        record = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == UNKNOWN)
        )).scalar_one()
    assert record.confidence == ProductConfidence.VERIFIED.value
    assert record.verified_by == "reviewer@glamgenius"
    assert record.verified_at is not None


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
