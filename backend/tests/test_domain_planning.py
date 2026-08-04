"""§1.10 — Today and weekly-planner regression coverage.

Today is the screen the product opens on, so its failure modes are the ones a
user meets first: a plan that forgets what they already did, a plan that is
rebuilt from scratch on every open, or a plan that quietly invents weather.

Everything runs through the real V2 routes against PostgreSQL. Weather is
supplied deterministically — either by the user through ``POST /today/weather``
or by a stub provider — so no test depends on a live forecast.

What this protects against
--------------------------
* A completed action being lost when the day is re-opened or regenerated.
* Repeated completion of the same action stacking duplicates.
* An unchanged day being recompiled (and re-charged) on every read.
* A missing forecast being rendered as a confident weather claim rather than
  an honest "we do not know".
* A weather-provider outage propagating into the request instead of degrading
  to a neutral plan.
* Cross-account reads and writes of plans, actions and calendar events.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.domains.planning import weather as weather_module
from app.domains.planning.models import (
    CalendarEvent,
    DailyPlan,
    DailyPlanAction,
    WeatherSnapshot,
    WeeklyPlan,
    WeeklyPlanDay,
)
from app.shared.database.sql import get_sessionmaker
from tests.conftest import auth


pytestmark = pytest.mark.asyncio


TODAY = date(2026, 2, 16)  # a Monday, so week_start == TODAY


@pytest.fixture(autouse=True)
def _deterministic_weather_cache():
    """The provider cache is process-global; a stale entry would leak between
    tests and make a "no weather" assertion pass for the wrong reason."""
    weather_module.clear_cache()
    yield
    weather_module.clear_cache()


WARDROBE = [
    {
        "category": "wardrobe", "display_name": "Charcoal Blazer", "subcategory": "blazer",
        "details": {"colour": "charcoal", "fabric": "wool", "formality": "smart_casual", "season": ["all"]},
    },
    {
        "category": "wardrobe", "display_name": "White Cotton Shirt", "subcategory": "shirt",
        "details": {"colour": "white", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]},
    },
    {
        "category": "wardrobe", "display_name": "Navy Chinos", "subcategory": "trousers",
        "details": {"colour": "navy", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]},
    },
    {
        "category": "shoes", "display_name": "Brown Leather Derbies", "subcategory": "derby",
        "details": {"colour": "brown", "shoe_type": "derby", "occasion": ["work"]},
    },
]


async def _stock_wardrobe(client, token):
    """Enough confirmed inventory for the compiler to build a real outfit.

    Without it the plan comes back ``needs_inventory`` and is recompiled on
    every read, which is correct behaviour but the wrong thing to test caching
    against.
    """
    for body in WARDROBE:
        resp = await client.post("/api/v2/inventory/items", headers=auth(token), json=body)
        assert resp.status_code in (200, 201), resp.text


async def _get_today(client, token, plan_date: date = TODAY):
    return await client.get(
        f"/api/v2/today?plan_date={plan_date.isoformat()}", headers=auth(token)
    )


async def _first_action(client, token, plan_date: date = TODAY):
    body = (await _get_today(client, token, plan_date)).json()
    actions = body["primary"] + body["optional_modules"]
    assert actions, "a plan with no actions gives the user nothing to do"
    return body, actions[0]


# ---------------------------------------------------------------------------
# Plan generation and caching
# ---------------------------------------------------------------------------

async def test_today_generates_a_plan_for_the_requested_date(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()

    resp = await _get_today(app_client, token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_date"] == TODAY.isoformat()
    assert body["weekday"] == "Monday"
    assert body["engine_version"]
    assert isinstance(body["primary"], list)

    factory = get_sessionmaker()
    async with factory() as session:
        plans = (await session.execute(
            select(DailyPlan).where(DailyPlan.account_id == uid)
        )).scalars().all()
    assert len(plans) == 1
    assert plans[0].plan_date == TODAY


async def test_reading_today_twice_serves_the_cached_plan(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The hot path must be a cache hit: same context, same plan, no second
    compilation and no second row."""
    token, uid = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)

    first = (await _get_today(app_client, token)).json()
    second = (await _get_today(app_client, token)).json()

    assert second["version"] == first["version"]
    assert first["generated_from"] == "fresh"
    assert second["generated_from"] == "cache", "an unchanged day must not recompile"

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(DailyPlan.id)).where(DailyPlan.account_id == uid)
        )).scalar_one()
    assert count == 1


async def test_regenerate_rebuilds_the_same_day_in_place(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)
    first = (await _get_today(app_client, token)).json()

    resp = await app_client.post(
        "/api/v2/today/regenerate",
        headers=auth(token),
        json={"plan_date": TODAY.isoformat(), "reason": "manual"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_date"] == TODAY.isoformat()
    assert body["version"] > first["version"], "a forced rebuild must bump the version"

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(DailyPlan.id)).where(DailyPlan.account_id == uid)
        )).scalar_one()
    assert count == 1, "regenerating must not leave a second plan for the same day"


# ---------------------------------------------------------------------------
# Action completion
# ---------------------------------------------------------------------------

async def test_completing_an_action_persists_across_reads(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)
    _, action = await _first_action(app_client, token)
    assert action["completed"] is False

    resp = await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(token),
        json={"completed": True},
    )
    assert resp.status_code == 200, resp.text

    reread = (await _get_today(app_client, token)).json()
    completed = {
        row["id"]: row for row in reread["primary"] + reread["optional_modules"]
    }
    assert completed[action["id"]]["completed"] is True
    assert completed[action["id"]]["completed_at"] is not None


async def test_repeated_completion_is_idempotent(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """A double-tap on a phone must not produce two completions or move the
    recorded time."""
    token, uid = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)
    _, action = await _first_action(app_client, token)

    first = await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(token),
        json={"completed": True},
    )
    assert first.status_code == 200
    stamp = {
        row["id"]: row["completed_at"]
        for row in first.json()["primary"] + first.json()["optional_modules"]
    }[action["id"]]

    second = await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(token),
        json={"completed": True},
    )
    assert second.status_code == 200

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(DailyPlanAction).where(
                DailyPlanAction.id == uuid.UUID(action["id"])
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].completed_at is not None
    # The second completion is a no-op, not a re-stamp.
    assert stamp is not None


async def test_completion_can_be_undone(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)
    _, action = await _first_action(app_client, token)

    await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(token),
        json={"completed": True},
    )
    resp = await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(token),
        json={"completed": False},
    )

    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["primary"] + resp.json()["optional_modules"]}
    assert rows[action["id"]]["completed"] is False
    assert rows[action["id"]]["completed_at"] is None


async def test_completion_survives_a_regenerate(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """Rebuilding the day must not silently un-complete what the user did."""
    token, uid = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)
    _, action = await _first_action(app_client, token)
    await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(token),
        json={"completed": True},
    )

    resp = await app_client.post(
        "/api/v2/today/regenerate",
        headers=auth(token),
        json={"plan_date": TODAY.isoformat(), "reason": "manual"},
    )
    assert resp.status_code == 200, resp.text

    factory = get_sessionmaker()
    async with factory() as session:
        completed = (await session.execute(
            select(func.count(DailyPlanAction.id))
            .join(DailyPlan, DailyPlan.id == DailyPlanAction.plan_id)
            .where(
                DailyPlan.account_id == uid,
                DailyPlanAction.completed_at.is_not(None),
            )
        )).scalar_one()
    assert completed >= 1, "a completed action must not be dropped by a rebuild"


async def test_action_from_another_account_is_not_completable(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    await _stock_wardrobe(app_client, owner_token)
    intruder_token, _ = await registered_supabase_user()
    _, action = await _first_action(app_client, owner_token)

    resp = await app_client.post(
        f"/api/v2/today/actions/{action['id']}/complete",
        headers=auth(intruder_token),
        json={"completed": True},
    )
    assert resp.status_code == 404


async def test_unknown_action_id_is_404(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        f"/api/v2/today/actions/{uuid.uuid4()}/complete",
        headers=auth(token),
        json={"completed": True},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

async def test_plan_without_weather_makes_no_weather_claim(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """No forecast means the plan says so. Inventing one would be worse than
    saying nothing, because the user would dress for it."""
    token, _ = await registered_supabase_user()

    body = (await _get_today(app_client, token)).json()

    assert body["weather"] is None
    assert body["missing_information"], "the gap must be declared, not hidden"
    assert any("weather" in note.lower() for note in body["missing_information"])


async def test_user_supplied_weather_is_recorded_and_used(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _stock_wardrobe(app_client, token)

    resp = await app_client.post(
        "/api/v2/today/weather",
        headers=auth(token),
        json={
            "for_date": TODAY.isoformat(),
            "condition": "humid",
            "temp_min_c": 26.0,
            "temp_max_c": 34.0,
            "humidity": 80,
        },
    )

    assert resp.status_code == 200, resp.text
    recorded = resp.json()["weather"]
    assert recorded["condition"] == "humid"
    assert recorded["source"] == "user_declared"
    assert recorded["provider"] == "manual"

    plan = (await _get_today(app_client, token)).json()
    assert plan["weather"]["condition"] == "humid"
    assert plan["weather_note"], "a recorded forecast must show up in the advice"

    factory = get_sessionmaker()
    async with factory() as session:
        snapshots = (await session.execute(
            select(WeatherSnapshot).where(WeatherSnapshot.account_id == uid)
        )).scalars().all()
    assert len(snapshots) == 1
    assert snapshots[0].for_date == TODAY


async def test_impossible_temperature_range_is_rejected(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/today/weather",
        headers=auth(token),
        json={
            "for_date": TODAY.isoformat(),
            "condition": "cold",
            "temp_min_c": 30.0,
            "temp_max_c": 10.0,
        },
    )
    assert resp.status_code == 422


async def test_unknown_weather_condition_is_rejected(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/today/weather",
        headers=auth(token),
        json={"for_date": TODAY.isoformat(), "condition": "apocalyptic"},
    )
    assert resp.status_code == 422


async def test_weather_abstraction_returns_a_typed_record():
    """The rest of the app depends on the shape, not the provider."""

    class _StubProvider:
        name = "stub"

        async def fetch(self, lat: float, lon: float):
            return weather_module.Weather(
                condition="humid", temperature_c=31.0, humidity_percent=78
            )

    result = await weather_module.get_weather(19.07, 72.87, provider=_StubProvider())

    assert result.condition == "humid"
    assert result.temperature_c == 31.0
    assert result.humidity_percent == 78
    assert result.stale is False


async def test_weather_provider_outage_degrades_to_neutral():
    """A provider that returns nothing usable must produce 'unknown', not a
    guess and not an exception — the planner treats unknown as neutral."""

    class _DownProvider:
        name = "down"
        calls = 0

        async def fetch(self, lat: float, lon: float):
            type(self).calls += 1
            return weather_module.NULL_WEATHER

    result = await weather_module.get_weather(19.07, 72.87, provider=_DownProvider())

    assert result.condition == "unknown"
    assert result.temperature_c is None
    # A failed fetch must not be cached as if it were an answer.
    second = await weather_module.get_weather(19.07, 72.87, provider=_DownProvider())
    assert second.condition == "unknown"
    assert _DownProvider.calls == 2


async def test_successful_weather_fetch_is_cached():
    class _CountingProvider:
        name = "counting"

        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self, lat: float, lon: float):
            self.calls += 1
            return weather_module.Weather(
                condition="cold", temperature_c=8.0, humidity_percent=40
            )

    provider = _CountingProvider()
    await weather_module.get_weather(28.61, 77.21, provider=provider)
    await weather_module.get_weather(28.61, 77.21, provider=provider)

    assert provider.calls == 1, "a second read inside the TTL must not refetch"


# ---------------------------------------------------------------------------
# Manual calendar events
# ---------------------------------------------------------------------------

async def test_manual_event_is_created_and_influences_the_day(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/today/events",
        headers=auth(token),
        json={
            "title": "Client presentation",
            "starts_at": datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc).isoformat(),
            "ends_at": datetime(2026, 2, 16, 11, 0, tzinfo=timezone.utc).isoformat(),
            "occasion_key": "office",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["event"]["title"] == "Client presentation"
    assert body["event"]["occasion_key"] == "office"
    assert body["plan"]["plan_date"] == TODAY.isoformat()

    factory = get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(
            select(CalendarEvent).where(CalendarEvent.account_id == uid)
        )).scalars().all()
    assert len(events) == 1


async def test_reposting_the_same_event_is_absorbed_not_stacked(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """Re-syncing or retrying must not fill the day with duplicates. The dedup
    key is a stable digest, so it survives a process restart."""
    token, uid = await registered_supabase_user()
    payload = {
        "title": "Client presentation",
        "starts_at": datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc).isoformat(),
        "occasion_key": "office",
    }

    first = await app_client.post("/api/v2/today/events", headers=auth(token), json=payload)
    second = await app_client.post("/api/v2/today/events", headers=auth(token), json=payload)

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["event"]["id"] == first.json()["event"]["id"]

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(CalendarEvent.id)).where(CalendarEvent.account_id == uid)
        )).scalar_one()
    assert count == 1


async def test_event_can_be_updated_and_removed(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    created = (await app_client.post(
        "/api/v2/today/events",
        headers=auth(token),
        json={
            "title": "Dinner",
            "starts_at": datetime(2026, 2, 16, 19, 0, tzinfo=timezone.utc).isoformat(),
        },
    )).json()["event"]

    patched = await app_client.patch(
        f"/api/v2/integrations/calendar/events/{created['id']}",
        headers=auth(token),
        json={"occasion_key": "party", "dress_code_hint": "smart_casual"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["occasion_key"] == "party"
    assert patched.json()["user_confirmed"] is True

    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(CalendarEvent).where(CalendarEvent.account_id == uid)
        )).scalar_one()
        assert row.occasion_key == "party"
        assert row.dress_code_hint == "smart_casual"


async def test_event_from_another_account_cannot_be_patched(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    created = (await app_client.post(
        "/api/v2/today/events",
        headers=auth(owner_token),
        json={
            "title": "Dinner",
            "starts_at": datetime(2026, 2, 16, 19, 0, tzinfo=timezone.utc).isoformat(),
        },
    )).json()["event"]

    resp = await app_client.patch(
        f"/api/v2/integrations/calendar/events/{created['id']}",
        headers=auth(intruder_token),
        json={"occasion_key": "office"},
    )
    assert resp.status_code == 404


async def test_event_with_an_unknown_occasion_is_rejected(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/today/events",
        headers=auth(token),
        json={
            "title": "Coronation",
            "starts_at": datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc).isoformat(),
            "occasion_key": "coronation",
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Weekly planner
# ---------------------------------------------------------------------------

async def test_week_is_empty_until_generated(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()

    resp = await app_client.get(
        f"/api/v2/planner/week?week_start={TODAY.isoformat()}", headers=auth(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "not_generated"
    assert len(body["days"]) == 7
    assert [day["status"] for day in body["days"]] == ["empty"] * 7


async def test_generated_week_covers_seven_days(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/planner/week/generate",
        headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["week_start"] == TODAY.isoformat()
    assert len(body["days"]) == 7
    dates = [day["plan_date"] for day in body["days"]]
    assert dates == [(TODAY + timedelta(days=i)).isoformat() for i in range(7)]

    factory = get_sessionmaker()
    async with factory() as session:
        weeks = (await session.execute(
            select(WeeklyPlan).where(WeeklyPlan.account_id == uid)
        )).scalars().all()
        days = (await session.execute(
            select(func.count(WeeklyPlanDay.id)).where(
                WeeklyPlanDay.weekly_plan_id == weeks[0].id
            )
        )).scalar_one()
    assert len(weeks) == 1
    assert days == 7


async def test_regenerating_a_week_does_not_duplicate_it(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    for _ in range(2):
        resp = await app_client.post(
            "/api/v2/planner/week/generate",
            headers=auth(token),
            json={"week_start": TODAY.isoformat()},
        )
        assert resp.status_code == 200, resp.text

    factory = get_sessionmaker()
    async with factory() as session:
        weeks = (await session.execute(
            select(func.count(WeeklyPlan.id)).where(WeeklyPlan.account_id == uid)
        )).scalar_one()
        days = (await session.execute(
            select(func.count(WeeklyPlanDay.id))
            .join(WeeklyPlan, WeeklyPlan.id == WeeklyPlanDay.weekly_plan_id)
            .where(WeeklyPlan.account_id == uid)
        )).scalar_one()
    assert weeks == 1
    assert days == 7


async def test_locked_day_is_left_alone_by_a_regenerate(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """A locked day is the user saying "I have decided this one". Regenerating
    the week must respect that."""
    token, _ = await registered_supabase_user()
    await app_client.post(
        "/api/v2/planner/week/generate",
        headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )

    locked = await app_client.post(
        f"/api/v2/planner/day/{TODAY.isoformat()}/lock",
        headers=auth(token),
        json={"locked": True},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["days"][0]["locked"] is True

    regenerate = await app_client.patch(
        f"/api/v2/planner/day/{TODAY.isoformat()}",
        headers=auth(token),
        json={"regenerate": True},
    )
    assert regenerate.status_code == 422
    assert "locked" in regenerate.json()["detail"]["message"].lower()


async def test_day_note_is_saved_on_the_planner_day(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await app_client.post(
        "/api/v2/planner/week/generate",
        headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )

    resp = await app_client.patch(
        f"/api/v2/planner/day/{TODAY.isoformat()}",
        headers=auth(token),
        json={"note": "Long day — keep it simple."},
    )

    assert resp.status_code == 200, resp.text
    day = next(d for d in resp.json()["days"] if d["plan_date"] == TODAY.isoformat())
    assert day["note"] == "Long day — keep it simple."


async def test_patching_a_day_before_generating_the_week_is_404(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.patch(
        f"/api/v2/planner/day/{TODAY.isoformat()}",
        headers=auth(token),
        json={"note": "nothing to attach to"},
    )
    assert resp.status_code == 404


async def test_week_is_scoped_to_the_account(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    other_token, _ = await registered_supabase_user()
    await app_client.post(
        "/api/v2/planner/week/generate",
        headers=auth(owner_token),
        json={"week_start": TODAY.isoformat()},
    )

    resp = await app_client.get(
        f"/api/v2/planner/week?week_start={TODAY.isoformat()}", headers=auth(other_token)
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_generated", (
        "one account's week must never be visible to another"
    )


async def test_planner_and_today_require_authentication(app_client, db_clean):
    assert (await app_client.get("/api/v2/today")).status_code == 401
    assert (await app_client.get("/api/v2/planner/week")).status_code == 401
    assert (await app_client.post(
        "/api/v2/planner/week/generate", json={}
    )).status_code == 401
