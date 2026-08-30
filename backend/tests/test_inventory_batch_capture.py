"""Multi-item capture: one shelf photo, one tap per thing on it.

The acceptance criteria are behaviours a person experiences, so most of this is
written against the API a phone actually calls. The one that matters most is
the one that is easiest to get wrong: **nothing enters the shelf unconfirmed**.
A candidate is not an item, and this file checks that from several directions —
the inventory listing, the routines shelf engine, and the duplicates queue.
"""
from __future__ import annotations

import time
import uuid

import pytest
from app.domains.inventory import batch
from app.domains.inventory.models import (
    DuplicateCandidate,
    InventoryImportCandidate,
    InventoryItem,
)
from app.domains.inventory.schemas import BATCH_ITEM_LIMIT, ExtractedInventoryBatch
from app.domains.routines import shelf
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth, png_bytes

# A real Indian care-and-supplement shelf: fifteen things, mixed categories.
SHELF_OF_FIFTEEN = [
    ("beauty", "Cetaphil Gentle Skin Cleanser", "Cetaphil", {"product_type": "cleanser", "size": "125 ml"}),
    ("beauty", "Minimalist Niacinamide 10%", "Minimalist", {"product_type": "serum", "size": "30 ml"}),
    ("beauty", "Re'equil Oxybenzone Free Sunscreen", "Re'equil", {"product_type": "sunscreen", "size": "50 g"}),
    ("beauty", "The Derma Co 2% Salicylic Acid", "The Derma Co", {"product_type": "serum"}),
    ("beauty", "Plum Green Tea Toner", "Plum", {"product_type": "toner", "size": "200 ml"}),
    ("hair", "Wow Apple Cider Vinegar Shampoo", "Wow", {"product_type": "shampoo", "size": "300 ml"}),
    ("hair", "Mamaearth Onion Hair Oil", "Mamaearth", {"product_type": "hair oil", "size": "150 ml"}),
    ("hair", "Tresemme Keratin Smooth Conditioner", "Tresemme", {"product_type": "conditioner"}),
    ("supplements", "HealthKart Vitamin D3 60K", "HealthKart", {"supplement_name": "Vitamin D3"}),
    ("supplements", "Carbamide Forte Magnesium Glycinate", "Carbamide Forte", {"supplement_name": "Magnesium glycinate"}),
    ("supplements", "Neuherbs Omega 3 Fish Oil", "Neuherbs", {"supplement_name": "Omega 3"}),
    ("supplements", "Fast&Up Vitamin C", "Fast&Up", {"supplement_name": "Vitamin C"}),
    ("supplements", "Himalaya Ashwagandha", "Himalaya", {"supplement_name": "Ashwagandha"}),
    ("perfumes", "Bella Vita Luxury CEO Man", "Bella Vita", {"concentration": "eau de parfum"}),
    ("beauty", "Dot & Key Vitamin C Moisturiser", "Dot & Key", {"product_type": "moisturiser"}),
]


def _extracted(rows=SHELF_OF_FIFTEEN, *, unreadable: int = 0) -> ExtractedInventoryBatch:
    """A batch payload shaped exactly as the model must return it."""
    return ExtractedInventoryBatch(
        items=[
            {
                "category": category,
                "display_name": name,
                "brand": brand,
                "confidence": 0.72,
                "details": details,
                "attributes": [{"key": "brand", "value": brand, "confidence": 0.8}],
                "uncertain_fields": [],
                "photo_quality_notes": "Label readable.",
            }
            for category, name, brand, details in rows
        ],
        photo_quality_notes="Even light; the back row is partly hidden.",
        unreadable_count=unreadable,
    )


def _fake_ai_result(data):
    from app.domains.ai_gateway.gateway import AIResult

    return AIResult(
        data=data, run_id=uuid.uuid4(), provider="test", model="test-model",
        prompt_version=batch.PROMPT_VERSION, schema_version=batch.SCHEMA_VERSION,
        confidence=0.72, latency_ms=1200, estimated_cost_usd=None,
    )


@pytest.fixture
def shelf_photo(monkeypatch):
    """Stub the one model call. Everything else is the real path."""
    def _install(payload: ExtractedInventoryBatch):
        async def fake_run(**kwargs):
            assert kwargs["feature"] == batch.FEATURE
            return _fake_ai_result(payload)
        monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", fake_run)
    return _install


async def _upload(app_client, token) -> str:
    response = await app_client.post(
        "/api/v2/media/upload", headers=auth(token),
        files={"file": ("shelf.png", png_bytes(), "image/png")},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _capture(app_client, token, payload=None, shelf_photo=None):
    shelf_photo(payload or _extracted())
    asset_id = await _upload(app_client, token)
    response = await app_client.post(
        "/api/v2/inventory/extract/batch", headers=auth(token),
        json={"media_asset_id": asset_id, "capture_type": "shelf_photo"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# One photo produces multiple candidates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_photo_produces_multiple_candidates(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)

    assert body["detected_count"] == 15
    assert len(body["candidates"]) == 15
    assert body["pending_count"] == 15
    assert {row["category"] for row in body["candidates"]} == {"beauty", "hair", "supplements", "perfumes"}
    # Stable order, so the review list does not shuffle between refreshes.
    assert [row["position"] for row in body["candidates"]] == list(range(15))


@pytest.mark.asyncio
async def test_the_batch_says_what_it_could_not_read(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """A list of eight from a shelf of twelve should say so, not imply it is all."""
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, _extracted(SHELF_OF_FIFTEEN[:8], unreadable=4), shelf_photo)

    assert body["detected_count"] == 8
    assert body["unreadable_count"] == 4


# ---------------------------------------------------------------------------
# Nothing enters the shelf unconfirmed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_candidate_is_not_an_inventory_item(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """The criterion, checked at the table itself."""
    token, _ = await registered_supabase_user()
    await _capture(app_client, token, shelf_photo=shelf_photo)

    factory = get_sessionmaker()
    async with factory() as session:
        items = (await session.execute(select(InventoryItem))).scalars().all()
        candidates = (await session.execute(select(InventoryImportCandidate))).scalars().all()
    assert items == [], "a shelf photo must not create inventory items"
    assert len(candidates) == 15


@pytest.mark.asyncio
async def test_an_unconfirmed_candidate_is_not_in_the_inventory_listing(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    await _capture(app_client, token, shelf_photo=shelf_photo)

    listing = await app_client.get("/api/v2/inventory/items", headers=auth(token))
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"] == []
    assert listing.json()["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_an_unconfirmed_candidate_does_not_reach_the_routines_shelf(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """The engine that writes routines must not see a guess nobody accepted."""
    token, account_id = await registered_supabase_user()
    await _capture(app_client, token, shelf_photo=shelf_photo)

    factory = get_sessionmaker()
    async with factory() as session:
        context = await shelf.gather(session, account_id=account_id)
    assert context.owned == []
    assert context.draft_count == 0


@pytest.mark.asyncio
async def test_an_unconfirmed_candidate_does_not_enter_the_duplicates_queue(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """Two photos of the same shelf must not fill the duplicates queue with guesses."""
    token, _ = await registered_supabase_user()
    await _capture(app_client, token, shelf_photo=shelf_photo)
    await _capture(app_client, token, shelf_photo=shelf_photo)

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(DuplicateCandidate))).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# One tap
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_tap_confirms_a_candidate_into_a_confirmed_item(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    candidate = body["candidates"][0]

    response = await app_client.post(
        f"/api/v2/inventory/imports/{body['job_id']}/candidates/{candidate['id']}/confirm",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    decided = response.json()
    assert decided["state"] == "confirmed"
    assert decided["item_id"]

    item = await app_client.get(f"/api/v2/inventory/items/{decided['item_id']}", headers=auth(token))
    assert item.status_code == 200
    # The tap *is* the confirmation. Asking again would be the same question.
    assert item.json()["verification_state"] == "confirmed"
    assert item.json()["display_name"] == candidate["display_name"]
    assert item.json()["source"] == "photo_extracted"


@pytest.mark.asyncio
async def test_one_tap_rejects_a_candidate_and_creates_nothing(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    candidate = body["candidates"][1]

    response = await app_client.post(
        f"/api/v2/inventory/imports/{body['job_id']}/candidates/{candidate['id']}/reject",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "rejected"
    assert response.json()["item_id"] is None

    listing = await app_client.get("/api/v2/inventory/items", headers=auth(token))
    assert listing.json()["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_a_repeated_tap_does_not_create_the_item_twice(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """A phone that retries a tap it already sent must not double the shelf."""
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    path = f"/api/v2/inventory/imports/{body['job_id']}/candidates/{body['candidates'][0]['id']}/confirm"

    first = await app_client.post(path, headers=auth(token))
    second = await app_client.post(path, headers=auth(token))
    assert first.json()["item_id"] == second.json()["item_id"]

    listing = await app_client.get("/api/v2/inventory/items", headers=auth(token))
    assert listing.json()["pagination"]["total"] == 1


@pytest.mark.asyncio
async def test_a_rejected_candidate_cannot_be_confirmed_afterwards(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    candidate_id = body["candidates"][0]["id"]
    base = f"/api/v2/inventory/imports/{body['job_id']}/candidates/{candidate_id}"

    await app_client.post(f"{base}/reject", headers=auth(token))
    after = await app_client.post(f"{base}/confirm", headers=auth(token))
    assert after.json()["state"] == "rejected"
    assert after.json()["item_id"] is None


@pytest.mark.asyncio
async def test_the_review_list_can_be_reread_after_a_dropped_connection(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    await app_client.post(
        f"/api/v2/inventory/imports/{body['job_id']}/candidates/{body['candidates'][0]['id']}/confirm",
        headers=auth(token),
    )

    resumed = await app_client.get(f"/api/v2/inventory/imports/{body['job_id']}", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["confirmed_count"] == 1
    assert resumed.json()["pending_count"] == 14


@pytest.mark.asyncio
async def test_another_account_cannot_see_or_decide_your_candidates(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    other_token, _ = await registered_supabase_user()

    read = await app_client.get(f"/api/v2/inventory/imports/{body['job_id']}", headers=auth(other_token))
    assert read.status_code == 404

    tap = await app_client.post(
        f"/api/v2/inventory/imports/{body['job_id']}/candidates/{body['candidates'][0]['id']}/confirm",
        headers=auth(other_token),
    )
    assert tap.status_code == 404


# ---------------------------------------------------------------------------
# Fifteen items
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fifteen_items_captured_one_tap_each(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """The target, driven the way a person does it: one photo, then taps.

    Twelve are kept and three are dropped, because a real shelf photo always
    contains something you do not want.
    """
    token, account_id = await registered_supabase_user()
    started = time.perf_counter()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)

    keep = body["candidates"][:12]
    drop = body["candidates"][12:]
    for candidate in keep:
        response = await app_client.post(
            f"/api/v2/inventory/imports/{body['job_id']}/candidates/{candidate['id']}/confirm",
            headers=auth(token),
        )
        assert response.status_code == 200, response.text
    for candidate in drop:
        response = await app_client.post(
            f"/api/v2/inventory/imports/{body['job_id']}/candidates/{candidate['id']}/reject",
            headers=auth(token),
        )
        assert response.status_code == 200, response.text
    elapsed = time.perf_counter() - started

    listing = await app_client.get("/api/v2/inventory/items", headers=auth(token))
    assert listing.json()["pagination"]["total"] == 12

    factory = get_sessionmaker()
    async with factory() as session:
        context = await shelf.gather(session, account_id=account_id)
    # Everything kept is usable by the engines immediately; nothing is a draft.
    assert len(context.owned) == 12
    assert context.draft_count == 0

    # Not the human number — that is measured separately — but the server must
    # not be the thing that spends the three minutes.
    assert elapsed < 30, f"the server side alone took {elapsed:.1f}s for 15 items"


@pytest.mark.asyncio
async def test_taps_can_be_sent_together_when_they_outrun_the_network(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    decisions = [
        {"candidate_id": row["id"], "accept": index < 12}
        for index, row in enumerate(body["candidates"])
    ]

    response = await app_client.post(
        f"/api/v2/inventory/imports/{body['job_id']}/decisions",
        headers=auth(token), json={"decisions": decisions},
    )
    assert response.status_code == 200, response.text
    assert response.json()["confirmed_count"] == 12
    assert response.json()["rejected_count"] == 3
    assert response.json()["pending_count"] == 0

    listing = await app_client.get("/api/v2/inventory/items", headers=auth(token))
    assert listing.json()["pagination"]["total"] == 12


@pytest.mark.asyncio
async def test_the_same_candidate_cannot_be_decided_twice_in_one_request(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    token, _ = await registered_supabase_user()
    body = await _capture(app_client, token, shelf_photo=shelf_photo)
    candidate_id = body["candidates"][0]["id"]

    response = await app_client.post(
        f"/api/v2/inventory/imports/{body['job_id']}/decisions",
        headers=auth(token),
        json={"decisions": [
            {"candidate_id": candidate_id, "accept": True},
            {"candidate_id": candidate_id, "accept": False},
        ]},
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------
def _prompt_text() -> str:
    """The prompt as the model reads it — line wrapping is not load-bearing."""
    return " ".join(batch.SYSTEM.lower().split())


def test_the_batch_prompt_keeps_the_single_item_boundary():
    lowered = _prompt_text()
    for rule in ("never diagnose", "only visible evidence", "never invent"):
        assert rule in lowered, f"the batch prompt does not say {rule!r}"
    # The supplement boundary, verbatim from the single-item prompt's intent.
    for word in ("dosage advice", "prescriptions", "disease claims", "pregnancy advice"):
        assert word in lowered, f"the batch prompt drops the supplement boundary: {word!r}"


def test_the_batch_prompt_forbids_padding_the_list():
    """A longer list looks more useful and is the easy way to be wrong."""
    lowered = _prompt_text()
    assert "never invent a product to make the list longer" in lowered
    assert "unreadable_count" in lowered


def test_a_candidate_validates_against_the_same_contract_as_a_typed_item():
    """A shelf photo cannot smuggle in a category or field a person could not type."""
    with pytest.raises(ValueError):
        _extracted([("cookware", "Steel kadhai", "Hawkins", {})])
    with pytest.raises(ValueError):
        _extracted([("beauty", "Serum", "Brand", {"heel_height": 3})])


def test_the_batch_is_capped_so_a_review_list_stays_finishable():
    rows = SHELF_OF_FIFTEEN * 3
    assert len(rows) > BATCH_ITEM_LIMIT
    with pytest.raises(ValueError):
        _extracted(rows)


@pytest.mark.asyncio
async def test_the_flag_still_gates_the_route(
    db_clean, app_client, registered_supabase_user, media_root, shelf_photo
):
    """On by default now, but an operator switching it off must still close it."""
    from app.shared.flags.models import FeatureFlag

    token, _ = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(FeatureFlag(key="v2_inventory_batch", enabled=False, description="off for this test"))
        await session.commit()

    shelf_photo(_extracted())
    asset_id = await _upload(app_client, token)
    response = await app_client.post(
        "/api/v2/inventory/extract/batch", headers=auth(token),
        json={"media_asset_id": asset_id, "capture_type": "shelf_photo"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["feature"] == "v2_inventory_batch"
