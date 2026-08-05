"""§1.12 (+ §1.4 and §3.1 remainders) — progress through the routes, plus the
inventory and seed cases the earlier files leave open.

``test_domain_progress.py`` covers the metric registry and the service. This
file covers what the app calls: the metrics endpoint and its published
formulas, self-reports, the photo-comparison refusal, purchase metadata on
inventory items, and the seed's behaviour when a run fails part-way.

What this protects against
--------------------------
* A metric shown without the formula and version that produced it.
* Two photos taken in different light being presented as a before/after, which
  is the single most misleading thing this product could do.
* An overall "appearance score" appearing by the back door.
* Purchase price and date being lost, so value-to-recover cannot be explained.
* A failed seed leaving the reference data half-written.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.bootstrap import run as run_seed
from app.domains.inventory.models import InventoryEvent, InventoryItem
from app.domains.progress.models import ProgressGoal
from app.shared.database.sql import get_sessionmaker
from tests.conftest import auth, png_bytes
from tests.journey import ok


pytestmark = pytest.mark.asyncio


async def _seed():
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()


# ---------------------------------------------------------------------------
# Metrics through the API
# ---------------------------------------------------------------------------

async def test_metric_catalogue_publishes_formula_and_version(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()

    body = ok(await app_client.get("/api/v2/progress/metrics", headers=auth(token)))

    assert body["metrics"], "the app must be able to list what it can measure"
    assert body["no_overall_score"] is True
    for metric in body["metrics"]:
        assert metric["formula"], f"{metric['key']} has no published formula"
        assert metric["formula_version"], f"{metric['key']} has no formula version"
        assert metric["explanation"]
        assert metric["not_a_measure_of"], (
            f"{metric['key']} must say what it deliberately does not mean"
        )
        assert metric["direction"] in {"higher_is_better", "lower_is_better", "neutral"}


async def test_no_metric_is_an_overall_appearance_score(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()

    body = ok(await app_client.get("/api/v2/progress/metrics", headers=auth(token)))

    keys = [metric["key"] for metric in body["metrics"]]
    for key in keys:
        assert "overall" not in key
        assert "attractive" not in key
        assert "score" != key
    assert body["note"], "the refusal to produce one headline number must be stated"


async def test_metric_with_no_data_says_so_rather_than_showing_zero(
    app_client, db_clean, registered_supabase_user
):
    """A zero and a "we do not know yet" mean completely different things to
    someone looking at their own progress."""
    token, _ = await registered_supabase_user()
    await _seed()

    body = ok(await app_client.get(
        "/api/v2/progress?as_of=2026-02-16", headers=auth(token)
    ))

    unavailable = [m for m in body["metrics"] if m["value"] is None]
    assert unavailable, "a brand-new account cannot have every metric populated"
    for metric in unavailable:
        assert metric["status"] == "unavailable"
        assert metric["missing_inputs"], "an empty metric must say what it needs"


async def test_single_metric_detail_carries_its_history(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()

    body = ok(await app_client.get(
        "/api/v2/progress/metrics/routine_consistency?as_of=2026-02-16",
        headers=auth(token),
    ))

    assert body["definition"]["key"] == "routine_consistency"
    assert body["definition"]["formula"]
    assert body["definition"]["formula_version"]
    assert body["current"]["key"] == "routine_consistency"
    assert "history" in body


async def test_unknown_metric_key_is_refused(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()
    resp = await app_client.get(
        "/api/v2/progress/metrics/attractiveness", headers=auth(token)
    )
    assert resp.status_code in (404, 422)


async def test_self_report_is_the_only_source_of_the_confidence_metric(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()

    first = await app_client.post(
        "/api/v2/progress/self-report",
        headers=auth(token),
        json={"rating": 4, "recorded_on": "2026-02-16", "note": "Felt good today."},
    )
    assert first.status_code == 200, first.text

    # One reading is not a measurement. The metric says so instead of showing it.
    body = ok(await app_client.get(
        "/api/v2/progress?as_of=2026-02-16", headers=auth(token)
    ))
    reported = next(
        m for m in body["metrics"] if m["key"] == "user_reported_confidence"
    )
    assert reported["value"] is None
    assert reported["status"] == "unavailable"
    assert "never guess" in reported["note"].lower()

    assert (await app_client.post(
        "/api/v2/progress/self-report",
        headers=auth(token),
        json={"rating": 5, "recorded_on": "2026-02-17"},
    )).status_code == 200

    body = ok(await app_client.get(
        "/api/v2/progress?as_of=2026-02-17", headers=auth(token)
    ))
    reported = next(
        m for m in body["metrics"] if m["key"] == "user_reported_confidence"
    )
    assert reported["value"] == 4.5, "the metric is the mean of what the user said"
    assert reported["status"] in {"ok", "partial"}


async def test_self_report_rating_is_bounded(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()
    resp = await app_client.post(
        "/api/v2/progress/self-report", headers=auth(token), json={"rating": 9}
    )
    assert resp.status_code == 422


async def test_progress_is_scoped_to_the_account(
    app_client, db_clean, registered_supabase_user
):
    first_token, _ = await registered_supabase_user()
    second_token, _ = await registered_supabase_user()
    await _seed()
    await app_client.post(
        "/api/v2/progress/self-report",
        headers=auth(first_token),
        json={"rating": 5, "recorded_on": "2026-02-16"},
    )

    other = ok(await app_client.get(
        "/api/v2/progress?as_of=2026-02-16", headers=auth(second_token)
    ))

    reported = next(
        m for m in other["metrics"] if m["key"] == "user_reported_confidence"
    )
    assert reported["value"] is None


# ---------------------------------------------------------------------------
# Photo comparison
# ---------------------------------------------------------------------------

async def _upload(client, token):
    return ok(await client.post(
        "/api/v2/media/upload",
        headers=auth(token),
        files={"file": ("p.png", png_bytes(), "image/png")},
    ))["id"]


async def test_photos_taken_in_different_light_are_refused_a_comparison(
    app_client, db_clean, registered_supabase_user, media_root
):
    """The refusal is the feature. Different light changes skin and hair more
    than almost anything the user could actually have changed."""
    token, _ = await registered_supabase_user()
    await _seed()

    baseline = await _upload(app_client, token)
    current = await _upload(app_client, token)
    ok(await app_client.post(
        "/api/v2/progress/photos",
        headers=auth(token),
        json={
            "media_id": baseline, "body_area": "face", "lighting": "daylight_window",
            "angle": "front", "framing": "face_close", "taken_on": "2026-01-01",
        },
    ))
    ok(await app_client.post(
        "/api/v2/progress/photos",
        headers=auth(token),
        json={
            "media_id": current, "body_area": "face", "lighting": "indoor_warm",
            "angle": "front", "framing": "face_close", "taken_on": "2026-02-16",
        },
    ))

    body = ok(await app_client.get(
        "/api/v2/progress/comparisons?body_area=face", headers=auth(token)
    ))

    assert body["photos_held"] == 2, "both photos must be considered"
    assert body["comparable"] is False, (
        "two photos in different light must never be shown as a before/after"
    )
    assert body["comparison"] is None
    assert body["rejected"], "the refusal must name the photo it refused and why"
    reasons = " ".join(
        reason for row in body["rejected"] for reason in row["reasons"]
    ).lower()
    assert "light" in reasons
    assert "lighting rather than you" in body["message"].lower()
    assert body["guidance"], "a refusal must say how to take a comparable photo"


async def test_photos_of_different_body_areas_are_never_compared(
    app_client, db_clean, registered_supabase_user, media_root
):
    token, _ = await registered_supabase_user()
    await _seed()

    for media_id, area, day in (
        (await _upload(app_client, token), "face", "2026-01-01"),
        (await _upload(app_client, token), "hair", "2026-02-16"),
    ):
        ok(await app_client.post(
            "/api/v2/progress/photos",
            headers=auth(token),
            json={
                "media_id": media_id, "body_area": area, "lighting": "daylight_window",
                "angle": "front", "framing": "face_close", "taken_on": day,
            },
        ))

    face = ok(await app_client.get(
        "/api/v2/progress/comparisons?body_area=face", headers=auth(token)
    ))
    # Only one face photo exists, so there is nothing to compare it with — and
    # the hair photo is never a candidate.
    assert face["comparable"] is False
    assert face["photos_held"] == 1
    assert face["message"], "the reason there is no comparison must be stated"


async def test_comparison_rejects_an_unknown_body_area(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await _seed()
    resp = await app_client.get(
        "/api/v2/progress/comparisons?body_area=aura", headers=auth(token)
    )
    assert resp.status_code == 422


async def test_progress_photo_requires_an_owned_media_asset(
    app_client, db_clean, registered_supabase_user, media_root
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    await _seed()
    media_id = await _upload(app_client, owner_token)

    resp = await app_client.post(
        "/api/v2/progress/photos",
        headers=auth(intruder_token),
        json={
            "media_id": media_id, "body_area": "face", "lighting": "daylight_window",
            "angle": "front", "framing": "face_close", "taken_on": "2026-02-16",
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

async def test_goal_lifecycle_through_the_api(
    app_client, db_clean, registered_supabase_user
):
    token, uid = await registered_supabase_user()
    await _seed()

    created = ok(await app_client.post(
        "/api/v2/goals",
        headers=auth(token),
        json={
            "kind": "routine",
            "title": "Stay consistent this month",
            "metric_key": "routine_consistency",
            "target_value": 0.8,
            "starts_on": "2026-02-16",
            "target_date": "2026-03-16",
        },
    ))
    assert created["status"] == "active"
    assert created["metric_key"] == "routine_consistency"
    # The metric has no data yet, so the goal says so rather than inventing a
    # starting point to measure against.
    assert "starting_value" in created
    assert created["progress"] is None
    assert created["progress_note"]

    listed = ok(await app_client.get("/api/v2/goals", headers=auth(token)))
    assert any(row["id"] == created["id"] for row in listed["goals"])

    achieved = ok(await app_client.patch(
        f"/api/v2/goals/{created['id']}",
        headers=auth(token),
        json={"status": "achieved"},
    ))
    assert achieved["status"] == "achieved"

    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(ProgressGoal).where(ProgressGoal.account_id == uid)
        )).scalar_one()
    assert row.completed_at is not None


async def test_goal_from_another_account_is_not_patchable(
    app_client, db_clean, registered_supabase_user
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    await _seed()
    goal = ok(await app_client.post(
        "/api/v2/goals",
        headers=auth(owner_token),
        json={"kind": "custom", "title": "Mine", "starts_on": "2026-02-16"},
    ))

    resp = await app_client.patch(
        f"/api/v2/goals/{goal['id']}",
        headers=auth(intruder_token),
        json={"status": "abandoned"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# §1.4 remainder — purchase metadata, notes, archive and restore
# ---------------------------------------------------------------------------

async def test_purchase_metadata_round_trips(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    created = ok(await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": "wardrobe",
            "display_name": "Charcoal Blazer",
            "brand": "Fable",
            "purchase_date": "2025-11-02",
            "purchase_price": "4999.00",
            "currency": "INR",
            "condition": "excellent",
        },
    ))

    assert created["purchase_date"] == "2025-11-02"
    assert created["purchase_price"] == 4999.0
    assert created["currency"] == "INR"
    assert created["condition"] == "excellent"

    read = ok(await app_client.get(
        f"/api/v2/inventory/items/{created['id']}", headers=auth(token)
    ))
    assert read["purchase_price"] == 4999.0


async def test_negative_purchase_price_is_refused(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": "wardrobe", "display_name": "Impossible Blazer",
            "purchase_price": "-10.00",
        },
    )
    assert resp.status_code == 422


async def test_archived_item_keeps_its_history_and_can_be_confirmed_back(
    app_client, db_clean, registered_supabase_user
):
    """Removing an item from the active list must not destroy what it recorded
    — the value-to-recover and usage numbers depend on that history."""
    token, uid = await registered_supabase_user()
    created = ok(await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={"category": "shoes", "display_name": "Brown Derbies"},
    ))
    ok(await app_client.post(
        f"/api/v2/inventory/items/{created['id']}/usage",
        headers=auth(token),
        json={"used_on": "2026-02-16"},
    ))

    removed = ok(await app_client.delete(
        f"/api/v2/inventory/items/{created['id']}", headers=auth(token)
    ))
    assert removed["status"] == "archived"
    assert "history is retained" in removed["message"]

    listing = ok(await app_client.get("/api/v2/inventory/items", headers=auth(token)))
    assert created["id"] not in {row["id"] for row in listing["items"]}

    # Archived means gone from the active surface: the item is no longer
    # readable, usable or re-archivable through the API.
    assert (await app_client.get(
        f"/api/v2/inventory/items/{created['id']}", headers=auth(token)
    )).status_code == 404
    assert (await app_client.post(
        f"/api/v2/inventory/items/{created['id']}/usage",
        headers=auth(token),
        json={"used_on": "2026-02-17"},
    )).status_code == 404

    # But the row and everything it recorded survive, so the history the
    # metrics are built from is not rewritten by a tidy-up.
    factory = get_sessionmaker()
    async with factory() as session:
        row = await session.get(InventoryItem, uuid.UUID(created["id"]))
        events = (await session.execute(
            select(func.count(InventoryEvent.id)).where(
                InventoryEvent.item_id == uuid.UUID(created["id"])
            )
        )).scalar_one()
    assert row is not None
    assert row.status == "archived"
    assert row.usage_count == 1
    assert events >= 3, "created, used and archived must all be on the record"


async def test_notes_ride_along_with_a_usage_event(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    created = ok(await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={"category": "perfumes", "display_name": "Citrus EDT",
              "details": {"fragrance_family": "citrus"}},
    ))

    ok(await app_client.post(
        f"/api/v2/inventory/items/{created['id']}/usage",
        headers=auth(token),
        json={"used_on": "2026-02-16", "note": "Wore it to the office."},
    ))

    detail = ok(await app_client.get(
        f"/api/v2/inventory/items/{created['id']}", headers=auth(token)
    ))
    usage_events = [e for e in detail["history"] if e["event_type"] == "usage_logged"]
    assert usage_events, "using something must show up in its history"
    assert usage_events[0]["payload"]["used_on"] == "2026-02-16"

    # The note the user typed is kept on the usage event itself.
    from app.domains.inventory.models import ItemUsageEvent

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(ItemUsageEvent).where(
                ItemUsageEvent.item_id == uuid.UUID(created["id"])
            )
        )).scalars().all()
    assert [row.note for row in rows] == ["Wore it to the office."]


# ---------------------------------------------------------------------------
# §3.1 remainder — a failing seed must not leave half-written reference data
# ---------------------------------------------------------------------------

async def test_failed_seed_rolls_back_completely(db_clean, monkeypatch):
    """The seed runs in one transaction. If a later domain raises, nothing it
    wrote — including the domains that had already succeeded — may persist."""
    from app import bootstrap
    from app.domains.inventory.models import InventoryCategory
    from app.shared.flags.models import FeatureFlag

    factory = get_sessionmaker()

    async def _explode(session):
        raise RuntimeError("seed failure injected by the test")

    monkeypatch.setattr(bootstrap, "seed_feature_flags", _explode)

    with pytest.raises(RuntimeError):
        async with factory() as session:
            await bootstrap.run(session)
            await session.commit()

    async with factory() as session:
        categories = (await session.execute(
            select(func.count(InventoryCategory.key))
        )).scalar_one()
        flags = (await session.execute(
            select(func.count(FeatureFlag.key))
        )).scalar_one()

    assert categories == 0, "a failed seed must not leave inventory categories behind"
    assert flags == 0

    # And a clean rerun still works, so the failure is recoverable.
    async with factory() as session:
        monkeypatch.undo()
        result = await bootstrap.run(session)
        await session.commit()
    assert result["counts"]["inventory_categories"] == 7
