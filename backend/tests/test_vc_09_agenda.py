"""Focused pure policy coverage for the VC-09 orchestration boundary."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.domains.planning import agenda, notifications
from app.domains.planning.models import NotificationDelivery, NotificationDevice
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select


def test_quiet_hours_crossing_midnight_are_deterministic() -> None:
    assert notifications.in_quiet_hours(22, 21, 7)
    assert notifications.in_quiet_hours(6, 21, 7)
    assert not notifications.in_quiet_hours(12, 21, 7)


def test_today_optional_value_never_becomes_push_eligible() -> None:
    tier, urgency, eligible = agenda._today_tier(SimpleNamespace(module="shopping", action_type="value_recovery"))
    assert (tier, urgency, eligible) == (4, "optional", False)
    tier, urgency, eligible = agenda._today_tier(SimpleNamespace(module="care", action_type="wear_low_use"))
    assert (tier, urgency, eligible) == (4, "optional", False)


def test_agenda_destinations_are_server_allowlisted() -> None:
    assert "/event-ready" in agenda.DESTINATIONS
    assert "https://example.invalid" not in agenda.DESTINATIONS
    assert agenda._safe_destination("maintenance", "maintenance") == ("/(tabs)/care", {})


def test_typed_event_priority_outranks_upkeep_without_copy_parsing() -> None:
    assert agenda._event_tier("event:unavailable", "preparation", 99)[0] == 0
    assert agenda._event_tier("care:hair_wash", "care", 40)[0] == 1


def test_notification_topics_and_targets_are_typed_and_fail_closed() -> None:
    event = SimpleNamespace(source_kind="event_preparation_entry", domain="event", provenance={})
    assert notifications.topic_for_candidate(event) == "event_preparation"
    care = SimpleNamespace(source_kind="today_action", domain="skincare", provenance={})
    assert notifications.topic_for_candidate(care) == "care"
    style = SimpleNamespace(source_kind="today_action", domain="outfit", provenance={})
    assert notifications.topic_for_candidate(style) == "today_style"
    maintenance = SimpleNamespace(source_kind="today_action", domain="maintenance", provenance={})
    assert notifications.topic_for_candidate(maintenance) == "maintenance"
    unknown = SimpleNamespace(source_kind="fabricated", domain="unknown", provenance={})
    assert notifications.topic_for_candidate(unknown) is None
    assert notifications._target("/event-ready", {"eventId": "owned-event"}) == ("/event-ready", {"eventId": "owned-event"})
    assert notifications._target("https://example.invalid", {"eventId": "owned-event"}) == (None, {})


@pytest.mark.asyncio
async def test_same_token_handoff_is_serialized_across_accounts(db_clean, registered_supabase_user):
    """Two PostgreSQL sessions cannot leave two active owners for one token."""
    _, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    token = f"ExponentPushToken[{uuid4().hex}]"
    factory = get_sessionmaker()

    async def register(account_id, device_key):
        async with factory() as session:
            await notifications.register_device(
                session, account_id, device_key=device_key,
                platform="android", expo_push_token=token,
            )
            await session.commit()

    await asyncio.gather(register(account_a, "install-a"), register(account_b, "install-b"))
    async with factory() as session:
        rows = list((await session.execute(select(NotificationDevice).where(
            NotificationDevice.expo_push_token == token,
        ))).scalars().all())
        active = [row for row in rows if row.status == "active"]
        assert len(active) == 1
        owner = active[0].account_id
        assert owner in {account_a, account_b}
        other = account_b if owner == account_a else account_a
        assert all(row.account_id != other for row in await notifications.active_devices(session, other))


@pytest.mark.asyncio
async def test_sequential_token_handoff_disables_previous_owner(db_clean, registered_supabase_user):
    _, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    token = f"ExponentPushToken[{uuid4().hex}]"
    factory = get_sessionmaker()
    async with factory() as session:
        await notifications.register_device(session, account_a, device_key="install-a", platform="ios", expo_push_token=token)
        await session.commit()
    async with factory() as session:
        await notifications.register_device(session, account_b, device_key="install-b", platform="ios", expo_push_token=token)
        await session.commit()
        assert not await notifications.active_devices(session, account_a)
        assert [row.account_id for row in await notifications.active_devices(session, account_b)] == [account_b]


@pytest.mark.asyncio
async def test_claim_delivery_reclaims_only_stale_sending_rows(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        stale = NotificationDelivery(
            account_id=account_id, plan_date=utcnow().date(), notification_key="stale",
            dedup_hash=uuid4().hex, title="Stale", status=notifications.STATUS_SENDING,
            claimed_at=utcnow() - timedelta(minutes=10), claim_token="old",
        )
        fresh = NotificationDelivery(
            account_id=account_id, plan_date=utcnow().date(), notification_key="fresh",
            dedup_hash=uuid4().hex, title="Fresh", status=notifications.STATUS_SENDING,
            claimed_at=utcnow(), claim_token="current",
        )
        session.add_all([stale, fresh])
        await session.commit()
        stale_id, fresh_id = stale.id, fresh.id
    async with factory() as session:
        assert await notifications.claim_delivery(session, fresh_id) is None
        reclaimed = await notifications.claim_delivery(session, stale_id)
        assert reclaimed
        await session.commit()


@pytest.mark.asyncio
async def test_suppression_reason_keeps_highest_authority(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        preference = await notifications.preferences_for(session, account_id, "Asia/Kolkata")
        preference.enabled = False
        preference.quiet_hours_start = 0
        preference.quiet_hours_end = 24
        row = await notifications.queue(
            session, account_id=account_id, plan_date=utcnow().date(), notification_key="disabled-first",
            title="Reminder", module="outfit", timezone_name="Asia/Kolkata",
        )
        await session.commit()
        assert row.status == notifications.STATUS_SUPPRESSED
        assert row.suppressed_reason == notifications.SUPPRESSED_DISABLED


@pytest.mark.asyncio
async def test_disabled_topic_skips_to_next_agenda_candidate(db_clean, registered_supabase_user, monkeypatch):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        preference = await notifications.preferences_for(session, account_id, "Asia/Kolkata")
        preference.topics = {**preference.topics, "event_preparation": False, "care": True}
        await session.commit()

    event = SimpleNamespace(
        key="event:first", source_kind="event_preparation_entry", source_id="event",
        source_action_id=None, domain="event", title="Event", body="Prepare", notification_eligible=True,
        destination="/event-ready", destination_params={"eventId": "event"},
    )
    care = SimpleNamespace(
        key="today:second", source_kind="today_action", source_id="plan", source_action_id="action",
        domain="skincare", title="Care", body="Care", notification_eligible=True,
        destination="/(tabs)/care", destination_params={},
    )

    async def fake_agenda(*args, **kwargs):
        return SimpleNamespace(items=(event, care))

    monkeypatch.setattr("app.domains.planning.agenda.build_agenda", fake_agenda)
    async with factory() as session:
        row = await notifications.queue_for_agenda(
            session, account_id=account_id, plan_date=utcnow().date(), timezone_name="Asia/Kolkata",
        )
        await session.commit()
    assert row is not None
    assert row.notification_key == "today:second"


@pytest.mark.asyncio
async def test_worker_commits_suppressed_decision(monkeypatch):
    from app.workers import notifications as worker

    class Session:
        commits = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            pass

    account_id = uuid4()
    preference = SimpleNamespace(account_id=account_id, enabled=True, native_push_enabled=True, timezone_name="Asia/Kolkata")
    decision = SimpleNamespace(status=notifications.STATUS_SUPPRESSED)
    monkeypatch.setattr(worker, "_preference_due", lambda *args, **kwargs: True)
    async def devices(*args, **kwargs):
        return [object()]
    monkeypatch.setattr(notifications, "active_devices", devices)
    async def no_context(*args, **kwargs):
        return None
    async def no_compile(*args, **kwargs):
        return None
    async def suppressed(*args, **kwargs):
        return decision
    monkeypatch.setattr(worker.context_stage, "gather", no_context)
    monkeypatch.setattr(worker.compiler, "compile_day", no_compile)
    monkeypatch.setattr(notifications, "queue_for_agenda", suppressed)
    session = Session()
    assert await worker.process_account(session, preference, now=utcnow()) == 0
    assert session.commits == 1
