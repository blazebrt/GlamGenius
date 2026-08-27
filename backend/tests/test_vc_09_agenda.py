"""Focused pure policy coverage for the VC-09 orchestration boundary."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.domains.planning import agenda, notifications
from app.domains.planning.models import NotificationDevice
from app.shared.database.sql import get_sessionmaker


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
