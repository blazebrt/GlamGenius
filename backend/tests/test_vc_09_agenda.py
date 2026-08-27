"""Focused pure policy coverage for the VC-09 orchestration boundary."""
from __future__ import annotations

from types import SimpleNamespace

from app.domains.planning import agenda, notifications


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
    maintenance = SimpleNamespace(source_kind="today_action", domain="maintenance", provenance={})
    assert notifications.topic_for_candidate(maintenance) == "maintenance"
    unknown = SimpleNamespace(source_kind="today_action", domain="unknown", provenance={})
    assert notifications.topic_for_candidate(unknown) == "today_style"
    assert notifications._target("/event-ready", {"eventId": "owned-event"}) == ("/event-ready", {"eventId": "owned-event"})
    assert notifications._target("https://example.invalid", {"eventId": "owned-event"}) == (None, {})
