"""VC-06 Skin and Hair maintenance timing.

Maintenance answers one question — when is this upkeep next worth doing — and
must never drift into selecting products, booking services, or inventing a
schedule the customer did not set.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

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
    lead_days_for,
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
        assert MIN_INTERVAL_DAYS <= kind.suggested_interval_days <= MAX_INTERVAL_DAYS


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


def test_tracking_alone_is_not_a_schedule():
    """Tracking says the kind matters, not how often. The preset stays a preset."""
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.status is MaintenanceStatus.NEEDS_CADENCE
    assert haircut.reason is MaintenanceReason.NO_CADENCE_SET
    assert haircut.interval_days is None, "the catalogue preset must not become their rhythm"
    assert haircut.suggested_interval_days == 42
    assert haircut.lead_days is None
    assert haircut.next_due_on is None
    assert decided.needs_cadence == (haircut,)
    assert decided.due == () and decided.coming_up == ()


def test_cadence_without_a_recorded_date_stays_unanchored():
    """No anchor means we say so, rather than quietly assuming today."""
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.status is MaintenanceStatus.NEEDS_ANCHOR
    assert haircut.reason is MaintenanceReason.NO_RECORDED_DATE
    assert haircut.next_due_on is None
    assert decided.needs_anchor == (haircut,)
    assert decided.due == ()


def test_incomplete_configuration_is_never_a_schedule():
    states = {
        "haircut": MaintenanceState(kind_key="haircut", tracked=True),
        "nail_care": MaintenanceState(kind_key="nail_care", tracked=True, interval_days=21),
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    assert {row.kind_key for row in decided.incomplete} == {"haircut", "nail_care"}
    assert decided.due == () and decided.coming_up == ()
    assert all(row.next_due_on is None for row in decided.incomplete)


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
            kind_key="haircut", tracked=True, interval_days=42,
            last_done_on=PLAN_DATE - timedelta(days=days_ago),
        )
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.status is expected_status
    assert haircut.reason is expected_reason
    assert haircut.next_due_on == PLAN_DATE - timedelta(days=days_ago) + timedelta(days=42)


def test_the_customers_rhythm_is_the_only_authority():
    last = PLAN_DATE - timedelta(days=20)
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=last, interval_days=14)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.interval_days == 14
    assert haircut.suggested_interval_days == 42, "the preset travels alongside, unused"
    assert haircut.status is MaintenanceStatus.DUE


def test_an_out_of_range_stored_interval_is_treated_as_unset():
    """A nonsense rhythm from an old row must not become a real schedule."""
    last = PLAN_DATE - timedelta(days=20)
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, last_done_on=last, interval_days=100000)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.interval_days is None
    assert haircut.status is MaintenanceStatus.NEEDS_CADENCE


def test_reminders_never_switch_on_for_an_untracked_kind():
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=False, reminders_enabled=True)}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.reminders_enabled is False


# --- Fingerprint -------------------------------------------------------------


def test_fingerprint_ignores_untracked_kinds_but_follows_tracked_change():
    base = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=10))}
    first = maintenance_fingerprint(decide_maintenance(base, plan_date=PLAN_DATE))

    with_untracked = dict(base)
    with_untracked["nail_care"] = MaintenanceState(kind_key="nail_care", tracked=False)
    assert maintenance_fingerprint(decide_maintenance(with_untracked, plan_date=PLAN_DATE)) == first

    moved = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=11))}
    assert maintenance_fingerprint(decide_maintenance(moved, plan_date=PLAN_DATE)) != first


def test_fingerprint_is_stable_across_repeated_evaluation():
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=50))}
    a = maintenance_fingerprint(decide_maintenance(states, plan_date=PLAN_DATE))
    b = maintenance_fingerprint(decide_maintenance(states, plan_date=PLAN_DATE))
    assert a == b


# --- Event Ready reuse -------------------------------------------------------


def test_due_by_event_date_reuses_the_same_authority():
    states = {
        "haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=40)),
        "nail_care": MaintenanceState(kind_key="nail_care", tracked=True, interval_days=21, last_done_on=PLAN_DATE - timedelta(days=1)),
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    # Haircut lands 2 days after the plan date; nail care is 20 days out.
    assert [row.kind_key for row in due_by_event_date(decided, PLAN_DATE + timedelta(days=3))] == ["haircut"]
    assert due_by_event_date(decided, PLAN_DATE + timedelta(days=1)) == ()
    assert [row.kind_key for row in due_by_event_date(decided, PLAN_DATE + timedelta(days=30))] == ["haircut", "nail_care"]


def test_an_untracked_kind_never_reaches_event_preparation():
    states = {"haircut": MaintenanceState(kind_key="haircut", tracked=False, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=400))}
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    assert due_by_event_date(decided, PLAN_DATE + timedelta(days=365)) == ()


# --- Persistence -------------------------------------------------------------


@pytest.mark.asyncio
async def test_recording_a_date_starts_tracking_but_declares_no_rhythm(db_clean, registered_supabase_user):
    """Recording a date says the kind matters, not how often they do it."""
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
        assert haircut.status is MaintenanceStatus.NEEDS_CADENCE
        assert haircut.last_done_on == PLAN_DATE - timedelta(days=50)

    async with factory() as session:
        await maintenance_service.set_preference(session, account_id, "haircut", interval_days=42)
        await session.commit()
    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
        assert haircut.status is MaintenanceStatus.DUE


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
        await maintenance_service.set_preference(session, account_id, "haircut", interval_days=42)
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
        await maintenance_service.set_preference(session, account_id, "haircut", interval_days=42)
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
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE)},
        plan_date=PLAN_DATE,
    )
    assert _maintenance_action(quiet) == []

    coming_up = decide_maintenance(
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=38))},
        plan_date=PLAN_DATE,
    )
    assert coming_up.coming_up, "precondition: this kind should read as coming up"
    assert _maintenance_action(coming_up) == [], "coming up belongs on Care, not on Today"

    due = decide_maintenance(
        {
            "haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=50)),
            "nail_care": MaintenanceState(kind_key="nail_care", tracked=True, interval_days=21, last_done_on=PLAN_DATE - timedelta(days=40)),
            "brow_upkeep": MaintenanceState(kind_key="brow_upkeep", tracked=True, interval_days=28, last_done_on=PLAN_DATE - timedelta(days=60)),
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
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=50))},
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
    assert haircut["interval_days"] == 30
    assert haircut["suggested_interval_days"] == 42
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


# --- Lead window derives from the rhythm actually chosen ----------------------


@pytest.mark.parametrize(
    ("interval", "expected_lead"),
    [
        (MIN_INTERVAL_DAYS, 1),   # 3 // 4 == 0, floored at a day
        (4, 1),
        (14, 3),
        (28, 7),
        (42, 7),                  # the catalogue haircut rhythm
        (MAX_INTERVAL_DAYS, 7),   # capped, so a yearly rhythm is not "coming up" for months
    ],
)
def test_lead_window_follows_the_chosen_rhythm(interval, expected_lead):
    assert lead_days_for(interval) == expected_lead


def test_a_short_rhythm_is_not_coming_up_the_moment_it_is_recorded():
    """The bug: a 3-day rhythm inheriting the 42-day haircut's 7-day lead.

    With a catalogue-derived lead it could never read ``not_due`` at all.
    """
    states = {
        "haircut": MaintenanceState(
            kind_key="haircut", tracked=True, interval_days=3, last_done_on=PLAN_DATE,
        )
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.lead_days == 1
    assert haircut.status is MaintenanceStatus.NOT_DUE
    assert haircut.days_until_due == 3

    # It becomes coming_up only inside its own one-day window.
    later = decide_maintenance(states, plan_date=PLAN_DATE + timedelta(days=2))
    assert next(r for r in later.decisions if r.kind_key == "haircut").status is MaintenanceStatus.COMING_UP


def test_a_long_rhythm_does_not_linger_in_coming_up():
    states = {
        "hair_trim": MaintenanceState(
            kind_key="hair_trim", tracked=True, interval_days=84,
            last_done_on=PLAN_DATE - timedelta(days=70),
        )
    }
    decided = decide_maintenance(states, plan_date=PLAN_DATE)
    trim = next(row for row in decided.decisions if row.kind_key == "hair_trim")
    assert trim.lead_days == 7
    assert trim.status is MaintenanceStatus.NOT_DUE, "14 days out is beyond the capped window"


# --- Fingerprint reacts to every declared fact -------------------------------


def test_fingerprint_follows_cadence_last_date_tracking_and_reminders():
    def print_of(**overrides):
        fields = {
            "kind_key": "haircut", "tracked": True, "interval_days": 42,
            "last_done_on": PLAN_DATE - timedelta(days=10),
        }
        fields.update(overrides)
        state = MaintenanceState(**fields)
        return maintenance_fingerprint(decide_maintenance({"haircut": state}, plan_date=PLAN_DATE))

    base = print_of()
    assert print_of(interval_days=30) != base, "cadence change must move the fingerprint"
    assert print_of(last_done_on=PLAN_DATE - timedelta(days=11)) != base
    assert print_of(reminders_enabled=True) != base
    assert print_of(tracked=False) != base
    assert print_of() == base


# --- Notification gating ------------------------------------------------------


def test_reminder_eligibility_requires_an_explicit_opt_in():
    from app.domains.care.maintenance import reminder_eligible

    due_without_opt_in = decide_maintenance(
        {"haircut": MaintenanceState(
            kind_key="haircut", tracked=True, interval_days=42,
            last_done_on=PLAN_DATE - timedelta(days=60),
        )},
        plan_date=PLAN_DATE,
    )
    assert due_without_opt_in.due, "precondition: this kind is due"
    assert reminder_eligible(due_without_opt_in) == ()

    opted_in = decide_maintenance(
        {"haircut": MaintenanceState(
            kind_key="haircut", tracked=True, interval_days=42,
            last_done_on=PLAN_DATE - timedelta(days=60), reminders_enabled=True,
        )},
        plan_date=PLAN_DATE,
    )
    assert [row.kind_key for row in reminder_eligible(opted_in)] == ["haircut"]

    not_yet_due = decide_maintenance(
        {"haircut": MaintenanceState(
            kind_key="haircut", tracked=True, interval_days=42,
            last_done_on=PLAN_DATE, reminders_enabled=True,
        )},
        plan_date=PLAN_DATE,
    )
    assert reminder_eligible(not_yet_due) == ()


def test_the_per_kind_switch_is_the_gate_not_the_module_default():
    """Excluding maintenance from the module map would strand the opt-in.

    Nothing in the product turns the generic module flag back on, so defaulting
    it off would mean an enabled per-kind reminder is suppressed as
    ``module_disabled`` and never sends. The real gate is the per-kind switch,
    which defaults to off.
    """
    from app.domains.planning.notifications import DEFAULT_MODULE_NOTIFICATIONS

    assert DEFAULT_MODULE_NOTIFICATIONS[MODULE_MAINTENANCE] is True
    assert all(DEFAULT_MODULE_NOTIFICATIONS.values())


@pytest.mark.asyncio
async def test_disabled_maintenance_reminders_never_reach_the_queue(
    db_clean, registered_supabase_user,
):
    """A due maintenance card must not become a notification without opt-in."""
    from app.domains.planning import notifications
    from app.domains.planning.models import DailyPlan, DailyPlanAction

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, interval_days=42,
        )
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=60), today=PLAN_DATE,
        )
        plan = DailyPlan(
            account_id=account_id, plan_date=PLAN_DATE, status="ready",
            headline="Your day", cache_key="k", generated_from="fresh",
        )
        session.add(plan)
        await session.flush()
        session.add(DailyPlanAction(
            plan_id=plan.id, module=MODULE_MAINTENANCE, action_type="maintenance_due",
            priority=1, title="Haircut is due", body="Due by your rhythm.",
        ))
        await session.commit()
        plan_id = plan.id

    async with factory() as session:
        plan = (await session.execute(select(DailyPlan).where(DailyPlan.id == plan_id))).scalar_one()
        queued = await notifications.queue_for_plan(
            session, plan=plan, timezone_name="Asia/Kolkata",
        )
        await session.commit()
    assert queued is None, "maintenance text must not be sent without an explicit opt-in"

    # Opting in makes the very same plan eligible.
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", reminders_enabled=True,
        )
        await session.commit()
    async with factory() as session:
        plan = (await session.execute(select(DailyPlan).where(DailyPlan.id == plan_id))).scalar_one()
        queued = await notifications.queue_for_plan(
            session, plan=plan, timezone_name="Asia/Kolkata",
        )
        await session.commit()
    # A row alone proves nothing: queue() returns suppressed rows too, which is
    # exactly how the first version of this test passed while the opt-in did
    # not actually deliver.
    assert queued is not None
    assert queued.status == "queued" and queued.sent_at is not None, queued.suppressed_reason
    assert "Haircut" in queued.body


@pytest.mark.asyncio
async def test_untracking_removes_maintenance_notification_eligibility(
    db_clean, registered_supabase_user,
):
    from app.domains.care.maintenance import reminder_eligible

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, interval_days=42, reminders_enabled=True,
        )
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=60), today=PLAN_DATE,
        )
        await session.commit()
    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        assert reminder_eligible(decided)

    async with factory() as session:
        await maintenance_service.set_preference(session, account_id, "haircut", tracked=False)
        await session.commit()
    async with factory() as session:
        decided = await maintenance_service.build_maintenance(session, account_id, plan_date=PLAN_DATE)
        assert reminder_eligible(decided) == ()


@pytest.mark.asyncio
async def test_quiet_hours_cap_and_dedup_still_apply_to_maintenance(
    db_clean, registered_supabase_user,
):
    """Opting in does not exempt maintenance from the existing safeguards."""
    from datetime import datetime as dt

    from app.domains.planning import notifications

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        preference = await notifications.preferences_for(session, account_id, "Asia/Kolkata")
        preference.modules = {**preference.modules, MODULE_MAINTENANCE: True}
        await session.commit()

    quiet_moment = dt(2026, 3, 16, 20, 0, tzinfo=UTC)  # 01:30 next day in Asia/Kolkata
    async with factory() as session:
        row = await notifications.queue(
            session, account_id=account_id, plan_date=PLAN_DATE,
            notification_key="daily_plan", title="Your day", body="Haircut is due.",
            module=MODULE_MAINTENANCE, timezone_name="Asia/Kolkata", moment=quiet_moment,
        )
        await session.commit()
    assert row.status == "suppressed" and row.suppressed_reason == notifications.SUPPRESSED_QUIET

    # The same content queued twice is one delivery, not two.
    async with factory() as session:
        again = await notifications.queue(
            session, account_id=account_id, plan_date=PLAN_DATE,
            notification_key="daily_plan", title="Your day", body="Haircut is due.",
            module=MODULE_MAINTENANCE, timezone_name="Asia/Kolkata", moment=quiet_moment,
        )
        await session.commit()
    assert again.id == row.id


@pytest.mark.asyncio
async def test_module_flag_off_still_suppresses_maintenance(db_clean, registered_supabase_user):
    from app.domains.planning import notifications

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        # Turning the module off explicitly still suppresses, per-kind opt-in
        # or not.
        preference = await notifications.preferences_for(session, account_id, "Asia/Kolkata")
        preference.modules = {**preference.modules, MODULE_MAINTENANCE: False}
        await session.flush()
        row = await notifications.queue(
            session, account_id=account_id, plan_date=PLAN_DATE,
            notification_key="daily_plan", title="Your day", body="Haircut is due.",
            module=MODULE_MAINTENANCE, timezone_name="Asia/Kolkata",
        )
        await session.commit()
    assert row.status == "suppressed" and row.suppressed_reason == notifications.SUPPRESSED_MODULE_OFF


@pytest.mark.asyncio
async def test_a_legacy_preference_row_reads_the_module_default(
    db_clean, registered_supabase_user,
):
    """Rows written before maintenance existed still serialize coherently."""
    from app.domains.planning import notifications
    from app.domains.planning.models import NotificationPreference

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(NotificationPreference(
            account_id=account_id, timezone_name="Asia/Kolkata",
            modules={"outfit": True, "skincare": True},
        ))
        await session.commit()
    async with factory() as session:
        row = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_id,
        ))).scalar_one()
        # A missing key reads as the module default; the per-kind switch, not
        # this flag, is what keeps a legacy account from being notified.
        assert notifications.serialize_preferences(row)["modules"][MODULE_MAINTENANCE] is True


# --- Atomic same-day recording ------------------------------------------------


@pytest.mark.asyncio
async def test_same_day_recording_is_conflict_safe(db_clean, registered_supabase_user):
    """Two concurrent retries must not collide on the unique constraint."""
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    done_on = PLAN_DATE - timedelta(days=5)

    async def record(note: str | None):
        async with factory() as session:
            await maintenance_service.record_done(
                session, account_id, "haircut", done_on=done_on, today=PLAN_DATE, note=note,
            )
            await session.commit()

    await record("first")
    await asyncio.gather(record(None), record(None))

    async with factory() as session:
        rows = (await session.execute(select(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id, MaintenanceEvent.kind_key == "haircut",
        ))).scalars().all()
    assert len(rows) == 1, "the same day is one fact however many times it is sent"
    assert rows[0].note == "first", "a bare retry must not erase the note it was recorded with"


@pytest.mark.asyncio
async def test_a_supplied_note_updates_the_same_fact(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.record_done(
            session, account_id, "nail_care", done_on=PLAN_DATE, today=PLAN_DATE, note="first",
        )
        await maintenance_service.record_done(
            session, account_id, "nail_care", done_on=PLAN_DATE, today=PLAN_DATE, note="corrected",
        )
        await session.commit()
    async with factory() as session:
        rows = (await session.execute(select(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id, MaintenanceEvent.kind_key == "nail_care",
        ))).scalars().all()
    assert len(rows) == 1 and rows[0].note == "corrected"


# --- Today never invents a task from incomplete configuration ------------------


def test_today_stays_silent_while_configuration_is_incomplete():
    from app.domains.planning.compiler import _maintenance_action

    no_cadence = decide_maintenance(
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True)}, plan_date=PLAN_DATE,
    )
    assert _maintenance_action(no_cadence) == []

    no_anchor = decide_maintenance(
        {"haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42)},
        plan_date=PLAN_DATE,
    )
    assert _maintenance_action(no_anchor) == []


# --- API: cadence, historical dates, reminders --------------------------------


@pytest.mark.asyncio
async def test_customer_can_set_change_and_clear_their_rhythm(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    headers = {"Authorization": f"Bearer {token}"}

    started = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"tracked": True})
    assert started.status_code == 200
    haircut = next(r for r in started.json()["kinds"] if r["kind"] == "haircut")
    assert haircut["status"] == "needs_cadence" and haircut["interval_days"] is None
    assert started.json()["needs_cadence"] == ["haircut"]

    chosen = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"interval_days": 21})
    haircut = next(r for r in chosen.json()["kinds"] if r["kind"] == "haircut")
    assert haircut["interval_days"] == 21 and haircut["status"] == "needs_anchor"

    changed = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"interval_days": 30})
    assert next(r for r in changed.json()["kinds"] if r["kind"] == "haircut")["interval_days"] == 30

    cleared = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"interval_days": None})
    haircut = next(r for r in cleared.json()["kinds"] if r["kind"] == "haircut")
    assert haircut["interval_days"] is None and haircut["status"] == "needs_cadence"


@pytest.mark.asyncio
async def test_customer_can_record_and_correct_a_historical_date(
    app_client, db_clean, registered_supabase_user,
):
    """Somebody whose last haircut was ten days ago must not have to say today."""
    token, _ = await registered_supabase_user()
    headers = {"Authorization": f"Bearer {token}"}
    await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"tracked": True, "interval_days": 42})

    ten_days_ago = (datetime.now(UTC).date() - timedelta(days=10)).isoformat()
    recorded = await app_client.post(
        "/api/v2/maintenance/haircut/done", headers=headers, json={"done_on": ten_days_ago},
    )
    assert recorded.status_code == 200
    haircut = next(r for r in recorded.json()["kinds"] if r["kind"] == "haircut")
    assert haircut["last_done_on"] == ten_days_ago
    assert haircut["status"] == "not_due"

    corrected_date = (datetime.now(UTC).date() - timedelta(days=40)).isoformat()
    await app_client.delete(f"/api/v2/maintenance/haircut/done/{ten_days_ago}", headers=headers)
    corrected = await app_client.post(
        "/api/v2/maintenance/haircut/done", headers=headers, json={"done_on": corrected_date},
    )
    haircut = next(r for r in corrected.json()["kinds"] if r["kind"] == "haircut")
    assert haircut["last_done_on"] == corrected_date


@pytest.mark.asyncio
async def test_reminder_preference_is_explicit_and_dies_with_tracking(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    headers = {"Authorization": f"Bearer {token}"}

    started = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"tracked": True})
    assert next(r for r in started.json()["kinds"] if r["kind"] == "haircut")["reminders_enabled"] is False

    on = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"reminders_enabled": True})
    assert next(r for r in on.json()["kinds"] if r["kind"] == "haircut")["reminders_enabled"] is True

    off = await app_client.put("/api/v2/maintenance/haircut", headers=headers, json={"tracked": False})
    assert next(r for r in off.json()["kinds"] if r["kind"] == "haircut")["reminders_enabled"] is False


# --- Event Ready: one authority, correct date, honest fingerprint -------------


def _material_with(maintenance):
    """The minimum Care material shape Event Ready reads."""
    return SimpleNamespace(
        maintenance=maintenance,
        decisions=SimpleNamespace(
            decision_version="v", product_decisions=[],
            skin_core_gap_count=0, hair_core_gap_count=0,
        ),
        care_plan=SimpleNamespace(
            plan_version="v", resolved_effort=SimpleNamespace(value="standard"),
            effort_source=SimpleNamespace(value="default"),
            active_skin_slot_count=0, active_hair_slot_count=0,
        ),
        hair_wash_cadence=SimpleNamespace(status=SimpleNamespace(value="not_due"), as_payload=dict),
        decision_fingerprint="d", routine_plan_fingerprint="r", hair_wash_cadence_fingerprint="h",
    )


def test_event_ready_compares_against_the_event_local_date_not_today():
    """A kind not due today, but due by the event, must reach the timeline."""
    from app.domains.planning.event_ready import _maintenance_actions

    today = PLAN_DATE
    event_local_date = PLAN_DATE + timedelta(days=20)
    decided = decide_maintenance(
        {"haircut": MaintenanceState(
            kind_key="haircut", tracked=True, interval_days=42,
            last_done_on=today - timedelta(days=30),
        )},
        plan_date=today,
    )
    haircut = next(row for row in decided.decisions if row.kind_key == "haircut")
    assert haircut.status is MaintenanceStatus.NOT_DUE, "precondition: not due on the plan date"
    assert haircut.next_due_on == today + timedelta(days=12)

    rows = _maintenance_actions(_material_with(decided), event_local_date)
    assert len(rows) == 1
    assert rows[0]["action_key"] == "preparation:maintenance_timing"
    assert rows[0]["material"]["kinds"] == ["haircut"]

    # An earlier event, before it comes round, gets nothing.
    assert _maintenance_actions(_material_with(decided), today + timedelta(days=5)) == []


def test_event_ready_stays_silent_for_incomplete_configuration():
    from app.domains.planning.event_ready import _maintenance_actions

    for state in (
        MaintenanceState(kind_key="haircut", tracked=True),
        MaintenanceState(kind_key="haircut", tracked=True, interval_days=42),
    ):
        decided = decide_maintenance({"haircut": state}, plan_date=PLAN_DATE)
        assert _maintenance_actions(_material_with(decided), PLAN_DATE + timedelta(days=365)) == []


def test_event_ready_care_payload_carries_the_maintenance_fingerprint():
    from app.domains.planning.event_ready import _care_payload

    base_state = MaintenanceState(
        kind_key="haircut", tracked=True, interval_days=42,
        last_done_on=PLAN_DATE - timedelta(days=30),
    )
    first = _care_payload(_material_with(decide_maintenance({"haircut": base_state}, plan_date=PLAN_DATE)))
    assert first["maintenance_fingerprint"] == maintenance_fingerprint(
        decide_maintenance({"haircut": base_state}, plan_date=PLAN_DATE)
    )

    same = _care_payload(_material_with(decide_maintenance({"haircut": base_state}, plan_date=PLAN_DATE)))
    assert same == first, "identical maintenance state must not move the fingerprint"

    for changed in (
        MaintenanceState(kind_key="haircut", tracked=True, interval_days=21, last_done_on=PLAN_DATE - timedelta(days=30)),
        MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=31)),
        MaintenanceState(kind_key="haircut", tracked=False, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=30)),
    ):
        moved = _care_payload(_material_with(decide_maintenance({"haircut": changed}, plan_date=PLAN_DATE)))
        assert moved["maintenance_fingerprint"] != first["maintenance_fingerprint"]


def test_event_ready_holds_no_second_timing_engine():
    """Event Ready must read the canonical authority, not re-derive schedules."""
    import inspect

    from app.domains.planning import event_ready

    source = inspect.getsource(event_ready)
    assert "due_by_event_date" in source
    for forbidden in ("timedelta(days=", "lead_days_for", "MAINTENANCE_KINDS", "suggested_interval_days"):
        assert forbidden not in source, f"event_ready must not compute maintenance timing itself ({forbidden})"


# --- Round two: findings raised on the correction itself ----------------------


@pytest.mark.asyncio
async def test_a_per_kind_opt_in_actually_delivers_for_a_new_account(
    db_clean, registered_supabase_user,
):
    """The opt-in must not be stranded behind a flag nothing can turn on.

    Defaulting the maintenance module off meant queue_for_plan selected the
    action and queue() then suppressed it as module_disabled, so an enabled
    reminder never sent.
    """
    from app.domains.planning import notifications
    from app.domains.planning.models import DailyPlan, DailyPlanAction

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, interval_days=42, reminders_enabled=True,
        )
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=60), today=PLAN_DATE,
        )
        plan = DailyPlan(
            account_id=account_id, plan_date=PLAN_DATE, status="ready",
            headline="Your day", cache_key="k2", generated_from="fresh",
        )
        session.add(plan)
        await session.flush()
        session.add(DailyPlanAction(
            plan_id=plan.id, module=MODULE_MAINTENANCE, action_type="maintenance_due",
            priority=1, title="Haircut is due", body="Due by your rhythm.",
        ))
        await session.commit()
        plan_id = plan.id

    # No notification preference row exists yet — the account is brand new.
    async with factory() as session:
        plan = (await session.execute(select(DailyPlan).where(DailyPlan.id == plan_id))).scalar_one()
        queued = await notifications.queue_for_plan(session, plan=plan, timezone_name="Asia/Kolkata")
        await session.commit()
    assert queued is not None
    assert queued.status == "queued" and queued.sent_at is not None, queued.suppressed_reason


@pytest.mark.asyncio
async def test_a_notification_never_names_a_kind_left_switched_off(
    db_clean, registered_supabase_user,
):
    """Two kinds due, one opted in: the reminder must name only that one."""
    from app.domains.planning import notifications
    from app.domains.planning.models import DailyPlan, DailyPlanAction

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, interval_days=42, reminders_enabled=True,
        )
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=PLAN_DATE - timedelta(days=60), today=PLAN_DATE,
        )
        await maintenance_service.set_preference(
            session, account_id, "nail_care", tracked=True, interval_days=21, reminders_enabled=False,
        )
        await maintenance_service.record_done(
            session, account_id, "nail_care", done_on=PLAN_DATE - timedelta(days=60), today=PLAN_DATE,
        )
        plan = DailyPlan(
            account_id=account_id, plan_date=PLAN_DATE, status="ready",
            headline="Your day", cache_key="k3", generated_from="fresh",
        )
        session.add(plan)
        await session.flush()
        # The plan's own card names both, as Today should.
        session.add(DailyPlanAction(
            plan_id=plan.id, module=MODULE_MAINTENANCE, action_type="maintenance_due",
            priority=1, title="Some upkeep is due", body="Haircut, Nail care are due by your own rhythm.",
        ))
        await session.commit()
        plan_id = plan.id

    async with factory() as session:
        plan = (await session.execute(select(DailyPlan).where(DailyPlan.id == plan_id))).scalar_one()
        queued = await notifications.queue_for_plan(session, plan=plan, timezone_name="Asia/Kolkata")
        await session.commit()

    assert queued is not None and queued.status == "queued"
    assert "Haircut" in queued.body
    assert "Nail care" not in queued.body, "a kind with reminders off must not be named"


def test_the_today_card_and_the_reminder_share_one_copy_builder():
    """Two builders would eventually describe different kinds."""
    from app.domains.care.maintenance import maintenance_headline
    from app.domains.planning.compiler import _maintenance_action

    decided = decide_maintenance(
        {
            "haircut": MaintenanceState(kind_key="haircut", tracked=True, interval_days=42, last_done_on=PLAN_DATE - timedelta(days=60)),
            "nail_care": MaintenanceState(kind_key="nail_care", tracked=True, interval_days=21, last_done_on=PLAN_DATE - timedelta(days=60)),
        },
        plan_date=PLAN_DATE,
    )
    title, body = maintenance_headline(decided.due)
    card = _maintenance_action(decided)[0]
    assert card["title"] == title and card["body"] == body

    # The same builder, given one row, names exactly that row.
    one_title, one_body = maintenance_headline(decided.due[:1])
    assert decided.due[0].label in one_body
    assert decided.due[1].label not in one_body
    assert one_title == f"{decided.due[0].label} is due"


def test_versions_advance_with_the_rules_they_identify():
    """A changed decision contract must not report the previous version."""
    from app.domains.care.maintenance_rules import MAINTENANCE_CATALOGUE_VERSION, MAINTENANCE_VERSION
    from app.domains.planning.event_ready import EVENT_READY_VERSION
    from app.domains.planning.models import PLANNER_VERSION

    assert MAINTENANCE_VERSION == "vc-06.1", "needs_cadence and the lead formula changed the contract"
    assert MAINTENANCE_CATALOGUE_VERSION.startswith("vc-06.1")
    assert EVENT_READY_VERSION == "vc-06-v1", "maintenance material and a new action rule changed it"
    assert PLANNER_VERSION == "vc06-v1"


@pytest.mark.asyncio
async def test_regenerating_a_week_reports_the_rules_that_rebuilt_it(
    db_clean, registered_supabase_user,
):
    from app.domains.planning.models import PLANNER_VERSION, WeeklyPlan
    from app.domains.planning.weekly import get_or_create_week

    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    week_start = PLAN_DATE - timedelta(days=PLAN_DATE.weekday())
    async with factory() as session:
        plan = await get_or_create_week(session, account_id, week_start, "Asia/Kolkata")
        # Simulate a week created under the previous rule set.
        plan.engine_version = "phase5-v1"
        await session.commit()
        plan_id = plan.id

    async with factory() as session:
        row = (await session.execute(select(WeeklyPlan).where(WeeklyPlan.id == plan_id))).scalar_one()
        assert row.engine_version == "phase5-v1"

    # The tail of weekly.generate is what advances it; assert that contract
    # directly rather than driving a whole week build here.
    import inspect

    from app.domains.planning import weekly

    source = inspect.getsource(weekly.generate)
    assert "plan.engine_version = PLANNER_VERSION" in source
    assert PLANNER_VERSION == "vc06-v1"
