"""VC-06 Skin and Hair maintenance timing.

Maintenance answers one question — when is this upkeep next worth doing — and
must never drift into selecting products, booking services, or inventing a
schedule the customer did not set.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.api.v2 import maintenance as maintenance_api
from app.domains.care import maintenance_service
from app.domains.care.maintenance import (
    MaintenanceReason,
    MaintenanceState,
    MaintenanceStatus,
    decide_maintenance,
    due_by_event_date,
    maintenance_fingerprint,
)
from app.domains.care.maintenance_rules import (
    MAINTENANCE_KIND_KEYS,
    MAINTENANCE_KINDS,
    MAX_INTERVAL_DAYS,
    MIN_INTERVAL_DAYS,
    MaintenanceDomain,
    get_kind,
)
from app.domains.planning.models import MODULE_MAINTENANCE, MODULES
from app.domains.routines.models import MaintenanceEvent, MaintenancePreference
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError
from sqlalchemy import select

PLAN_DATE = date(2026, 3, 16)


# --- Catalogue ---------------------------------------------------------------


def test_catalogue_is_upkeep_timing_only_and_never_a_marketplace():
    """No kind may imply booking, buying, pricing, or a judgement about looks."""
    forbidden = (
        "salon", "book", "appointment", "price", "cost", "deal", "offer",
        "buy", "shop", "clinic", "treatment", "therapy", "procedure",
        "money wasted", "bad", "ugly", "unattractive", "poor", "should",
        "overdue", "neglect", "fix",
    )
    assert MAINTENANCE_KINDS, "the catalogue must not be empty"
    for kind in MAINTENANCE_KINDS:
        blob = f"{kind.label} {kind.description}".lower()
        for word in forbidden:
            assert word not in blob, f"{kind.key} says {word!r}"
        assert kind.domain in (MaintenanceDomain.HAIR, MaintenanceDomain.SKIN)
        assert MIN_INTERVAL_DAYS <= kind.default_interval_days <= MAX_INTERVAL_DAYS
        assert 1 <= kind.lead_days <= 7


def test_catalogue_keys_are_unique_and_stable():
    assert len(set(MAINTENANCE_KIND_KEYS)) == len(MAINTENANCE_KIND_KEYS)
    assert get_kind("haircut") is not None
    assert get_kind("not-a-kind") is None


# --- Deterministic timing ----------------------------------------------------


def test_untracked_kind_is_never_scheduled():
    decided = decide_maintenance({}, plan_date=PLAN_DATE)
    assert {row.kind_key for row in decided.decisions} == set(MAINTENANCE_KIND_KEYS)
    for row in decided.decisions:
        assert row.status is MaintenanceStatus.NOT_TRACKED
        assert row.reason is MaintenanceReason.NOT_TRACKED
        assert row.next_due_on is None and row.days_until_due is None
    assert decided.due == () and decided.coming_up == () and decided.needs_anchor == ()


def test_tracked_without_a_recorded_date_stays_unanchored():
    """No anchor means we say so, rather than quietly assuming today."""
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.status is MaintenanceStatus.NEEDS_ANCHOR
    assert haircut.reason is MaintenanceReason.NO_RECORDED_DATE
    assert haircut.next_due_on is None
    assert decided.needs_anchor == (haircut,)
    assert decided.due == ()


@pytest.mark.parametrize(
    ("days_ago", "expected_status", "expected_reason"),
    [
        (0, MaintenanceStatus.NOT_DUE, MaintenanceReason.INTERVAL_NOT_ELAPSED),
        (34, MaintenanceStatus.NOT_DUE, MaintenanceReason.INTERVAL_NOT_ELAPSED),
        (36, MaintenanceStatus.COMING_UP, MaintenanceReason.INTERVAL_APPROACHING),
        (42, MaintenanceStatus.DUE, MaintenanceReason.INTERVAL_ELAPSED),
        (90, MaintenanceStatus.DUE, MaintenanceReason.INTERVAL_ELAPSED),
    ],
)
def test_interval_boundaries_are_exact(days_ago, expected_status, expected_reason):
    """A 42-day haircut has a 7-day lead, so 36 days ago is the first heads-up."""
    states = {
        "haircut": MaintenanceState(
            kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=days_ago),
        )
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.status is expected_status
    assert haircut.reason is expected_reason
    assert haircut.next_due_on == PLAN_DATE - timedelta(days=days_ago) + timedelta(days=42)


def test_customer_interval_overrides_the_catalogue_rhythm():
    last = PLAN_DATE - timedelta(days=20)
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=last, interval_days=14)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.interval_days == 14 and haircut.interval_is_custom is True
    assert haircut.status is MaintenanceStatus.DUE


def test_an_out_of_range_stored_interval_falls_back_to_the_catalogue():
    """A nonsense rhythm from an old row must not become a real schedule."""
    last = PLAN_DATE - timedelta(days=20)
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=last, interval_days=100000)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.interval_days == 42 and haircut.interval_is_custom is False


def test_reminders_never_switch_on_for_an_untracked_kind():
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=False, reminders_enabled=True)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.reminders_enabled is False


# --- Fingerprint -------------------------------------------------------------


def test_fingerprint_ignores_untracked_kinds_but_follows_tracked_change():
    base = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=10))}
    first = maintenance_fingerprint(decide_maintenance(base, plan_date=PLAN_DATE))

    with_untracked = dict(base)
    with_untracked["nail_care"] = MaintenanceState(kind_key="nail_care", tracked=False)
    assert maintenance_fingerprint(decide_maintenance(with_untracked, plan_date=PLAN_DATE)) == first

    moved = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=11))}
    assert maintenance_fingerprint(decide_maintenance(moved, plan_date=PLAN_DATE)) != first


def test_fingerprint_is_stable_across_repeated_evaluation():
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=50))}
    a = maintenance_fingerprint(decide_maintenance(states, plan_date=PLAN_DATE))
    b = maintenance_fingerprint(decide_maintenance(states, plan_date=PLAN_DATE))
    assert a == b


# --- Event Ready reuse -------------------------------------------------------


def test_due_by_event_date_reuses_the_same_authority():
    states = {
        "haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=40)),
        "nail_care": MaintenanceState(kind_key="nail_care", tracked=True, last_done_on=PLAN_DATE - timedelta(days=1)),
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    # Haircut lands 2 days after the plan date; nail care is 20 days out.
    assert [row.kind_key for row in due_by_event_date(decided, PLAN_DATE + timedelta(days=3))] == ["haircut"]
    assert due_by_event_date(decided, PLAN_DATE + timedelta(days=1)) == ()
    assert [row.kind_key for row in due_by_event_date(decided, PLAN_DATE + timedelta(days=30))] == ["haircut", "nail_care"]


def test_an_untracked_kind_never_reaches_event_preparation():
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=False, last_done_on=PLAN_DATE - timedelta(days=400))}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    assert due_by_event_date(decided, PLAN_DATE + timedelta(days=365)) == ()


# --- Persistence -------------------------------------------------------------


@pytest.mark.asyncio
async def test_recording_a_date_starts_tracking_and_anchors_the_schedule(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=50), today=PLAN_DATE,
        )
        await session.commit()

    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
        assert haircut.tracked is True
        assert haircut.status is MaintenanceStatus.DUE
        assert haircut.last_done_on == PLAN_DATE - timedelta(days=50)


@pytest.mark.asyncio
async def test_only_the_latest_date_on_or_before_the_plan_day_anchors(db_clean, registered_supabase_user):
    """A date in the future cannot anchor today, and older dates are superseded."""
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        for offset in (90, 50, 10):
            await maintenance_service.record_done(
                session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=offset), today=PLAN_DATE,
            )
        session.add(MaintenanceEvent(
            account_id=account_id, kind_key="haircut",
            done_on=PLAN_DATE + timedelta(days=5), source="user_declared",
        ))
        await session.commit()

    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
        assert haircut.last_done_on == PLAN_DATE - timedelta(days=10)


@pytest.mark.asyncio
async def test_recording_the_same_day_twice_is_one_fact(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.record_done(session, account_id, "nail_care", done_on=PLAN_DATE, today=PLAN_DATE)
        await maintenance_service.record_done(
            session, account_id, "nail_care", done_on=PLAN_DATE, today=PLAN_DATE, note="second",
        )
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(select(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id, MaintenanceEvent.kind_key == "nail_care",
        ))).scalars().all()
        assert len(rows) == 1 and rows[0].note == "second"


@pytest.mark.asyncio
async def test_a_future_date_is_refused(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await maintenance_service.record_done(
                session, account_id, "haircut", done_on=PLAN_DATE + timedelta(days=1), today=PLAN_DATE,
            )


@pytest.mark.asyncio
async def test_an_unknown_kind_is_not_found(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await maintenance_service.set_preference(session, account_id, "laser-surgery", tracked=True)
        with pytest.raises(NotFoundError):
            await maintenance_service.record_done(
                session, account_id, "laser-surgery", done_on=PLAN_DATE, today=PLAN_DATE,
            )


@pytest.mark.asyncio
async def test_an_out_of_range_interval_is_refused_rather_than_squashed(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await maintenance_service.set_preference(
                session, account_id, "haircut", tracked=True, interval_days=MAX_INTERVAL_DAYS + 1,
            )
        with pytest.raises(ValidationFailedError):
            await maintenance_service.set_preference(
                session, account_id, "haircut", tracked=True, interval_days=MIN_INTERVAL_DAYS - 1,
            )


@pytest.mark.asyncio
async def test_untracking_turns_reminders_off(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, reminders_enabled=True,
        )
        await session.commit()
        row = (await session.execute(select(MaintenancePreference).where(
            MaintenancePreference.account_id == account_id,
        ))).scalar_one()
        assert row.reminders_enabled is True

        await maintenance_service.set_preference(session, account_id, "haircut", tracked=False)
        await session.commit()
        await session.refresh(row)
        assert row.tracked is False and row.reminders_enabled is False


@pytest.mark.asyncio
async def test_forgetting_a_date_removes_the_anchor(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=50), today=PLAN_DATE,
        )
        await session.commit()
    async with factory() as session:
        assert await maintenance_service.forget_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=50),
        ) is True
        await session.commit()
    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
        # Still tracked, but with nothing to schedule from.
        assert haircut.tracked is True and haircut.status is MaintenanceStatus.NEEDS_ANCHOR


@pytest.mark.asyncio
async def test_one_account_never_sees_another_accounts_maintenance(db_clean, registered_supabase_user):
    _, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.record_done(
            session, account_a, "haircut", done_on=PLAN_DATE - timedelta(days=50), today=PLAN_DATE,
        )
        await session.commit()

    async with factory() as session:
        theirs = await maintenance_service.build_maintenance(session, account_b, plan_date=PLAN_DATE)
        assert all(row.status is MaintenanceStatus.NOT_TRACKED for row in theirs.decisions)
        assert await maintenance_service.history(session, account_b, "haircut") == []


@pytest.mark.asyncio
async def test_a_preference_for_a_retired_kind_is_inert(db_clean, registered_supabase_user):
    """An unknown stored kind must not break the customer's other choices."""
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(MaintenancePreference(account_id=account_id, kind_key="retired_kind", tracked=True))
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=50), today=PLAN_DATE,
        )
        await session.commit()

    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        assert {row.kind_key for row in decided.decisions} == set(MAINTENANCE_KIND_KEYS)
        haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
        assert haircut.status is MaintenanceStatus.DUE


# --- Today integration -------------------------------------------------------


def test_maintenance_is_a_registered_today_module():
    assert MODULE_MAINTENANCE in MODULES


def test_today_shows_one_card_only_when_something_is_due():
    from app.domains.planning.compiler import _maintenance_action

    quiet = decide_maintenance(
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE)},
        plan_date=PLAN_DATE,
    )
    assert _maintenance_action(quiet) == []

    coming_up = decide_maintenance(
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=38))},
        plan_date=PLAN_DATE,
    )
    assert coming_up.coming_up, "precondition: this kind should read as coming up"
    assert _maintenance_action(coming_up) == [], "coming up belongs on Care, not on Today"

    due = decide_maintenance(
        {
            "haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=50)),
            "nail_care": MaintenanceState(kind_key="nail_care", tracked=True, last_done_on=PLAN_DATE - timedelta(days=40)),
            "brow_upkeep": MaintenanceState(kind_key="brow_upkeep", tracked=True, last_done_on=PLAN_DATE - timedelta(days=60)),
        },
        plan_date=PLAN_DATE,
    )
    rows = _maintenance_action(due)
    assert len(rows) == 1, "Today must not be flooded with a card per kind"
    assert rows[0]["module"] == MODULE_MAINTENANCE
    assert rows[0]["action_type"] == "maintenance_due"
    assert "1 more" in rows[0]["body"]


def test_today_maintenance_copy_stays_constructive():
    from app.domains.planning.compiler import _maintenance_action

    due = decide_maintenance(
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=PLAN_DATE - timedelta(days=50))},
        plan_date=PLAN_DATE,
    )
    row = _maintenance_action(due)[0]
    blob = f"{row['title']} {row['body']} {row['relevance']}".lower()
    for word in ("overdue", "late", "neglect", "should", "bad", "poor", "fix", "book", "salon"):
        assert word not in blob


# --- API ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_routes_are_account_scoped_and_never_promotional(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    headers = {"Authorization": f"Bearer {token}"}

    listed = await app_client.get("/api/v2/maintenance", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {row["kind"] for row in body["kinds"]} == set(MAINTENANCE_KIND_KEYS)
    assert all(row["status"] == "not_tracked" for row in body["kinds"])
    assert body["interval_bounds"] == {"min_days": MIN_INTERVAL_DAYS, "max_days": MAX_INTERVAL_DAYS}
    assert "does not book appointments" in body["note"]

    tracked = await app_client.put(
        "/api/v2/maintenance/haircut", headers=headers,
        json={"tracked": True, "interval_days": 30, "reminders_enabled": True},
    )
    assert tracked.status_code == 200, tracked.text
    haircut = next(row for row in tracked.json()["kinds"] if row["kind"] == "haircut")
    assert haircut["tracked"] is True
    assert haircut["interval_days"] == 30 and haircut["interval_is_custom"] is True
    assert haircut["reminders_enabled"] is True
    assert haircut["status"] == "needs_anchor"
    assert tracked.json()["needs_anchor"] == ["haircut"]

    recorded = await app_client.post(
        "/api/v2/maintenance/haircut/done", headers=headers,
        json={"done_on": "2020-01-01", "note": "at home"},
    )
    assert recorded.status_code == 200, recorded.text
    haircut = next(row for row in recorded.json()["kinds"] if row["kind"] == "haircut")
    assert haircut["status"] == "due" and haircut["last_done_on"] == "2020-01-01"

    history = await app_client.get("/api/v2/maintenance/haircut/history", headers=headers)
    assert history.status_code == 200
    assert history.json()["entries"] == [
        {"done_on": "2020-01-01", "source": "user_declared", "note": "at home"}
    ]

    removed = await app_client.delete("/api/v2/maintenance/haircut/done/2020-01-01", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["removed"] is True
    haircut = next(row for row in removed.json()["kinds"] if row["kind"] == "haircut")
    assert haircut["status"] == "needs_anchor"


@pytest.mark.asyncio
async def test_maintenance_routes_reject_bad_input(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    headers = {"Authorization": f"Bearer {token}"}

    bad_kind = await app_client.put("/api/v2/maintenance/tattoo", headers=headers, json={"tracked": True})
    assert bad_kind.status_code == 404

    too_long = await app_client.put(
        "/api/v2/maintenance/haircut", headers=headers, json={"interval_days": MAX_INTERVAL_DAYS + 1},
    )
    assert too_long.status_code == 422

    extra = await app_client.put(
        "/api/v2/maintenance/haircut", headers=headers, json={"tracked": True, "book_salon": True},
    )
    assert extra.status_code == 422

    future = await app_client.post(
        "/api/v2/maintenance/haircut/done", headers=headers,
        json={"done_on": (datetime.now(UTC).date() + timedelta(days=2)).isoformat()},
    )
    assert future.status_code == 422


@pytest.mark.asyncio
async def test_maintenance_routes_require_a_token(app_client, db_clean):
    assert (await app_client.get("/api/v2/maintenance")).status_code == 401
    assert (await app_client.put("/api/v2/maintenance/haircut", json={"tracked": True})).status_code == 401
    assert (await app_client.post("/api/v2/maintenance/haircut/done", json={})).status_code == 401


@pytest.mark.asyncio
async def test_route_never_accepts_an_account_identifier():
    """Ownership comes from the token; there is nothing to tamper with."""
    import inspect

    for handler in (
        maintenance_api.list_maintenance, maintenance_api.update_maintenance,
        maintenance_api.record_maintenance, maintenance_api.forget_maintenance,
        maintenance_api.maintenance_history,
    ):
        assert "account_id" not in inspect.signature(handler).parameters


# --- Privacy -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_is_exported_and_deleted_with_the_account(
    db_clean, registered_supabase_user,
):
    from app.domains.privacy import REGISTRY, Classification
    from app.domains.privacy import export as export_service

    assert REGISTRY["maintenance_preferences"] is Classification.INCLUDED
    assert REGISTRY["maintenance_events"] is Classification.INCLUDED

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, interval_days=30,
        )
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=10), today=PLAN_DATE,
        )
        await session.commit()

    async with factory() as session:
        payload = await export_service.build_export(session, account_id)
    routines = payload["domains"]["routines"]
    assert [row["kind_key"] for row in routines["maintenance_preferences"]] == ["haircut"]
    assert [row["done_on"] for row in routines["maintenance_events"]] == [
        PLAN_DATE - timedelta(days=10)
    ]

    async with factory() as session:
        from app.domains.identity.models import Account
        from sqlalchemy import delete as sa_delete

        await session.execute(sa_delete(Account).where(Account.id == account_id))
        await session.commit()

    async with factory() as session:
        left = (await session.execute(select(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id,
        ))).scalars().all()
        prefs = (await session.execute(select(MaintenancePreference).where(
            MaintenancePreference.account_id == account_id,
        ))).scalars().all()
    assert left == [] and prefs == []


@pytest.mark.asyncio
async def test_maintenance_state_is_ignored_for_a_nonexistent_account(db_clean):
    """A random account id sees the untracked catalogue, never someone else's."""
    factory = get_sessionmaker()
    async with factory() as session:
        decided = await maintenance_service.build_maintenance(
            session, uuid.uuid4(), plan_date=PLAN_DATE,
        )
    assert all(row.status is MaintenanceStatus.NOT_TRACKED for row in decided.decisions)
