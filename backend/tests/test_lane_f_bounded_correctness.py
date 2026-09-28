"""Lane F: bounded correctness cleanup, against PostgreSQL 16.

Each block below is one independently reproduced defect, proved through the
real routes and services it lived in:

* A — a retained wardrobe, shoe or accessory row crashed the Care shelf
* B — archiving one item poisoned the duplicate list and the whole summary
* C — a supplement PATCH carrying ``raw_name: null`` became a server error
* D — account-facing "today" came from the server's UTC date, not the customer's
* H — a Unicode scheduler credential became a 500 instead of the refusal
* I — deleting a photo left inventory items pointing at it
* J — the deferred-purchase notice swallowed every error, not just the expected
* K — the unused weather module and its background refresh are gone
* P — two tests that only passed on POSIX paths and a UTF-8 locale

The scan-history (E), invite-reservation (F) and allowance-reservation (G)
defects have their own modules. No test here waits on the wall clock: the one
instant that matters is injected into :mod:`app.domains.planning.clock`.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PureWindowsPath

import pytest
from app import config
from app.api.v2 import internal_scheduler
from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import (
    AccessoryItemDetail,
    DuplicateCandidate,
    InventoryCategory,
    InventoryItem,
    InventoryItemImage,
    ShoeItemDetail,
    WardrobeItemDetail,
)
from app.domains.media.models import MEDIA_STATUS_DELETED, MediaAsset
from app.domains.media.storage import factory as storage_factory
from app.domains.planning import clock, notifications
from app.domains.planning import context as planning_context
from app.domains.purchase import check_service
from app.domains.recommendation import context as style_context
from app.domains.recommendation.models import PurchaseDecision, ShoppingCandidate
from app.domains.routines import shelf
from app.domains.supplements import service as supplement_service
from app.domains.supplements.identity import component_identity
from app.domains.supplements.models import SupplementLabelComponent
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError
from app.workers import notifications as worker
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tests.conftest import auth, png_bytes
from tests.test_domain_media import _MemoryStorage
from tests.test_notification_worker_operations import _at_local_hour, _opt_in

pytestmark = pytest.mark.asyncio

BACKEND = Path(__file__).resolve().parents[1]
LEGACY = ("wardrobe", "shoes", "accessories")
LEGACY_DETAIL = {
    "wardrobe": (WardrobeItemDetail, {"colour": "navy", "fabric": "cotton"}),
    "shoes": (ShoeItemDetail, {"shoe_type": "loafer", "colour": "brown"}),
    "accessories": (AccessoryItemDetail, {"accessory_type": "watch", "metal": "steel"}),
}

#: 19:00 UTC on 27 September is 00:30 on 28 September in India. For five and a
#: half hours of every day a UTC server is on the customer's yesterday.
INSTANT = datetime(2026, 9, 27, 19, 0, tzinfo=UTC)
INDIA_TODAY = date(2026, 9, 28)
SERVER_UTC_DATE = date(2026, 9, 27)


def _factory():
    return get_sessionmaker()


def ok(response, *allowed: int):
    codes = allowed or (200, 201)
    assert response.status_code in codes, response.text
    return response.json()


async def _item(app_client, token, category: str, name: str, details: dict | None = None) -> str:
    body = {"category": category, "display_name": name}
    if details:
        body["details"] = details
    return ok(await app_client.post("/api/v2/inventory/items", headers=auth(token), json=body))["id"]


# ---------------------------------------------------------------------------
# A. A retained legacy row cannot crash the Care shelf
# ---------------------------------------------------------------------------
async def _legacy_history(account_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """What production still holds from the retired surfaces: the categories and
    one confirmed, active item of each, with its legacy detail row."""
    async with _factory()() as session:
        await session.execute(pg_insert(InventoryCategory).values([
            {"key": key, "display_name": key.title(), "position": 90 + index, "active": False}
            for index, key in enumerate(LEGACY)
        ]).on_conflict_do_nothing(index_elements=["key"]))
        ids = {}
        for category in LEGACY:
            item = InventoryItem(
                account_id=account_id, category=category, display_name=f"Old {category}",
                verification_state="confirmed", status="active",
            )
            session.add(item)
            await session.flush()
            model, fields = LEGACY_DETAIL[category]
            session.add(model(item_id=item.id, **fields))
            ids[category] = item.id
        await session.commit()
    return ids


@pytest.mark.parametrize("category", LEGACY)
async def test_f_a1_a_retained_legacy_row_cannot_crash_the_care_shelf(
    app_client, db_clean, registered_supabase_user, category,
):
    token, account_id = await registered_supabase_user()
    beauty = await _item(app_client, token, "beauty", "Barrier cream", {"product_type": "moisturiser"})
    legacy = (await _legacy_history(account_id))[category]

    # The service the shelf, Care, the manager and the notification worker all
    # read through: it used to raise KeyError(category) here.
    async with _factory()() as session:
        context = await shelf.gather(session, account_id=account_id, today=date(2026, 9, 28))
        owned, drafts = await style_context.confirmed_inventory(session, account_id)
    assert {str(row.id) for row in context.owned} == {beauty}
    assert {str(row.id) for row in owned} == {beauty} and drafts == 0

    for path in ("/api/v2/shelf/summary", "/api/v2/shelf/expiring", "/api/v2/shelf/low-use",
                 "/api/v2/shelf/value-to-recover", "/api/v2/routines/improve"):
        body = ok(await app_client.get(path, headers=auth(token)))
        assert str(legacy) not in str(body), path


async def test_f_a1_the_detail_loader_leaves_a_legacy_row_out_instead_of_raising(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    beauty = await _item(app_client, token, "beauty", "Barrier cream", {"product_type": "moisturiser"})
    legacy = await _legacy_history(account_id)
    async with _factory()() as session:
        rows = (await session.execute(
            select(InventoryItem).where(InventoryItem.account_id == account_id)
        )).scalars().all()
        details = await inventory_service.details_for_many(session, rows)
    # No current detail semantics are invented for a retired category.
    assert set(details) == {uuid.UUID(beauty)}
    assert not set(legacy.values()) & set(details)
    assert all(not inventory_service.has_detail_authority(key) for key in LEGACY)


async def test_f_a2_the_current_beauty_and_hair_shelf_is_unchanged(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    cream = await _item(app_client, token, "beauty", "Barrier cream",
                        {"product_type": "moisturiser", "expiry_date": "2030-01-01"})
    oil = await _item(app_client, token, "hair", "Hair oil", {"product_type": "hair_oil"})
    perfume = await _item(app_client, token, "perfumes", "Evening scent", {"fragrance_family": "woody"})
    await _legacy_history(account_id)

    async with _factory()() as session:
        context = await shelf.gather(session, account_id=account_id, today=date(2026, 9, 28))
    assert {str(row.id) for row in context.owned} == {cream, oil, perfume}
    summary = ok(await app_client.get("/api/v2/shelf/summary", headers=auth(token)))
    assert summary["categories"]["beauty"]["product_count"] == 1
    assert summary["categories"]["hair"]["product_count"] == 1
    cream_row = summary["reports"]["beauty"]["products"][0]
    assert cream_row["inventory_item_id"] == cream and cream_row["effective_expiry"] == "2030-01-01"
    ranked = ok(await app_client.get("/api/v2/perfume/recommendation", headers=auth(token)))
    assert [row["inventory_item_id"] for row in ranked["recommendations"]] == [perfume]


async def test_f_a2_the_inventory_api_never_restores_a_retired_category(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _item(app_client, token, "beauty", "Barrier cream")
    legacy = await _legacy_history(account_id)
    headers = auth(token)

    for category in LEGACY:
        created = await app_client.post(
            "/api/v2/inventory/items", headers=headers, json={"category": category, "display_name": "New"},
        )
        assert created.status_code == 422, created.text
        listed = await app_client.get("/api/v2/inventory/items", headers=headers, params={"category": category})
        assert listed.status_code == 422, listed.text
        assert (await app_client.get(f"/api/v2/inventory/items/{legacy[category]}", headers=headers)).status_code == 404

    items = ok(await app_client.get("/api/v2/inventory/items", headers=headers))["items"]
    assert [row["category"] for row in items] == ["beauty"]
    summary = ok(await app_client.get("/api/v2/inventory/summary", headers=headers))
    assert set(summary["categories"]) == {"beauty", "hair", "perfumes", "supplements"}
    assert summary["total_items"] == 1


# ---------------------------------------------------------------------------
# B. Archiving one item cannot poison the duplicate list or the summary
# ---------------------------------------------------------------------------
async def _pair(account_id, a, b) -> uuid.UUID:
    async with _factory()() as session:
        row = DuplicateCandidate(
            account_id=account_id, item_a_id=uuid.UUID(str(a)), item_b_id=uuid.UUID(str(b)),
            confidence=0.9, reason="same product", status="pending",
        )
        session.add(row)
        await session.commit()
        return row.id


async def _candidate(candidate_id) -> DuplicateCandidate:
    async with _factory()() as session:
        return await session.get(DuplicateCandidate, candidate_id)


async def test_f_b1_archiving_an_item_retires_its_pending_duplicate_pairs(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    a = await _item(app_client, token, "beauty", "Cream A")
    b = await _item(app_client, token, "beauty", "Cream B")
    c = await _item(app_client, token, "beauty", "Cream C")
    ab, bc = await _pair(account_id, a, b), await _pair(account_id, b, c)

    ok(await app_client.delete(f"/api/v2/inventory/items/{b}", headers=auth(token)))

    for candidate_id in (ab, bc):
        row = await _candidate(candidate_id)
        assert (row.status, row.resolution) == ("resolved", inventory_service.DUPLICATE_RESOLUTION_ITEM_ARCHIVED)
        assert row.resolved_at is not None
    assert ok(await app_client.get("/api/v2/inventory/duplicates", headers=auth(token)))["candidates"] == []
    summary = ok(await app_client.get("/api/v2/inventory/summary", headers=auth(token)))
    assert summary["duplicate_candidates"] == 0 and summary["total_items"] == 2


async def test_f_b1_merging_a_pair_retires_the_archived_items_other_pairs(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    a = await _item(app_client, token, "beauty", "Cream A")
    b = await _item(app_client, token, "beauty", "Cream B")
    c = await _item(app_client, token, "beauty", "Cream C")
    ab, bc = await _pair(account_id, a, b), await _pair(account_id, b, c)

    ok(await app_client.post(
        f"/api/v2/inventory/duplicates/{ab}/resolve", headers=auth(token),
        json={"resolution": "merge", "canonical_item_id": a},
    ))
    merged, other = await _candidate(ab), await _candidate(bc)
    assert (merged.status, merged.resolution) == ("resolved", "merge")
    assert (other.status, other.resolution) == ("resolved", inventory_service.DUPLICATE_RESOLUTION_ITEM_ARCHIVED)
    ok(await app_client.get("/api/v2/inventory/summary", headers=auth(token)))


async def test_f_b2_b3_a_stale_historical_pair_is_retired_and_the_valid_pair_is_still_served(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    a = await _item(app_client, token, "beauty", "Cream A")
    b = await _item(app_client, token, "beauty", "Cream B")
    c = await _item(app_client, token, "hair", "Oil C")
    d = await _item(app_client, token, "hair", "Oil D")
    stale, valid = await _pair(account_id, a, b), await _pair(account_id, c, d)
    # History from before archiving retired pairs: B archived, the pair left pending.
    async with _factory()() as session:
        await session.execute(update(InventoryItem).where(InventoryItem.id == uuid.UUID(b)).values(status="archived"))
        await session.commit()

    summary = ok(await app_client.get("/api/v2/inventory/summary", headers=auth(token)))
    assert summary["duplicate_candidates"] == 1
    served = ok(await app_client.get("/api/v2/inventory/duplicates", headers=auth(token)))["candidates"]
    assert [row["id"] for row in served] == [str(valid)]
    assert {served[0]["item_a"]["id"], served[0]["item_b"]["id"]} == {c, d}

    retired = await _candidate(stale)
    assert (retired.status, retired.resolution) == ("resolved", inventory_service.DUPLICATE_RESOLUTION_ITEM_UNAVAILABLE)
    assert (await _candidate(valid)).status == "pending"


async def test_f_b2_a_cross_account_pair_is_never_exposed_and_never_fails_the_summary(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, _account_b = await registered_supabase_user()
    mine = await _item(app_client, token_a, "beauty", "Mine")
    theirs = await _item(app_client, token_b, "beauty", "Their secret serum")
    corrupt = await _pair(account_a, mine, theirs)

    summary = ok(await app_client.get("/api/v2/inventory/summary", headers=auth(token_a)))
    listed = ok(await app_client.get("/api/v2/inventory/duplicates", headers=auth(token_a)))
    assert summary["duplicate_candidates"] == 0 and listed["candidates"] == []
    assert theirs not in str(listed) and "Their secret serum" not in str(listed)
    assert (await _candidate(corrupt)).resolution == inventory_service.DUPLICATE_RESOLUTION_ITEM_UNAVAILABLE
    their_items = ok(await app_client.get("/api/v2/inventory/items", headers=auth(token_b)))["items"]
    assert [row["id"] for row in their_items] == [theirs]


async def test_f_b_a_database_failure_is_not_hidden_as_a_stale_pair(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    a = await _item(app_client, token, "beauty", "Cream A")
    b = await _item(app_client, token, "beauty", "Cream B")
    await _pair(account_id, a, b)

    async def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(inventory_service, "serialize_items", broken)
    with pytest.raises(RuntimeError):
        async with _factory()() as session:
            await inventory_service.duplicates(session, account_id, today=INDIA_TODAY)


# ---------------------------------------------------------------------------
# C. ``raw_name: null`` is refused at validation, never a 500
# ---------------------------------------------------------------------------
async def _supplement_fact(app_client, token) -> tuple[str, dict]:
    item = await _item(app_client, token, "supplements", "Vitamin C", {"supplement_name": "Vitamin C"})
    fact = ok(await app_client.post(
        f"/api/v2/supplements/items/{item}/label-facts", headers=auth(token),
        json={"raw_name": "Vitamin C (as ascorbic acid)", "amount": "500", "unit": "mg"},
    ))
    return item, fact


async def _stored_fact(fact_id) -> SupplementLabelComponent:
    async with _factory()() as session:
        return await session.get(SupplementLabelComponent, uuid.UUID(fact_id))


async def test_f_c1_an_omitted_raw_name_leaves_the_recorded_name_alone(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    item, fact = await _supplement_fact(app_client, token)
    before = await _stored_fact(fact["id"])
    base = f"/api/v2/supplements/items/{item}/label-facts/{fact['id']}"

    ok(await app_client.patch(base, headers=auth(token), json={}))
    ok(await app_client.patch(base, headers=auth(token), json={"amount": "250"}))

    after = await _stored_fact(fact["id"])
    assert (after.raw_name, after.normalized_name, after.canonical_component_key) == (
        before.raw_name, before.normalized_name, before.canonical_component_key,
    )
    assert str(after.amount) in {"250", "250.000000"}


async def test_f_c2_an_explicit_null_raw_name_is_refused_before_the_service(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, _ = await registered_supabase_user()
    item, fact = await _supplement_fact(app_client, token)
    before = await _stored_fact(fact["id"])
    reached: list = []

    def guarded(name):
        reached.append(name)
        return component_identity(name)

    monkeypatch.setattr(supplement_service, "component_identity", guarded)
    response = await app_client.patch(
        f"/api/v2/supplements/items/{item}/label-facts/{fact['id']}", headers=auth(token),
        json={"raw_name": None},
    )
    assert response.status_code == 422, response.text
    assert reached == [], "the null reached name normalisation"
    after = await _stored_fact(fact["id"])
    assert after.raw_name == before.raw_name and after.normalized_name == before.normalized_name


async def test_f_c3_a_valid_rename_still_recomputes_the_identity(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    item, fact = await _supplement_fact(app_client, token)
    ok(await app_client.patch(
        f"/api/v2/supplements/items/{item}/label-facts/{fact['id']}", headers=auth(token),
        json={"raw_name": "  Zinc (as zinc gluconate) "},
    ))
    after = await _stored_fact(fact["id"])
    canonical, _display = component_identity("  Zinc (as zinc gluconate) ")
    assert after.raw_name == "Zinc (as zinc gluconate)"
    assert after.normalized_name == canonical and after.canonical_component_key == (canonical or None)


# ---------------------------------------------------------------------------
# D. "Today" is the customer's date, resolved once and passed down
# ---------------------------------------------------------------------------
@pytest.fixture
def half_past_midnight_in_india(monkeypatch):
    """Every "now" the account-facing clock reads is ``INSTANT``."""
    monkeypatch.setattr(clock, "utcnow", lambda: INSTANT)
    assert clock.local_today(clock.DEFAULT_TIMEZONE) == INDIA_TODAY
    assert INSTANT.astimezone(UTC).date() == SERVER_UTC_DATE


async def _backdate(item_id: str, created: datetime) -> None:
    async with _factory()() as session:
        await session.execute(
            update(InventoryItem).where(InventoryItem.id == uuid.UUID(item_id)).values(created_at=created)
        )
        await session.commit()


async def test_f_d1_inventory_uses_the_india_date_at_00_30_ist(
    app_client, db_clean, registered_supabase_user, half_past_midnight_in_india,
):
    token, _ = await registered_supabase_user()
    headers = auth(token)
    # Expired on the customer's date (-1), "today" on the server's (0).
    cream = await _item(app_client, token, "beauty", "Barrier cream",
                        {"product_type": "moisturiser", "expiry_date": "2026-09-27"})
    # Thirty days old for the customer, twenty-nine for the server.
    await _backdate(cream, datetime(2026, 8, 29, 12, 0, tzinfo=UTC))

    expiring = ok(await app_client.get("/api/v2/inventory/expiring", headers=headers))["items"]
    row = next(row for row in expiring if row["id"] == cream)
    assert (row["days_to_expiry"], row["expiry_status"]) == (-1, "expired")
    assert cream in [row["id"] for row in ok(await app_client.get("/api/v2/inventory/low-use", headers=headers))["items"]]
    assert ok(await app_client.get(f"/api/v2/inventory/items/{cream}", headers=headers))["low_use"] is True
    value = ok(await app_client.get("/api/v2/inventory/value-to-recover", headers=headers))["items"]
    inputs = next(row["inputs"] for row in value if row["item_id"] == cream)
    assert (inputs["days_to_expiry"], inputs["days_since_last_use"]) == (-1, 30)
    assert ok(await app_client.get("/api/v2/inventory/summary", headers=headers))["low_use_products"] == 1
    listed = ok(await app_client.get("/api/v2/inventory/items", headers=headers, params={"expiry_status": "expired"}))
    assert [row["id"] for row in listed["items"]] == [cream]

    # A usage logged without a date is logged on the customer's date.
    oil = await _item(app_client, token, "hair", "Hair oil")
    logged = ok(await app_client.post(f"/api/v2/inventory/items/{oil}/usage", headers=headers, json={}))
    assert logged["last_used_at"] == INDIA_TODAY.isoformat()


async def test_f_d1_care_and_routines_agree_on_the_india_date(
    app_client, db_clean, registered_supabase_user, half_past_midnight_in_india,
):
    token, _ = await registered_supabase_user()
    headers = auth(token)
    cream = await _item(app_client, token, "beauty", "Barrier cream",
                        {"product_type": "moisturiser", "routine_position": "moisturise", "expiry_date": "2026-09-27"})
    await _backdate(cream, datetime(2026, 8, 29, 12, 0, tzinfo=UTC))

    expiring = ok(await app_client.get("/api/v2/shelf/expiring", headers=headers))
    assert [(row["inventory_item_id"], row["days_to_expiry"]) for row in expiring["expired"]] == [(cream, -1)]
    assert expiring["expiring_soon"] == []
    low = ok(await app_client.get("/api/v2/shelf/low-use", headers=headers))
    assert [row["inventory_item_id"] for row in low["products"]] == [cream]
    value = ok(await app_client.get("/api/v2/shelf/value-to-recover", headers=headers))
    assert value["items"][0]["inputs"]["days_to_expiry"] == -1

    # One page, two halves, one date: the shelf and Care both say "expired".
    improve = ok(await app_client.get("/api/v2/routines/improve", headers=headers))
    assert [row["inventory_item_id"] for row in improve["expiring"]["expired"]] == [cream]
    control = next(row for row in improve["care_product_controls"] if row["inventory_item_id"] == cream)
    assert control["eligible"] is False


async def test_f_d1_supplements_and_perfume_use_the_india_date(
    app_client, db_clean, registered_supabase_user, half_past_midnight_in_india,
):
    token, _ = await registered_supabase_user()
    headers = auth(token)
    tablet = await _item(app_client, token, "supplements", "Vitamin D",
                         {"supplement_name": "Vitamin D", "expiry_date": "2026-09-27"})
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers))
    row = next(row for row in summary["supplements"] if row["inventory_item_id"] == tablet)
    assert (row["expiry_state"], row["days_to_expiry"]) == ("past", -1)
    assert "expired" in [flag["flag"] for flag in row["flags"]]
    detail = ok(await app_client.get(f"/api/v2/supplements/items/{tablet}", headers=headers))
    assert (detail["expiry"]["state"], detail["expiry"]["days_to_expiry"]) == ("past", -1)

    # Worn four days ago for the customer, three for the server: only the
    # server's date would call it "recently worn".
    scent = await _item(app_client, token, "perfumes", "Evening scent", {"fragrance_family": "woody"})
    ok(await app_client.post(f"/api/v2/inventory/items/{scent}/usage", headers=headers, json={"used_on": "2026-09-24"}))
    ranked = ok(await app_client.get("/api/v2/perfume/recommendation", headers=headers))["recommendations"]
    reasons = next(row["reasons"] for row in ranked if row["inventory_item_id"] == scent)
    assert "perfume.recently_worn" not in [reason["rule_id"] for reason in reasons]


async def test_f_d3_a_goal_target_is_checked_against_the_india_date(
    app_client, db_clean, registered_supabase_user, half_past_midnight_in_india,
):
    token, _ = await registered_supabase_user()
    headers = auth(token)
    # With no stated start the goal starts on the customer's today (28th), so
    # a target of the 27th is before it — the server's date would allow it.
    refused = await app_client.post(
        "/api/v2/goals", headers=headers,
        json={"kind": "custom", "title": "Finish the serum", "target_date": SERVER_UTC_DATE.isoformat()},
    )
    assert refused.status_code == 422, refused.text
    accepted = ok(await app_client.post(
        "/api/v2/goals", headers=headers,
        json={"kind": "custom", "title": "Finish the serum", "target_date": INDIA_TODAY.isoformat()},
    ))
    assert accepted["starts_on"] == INDIA_TODAY.isoformat()
    # A stated start is still checked by the schema, whatever today is.
    stated = await app_client.post(
        "/api/v2/goals", headers=headers,
        json={"kind": "custom", "title": "Stated", "starts_on": "2026-09-20", "target_date": "2026-09-19"},
    )
    assert stated.status_code == 422, stated.text
    ok(await app_client.post(
        "/api/v2/goals", headers=headers,
        json={"kind": "custom", "title": "Stated", "starts_on": "2026-09-20", "target_date": "2026-09-25"},
    ))


async def test_f_d2_an_explicit_account_timezone_is_honoured(
    app_client, db_clean, registered_supabase_user, half_past_midnight_in_india,
):
    token, account_id = await registered_supabase_user()
    cream = await _item(app_client, token, "beauty", "Barrier cream",
                        {"product_type": "moisturiser", "expiry_date": "2026-09-27"})
    async with _factory()() as session:
        india = await planning_context.account_today(session, account_id)
        los_angeles = await planning_context.account_today(session, account_id, timezone_name="America/Los_Angeles")
        assert (india, los_angeles) == (INDIA_TODAY, date(2026, 9, 27))
        for today, expected in ((india, (-1, "expired")), (los_angeles, (0, "expiring_soon"))):
            rows = await inventory_service.expiring_items(session, account_id, today=today)
            row = next(row for row in rows if row["id"] == cream)
            assert (row["days_to_expiry"], row["expiry_status"]) == expected
            context = await shelf.gather(session, account_id=account_id, today=today)
            assert context.today == today


#: Every account-facing module this lane repaired. Pure calendar helpers
#: elsewhere may still compare dates; these may not decide "today" themselves.
REPAIRED_DATE_MODULES = (
    "app/api/v2/inventory.py",
    "app/api/v2/supplements.py",
    "app/domains/care/service.py",
    "app/domains/inventory/schemas.py",
    "app/domains/inventory/service.py",
    "app/domains/planning/compiler.py",
    "app/domains/progress/metrics.py",
    "app/domains/progress/schemas.py",
    "app/domains/progress/service.py",
    "app/domains/purchase/value_service.py",
    "app/domains/routines/compiler.py",
    "app/domains/routines/manager.py",
    "app/domains/routines/perfume.py",
    "app/domains/routines/rules.py",
    "app/domains/routines/service.py",
    "app/domains/routines/shelf.py",
    "app/domains/supplements/detail.py",
    "app/domains/supplements/engine.py",
    "app/domains/supplements/service.py",
)


def _server_date_reads(path: Path) -> list[int]:
    """Lines reading the server's calendar date: ``date.today`` or ``datetime.today``,
    called or passed as a factory."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Attribute) and node.attr == "today"
                and isinstance(node.value, ast.Name) and node.value.id in {"date", "datetime"}):
            found.append(node.lineno)
    return sorted(found)


async def test_f_d_static_guard_no_repaired_path_reads_the_server_date():
    for relative in REPAIRED_DATE_MODULES:
        assert _server_date_reads(BACKEND / relative) == [], relative


async def test_f_d_static_guard_catches_what_it_is_for(tmp_path):
    module = tmp_path / "regressed.py"
    module.write_text(
        "from datetime import date\n"
        "def f(today=None):\n    return today or date.today()\n"
        "FACTORY = date.today\n",
        encoding="utf-8",
    )
    assert _server_date_reads(module) == [3, 4]


# ---------------------------------------------------------------------------
# H. Every invalid scheduler credential is the same 401, never a 500
# ---------------------------------------------------------------------------
@pytest.fixture
def scheduler(monkeypatch):
    class Summary:
        ok = True
        processed = 0

    async def cycle():
        return Summary()

    monkeypatch.setattr(internal_scheduler.account_deletion, "run_cycle", cycle)

    def configure(token: str) -> None:
        monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", token)

    return configure


async def _call_scheduler(app_client, authorization: bytes | None):
    headers = {} if authorization is None else {"Authorization": authorization}
    return await app_client.post("/api/v2/internal/scheduler/account-deletion", headers=headers)


def _is_the_refusal(response) -> bool:
    return (
        response.status_code == 401
        and response.headers.get("www-authenticate") == "Bearer"
        and response.json()["detail"] == "Scheduler credentials are not valid."
    )


@pytest.mark.parametrize("configured", ["ascii-scheduler-secret-0123456789", "sécrét-plañificador-秘密"])
@pytest.mark.parametrize("presented", [
    None, b"Basic abc", b"Bearer ", b"Bearer    ", b"Bearer wrong-ascii-value",
    "Bearer ñandú-€-秘密".encode(), "Bearer sécrét-plañificador-秘".encode(), b"Bearer \xff\xfe\xfd",
])
async def test_f_h1_an_invalid_credential_is_always_the_governed_401(
    app_client, db_clean, scheduler, configured, presented,
):
    scheduler(configured)
    response = await _call_scheduler(app_client, presented)
    assert _is_the_refusal(response), (response.status_code, response.text)


async def test_f_h1_the_comparison_never_raises_for_any_string(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", "sécrét-秘密")
    for presented in ("Bearer ñ", "Bearer \ud800", "Bearer 秘密", "Bearer x" * 50):
        with pytest.raises(HTTPException) as refused:
            internal_scheduler.require_scheduler_token(presented)
        assert refused.value.status_code == 401


async def test_f_h2_the_correct_credential_still_works(app_client, db_clean, scheduler, monkeypatch):
    scheduler("ascii-scheduler-secret-0123456789")
    response = await _call_scheduler(app_client, b"Bearer ascii-scheduler-secret-0123456789")
    assert response.status_code == 200, response.text
    # A Unicode configured value compares by its UTF-8 bytes and is accepted.
    monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", "sécrét-秘密")
    assert internal_scheduler.require_scheduler_token("Bearer sécrét-秘密") is None


# ---------------------------------------------------------------------------
# I. Deleting a photo takes it off the account's items, and only theirs
# ---------------------------------------------------------------------------
@pytest.fixture
def memory_storage():
    adapter = _MemoryStorage()
    storage_factory.set_storage(adapter)
    yield adapter
    storage_factory.set_storage(None)


async def _photo(app_client, token) -> str:
    return ok(await app_client.post(
        "/api/v2/media/upload", headers=auth(token), files={"file": ("p.png", png_bytes(), "image/png")},
    ))["id"]


async def test_f_i1_i2_i3_deleting_a_photo_unlinks_it_from_the_owners_items_only(
    app_client, db_clean, registered_supabase_user, memory_storage,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    doomed, kept = await _photo(app_client, token_a), await _photo(app_client, token_a)
    theirs = await _photo(app_client, token_b)
    item = ok(await app_client.post("/api/v2/inventory/items", headers=auth(token_a), json={
        "category": "beauty", "display_name": "Cream", "image_ids": [doomed, kept],
    }))
    their_item = ok(await app_client.post("/api/v2/inventory/items", headers=auth(token_b), json={
        "category": "beauty", "display_name": "Theirs", "image_ids": [theirs],
    }))
    # A malformed link from B's item to A's photo: not this deletion's to touch.
    async with _factory()() as session:
        session.add(InventoryItemImage(item_id=uuid.UUID(their_item["id"]), media_asset_id=uuid.UUID(doomed), position=9))
        await session.commit()
        doomed_key = (await session.get(MediaAsset, uuid.UUID(doomed))).storage_key
    assert doomed_key in memory_storage.objects

    ok(await app_client.delete(f"/api/v2/media/{doomed}", headers=auth(token_a)))

    assert doomed_key not in memory_storage.objects
    async with _factory()() as session:
        assert (await session.get(MediaAsset, uuid.UUID(doomed))).status == MEDIA_STATUS_DELETED
        assert await session.get(InventoryItem, uuid.UUID(item["id"])) is not None
    body = ok(await app_client.get(f"/api/v2/inventory/items/{item['id']}", headers=auth(token_a)))
    assert body["image_ids"] == [kept]
    updated = ok(await app_client.patch(f"/api/v2/inventory/items/{item['id']}", headers=auth(token_a), json={
        "display_name": "Cream, renamed", "image_ids": body["image_ids"],
    }))
    assert updated["image_ids"] == [kept] and updated["display_name"] == "Cream, renamed"
    their_body = ok(await app_client.get(f"/api/v2/inventory/items/{their_item['id']}", headers=auth(token_b)))
    assert set(their_body["image_ids"]) == {theirs, doomed}


# ---------------------------------------------------------------------------
# J. Deferred purchases skip only the expected refusals
# ---------------------------------------------------------------------------
async def _waiting_decisions(account_id, count: int) -> list[uuid.UUID]:
    candidates = []
    async with _factory()() as session:
        for index in range(count):
            candidate = ShoppingCandidate(account_id=account_id, source="manual", category="beauty",
                                          display_name=f"Candidate {index}")
            session.add(candidate)
            await session.flush()
            session.add(PurchaseDecision(
                account_id=account_id, candidate_id=candidate.id, strategy_key="care_purchase",
                recommendation_verdict="WAIT", recommendation_version="test",
                recommendation_snapshot={"environment": {"currently_deferred": True}},
                decision="waiting",
            ))
            await session.flush()
            candidates.append(candidate.id)
            # ``updated_at`` orders the walk: the first candidate is newest.
            await session.execute(update(PurchaseDecision).where(PurchaseDecision.candidate_id == candidate.id)
                                  .values(updated_at=datetime(2026, 9, 20, tzinfo=UTC) - timedelta(days=index)))
        await session.commit()
    return candidates


@pytest.mark.parametrize("refusal", [
    NotFoundError("We could not find that shopping candidate."),
    ValidationFailedError("Review and confirm the product details first.", field="verification_state"),
])
async def test_f_j1_an_unavailable_candidate_is_skipped_and_the_next_is_still_considered(
    db_clean, registered_supabase_user, monkeypatch, refusal,
):
    _token, account_id = await registered_supabase_user()
    first, second = await _waiting_decisions(account_id, 2)
    seen, queued = [], []

    async def resolve(session, *, candidate_id, **kwargs):
        seen.append(candidate_id)
        if candidate_id == first:
            raise refusal
        return {"verdict": {"environment": {"currently_deferred": False}}}

    async def queue(session, **kwargs):
        queued.append(kwargs["source_id"])
        return "queued"

    monkeypatch.setattr(check_service, "resolve_care_purchase_check", resolve)
    monkeypatch.setattr(notifications, "queue", queue)
    async with _factory()() as session:
        result = await notifications.queue_for_deferred_purchase_relevance(
            session, account_id=account_id, plan_date=INDIA_TODAY, timezone_name=clock.DEFAULT_TIMEZONE,
        )
    assert result == "queued"
    assert seen == [first, second] and queued == [str(second)]


async def test_f_j2_an_unexpected_failure_is_logged_generically_and_raised(
    db_clean, registered_supabase_user, monkeypatch, caplog,
):
    _token, account_id = await registered_supabase_user()
    await _waiting_decisions(account_id, 2)

    async def resolve(session, **kwargs):
        raise RuntimeError("private customer words: my eczema cream")

    monkeypatch.setattr(check_service, "resolve_care_purchase_check", resolve)
    caplog.set_level(logging.ERROR, logger="app.domains.planning.notifications")
    with pytest.raises(RuntimeError):
        async with _factory()() as session:
            await notifications.queue_for_deferred_purchase_relevance(
                session, account_id=account_id, plan_date=INDIA_TODAY, timezone_name=clock.DEFAULT_TIMEZONE,
            )
    lines = [record.getMessage() for record in caplog.records]
    assert "deferred_purchase_relevance_failed error_type=RuntimeError" in lines
    assert not any("eczema" in line or "private" in line for line in lines)


async def test_f_j2_the_worker_observes_the_failure_for_that_account(
    db_clean, registered_supabase_user, monkeypatch,
):
    _token, account_id = await registered_supabase_user()
    await _opt_in(account_id, hour=9)
    await _waiting_decisions(account_id, 1)

    async def nothing(*args, **kwargs):
        return None

    async def broken(session, **kwargs):
        raise RuntimeError("database connection lost")

    for trigger in ("queue_for_product_watch", "queue_for_environment_crossing",
                    "queue_for_protocol_day", "queue_for_running_out"):
        monkeypatch.setattr(notifications, trigger, nothing)
    monkeypatch.setattr(check_service, "resolve_care_purchase_check", broken)
    summary = worker.RunSummary()
    await worker.process_once(now=_at_local_hour(9), summary=summary)
    assert summary.accounts_failed == 1 and summary.failed_account_ids == [str(account_id)]


# ---------------------------------------------------------------------------
# K. The unused weather module is gone, and nothing needed it
# ---------------------------------------------------------------------------
async def test_f_k1_the_weather_module_has_no_production_caller_and_is_removed():
    assert importlib.util.find_spec("app.domains.planning.weather") is None
    needles = ("planning.weather", "planning import weather", "get_weather(")
    for root in (BACKEND / "app", BACKEND.parent / ".github" / "workflows"):
        for path in root.rglob("*"):
            if path.suffix in {".py", ".yml", ".yaml"}:
                text = path.read_text(encoding="utf-8")
                assert not any(needle in text for needle in needles), path
    # The live weather path is the provider adapter, which stays.
    assert importlib.util.find_spec("app.domains.planning.providers.open_meteo") is not None


# ---------------------------------------------------------------------------
# P. Portability: the two tests that only passed on POSIX with a UTF-8 locale
# ---------------------------------------------------------------------------
def _calls(path: Path):
    return [node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Call)]


async def test_f_p1_the_product_watch_path_comparison_is_platform_neutral():
    source = (BACKEND / "tests" / "test_step12c_product_watch.py").read_text(encoding="utf-8")
    assert "str(path.relative_to(backend_app))" not in source
    assert "path.relative_to(backend_app).as_posix()" in source
    # What Windows produces, and what the test now compares.
    assert PureWindowsPath("domains", "product", "watch.py").as_posix() == "domains/product/watch.py"
    assert str(PureWindowsPath("domains", "product", "watch.py")) != "domains/product/watch.py"


@pytest.mark.parametrize("name", ["test_step13_supplements.py", "test_step13_supplement_photo.py"])
async def test_f_p2_supplement_source_reads_are_explicitly_utf8(name):
    reads = [call for call in _calls(BACKEND / "tests" / name)
             if isinstance(call.func, ast.Attribute) and call.func.attr == "read_text"]
    assert reads, name
    for call in reads:
        encodings = [kw.value.value for kw in call.keywords if kw.arg == "encoding"]
        assert encodings == ["utf-8"], (name, call.lineno)
