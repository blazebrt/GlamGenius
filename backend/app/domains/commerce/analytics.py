"""Commerce telemetry: one closed event over the existing ``app_events`` table.

``commerce.outbound_open`` records that a signed-in person opened a disclosed
outbound link from a Product Result. That is all GlamGenius can know: the
partner's page opened. Whether anything was bought, for how much, and what the
partner paid is not known here, and no field pretends otherwise.

* **One event name.** Anything else is refused.
* **Every property listed, required and an enum.** ``surface``, ``target``,
  ``decision``, ``partner`` (a registry key) and ``affiliate`` (a boolean).
  There is no string a client chooses, so there is nowhere to put a barcode,
  a product name or brand, an address, an affiliate URL, a search phrase, an
  account, device, household or subject id, an order, a basket, an amount, a
  commission or free text of any kind.
* **Consistent or refused.** ``current_product`` only with ``buy``;
  ``alternative`` only with ``wait`` or ``skip`` — the only combinations
  ``commerce-handoff-v1`` can produce.
* **Account-only.** An anonymous device still gets its link; its open is not
  recorded, and no device identifier is introduced to count it.
* **Idempotent.** ``client_event_id`` is a random operation UUID, the Step 15
  shape, so a retry is not counted twice.
* **Disposable.** The existing 90-day ``app_events`` retention applies and the
  rows leave with the account. They are exported under the existing generic
  ``ai_and_ops.app_events`` coverage, with ``client_event_id`` withheld.

This is a separate contract from Step 15's growth telemetry. The two share a
table and its retention, nothing else.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.analytics.models import AppEvent
from app.domains.commerce.partners import PARTNER_KEYS
from app.shared.database.base import new_uuid

EVENT_OUTBOUND_OPEN = "commerce.outbound_open"

#: name -> property -> the only values it may take. Every property is required.
EVENT_SCHEMAS: dict[str, dict[str, tuple[Any, ...]]] = {
    EVENT_OUTBOUND_OPEN: {
        "surface": ("product_result",),
        "target": ("current_product", "alternative"),
        "decision": ("buy", "wait", "skip"),
        "partner": PARTNER_KEYS,
        "affiliate": (True, False),
    },
}
EVENT_NAMES: tuple[str, ...] = tuple(EVENT_SCHEMAS)

#: (target, decision) pairs the handoff authority can produce.
_CONSISTENT: frozenset[tuple[str, str]] = frozenset({
    ("current_product", "buy"), ("alternative", "wait"), ("alternative", "skip"),
})


class InvalidCommerceEvent(ValueError):
    """The event is not one this module records. Carries a code, never input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _value_allowed(value: Any, allowed: tuple[Any, ...]) -> bool:
    # ``True == 1`` in Python: the type must match exactly as well as the value.
    return any(type(value) is type(option) and value == option for option in allowed)


def validate_event(name: Any, properties: Any) -> dict[str, Any]:
    """The canonical property bag for the whitelisted event, or a refusal."""
    if not isinstance(name, str) or name not in EVENT_SCHEMAS:
        raise InvalidCommerceEvent("event_not_allowed")
    if not isinstance(properties, dict):
        raise InvalidCommerceEvent("properties_invalid")
    schema = EVENT_SCHEMAS[name]
    if set(properties) != set(schema):
        raise InvalidCommerceEvent("properties_invalid")
    for key, allowed in schema.items():
        if not _value_allowed(properties[key], allowed):
            raise InvalidCommerceEvent("properties_invalid")
    if (properties["target"], properties["decision"]) not in _CONSISTENT:
        raise InvalidCommerceEvent("properties_inconsistent")
    return {key: properties[key] for key in schema}


async def record_event(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    name: str,
    client_event_id: uuid.UUID,
    properties: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Write one whitelisted event. ``False`` when it was a retry already held.

    The caller commits. Refuses with :class:`InvalidCommerceEvent` before any
    write if the event is not exactly one this module knows.
    """
    clean = validate_event(name, properties)
    if not isinstance(client_event_id, uuid.UUID):
        raise InvalidCommerceEvent("client_event_id_invalid")
    values: dict[str, Any] = {
        "id": new_uuid(),
        "account_id": account_id,
        "name": name,
        "properties": clean,
        "client_event_id": client_event_id,
    }
    if now is not None:
        values["created_at"] = now
        values["updated_at"] = now
    stmt = (
        pg_insert(AppEvent)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["account_id", "name", "client_event_id"],
            index_where=AppEvent.client_event_id.is_not(None),
        )
        .returning(AppEvent.id)
    )
    return (await session.execute(stmt)).first() is not None


__all__ = [
    "EVENT_NAMES",
    "EVENT_OUTBOUND_OPEN",
    "EVENT_SCHEMAS",
    "InvalidCommerceEvent",
    "record_event",
    "validate_event",
]
