"""Phase 3 complete inventory contracts."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.domains.inventory.models import InventoryImportJob, InventoryItem, ItemExpiryEvent
from app.domains.inventory.taxonomy import SUPPLEMENT_DISCLAIMER
from app.shared.database import sql
from app.shared.database.base import utcnow
from tests.conftest import auth, png_bytes


CATEGORY_DETAILS = {
    "wardrobe": {"colour": "teal", "fabric": "cotton", "occasion": ["work"], "season": ["summer"]},
    "shoes": {"shoe_type": "loafers", "colour": "tan", "size": "39", "comfort": "all day"},
    "accessories": {"accessory_type": "earrings", "metal": "silver", "occasion": ["events"]},
    "beauty": {"product_type": "serum", "active_ingredients": ["niacinamide"], "remaining_percent": 70},
    "hair": {"product_type": "shampoo", "purpose": "cleansing", "remaining_percent": 50},
    "perfumes": {"fragrance_family": "floral", "concentration": "EDP", "season": ["spring"]},
    "supplements": {"supplement_name": "Vitamin label", "user_entered_purpose": "My own inventory note", "label_information": "Label text only"},
}


async def create(client, token, category="wardrobe", name=None, **overrides):
    payload = {"category": category, "display_name": name or f"{category.title()} item", "brand": "Test Brand", "details": CATEGORY_DETAILS[category]}
    payload.update(overrides)
    return await client.post("/api/v2/inventory/items", json=payload, headers=auth(token))


async def upload(client, token):
    return await client.post("/api/v2/media/upload", files={"file": ("item.png", png_bytes(), "image/png")}, data={"purpose": "inventory_item"}, headers=auth(token))


async def test_inventory_requires_authentication(app_client):
    for method, path in [("GET", "/api/v2/inventory/items"), ("GET", "/api/v2/inventory/summary"), ("POST", "/api/v2/inventory/items")]:
        response = await app_client.request(method, path, json={"category": "wardrobe", "display_name": "Kurta"} if method == "POST" else None)
        assert response.status_code == 401


async def test_all_seven_categories_create_typed_details(app_client, make_user):
    _, token = await make_user()
    for category, details in CATEGORY_DETAILS.items():
        response = await create(app_client, token, category)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["category"] == category and body["verification_state"] == "confirmed"
        for key in details:
            assert key in body["details"]
    summary = (await app_client.get("/api/v2/inventory/summary", headers=auth(token))).json()
    assert summary["total_items"] == 7
    assert all(summary["categories"][key] == 1 for key in CATEGORY_DETAILS)


async def test_extraction_creates_draft_then_confirm_and_correct(app_client, make_user, fake_provider):
    _, token = await make_user(); media_id = (await upload(app_client, token)).json()["id"]
    fake_provider.text = json.dumps({"category": "wardrobe", "subcategory": "kurta", "display_name": "Teal cotton kurta", "brand": None, "confidence": 0.78, "details": {"colour": "blue", "fabric": "cotton", "occasion": ["work"]}, "attributes": [{"key": "colour", "value": "blue", "confidence": 0.68}], "uncertain_fields": ["colour"], "photo_quality_notes": "Label is clear; colour may vary by lighting"})
    extracted = await app_client.post("/api/v2/inventory/extract", json={"media_asset_id": media_id, "capture_type": "item_photo"}, headers=auth(token))
    assert extracted.status_code == 200, extracted.text
    body = extracted.json(); item = body["item"]
    assert item["verification_state"] == "draft" and item["image_ids"] == [media_id]
    colour = next(row for row in item["attributes"] if row["key"] == "colour")
    assert colour["confidence"] == 0.68 and colour["verification_state"] == "draft"
    corrected = await app_client.patch(f"/api/v2/inventory/items/{item['id']}", json={"expected_version": item["version"], "details": {"colour": "teal"}, "attributes": [{"key": "colour", "value": "teal"}]}, headers=auth(token))
    assert corrected.status_code == 200 and corrected.json()["details"]["colour"] == "teal"
    confirmed = await app_client.post(f"/api/v2/inventory/items/{item['id']}/confirm", headers=auth(token))
    assert confirmed.json()["verification_state"] == "confirmed"


async def test_item_and_media_ownership_are_enforced(app_client, make_user):
    _, owner = await make_user(); _, other = await make_user()
    item_id = (await create(app_client, owner, name="Private kurta")).json()["id"]
    assert (await app_client.get(f"/api/v2/inventory/items/{item_id}", headers=auth(other))).status_code == 404
    media_id = (await upload(app_client, owner)).json()["id"]
    response = await app_client.post("/api/v2/inventory/extract", json={"media_asset_id": media_id}, headers=auth(other))
    assert response.status_code == 404


async def test_duplicate_candidates_can_be_reviewed_and_resolved(app_client, make_user):
    _, token = await make_user()
    first = (await create(app_client, token, name="Everyday kurta")).json()
    second = (await create(app_client, token, name="Everyday kurta")).json()
    listing = (await app_client.get("/api/v2/inventory/duplicates", headers=auth(token))).json()
    assert listing["label"] == "Duplicate Candidates" and len(listing["candidates"]) == 1
    candidate = listing["candidates"][0]
    resolved = await app_client.post(f"/api/v2/inventory/duplicates/{candidate['id']}/resolve", json={"resolution": "merge", "canonical_item_id": first["id"]}, headers=auth(token))
    assert resolved.json()["status"] == "resolved"
    assert (await app_client.get(f"/api/v2/inventory/items/{second['id']}", headers=auth(token))).status_code == 404


async def test_expiry_and_period_after_opening_are_deterministic(app_client, make_user):
    _, token = await make_user(); today = date.today()
    beauty = (await create(app_client, token, "beauty", details={"product_type": "serum", "expiry_date": (today + timedelta(days=20)).isoformat()})).json()
    hair = (await create(app_client, token, "hair", details={"product_type": "mask", "opened_date": today.isoformat(), "period_after_opening_months": 2})).json()
    assert beauty["effective_expiry"] == (today + timedelta(days=20)).isoformat()
    assert hair["effective_expiry"] is not None
    expiring = (await app_client.get("/api/v2/inventory/expiring?days=100", headers=auth(token))).json()["items"]
    assert {row["id"] for row in expiring} == {beauty["id"], hair["id"]}
    filtered = (await app_client.get("/api/v2/inventory/search?expiry_status=expiring_soon", headers=auth(token))).json()["items"]
    assert {row["id"] for row in filtered} == {beauty["id"], hair["id"]}
    factory = sql.get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(select(ItemExpiryEvent))).scalars().all()
    assert any(str(event.item_id) == beauty["id"] for event in events)


async def test_usage_condition_and_low_use_calculation(app_client, make_user):
    _, token = await make_user(); item = (await create(app_client, token, name="Rarely worn sari")).json()
    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = await session.get(InventoryItem, item["id"]); row.created_at = utcnow() - timedelta(days=60); await session.commit()
    low = (await app_client.get("/api/v2/inventory/low-use", headers=auth(token))).json()
    assert low["label"] == "Low-Use Products" and [row["id"] for row in low["items"]] == [item["id"]]
    used = await app_client.post(f"/api/v2/inventory/items/{item['id']}/usage", json={"used_on": date.today().isoformat(), "quantity": 1}, headers=auth(token))
    assert used.json()["usage_count"] == 1 and used.json()["low_use"] is False
    condition = await app_client.post(f"/api/v2/inventory/items/{item['id']}/condition", json={"condition": "needs_attention"}, headers=auth(token))
    assert condition.json()["condition"] == "needs_attention"


async def test_value_to_recover_is_explained_and_handles_missing_price(app_client, make_user):
    _, token = await make_user()
    priced = (await create(app_client, token, "beauty", name="Face serum", purchase_price="1200.00", details={"product_type": "serum", "remaining_percent": 50})).json()
    missing = (await create(app_client, token, "perfumes", name="Floral scent")).json()
    report = (await app_client.get("/api/v2/inventory/value-to-recover", headers=auth(token))).json()
    assert report["label"] == "Value to Recover" and report["is_estimate"] is True and "never exact" in report["explanation"]
    by_id = {row["item_id"]: row for row in report["items"]}
    assert by_id[priced["id"]]["estimated_value"] is not None
    assert by_id[missing["id"]]["estimated_value"] is None and by_id[missing["id"]]["missing_inputs"] == ["purchase_price"]
    assert "Money Wasted" not in json.dumps(report)


async def test_search_filters_pagination_and_sorting(app_client, make_user):
    _, token = await make_user()
    await create(app_client, token, "wardrobe", name="Teal office kurta", brand="FabIndia", details={"colour": "teal", "occasion": ["office"], "season": ["summer"]})
    await create(app_client, token, "beauty", name="Daily serum", brand="Minimalist", details={"product_type": "serum", "ingredients_text": "Niacinamide and zinc", "active_ingredients": ["niacinamide"]})
    for index in range(4): await create(app_client, token, "shoes", name=f"Shoe {index}")
    colour = (await app_client.get("/api/v2/inventory/search?colour=teal&occasion=office", headers=auth(token))).json()
    assert [row["display_name"] for row in colour["items"]] == ["Teal office kurta"]
    ingredient = (await app_client.get("/api/v2/inventory/search?q=niacinamide", headers=auth(token))).json()
    assert [row["display_name"] for row in ingredient["items"]] == ["Daily serum"]
    page = (await app_client.get("/api/v2/inventory/items?category=shoes&page=2&page_size=2&sort=name", headers=auth(token))).json()
    assert page["pagination"] == {"page": 2, "page_size": 2, "total": 4, "pages": 2} and len(page["items"]) == 2


async def test_offline_idempotency_and_sync_conflict(app_client, make_user):
    _, token = await make_user(); payload = {"category": "accessories", "display_name": "Silver hoops", "client_mutation_id": "offline-create-1"}
    first = (await app_client.post("/api/v2/inventory/items", json=payload, headers=auth(token))).json()
    replay = (await app_client.post("/api/v2/inventory/items", json=payload, headers=auth(token))).json()
    assert replay["id"] == first["id"]
    changed = (await app_client.patch(f"/api/v2/inventory/items/{first['id']}", json={"expected_version": first["version"], "display_name": "Silver hoop earrings"}, headers=auth(token))).json()
    stale = await app_client.patch(f"/api/v2/inventory/items/{first['id']}", json={"expected_version": first["version"], "brand": "Local"}, headers=auth(token))
    assert changed["version"] == first["version"] + 1 and stale.status_code == 409 and stale.json()["detail"]["current_version"] == changed["version"]


async def test_large_inventory_remains_paginated(app_client, make_user):
    _, token = await make_user(); seed = (await create(app_client, token, name="Seed")).json()
    factory = sql.get_sessionmaker()
    async with factory() as session:
        seed_row = await session.get(InventoryItem, seed["id"])
        session.add_all([InventoryItem(account_id=seed_row.account_id, category="wardrobe", display_name=f"Bulk {index:03d}", source="user_declared", verification_state="confirmed", confidence=1.0) for index in range(250)])
        await session.commit()
    started = time.perf_counter(); response = await app_client.get("/api/v2/inventory/items?page_size=50", headers=auth(token)); elapsed = time.perf_counter() - started
    assert response.status_code == 200 and response.json()["pagination"]["total"] == 251 and len(response.json()["items"]) == 50 and elapsed < 5


async def test_supplement_language_is_inventory_only_and_batch_is_gated(app_client, make_user, fake_provider):
    _, token = await make_user()
    item = (await create(app_client, token, "supplements", name="Supplement label")).json()
    text = json.dumps(item).lower()
    assert item["details"]["safety_disclaimer"] == SUPPLEMENT_DISCLAIMER
    assert "does not provide supplement dosage" in text and "recommended dose" not in text
    from app.domains.inventory.extraction import SYSTEM, prompt
    ai_text = (SYSTEM + prompt("supplements")).lower()
    assert "never provide dosage advice" in ai_text and "do not return supplement dosage" in ai_text
    media_id = (await upload(app_client, token)).json()["id"]
    blocked = await app_client.post("/api/v2/inventory/extract", json={"media_asset_id": media_id, "capture_type": "shelf_photo"}, headers=auth(token))
    assert blocked.status_code == 404 and fake_provider.calls == 0
    dosage = await app_client.post("/api/v2/inventory/items", json={"category": "supplements", "display_name": "Unsafe field", "attributes": [{"key": "dosage_advice", "value": "two daily"}]}, headers=auth(token))
    assert dosage.status_code == 422


async def test_failed_extraction_is_recorded_without_a_draft(app_client, make_user, fake_provider):
    from app.domains.ai_gateway.providers.gemini import ProviderCallFailed
    _, token = await make_user(); media_id = (await upload(app_client, token)).json()["id"]
    fake_provider.raises = ProviderCallFailed("quota unavailable")
    response = await app_client.post("/api/v2/inventory/extract", json={"media_asset_id": media_id}, headers=auth(token))
    assert response.status_code == 503
    factory = sql.get_sessionmaker()
    async with factory() as session:
        job = (await session.execute(select(InventoryImportJob).where(InventoryImportJob.media_asset_id == media_id))).scalar_one()
        items = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == job.account_id, InventoryItem.source == "photo_extracted"))).scalars().all()
    assert job.status == "failed" and job.error_code == "provider_error"
    assert items == []


async def test_privacy_export_contains_inventory_history(app_client, make_user):
    _, token = await make_user(); item = (await create(app_client, token, name="Exported kurta")).json()
    exported = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    row = next(value for value in exported["appearance_inventory"] if value["id"] == item["id"])
    assert row["history"][0]["event_type"] == "created"
