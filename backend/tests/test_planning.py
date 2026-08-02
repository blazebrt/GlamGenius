"""Phase 5 Today engine and weekly planner contracts.

The tests that matter most here are about time and about cost. Time, because a
plan built on the server's date is wrong for five and a half hours a day in
India. Cost, because a Today screen that recomputes on every open is a Today
screen nobody can afford to run.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from sqlalchemy import select

from app.domains.planning import clock, notifications
from app.domains.planning.context import cache_key, infer_occasion
from app.domains.planning.models import (
    DailyPlan, DailyPlanInput, NotificationDelivery, OutfitSchedule, PlanRecalculationEvent,
)
from app.domains.planning.notifications import dedup_hash, in_quiet_hours
from app.domains.planning.weekly import repetition_report
from app.shared.database import sql
from tests.conftest import auth

IST = "Asia/Kolkata"

WARDROBE = [
    ("wardrobe", "Navy formal shirt", {"colour": "navy", "fabric": "cotton", "fit": "slim shirt", "size": "M", "formality": "business formal", "occasion": ["office"]}),
    ("wardrobe", "White cotton shirt", {"colour": "white", "fabric": "cotton", "fit": "regular shirt", "size": "M", "formality": "business casual", "occasion": ["office"]}),
    ("wardrobe", "Grey linen shirt", {"colour": "grey", "fabric": "linen", "fit": "regular shirt", "size": "M", "formality": "business casual"}),
    ("wardrobe", "Charcoal formal trousers", {"colour": "charcoal", "fabric": "wool", "fit": "tailored trousers", "size": "32", "formality": "business formal"}),
    ("wardrobe", "Beige chino trousers", {"colour": "beige", "fabric": "cotton", "fit": "regular chinos", "size": "32", "formality": "business casual"}),
    ("wardrobe", "Olive cotton trousers", {"colour": "olive green", "fabric": "cotton", "fit": "regular trousers", "size": "32", "formality": "casual"}),
    ("shoes", "Black leather derby shoes", {"shoe_type": "derby", "colour": "black", "size": "9", "comfort": "all day"}),
    ("shoes", "Tan loafers", {"shoe_type": "loafers", "colour": "tan", "size": "9", "comfort": "comfortable"}),
    ("accessories", "Silver wristwatch", {"accessory_type": "watch", "metal": "silver", "colour": "silver"}),
    ("beauty", "Daily face serum", {"product_type": "serum", "routine_position": "morning", "remaining_percent": 60}),
    ("perfumes", "Cedar day perfume", {"fragrance_family": "woody", "concentration": "EDP"}),
]


async def add_item(client, token, category, name, details):
    response = await client.post(
        "/api/v2/inventory/items",
        json={"category": category, "display_name": name, "details": details},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def stock(client, token, rows=WARDROBE):
    return [await add_item(client, token, *row) for row in rows]


async def today(client, token, plan_date=None):
    params = {"plan_date": plan_date} if plan_date else None
    return await client.get("/api/v2/today", params=params, headers=auth(token))


# --- Timezone ---------------------------------------------------------------


def test_today_is_the_users_local_date_not_the_servers():
    """20:00 UTC is already tomorrow in India. The plan must follow the user."""
    evening_utc = datetime(2026, 8, 1, 20, 0, tzinfo=dt_timezone.utc)
    assert clock.local_today(IST, moment=evening_utc) == date(2026, 8, 2)
    assert clock.local_today("UTC", moment=evening_utc) == date(2026, 8, 1)

    # And just before the rollover it is still the same day.
    late_afternoon = datetime(2026, 8, 1, 18, 0, tzinfo=dt_timezone.utc)
    assert clock.local_today(IST, moment=late_afternoon) == date(2026, 8, 1)


def test_indian_cities_resolve_to_india_and_junk_falls_back_safely():
    for city in ("Mumbai", "bengaluru", " New Delhi ", "KOLKATA", "Gurugram"):
        assert clock.resolve_timezone(None, city=city) == IST
    # An unknown city or a bad timezone string must not raise.
    assert clock.resolve_timezone(None, city="Atlantis") == IST
    assert clock.resolve_timezone("Not/AZone") == IST
    assert clock.resolve_timezone("Europe/London") == "Europe/London"


def test_day_bounds_are_built_in_the_users_zone():
    start, end = clock.day_bounds(date(2026, 8, 2), IST)
    # Midnight in Kolkata is 18:30 the previous day in UTC.
    assert start.astimezone(dt_timezone.utc) == datetime(2026, 8, 1, 18, 30, tzinfo=dt_timezone.utc)
    assert (end - start) == timedelta(days=1)


def test_the_week_always_starts_on_monday():
    assert clock.week_start(date(2026, 8, 1)) == date(2026, 7, 27)     # a Saturday
    assert clock.week_start(date(2026, 7, 27)) == date(2026, 7, 27)    # already Monday
    dates = clock.week_dates(date(2026, 7, 27))
    assert len(dates) == 7
    assert dates[0].strftime("%A") == "Monday" and dates[-1].strftime("%A") == "Sunday"


def test_indian_seasons_map_to_the_months_people_actually_use():
    assert clock.season_for(date(2026, 1, 15)) == "winter"
    assert clock.season_for(date(2026, 4, 15)) == "summer"
    assert clock.season_for(date(2026, 7, 15)) == "monsoon"
    assert clock.season_for(date(2026, 10, 15)) == "autumn"


# --- Authorization ----------------------------------------------------------


async def test_every_phase_5_route_requires_authentication(app_client):
    day = date.today().isoformat()
    calls = [
        ("GET", "/api/v2/today", None),
        ("POST", "/api/v2/today/regenerate", {}),
        ("POST", f"/api/v2/today/actions/{uuid.uuid4()}/complete", {}),
        ("POST", "/api/v2/today/outfit/swap", {"slot": "shoes"}),
        ("POST", "/api/v2/today/feedback", {"rating": "wore_it"}),
        ("POST", "/api/v2/today/weather", {"for_date": day, "condition": "hot"}),
        ("GET", "/api/v2/planner/week", None),
        ("POST", "/api/v2/planner/week/generate", {}),
        ("PATCH", f"/api/v2/planner/day/{day}", {}),
        ("POST", f"/api/v2/planner/day/{day}/lock", {}),
        ("GET", "/api/v2/integrations/calendar/status", None),
        ("POST", "/api/v2/integrations/calendar/connect", {}),
        ("DELETE", "/api/v2/integrations/calendar", None),
    ]
    for method, path, body in calls:
        response = await app_client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


async def test_one_account_cannot_touch_another_accounts_plan(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await stock(app_client, owner)

    plan = (await today(app_client, owner)).json()
    action_id = plan["primary"][0]["id"]

    assert (await app_client.post(
        f"/api/v2/today/actions/{action_id}/complete", json={}, headers=auth(stranger)
    )).status_code == 404
    # A stranger asking for "today" gets their own empty day, never the owner's.
    theirs = (await today(app_client, stranger)).json()
    assert theirs["outfit"] is None


async def test_an_account_id_in_the_body_is_rejected(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/today/regenerate",
        json={"account_id": str(uuid.uuid4())}, headers=auth(token),
    )
    assert response.status_code == 422


# --- The Today plan ---------------------------------------------------------


async def test_today_returns_one_short_list_not_a_dashboard(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    body = (await today(app_client, token)).json()

    assert body["status"] == "ready"
    assert body["outfit"] is not None
    assert body["headline"]
    # The screen opens with the essentials only.
    assert 1 <= len(body["primary"]) <= 5
    for key in ("plan_date", "timezone", "weekday", "confidence", "primary",
                "optional_modules", "missing_information", "generated_from"):
        assert key in body
    assert body["timezone"] == IST


async def test_the_outfit_only_uses_items_the_user_owns(app_client, make_user):
    _, token = await make_user()
    created = await stock(app_client, token)
    real_ids = {row["id"] for row in created}

    body = (await today(app_client, token)).json()
    owned = body["outfit"]["owned_items"]
    assert owned
    for piece in owned:
        assert piece["owned"] is True
        assert piece["inventory_item_id"] in real_ids
    for optional in body["outfit"]["optional_additions"]:
        assert optional["inventory_item_id"] is None


async def test_an_empty_wardrobe_says_so_rather_than_inventing_an_outfit(app_client, make_user):
    _, token = await make_user()
    body = (await today(app_client, token)).json()
    assert body["status"] == "needs_inventory"
    assert body["outfit"] is None
    assert any("invent" in row["body"].lower() for row in body["primary"])


async def test_optional_modules_appear_only_when_relevant(app_client, make_user):
    _, token = await make_user()
    # No beauty, hair or perfume products: those modules must stay silent.
    await stock(app_client, token, [row for row in WARDROBE if row[0] in ("wardrobe", "shoes")])
    body = (await today(app_client, token)).json()
    modules = {row["module"] for row in body["optional_modules"]}
    assert "skincare" not in modules
    assert "perfume" not in modules

    _, other = await make_user()
    await stock(app_client, other)
    await app_client.post("/api/v2/today/weather", json={"for_date": date.today().isoformat(), "condition": "hot"}, headers=auth(other))
    rich = (await today(app_client, other)).json()
    rich_modules = {row["module"] for row in rich["primary"] + rich["optional_modules"]}
    # Hot weather makes hydration relevant; owning a serum makes skincare relevant.
    assert "hydration" in rich_modules
    for row in rich["optional_modules"]:
        assert row["relevance"], "every optional module must say why it is here"


# --- Caching and recomputation ----------------------------------------------


async def test_today_is_served_from_cache_when_nothing_material_changed(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)

    first = (await today(app_client, token)).json()
    assert first["generated_from"] == "fresh"

    second = (await today(app_client, token)).json()
    assert second["generated_from"] == "cache", "a second open must not recompute"
    assert second["version"] == first["version"]
    assert second["outfit"]["id"] == first["outfit"]["id"]

    third = (await today(app_client, token)).json()
    assert third["generated_from"] == "cache"


async def test_a_daily_plan_is_idempotent_and_never_duplicated(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    for _ in range(4):
        await today(app_client, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(DailyPlan).where(DailyPlan.plan_date == date.today())
        )).scalars().all()
        mine = [row for row in rows if str(row.account_id)]
        # The unique constraint is the guarantee; this asserts it holds in practice.
        by_account = {}
        for row in mine:
            by_account.setdefault(row.account_id, []).append(row)
        assert all(len(value) == 1 for value in by_account.values())


async def test_changing_the_weather_invalidates_the_cache_and_rebuilds(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    first = (await today(app_client, token)).json()
    assert (await today(app_client, token)).json()["generated_from"] == "cache"

    changed = (await app_client.post(
        "/api/v2/today/weather",
        json={"for_date": date.today().isoformat(), "condition": "rainy", "precipitation_chance": 80},
        headers=auth(token),
    )).json()

    assert changed["plan"]["generated_from"] == "fresh", "new weather must rebuild the day"
    assert changed["plan"]["version"] > first["version"]
    titles = " ".join(row["title"] for row in changed["plan"]["primary"]).lower()
    assert "rain" in titles

    factory = sql.get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(
            select(PlanRecalculationEvent).where(PlanRecalculationEvent.plan_date == date.today())
        )).scalars().all()
        assert any(row.trigger == "weather_changed" and row.recomputed for row in events)


async def test_marking_an_item_unavailable_rebuilds_without_it(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    first = (await today(app_client, token)).json()
    worn = first["outfit"]["owned_items"][0]["inventory_item_id"]

    rebuilt = (await app_client.post(
        "/api/v2/today/items/unavailable",
        json={"item_id": worn, "state": "in_wash"}, headers=auth(token),
    )).json()

    used = {piece["inventory_item_id"] for piece in rebuilt["outfit"]["owned_items"]}
    assert worn not in used, "an item in the wash must not be planned"
    assert any("unavailable" in note.lower() for note in rebuilt["missing_information"])


async def test_an_item_returns_once_its_available_from_date_passes(app_client, make_user):
    _, token = await make_user()
    created = await stock(app_client, token)
    shirt = next(row for row in created if row["display_name"] == "Navy formal shirt")

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await app_client.post(
        "/api/v2/today/items/unavailable",
        json={"item_id": shirt["id"], "state": "in_wash", "available_from": yesterday},
        headers=auth(token),
    )
    body = (await today(app_client, token)).json()
    # available_from is in the past, so it is wearable again and not reported blocked.
    assert not any("unavailable" in note.lower() for note in body["missing_information"])


async def test_the_cache_key_ignores_the_clock_but_notices_real_change(app_client, make_user):
    """The hash must not move just because time passed."""
    from app.domains.planning import context as context_stage

    _, token = await make_user()
    await stock(app_client, token)
    await today(app_client, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        from app.domains.identity.models import AccountLink

        account_id = (await session.execute(select(AccountLink.id).order_by(AccountLink.created_at.desc()).limit(1))).scalar_one()
        first = await context_stage.gather(session, account_id=account_id)
        key_one = cache_key(first)

        second = await context_stage.gather(session, account_id=account_id)
        assert cache_key(second) == key_one, "the same day must hash the same twice"

        second.occasion_key = "wedding"
        assert cache_key(second) != key_one, "a different occasion must change the hash"


# --- Repetition -------------------------------------------------------------


async def test_the_planner_avoids_wearing_the_same_thing_two_days_running(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)

    monday = clock.week_start(date.today())
    (await app_client.post(
        "/api/v2/planner/week/generate",
        json={"week_start": monday.isoformat()}, headers=auth(token),
    )).json()

    week = (await app_client.get("/api/v2/planner/week", params={"week_start": monday.isoformat()}, headers=auth(token))).json()
    cores = []
    for day in week["days"]:
        core = frozenset(
            piece["inventory_item_id"] for piece in day["owned_items"]
            if piece["slot"] in ("clothing", "shoes")
        )
        if core:
            cores.append((day["plan_date"], core))

    for (first_date, first_core), (second_date, second_core) in zip(cores, cores[1:]):
        assert first_core != second_core, f"{first_date} and {second_date} are the same outfit"


def test_the_repetition_report_surfaces_repeats_without_forbidding_them():
    days = [
        {"plan_date": "2026-08-03", "owned_items": [{"display_name": "Navy shirt"}, {"display_name": "Black shoes"}]},
        {"plan_date": "2026-08-04", "owned_items": [{"display_name": "White shirt"}, {"display_name": "Black shoes"}]},
    ]
    report = repetition_report(days)
    names = {row["display_name"]: row for row in report["repeated_items"]}
    assert "Black shoes" in names and names["Black shoes"]["times"] == 2
    assert "Navy shirt" not in names
    assert "normal" in report["note"].lower()


# --- Weekly planner ---------------------------------------------------------


async def test_a_full_monday_to_sunday_week_is_generated(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    monday = clock.week_start(date.today())

    week = (await app_client.post(
        "/api/v2/planner/week/generate",
        json={"week_start": monday.isoformat()}, headers=auth(token),
    )).json()

    assert len(week["days"]) == 7
    assert week["days"][0]["weekday"] == "Monday"
    assert week["days"][-1]["weekday"] == "Sunday"
    assert week["week_start"] == monday.isoformat()
    assert all(day["status"] in ("ready", "needs_inventory") for day in week["days"])
    assert "repetition" in week


async def test_an_ungenerated_week_says_so_instead_of_erroring(app_client, make_user):
    _, token = await make_user()
    week = (await app_client.get("/api/v2/planner/week", headers=auth(token))).json()
    assert week["status"] == "not_generated"
    assert len(week["days"]) == 7
    assert "generate" in week["message"].lower()


async def test_a_locked_day_survives_regenerating_the_week(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    monday = clock.week_start(date.today())
    await app_client.post("/api/v2/planner/week/generate", json={"week_start": monday.isoformat()}, headers=auth(token))

    target = (monday + timedelta(days=2)).isoformat()
    locked = (await app_client.post(f"/api/v2/planner/day/{target}/lock", json={"locked": True}, headers=auth(token))).json()
    before = next(day for day in locked["days"] if day["plan_date"] == target)
    assert before["locked"] is True
    before_items = {piece["inventory_item_id"] for piece in before["owned_items"]}

    regenerated = (await app_client.post(
        "/api/v2/planner/week/generate",
        json={"week_start": monday.isoformat()}, headers=auth(token),
    )).json()
    after = next(day for day in regenerated["days"] if day["plan_date"] == target)
    assert after["locked"] is True
    assert {piece["inventory_item_id"] for piece in after["owned_items"]} == before_items


async def test_a_locked_day_refuses_to_be_regenerated_on_its_own(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    monday = clock.week_start(date.today())
    await app_client.post("/api/v2/planner/week/generate", json={"week_start": monday.isoformat()}, headers=auth(token))
    target = (monday + timedelta(days=3)).isoformat()
    await app_client.post(f"/api/v2/planner/day/{target}/lock", json={"locked": True}, headers=auth(token))

    response = await app_client.patch(f"/api/v2/planner/day/{target}", json={"regenerate": True}, headers=auth(token))
    assert response.status_code == 422
    assert "locked" in json.dumps(response.json()).lower()


async def test_two_days_can_be_swapped_and_locked_days_cannot(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    monday = clock.week_start(date.today())
    week = (await app_client.post("/api/v2/planner/week/generate", json={"week_start": monday.isoformat()}, headers=auth(token))).json()

    first, second = week["days"][0]["plan_date"], week["days"][1]["plan_date"]
    before_first = {piece["inventory_item_id"] for piece in week["days"][0]["owned_items"]}
    before_second = {piece["inventory_item_id"] for piece in week["days"][1]["owned_items"]}

    swapped = (await app_client.patch(
        f"/api/v2/planner/day/{first}", json={"swap_with_date": second}, headers=auth(token)
    )).json()
    after_first = {piece["inventory_item_id"] for piece in swapped["days"][0]["owned_items"]}
    after_second = {piece["inventory_item_id"] for piece in swapped["days"][1]["owned_items"]}
    assert after_first == before_second and after_second == before_first

    await app_client.post(f"/api/v2/planner/day/{first}/lock", json={"locked": True}, headers=auth(token))
    blocked = await app_client.patch(f"/api/v2/planner/day/{first}", json={"swap_with_date": second}, headers=auth(token))
    assert blocked.status_code == 422


async def test_days_cannot_be_swapped_across_weeks(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    monday = clock.week_start(date.today())
    await app_client.post("/api/v2/planner/week/generate", json={"week_start": monday.isoformat()}, headers=auth(token))

    response = await app_client.patch(
        f"/api/v2/planner/day/{monday.isoformat()}",
        json={"swap_with_date": (monday + timedelta(days=9)).isoformat()}, headers=auth(token),
    )
    assert response.status_code == 422


# --- Swap workflow ----------------------------------------------------------


async def test_one_item_can_be_swapped_without_regenerating_the_day(app_client, make_user):
    _, token = await make_user()
    created = await stock(app_client, token)
    body = (await today(app_client, token)).json()
    worn_shoes = next(piece for piece in body["outfit"]["owned_items"] if piece["slot"] == "shoes")
    other = next(row for row in created if row["category"] == "shoes" and row["id"] != worn_shoes["inventory_item_id"])

    swapped = (await app_client.post(
        "/api/v2/today/outfit/swap",
        json={"slot": "shoes", "from_item_id": worn_shoes["inventory_item_id"], "to_item_id": other["id"]},
        headers=auth(token),
    )).json()

    shoes_now = [p["inventory_item_id"] for p in swapped["outfit"]["owned_items"] if p["slot"] == "shoes"]
    assert shoes_now == [other["id"]]
    # The rest of the outfit is untouched — this is a swap, not a regeneration.
    before_clothing = {p["inventory_item_id"] for p in body["outfit"]["owned_items"] if p["slot"] == "clothing"}
    after_clothing = {p["inventory_item_id"] for p in swapped["outfit"]["owned_items"] if p["slot"] == "clothing"}
    assert after_clothing == before_clothing


async def test_a_swap_cannot_bring_in_an_item_the_user_does_not_own(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await stock(app_client, owner)
    theirs = await add_item(app_client, stranger, "shoes", "Their shoes", {"shoe_type": "loafers", "colour": "black", "size": "9"})
    await today(app_client, owner)

    response = await app_client.post(
        "/api/v2/today/outfit/swap",
        json={"slot": "shoes", "to_item_id": theirs["id"]}, headers=auth(owner),
    )
    assert response.status_code == 404


async def test_a_user_swap_is_not_overwritten_by_a_later_context_change(app_client, make_user):
    _, token = await make_user()
    created = await stock(app_client, token)
    body = (await today(app_client, token)).json()
    worn = next(p for p in body["outfit"]["owned_items"] if p["slot"] == "shoes")
    other = next(r for r in created if r["category"] == "shoes" and r["id"] != worn["inventory_item_id"])

    await app_client.post(
        "/api/v2/today/outfit/swap",
        json={"slot": "shoes", "from_item_id": worn["inventory_item_id"], "to_item_id": other["id"]},
        headers=auth(token),
    )
    after = (await today(app_client, token)).json()
    shoes = [p["inventory_item_id"] for p in after["outfit"]["owned_items"] if p["slot"] == "shoes"]
    assert shoes == [other["id"]], "the user's own choice must survive a re-open"


# --- Feedback and worn history ----------------------------------------------


async def test_recording_what_was_worn_feeds_tomorrows_repetition_rules(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    body = (await today(app_client, token)).json()

    fed = (await app_client.post("/api/v2/today/feedback", json={"rating": "wore_it"}, headers=auth(token))).json()
    assert fed["worn"] is True

    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(OutfitSchedule).where(OutfitSchedule.plan_date == date.today(), OutfitSchedule.status == "worn")
        )).scalars().first()
        assert row is not None and row.item_ids


async def test_an_action_can_be_completed_and_uncompleted(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    plan = (await today(app_client, token)).json()
    action_id = plan["primary"][0]["id"]

    done = (await app_client.post(f"/api/v2/today/actions/{action_id}/complete", json={}, headers=auth(token))).json()
    assert next(row for row in done["primary"] if row["id"] == action_id)["completed"] is True

    undone = (await app_client.post(
        f"/api/v2/today/actions/{action_id}/complete", json={"completed": False}, headers=auth(token)
    )).json()
    assert next(row for row in undone["primary"] if row["id"] == action_id)["completed"] is False


# --- Calendar ---------------------------------------------------------------


def test_event_titles_are_only_matched_on_real_words():
    assert infer_occasion("Final interview with the panel")[0] == "interview"
    assert infer_occasion("Rahul's wedding reception")[0] == "wedding"
    assert infer_occasion("Client meeting at their office")[0] == "business_meeting"
    assert infer_occasion("Gym")[0] == "gym"
    # Nothing recognisable means no guess, not a bad guess.
    assert infer_occasion("asdfgh")[0] is None
    assert infer_occasion("")[0] is None


async def test_adding_an_event_shapes_the_day_and_shows_the_commitment(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    starts = datetime.combine(date.today(), datetime.min.time()).replace(hour=10, tzinfo=dt_timezone.utc)

    body = (await app_client.post(
        "/api/v2/today/events",
        json={"title": "Final interview with the panel", "starts_at": starts.isoformat()},
        headers=auth(token),
    )).json()

    assert body["created"] is True
    assert body["event"]["occasion_key"] == "interview"
    plan = body["plan"]
    titles = " ".join(row["title"] for row in plan["primary"])
    assert "interview" in titles.lower()


async def test_the_same_event_sent_twice_is_absorbed_not_duplicated(app_client, make_user):
    _, token = await make_user()
    starts = datetime.now(dt_timezone.utc).replace(microsecond=0)
    payload = {"title": "Team offsite", "starts_at": starts.isoformat(), "external_id": "evt-1"}

    first = (await app_client.post("/api/v2/today/events", json=payload, headers=auth(token))).json()
    second = (await app_client.post("/api/v2/today/events", json=payload, headers=auth(token))).json()

    assert first["created"] is True
    assert second["created"] is False, "a repeated event must not create a second row"
    assert first["event"]["id"] == second["event"]["id"]


async def test_connecting_a_calendar_seeds_events_and_ignores_duplicates(app_client, make_user):
    _, token = await make_user()
    starts = datetime.now(dt_timezone.utc).replace(microsecond=0)
    events = [
        {"title": "Board meeting", "starts_at": starts.isoformat(), "external_id": "a"},
        {"title": "Board meeting", "starts_at": starts.isoformat(), "external_id": "a"},
        {"title": "Gym", "starts_at": (starts + timedelta(hours=6)).isoformat(), "external_id": "b"},
    ]
    body = (await app_client.post(
        "/api/v2/integrations/calendar/connect",
        json={"provider": "manual", "label": "My calendar", "events": events}, headers=auth(token),
    )).json()

    assert body["connected"] is True
    assert body["events_added"] == 2
    assert body["duplicates_ignored"] == 1
    assert all(row["stores_credentials"] is False for row in body["integrations"])


async def test_an_unknown_calendar_provider_is_refused(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/integrations/calendar/connect",
        json={"provider": "myspace"}, headers=auth(token),
    )
    assert response.status_code == 422


async def test_a_declared_but_unconnected_provider_reports_itself_honestly(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/integrations/providers", headers=auth(token))).json()
    providers = {row["key"]: row["available"] for row in body["calendar"]}
    assert providers["manual"] is True
    assert providers["google"] is False, "an unwired provider must not claim to be available"


async def test_disconnecting_a_calendar_actually_stops_using_its_events(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    starts = datetime.combine(date.today(), datetime.min.time()).replace(hour=9, tzinfo=dt_timezone.utc)
    await app_client.post(
        "/api/v2/integrations/calendar/connect",
        json={"provider": "manual", "events": [
            {"title": "Client meeting downtown", "starts_at": starts.isoformat(), "external_id": "sync-1"}
        ]},
        headers=auth(token),
    )
    with_calendar = (await today(app_client, token)).json()
    assert "client meeting" in json.dumps(with_calendar).lower()

    revoked = (await app_client.delete("/api/v2/integrations/calendar", headers=auth(token))).json()
    assert revoked["connected"] is False
    assert revoked["revoked"] >= 1

    after = (await app_client.post("/api/v2/today/regenerate", json={}, headers=auth(token))).json()
    assert "client meeting" not in json.dumps(after["primary"]).lower()

    status = (await app_client.get("/api/v2/integrations/calendar/status", headers=auth(token))).json()
    assert status["connected"] is False
    assert all(row["status"] == "revoked" for row in status["integrations"])


async def test_a_user_typed_event_survives_disconnecting_a_calendar(app_client, make_user):
    _, token = await make_user()
    starts = datetime.combine(date.today(), datetime.min.time()).replace(hour=11, tzinfo=dt_timezone.utc)
    await app_client.post(
        "/api/v2/today/events",
        json={"title": "My own dinner plan", "starts_at": starts.isoformat()}, headers=auth(token),
    )
    await app_client.delete("/api/v2/integrations/calendar", headers=auth(token))

    timezone_name = "Asia/Kolkata"
    events = (await app_client.get("/api/v2/today", headers=auth(token))).json()
    assert "my own dinner plan" in json.dumps(events).lower(), "your own events are yours to keep"


# --- Clarification ----------------------------------------------------------


async def test_a_low_confidence_day_asks_exactly_one_question(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    starts = datetime.combine(date.today(), datetime.min.time()).replace(hour=15, tzinfo=dt_timezone.utc)
    # "work" is a weak single-word signal, deliberately below the threshold.
    body = (await app_client.post(
        "/api/v2/today/events",
        json={"title": "work thing", "starts_at": starts.isoformat()}, headers=auth(token),
    )).json()

    plan = body["plan"]
    assert plan["needs_clarification"] is True
    question = plan["clarification"]
    assert question["question"] and question["options"]
    assert question["why"], "a question must say why it is worth answering"


async def test_answering_the_question_rebuilds_and_stops_it_being_asked_again(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    starts = datetime.combine(date.today(), datetime.min.time()).replace(hour=15, tzinfo=dt_timezone.utc)
    plan = (await app_client.post(
        "/api/v2/today/events",
        json={"title": "work thing", "starts_at": starts.isoformat()}, headers=auth(token),
    )).json()["plan"]
    assert plan["needs_clarification"] is True

    answered = (await app_client.post(
        "/api/v2/today/clarify",
        json={"question_key": plan["clarification"]["key"], "answer": "business_meeting"},
        headers=auth(token),
    )).json()

    assert answered["clarification"] is None or answered["clarification"]["key"] != "occasion_key"
    # Re-opening must not ask the same thing again.
    again = (await today(app_client, token)).json()
    if again["needs_clarification"]:
        assert again["clarification"]["key"] != "occasion_key"


async def test_a_confident_day_asks_nothing_at_all(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    await app_client.post(
        "/api/v2/today/weather",
        json={"for_date": date.today().isoformat(), "condition": "mild"}, headers=auth(token),
    )
    body = (await today(app_client, token)).json()
    assert body["needs_clarification"] is False, "do not ask what we already know"


async def test_answering_a_question_that_was_not_asked_is_refused(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    await today(app_client, token)
    response = await app_client.post(
        "/api/v2/today/clarify",
        json={"question_key": "occasion_key", "answer": "wedding"}, headers=auth(token),
    )
    assert response.status_code == 422


# --- Notifications ----------------------------------------------------------


def test_quiet_hours_handle_a_window_crossing_midnight():
    # 21:00 to 07:00 is the default and is the awkward case.
    assert in_quiet_hours(22, 21, 7) is True
    assert in_quiet_hours(3, 21, 7) is True
    assert in_quiet_hours(8, 21, 7) is False
    assert in_quiet_hours(20, 21, 7) is False
    # A same-day window still works.
    assert in_quiet_hours(13, 12, 14) is True
    assert in_quiet_hours(15, 12, 14) is False
    assert in_quiet_hours(5, 9, 9) is False


def test_the_dedup_hash_is_stable_and_content_sensitive():
    account = uuid.uuid4()
    day = date(2026, 8, 2)
    first = dedup_hash(account, day, "daily_plan", "Monday: office")
    assert first == dedup_hash(account, day, "daily_plan", "Monday: office")
    assert first != dedup_hash(account, day, "daily_plan", "Monday: wedding")
    assert first != dedup_hash(uuid.uuid4(), day, "daily_plan", "Monday: office")


async def test_the_same_notification_is_never_queued_twice(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    await today(app_client, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        from app.domains.identity.models import AccountLink

        account_id = (await session.execute(
            select(AccountLink.id).order_by(AccountLink.created_at.desc()).limit(1)
        )).scalar_one()
        morning = datetime(2026, 8, 3, 3, 0, tzinfo=dt_timezone.utc)  # 08:30 IST

        first = await notifications.queue(
            session, account_id=account_id, plan_date=date(2026, 8, 3),
            notification_key="daily_plan", title="Monday: office", moment=morning,
        )
        second = await notifications.queue(
            session, account_id=account_id, plan_date=date(2026, 8, 3),
            notification_key="daily_plan", title="Monday: office", moment=morning,
        )
        await session.commit()

        assert first.id == second.id, "queueing twice must return the first decision"
        rows = (await session.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.account_id == account_id,
                NotificationDelivery.plan_date == date(2026, 8, 3),
            )
        )).scalars().all()
        assert len(rows) == 1


async def test_the_daily_cap_holds_at_one_by_default(app_client, make_user):
    _, token = await make_user()

    factory = sql.get_sessionmaker()
    async with factory() as session:
        from app.domains.identity.models import AccountLink

        account_id = (await session.execute(
            select(AccountLink.id).order_by(AccountLink.created_at.desc()).limit(1)
        )).scalar_one()
        morning = datetime(2026, 8, 4, 4, 0, tzinfo=dt_timezone.utc)  # 09:30 IST
        day = date(2026, 8, 4)

        first = await notifications.queue(session, account_id=account_id, plan_date=day, notification_key="a", title="First", moment=morning)
        second = await notifications.queue(session, account_id=account_id, plan_date=day, notification_key="b", title="Second", moment=morning)
        third = await notifications.queue(session, account_id=account_id, plan_date=day, notification_key="c", title="Third", moment=morning)
        await session.commit()

        assert first.status == "queued"
        assert second.status == "suppressed" and second.suppressed_reason == "daily_cap_reached"
        assert third.status == "suppressed"


async def test_quiet_hours_suppress_a_notification_in_the_users_local_time(app_client, make_user):
    _, token = await make_user()

    factory = sql.get_sessionmaker()
    async with factory() as session:
        from app.domains.identity.models import AccountLink

        account_id = (await session.execute(
            select(AccountLink.id).order_by(AccountLink.created_at.desc()).limit(1)
        )).scalar_one()
        # 19:00 UTC is 00:30 IST — the middle of the night for the user.
        midnight_ist = datetime(2026, 8, 5, 19, 0, tzinfo=dt_timezone.utc)
        row = await notifications.queue(
            session, account_id=account_id, plan_date=date(2026, 8, 6),
            notification_key="daily_plan", title="Night", moment=midnight_ist,
        )
        await session.commit()
        assert row.status == "suppressed" and row.suppressed_reason == "quiet_hours"


async def test_notification_preferences_can_be_read_and_changed(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/today/notifications", headers=auth(token))).json()
    assert body["preferences"]["daily_cap"] == 1
    assert body["preferences"]["enabled"] is True

    updated = (await app_client.patch(
        "/api/v2/today/notifications",
        json={"daily_cap": 2, "modules": {"nutrition": False}}, headers=auth(token),
    )).json()
    assert updated["preferences"]["daily_cap"] == 2
    assert updated["preferences"]["modules"]["nutrition"] is False
    assert updated["preferences"]["modules"]["outfit"] is True

    bad = await app_client.patch("/api/v2/today/notifications", json={"modules": {"astrology": True}}, headers=auth(token))
    assert bad.status_code == 422


# --- Provenance and flags ----------------------------------------------------


async def test_a_plan_records_the_inputs_that_produced_it(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    await today(app_client, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(DailyPlan.plan_date == date.today()).order_by(DailyPlan.created_at.desc()).limit(1)
        )).scalar_one()
        rows = (await session.execute(
            select(DailyPlanInput).where(DailyPlanInput.plan_id == plan.id)
        )).scalars().all()
        keys = {row.input_key for row in rows}
        assert {"plan_date", "timezone", "season", "occasion_key", "available_item_count"} <= keys
        assert all(row.source for row in rows)


async def test_today_and_planner_sit_behind_their_own_flags(app_client, make_user, monkeypatch):
    from app.shared.flags import service as flag_service

    _, token = await make_user()
    original = flag_service.is_enabled

    async def planner_off(session, key):
        if key == "v2_planner":
            return False
        return await original(session, key)

    monkeypatch.setattr(flag_service, "is_enabled", planner_off)
    assert (await app_client.get("/api/v2/planner/week", headers=auth(token))).status_code == 404
    assert (await app_client.get("/api/v2/today", headers=auth(token))).status_code == 200


async def test_no_plan_response_uses_banned_language(app_client, make_user):
    _, token = await make_user()
    await stock(app_client, token)
    await app_client.post("/api/v2/today/weather", json={"for_date": date.today().isoformat(), "condition": "hot"}, headers=auth(token))
    plan = (await today(app_client, token)).json()
    monday = clock.week_start(date.today())
    week = (await app_client.post("/api/v2/planner/week/generate", json={"week_start": monday.isoformat()}, headers=auth(token))).json()

    text = (json.dumps(plan) + json.dumps(week)).lower()
    for banned in ("money wasted", "slimming", "body type", "problem area",
                   "attractiveness", "lose weight", "overall score"):
        assert banned not in text, f"response contained banned wording: {banned}"
    # The one legitimate use of the word: the disclaimer that says we are not
    # doing it. Its absence would be the actual problem.
    assert "not medical or diagnostic advice" in plan["disclaimer"].lower()


async def test_the_planner_never_calls_a_model(app_client, make_user, fake_provider):
    """Deterministic by construction: a week is seven plans and zero AI calls."""
    _, token = await make_user()
    await stock(app_client, token)
    monday = clock.week_start(date.today())

    await app_client.post("/api/v2/planner/week/generate", json={"week_start": monday.isoformat()}, headers=auth(token))
    await today(app_client, token)
    await app_client.post("/api/v2/today/regenerate", json={}, headers=auth(token))

    assert fake_provider.calls == 0, "the Today engine must not spend money per user per day"


async def test_phase_5_does_not_disturb_the_earlier_phases(app_client, make_user):
    _, token = await make_user()
    created = await stock(app_client, token)
    await today(app_client, token)

    summary = (await app_client.get("/api/v2/inventory/summary", headers=auth(token))).json()
    assert summary["total_items"] == len(created)

    item = (await app_client.get(f"/api/v2/inventory/items/{created[0]['id']}", headers=auth(token))).json()
    assert item["version"] == 1, "planning must not mutate inventory items"

    # Phase 4 still works on its own terms.
    styled = (await app_client.post(
        "/api/v2/style/occasion", json={"occasion": {"occasion_key": "wedding"}}, headers=auth(token)
    )).json()
    assert styled["status"] in ("ready", "not_enough_inventory")
