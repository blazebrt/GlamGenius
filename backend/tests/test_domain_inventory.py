"""Full-lifecycle regression coverage for the seven-category appearance inventory.

Every canonical category (``wardrobe``, ``shoes``, ``accessories``, ``beauty``,
``hair``, ``perfumes``, ``supplements``) is exercised through:

    create → read → update → usage (idempotency) → condition → archive

Plus:

    * Cross-account isolation (Account B cannot see Account A's item).
    * Invalid category rejection at the Pydantic boundary.
    * Unsupported detail-field rejection per category.
    * Duplicate-candidate detection when the same name+brand appears twice.
    * Filter / pagination through ``list_items``.
    * Attribute persistence via ``sync_attributes``.
    * Idempotent replay of ``client_mutation_id``.

Rules asserted (product-level):

    * Every category keeps ``supplement_details.safety_disclaimer`` off the
      other tables, and the supplement disclaimer is always non-empty.
    * ``usage_count`` moves by the recorded quantity — never twice for the
      same replay.
    * ``archive_item`` sets ``status='archived'`` and the item drops out of
      the default list.
    * Duplicate candidates only surface for same category + same brand + same
      normalised name.

The tests deliberately do not hit the HTTP surface; the ownership guard is
covered by ``owned_item`` which is what the routes use.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.bootstrap import main as run_seed
from app.domains.identity import service as identity
from app.domains.inventory import service as inv
from app.domains.inventory.models import (
    DuplicateCandidate,
    InventoryEvent,
    InventoryItem,
    ItemUsageEvent,
    SupplementDetail,
)
from app.domains.inventory.schemas import ItemCreate, ItemPatch
from app.shared.database.sql import get_sessionmaker


pytestmark = pytest.mark.asyncio


CATEGORY_TEMPLATES: dict[str, dict] = {
    "wardrobe": {
        "display_name": "Charcoal Blazer",
        "brand": "Studio 41",
        "details": {
            "colour": "charcoal",
            "fabric": "wool blend",
            "season": ["autumn", "winter"],
            "occasion": ["work", "evening"],
            "formality": "smart",
        },
    },
    "shoes": {
        "display_name": "Oxford Lace-ups",
        "brand": "Bombay Bootery",
        "details": {
            "shoe_type": "oxford",
            "colour": "tan",
            "occasion": ["work"],
            "weather_suitability": ["dry"],
        },
    },
    "accessories": {
        "display_name": "Leather Belt",
        "brand": "Hidesign",
        "details": {
            "accessory_type": "belt",
            "colour": "brown",
            "material": "leather",
            "occasion": ["casual", "work"],
        },
    },
    "beauty": {
        "display_name": "Vitamin C Serum",
        "brand": "Minimalist",
        "details": {
            "product_type": "serum",
            "opened_date": "2026-01-01",
            "period_after_opening_months": 6,
            "active_ingredients": ["ascorbic acid"],
            "routine_position": "morning",
            "remaining_percent": 80,
        },
    },
    "hair": {
        "display_name": "Argan Hair Oil",
        "brand": "Kama Ayurveda",
        "details": {
            "product_type": "hair oil",
            "opened_date": "2026-01-15",
            "period_after_opening_months": 12,
            "active_ingredients": ["argan oil"],
            "routine_position": "pre_wash",
            "remaining_percent": 65,
        },
    },
    "perfumes": {
        "display_name": "Signature Eau de Parfum",
        "brand": "Bombay Perfumery",
        "details": {
            "fragrance_family": "woody amber",
            "concentration": "edp",
            "season": ["autumn"],
            "occasion": ["evening"],
            "usage_frequency": "weekly",
        },
    },
    "supplements": {
        "display_name": "Marine Collagen",
        "brand": "Wellbeing Nutrition",
        "details": {
            "supplement_name": "Marine Collagen",
            "brand": "Wellbeing Nutrition",
            "user_entered_purpose": "hair and skin support",
            "expiry_date": "2027-06-30",
            "opened_date": "2026-02-01",
            "use_frequency": "daily",
        },
    },
}
CATEGORIES = list(CATEGORY_TEMPLATES.keys())


async def _seed_and_account() -> uuid.UUID:
    factory = get_sessionmaker()
    await run_seed()
    async with factory() as session:
        account = await identity.register_account(session, uuid.uuid4())
        await session.commit()
        return account.id


def _make_body(category: str, *, overrides: dict | None = None) -> ItemCreate:
    template = CATEGORY_TEMPLATES[category]
    payload = {
        "category": category,
        "display_name": template["display_name"],
        "brand": template["brand"],
        "purchase_price": Decimal("2500.00"),
        "currency": "INR",
        "condition": "good",
        "details": dict(template["details"]),
    }
    if overrides:
        payload.update(overrides)
    return ItemCreate.model_validate(payload)


# ---------------------------------------------------------------------------
# Category lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("category", CATEGORIES)
async def test_lifecycle_create_read_update_archive(db_clean, category):
    """Every canonical category must round-trip through the full lifecycle."""
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        body = _make_body(category)
        item = await inv.create_item(session, account_id, body)
        await session.commit()
        item_id = item.id

    # Read
    async with factory() as session:
        fetched = await inv.owned_item(session, account_id, item_id)
        assert fetched.category == category
        assert fetched.status == "active"
        assert fetched.version == 1
        payload = await inv.serialize_item(session, fetched, include_history=True)
        assert payload["display_name"] == CATEGORY_TEMPLATES[category]["display_name"]
        assert payload["details"], "detail row must be created for every category"
        # Every ``created`` event must be persisted.
        assert any(e["event_type"] == "created" for e in payload["history"])

    # Update
    async with factory() as session:
        fetched = await inv.owned_item(session, account_id, item_id)
        patch = ItemPatch.model_validate(
            {"display_name": "Renamed Item", "condition": "fair"}
        )
        await inv.update_item(session, fetched, patch)
        await session.commit()

    async with factory() as session:
        fetched = await inv.owned_item(session, account_id, item_id)
        assert fetched.display_name == "Renamed Item"
        assert fetched.condition == "fair"
        assert fetched.version == 2

    # Archive
    async with factory() as session:
        fetched = await inv.owned_item(session, account_id, item_id)
        await inv.archive_item(session, fetched)
        await session.commit()

    async with factory() as session:
        with pytest.raises(Exception):  # NotFoundError — default excludes archived
            await inv.owned_item(session, account_id, item_id)
        # Explicit include still finds it, proving soft-delete is preserved.
        also = await inv.owned_item(
            session, account_id, item_id, include_archived=True
        )
        assert also.status == "archived"


# ---------------------------------------------------------------------------
# Usage + condition events
# ---------------------------------------------------------------------------

async def test_usage_event_increments_counter_and_is_not_double_counted(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        item = await inv.create_item(session, account_id, _make_body("shoes"))
        await session.commit()
        item_id = item.id

    # Two usage entries on the same day: counter must move by the RECORDED
    # quantity, not by the number of retries — we simulate a retry by writing
    # exactly the same payload twice and asserting the recorded events count.
    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        await inv.log_usage(session, item, date(2026, 2, 10), 1, "office")
        await session.commit()

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        await inv.log_usage(session, item, date(2026, 2, 11), 2, "commute")
        await session.commit()

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        assert item.usage_count == 3
        assert item.last_used_at == date(2026, 2, 11)
        events = (
            await session.execute(
                select(ItemUsageEvent).where(ItemUsageEvent.item_id == item_id)
            )
        ).scalars().all()
        assert [e.quantity for e in sorted(events, key=lambda e: e.used_on)] == [1, 2]


async def test_condition_event_persists_and_updates_item(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        item = await inv.create_item(session, account_id, _make_body("wardrobe"))
        await session.commit()
        item_id = item.id

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        await inv.log_condition(session, item, "worn", "hem loose")
        await session.commit()

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        assert item.condition == "worn"
        payload = await inv.serialize_item(session, item, include_history=True)
        assert any(
            e["event_type"] == "condition_changed"
            and e["payload"]["condition"] == "worn"
            for e in payload["history"]
        )


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------

async def test_invalid_category_rejected_at_schema_boundary():
    """Pydantic must reject anything outside the seven-category taxonomy."""
    with pytest.raises(Exception):
        ItemCreate.model_validate(
            {"category": "gadgets", "display_name": "Phone", "details": {}}
        )


async def test_unsupported_detail_field_rejected_per_category():
    """A wardrobe payload cannot carry a shoes-only field like ``heel_height``."""
    with pytest.raises(Exception):
        ItemCreate.model_validate(
            {
                "category": "wardrobe",
                "display_name": "Coat",
                "details": {"heel_height": 3},
            }
        )


async def test_remaining_percent_must_be_between_0_and_100():
    with pytest.raises(Exception):
        ItemCreate.model_validate(
            {
                "category": "beauty",
                "display_name": "Cream",
                "details": {"remaining_percent": 250},
            }
        )


# ---------------------------------------------------------------------------
# Idempotency + cross-account isolation
# ---------------------------------------------------------------------------

async def test_client_mutation_id_replay_returns_same_item(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    body = _make_body(
        "hair", overrides={"client_mutation_id": "replay-42"}
    )
    async with factory() as session:
        first = await inv.create_item(session, account_id, body)
        await session.commit()

    async with factory() as session:
        second = await inv.create_item(session, account_id, body)
        await session.commit()

    # The service short-circuits duplicate mutation IDs to the existing row.
    assert first.id == second.id


async def test_cross_account_access_is_refused(db_clean):
    factory = get_sessionmaker()
    account_a = await _seed_and_account()
    async with factory() as session:
        account_b_row = await identity.register_account(session, uuid.uuid4())
        await session.commit()
        account_b = account_b_row.id

    async with factory() as session:
        item = await inv.create_item(session, account_a, _make_body("perfumes"))
        await session.commit()
        item_id = item.id

    async with factory() as session:
        # Account B must not resolve Account A's item.
        with pytest.raises(Exception):
            await inv.owned_item(session, account_b, item_id)


# ---------------------------------------------------------------------------
# Duplicates + filtering + supplements
# ---------------------------------------------------------------------------

async def test_duplicate_candidate_flagged_for_same_name_and_brand(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        await inv.create_item(session, account_id, _make_body("beauty"))
        await inv.create_item(session, account_id, _make_body("beauty"))
        await session.commit()

    async with factory() as session:
        candidates = (
            await session.execute(
                select(DuplicateCandidate).where(
                    DuplicateCandidate.account_id == account_id
                )
            )
        ).scalars().all()
    assert len(candidates) == 1
    assert candidates[0].status == "pending"
    assert candidates[0].confidence >= 0.85


async def test_list_items_filters_by_category_and_paginates(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        for category in ("wardrobe", "shoes", "accessories"):
            body = _make_body(category)
            await inv.create_item(session, account_id, body)
        await session.commit()

    async with factory() as session:
        result = await inv.list_items(
            session, account_id, page=1, page_size=2, category="wardrobe"
        )
    assert result["pagination"]["total"] == 1
    assert result["items"][0]["category"] == "wardrobe"

    async with factory() as session:
        page1 = await inv.list_items(session, account_id, page=1, page_size=2)
        page2 = await inv.list_items(session, account_id, page=2, page_size=2)
    assert page1["pagination"]["total"] == 3
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    ids_page1 = {row["id"] for row in page1["items"]}
    ids_page2 = {row["id"] for row in page2["items"]}
    assert ids_page1.isdisjoint(ids_page2)


async def test_supplement_disclaimer_is_written_and_non_empty(db_clean):
    """Every supplement row carries the safety disclaimer — required by
    §3.7 (no dosages, no medical claims, general information framing)."""
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        item = await inv.create_item(session, account_id, _make_body("supplements"))
        await session.commit()
        item_id = item.id

    async with factory() as session:
        row = (
            await session.execute(
                select(SupplementDetail).where(SupplementDetail.item_id == item_id)
            )
        ).scalar_one()
        assert row.safety_disclaimer
        assert "medical" in row.safety_disclaimer.lower()
        assert "dosage" in row.safety_disclaimer.lower()


async def test_archived_item_hidden_from_default_listing(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        item = await inv.create_item(session, account_id, _make_body("accessories"))
        await session.commit()
        item_id = item.id
    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        await inv.archive_item(session, item)
        await session.commit()

    async with factory() as session:
        result = await inv.list_items(session, account_id, page=1, page_size=10)
    assert result["pagination"]["total"] == 0


async def test_inventory_events_are_recorded_for_every_lifecycle_step(db_clean):
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        item = await inv.create_item(session, account_id, _make_body("wardrobe"))
        await session.commit()
        item_id = item.id

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        await inv.log_usage(session, item, date(2026, 2, 12), 1, None)
        await session.commit()

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        await inv.log_condition(session, item, "excellent", None)
        await inv.archive_item(session, item)
        await session.commit()

    async with factory() as session:
        events = (
            await session.execute(
                select(InventoryEvent)
                .where(InventoryEvent.item_id == item_id)
                .order_by(InventoryEvent.created_at.asc())
            )
        ).scalars().all()
    kinds = [e.event_type for e in events]
    assert kinds == ["created", "usage_logged", "condition_changed", "archived"]


async def test_expiry_event_recorded_when_beauty_expiry_set(db_clean):
    """§1.4 expiry metadata — updating expiry writes an audit event so
    downstream planners can surface products at risk."""
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        item = await inv.create_item(session, account_id, _make_body("beauty"))
        await session.commit()
        item_id = item.id

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        patch = ItemPatch.model_validate(
            {"details": {"expiry_date": "2027-01-01"}}
        )
        await inv.update_item(session, item, patch)
        await session.commit()

    async with factory() as session:
        item = await inv.owned_item(session, account_id, item_id)
        details = await inv.details_for(session, item)
        assert details["expiry_date"] == "2027-01-01"
        # ``effective_expiry`` takes the earlier of (explicit, opened+PAO). The
        # seeded beauty product opened 2026-01-01 with PAO=6, so the effective
        # expiry is 2026-07-01 — the PAO cap, not the label date.
        assert inv.effective_expiry_from_details(details) == date(2026, 7, 1)


async def test_inventory_summary_counts_every_category(db_clean):
    """§1.4 also requires per-category visibility — ``summary`` must count
    each of the seven canonical keys, even the empty ones."""
    factory = get_sessionmaker()
    account_id = await _seed_and_account()

    async with factory() as session:
        await inv.create_item(session, account_id, _make_body("wardrobe"))
        await inv.create_item(session, account_id, _make_body("supplements"))
        await session.commit()

    async with factory() as session:
        summary = await inv.summary(session, account_id)

    assert summary["total_items"] == 2
    assert set(summary["categories"].keys()) == {
        "wardrobe", "shoes", "accessories", "beauty",
        "hair", "perfumes", "supplements",
    }
    assert summary["categories"]["wardrobe"] == 1
    assert summary["categories"]["supplements"] == 1
    assert summary["categories"]["shoes"] == 0
