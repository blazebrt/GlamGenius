"""Pure contract checks for the VC-02 Event Ready foundation."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.domains.planning.event_ready import EVENT_READY_VERSION, _context_payload, _event_payload, _sha, _status
from app.domains.planning.schemas import EventReadyActionComplete, EventReadyLookPatch


def _event(**overrides):
    values = {
        "id": uuid4(), "title": "Wedding", "starts_at": datetime(2030, 1, 12, 12, tzinfo=UTC),
        "ends_at": None, "all_day": False, "location": "Hall", "occasion_key": "wedding", "dress_code_hint": None,
        "user_confirmed": True, "provider": "manual", "source": "user_declared", "status": "active", "inference_confidence": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_event_ready_version_and_fingerprint_are_stable():
    event = _event()
    first = _event_payload(event, "Asia/Kolkata")
    assert EVENT_READY_VERSION == "vc-02-v1"
    assert _sha(first) == _sha(dict(first))
    assert _sha(first) == _sha(first)


def test_unconfirmed_event_needs_confirmation_without_promoting_inference():
    event = _event(user_confirmed=False)
    day = SimpleNamespace(timezone_name="Asia/Kolkata", now_local=datetime(2030, 1, 1, tzinfo=UTC))
    assert _status(event, day) == "needs_confirmation"


def test_context_payload_sorts_unavailable_items():
    day = SimpleNamespace(
        unavailable_item_ids=[uuid4(), uuid4()],
        weather=None,
        air_quality=None,
    )
    payload = _context_payload(day)
    assert payload["weather"] is None
    assert payload["air_quality"] is None
    assert payload["unavailable_item_ids"] == sorted(payload["unavailable_item_ids"])


def test_mutation_contracts_forbid_account_injection_and_preserve_clear_values():
    assert EventReadyLookPatch(look_id=None).look_id is None
    assert EventReadyActionComplete(completed=False).completed is False
    for model, values in ((EventReadyLookPatch, {"account_id": "wrong"}), (EventReadyActionComplete, {"account_id": "wrong"})):
        try:
            model(**values)
        except Exception:
            pass
        else:
            raise AssertionError("Event Ready mutation accepted account_id")
