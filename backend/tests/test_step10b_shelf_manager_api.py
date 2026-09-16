"""Step 10B: the manager against a real database, over the real routes.

The compiler is proved pure in ``test_step10b_shelf_manager.py``. What is proved
here is everything that only shows up once there is a database, a second
account, and a client that can send whatever it likes:

* the request carries four fields and the server derives the rest;
* a decision that has moved on cannot be acted on, and acting fails closed;
* an override costs nothing and is remembered;
* a product the manager paused comes back when it should, and only when the
  person has not already decided otherwise themselves;
* a navigation resolves nothing;
* the log is the account's own — exported with them, erased with them.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.domains.care.product_preferences import CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY
from app.domains.inventory.models import InventoryAttribute, InventoryEvent, InventoryItem
from app.domains.privacy import deletion_service
from app.domains.privacy.export import build_export
from app.domains.routines import manager
from app.domains.routines import rules as rules_engine
from app.domains.routines.models import ProductIngredient, RoutineAdherence, ShelfManagerDecisionEvent
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_v3_03_3_integration import _seed

pytestmark = pytest.mark.asyncio

TODAY = date.today()


# ---------------------------------------------------------------------------
# The deletion worker reaches Supabase Auth, which a test may not touch for
# real. Object storage does not need a double: ``media_root`` in conftest
# already points the storage factory at a temporary directory through the real
# local adapter, so the storage stage runs the code that ships.
# ---------------------------------------------------------------------------


class _FakeSupabaseAdmin:
    class _AuthAdmin:
        def __init__(self, outer: _FakeSupabaseAdmin) -> None:
            self._outer = outer

        def delete_user(self, user_id: str) -> None:
            self._outer.deleted_users.append(user_id)

    class _Auth:
        def __init__(self, outer: _FakeSupabaseAdmin) -> None:
            self.admin = _FakeSupabaseAdmin._AuthAdmin(outer)

    def __init__(self) -> None:
        self.deleted_users: list[str] = []
        self.auth = _FakeSupabaseAdmin._Auth(self)


@pytest.fixture
def fake_admin(monkeypatch):
    admin = _FakeSupabaseAdmin()
    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin", lambda: admin,
    )
    return admin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _add(
    client,
    token: str,
    *,
    name: str,
    category: str = "beauty",
    product_type: str = "moisturiser",
    expiry: date | None = None,
    actives: list[str] | None = None,
) -> str:
    """One product, added by hand. No scan, no barcode, no product link."""
    details: dict[str, object] = {"product_type": product_type}
    if expiry is not None:
        details["expiry_date"] = expiry.isoformat()
    if actives is not None:
        details["active_ingredients"] = actives
    response = await client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": category, "display_name": name,
            "subcategory": product_type, "details": details,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _manager(client, token: str) -> dict:
    response = await client.get("/api/v2/shelf/manager", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _respond(client, token: str, primary: dict, choice: str, key: str | None = None):
    return await client.post(
        "/api/v2/shelf/manager/respond",
        headers=auth(token),
        json={
            "decision_key": primary["decision_key"],
            "decision_fingerprint": primary["decision_fingerprint"],
            "choice": choice,
            "client_mutation_id": key or f"mut-{uuid.uuid4().hex[:12]}",
        },
    )


async def _events(account_id: uuid.UUID) -> list[ShelfManagerDecisionEvent]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(ShelfManagerDecisionEvent)
            .where(ShelfManagerDecisionEvent.account_id == account_id)
            .order_by(ShelfManagerDecisionEvent.created_at, ShelfManagerDecisionEvent.id)
        )).scalars().all())


async def _is_paused(item_id: str) -> bool:
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(InventoryAttribute).where(
            InventoryAttribute.item_id == uuid.UUID(item_id),
            InventoryAttribute.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
        ))).scalar_one_or_none()
    return row is not None and row.value is True


async def _set_expiry(client, token: str, item_id: str, value: date) -> None:
    """Correct a recorded date the way the person would, through the real route."""
    response = await client.patch(
        f"/api/v2/inventory/items/{item_id}",
        headers=auth(token),
        json={"details": {"expiry_date": value.isoformat()}},
    )
    assert response.status_code == 200, response.text


async def _expired_shelf(client, token: str) -> str:
    """One expired product plus enough around it that the shelf is realistic."""
    item_id = await _add(
        client, token, name="Expired Cleanser", product_type="cleanser",
        expiry=TODAY - timedelta(days=30),
    )
    await _add(
        client, token, name="Good Moisturiser", product_type="moisturiser",
        expiry=TODAY + timedelta(days=400),
    )
    await _add(
        client, token, name="Good Sunscreen", product_type="sunscreen",
        expiry=TODAY + timedelta(days=400),
    )
    # Something further down the queue, so "what comes next" is a real question.
    await _add(client, token, name="Undated Toner", product_type="toner")
    return item_id


# ---------------------------------------------------------------------------
# Reading the queue
# ---------------------------------------------------------------------------


async def test_a_new_account_gets_an_honest_empty_answer(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)

    payload = await _manager(app_client, token)

    assert payload["primary"] is None
    assert payload["remaining_count"] == 0
    assert payload["counts"] == {"active": 0, "overridden": 0, "give_back": 0}
    assert payload["message"] == manager.NO_PRODUCTS_MESSAGE
    assert payload["contract_version"] == "step-10b-v1"
    assert payload["disclaimer"]


async def test_the_manager_reads_products_added_by_hand_with_no_scan_behind_them(
    app_client, db_clean, registered_supabase_user,
):
    """Nothing here depends on a barcode, a pack or a product link."""
    from app.domains.inventory.models import InventoryProductLink

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)

    payload = await _manager(app_client, token)

    async with get_sessionmaker()() as session:
        links = await session.scalar(select(func.count(InventoryProductLink.id)))
        item = await session.get(InventoryItem, uuid.UUID(item_id))
    assert links == 0
    assert item.source == "user_declared"
    assert payload["primary"]["item_ids"] == [item_id]
    assert payload["primary"]["rule_id"] == rules_engine.RULE_EXPIRED


async def test_the_queue_requires_a_signed_in_account(app_client, db_clean):
    response = await app_client.get("/api/v2/shelf/manager")
    assert response.status_code in (401, 403)

    response = await app_client.post("/api/v2/shelf/manager/respond", json={
        "decision_key": "rule.product_expired:item:x",
        "decision_fingerprint": "0" * 64,
        "choice": "accept",
        "client_mutation_id": "mut-abcdef",
    })
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# What a client is allowed to send
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forged", [
    {"account_id": "00000000-0000-0000-0000-000000000001"},
    {"inventory_item_id": "00000000-0000-0000-0000-000000000002"},
    {"target_inventory_item_id": "00000000-0000-0000-0000-000000000002"},
    {"rule_id": "rule.product_expired"},
    {"action_kind": "pause_product"},
    {"action": {"kind": "pause_product"}},
    {"severity": "avoid"},
    {"reason": "because I said so"},
    {"item_ids": ["00000000-0000-0000-0000-000000000002"]},
])
async def test_the_request_has_no_field_worth_forging(
    app_client, db_clean, registered_supabase_user, forged,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    response = await app_client.post(
        "/api/v2/shelf/manager/respond",
        headers=auth(token),
        json={
            "decision_key": primary["decision_key"],
            "decision_fingerprint": primary["decision_fingerprint"],
            "choice": "accept",
            "client_mutation_id": "mut-forged-01",
            **forged,
        },
    )

    assert response.status_code == 422, response.text
    assert await _events(account_id) == []
    assert await _is_paused(item_id) is False


async def test_the_choice_is_only_ever_accept_or_override(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    for value in ("accepted", "restored", "restore_overridden", "anything"):
        response = await app_client.post(
            "/api/v2/shelf/manager/respond",
            headers=auth(token),
            json={
                "decision_key": primary["decision_key"],
                "decision_fingerprint": primary["decision_fingerprint"],
                "choice": value,
                "client_mutation_id": f"mut-choice-{value}",
            },
        )
        assert response.status_code == 422, value
    assert await _events(account_id) == []


# ---------------------------------------------------------------------------
# Accepting a decision
# ---------------------------------------------------------------------------


async def test_accepting_a_pause_pauses_the_product_through_the_care_authority(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    assert primary["action"]["kind"] == manager.ACTION_PAUSE_PRODUCT

    response = await _respond(app_client, token, primary, "accept", key="mut-accept-01")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == {
        "choice": "accepted", "action_kind": "pause_product",
        "action_applied": True, "replayed": False,
    }
    assert await _is_paused(item_id) is True

    events = await _events(account_id)
    assert len(events) == 1
    assert events[0].choice == "accepted"
    assert events[0].action_kind == "pause_product"
    assert str(events[0].target_inventory_item_id) == item_id
    assert events[0].decision_key == primary["decision_key"]

    # The canonical Care path ran, so its own inventory event exists too.
    async with get_sessionmaker()() as session:
        kinds = (await session.execute(select(InventoryEvent.event_type).where(
            InventoryEvent.item_id == uuid.UUID(item_id),
        ))).scalars().all()
    assert "care_routine_paused" in kinds

    # And the decision is settled: the same thing is not asked again.
    after = await _manager(app_client, token)
    assert after["primary"] is None or after["primary"]["decision_key"] != primary["decision_key"]


async def test_a_retry_with_the_same_key_applies_nothing_twice(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    first = await _respond(app_client, token, primary, "accept", key="mut-replay-01")
    second = await _respond(app_client, token, primary, "accept", key="mut-replay-01")

    assert first.status_code == 200
    assert second.status_code == 200, second.text
    assert second.json()["applied"] == {
        "choice": "accepted", "action_kind": "pause_product",
        "action_applied": False, "replayed": True,
    }
    assert len(await _events(account_id)) == 1
    assert await _is_paused(item_id) is True

    async with get_sessionmaker()() as session:
        pauses = await session.scalar(select(func.count(InventoryEvent.id)).where(
            InventoryEvent.item_id == uuid.UUID(item_id),
            InventoryEvent.event_type == "care_routine_paused",
        ))
    assert pauses == 1


async def test_the_same_key_used_for_a_different_answer_is_refused(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    assert (await _respond(app_client, token, primary, "accept", key="mut-clash")).status_code == 200
    clash = await _respond(app_client, token, primary, "override", key="mut-clash")

    assert clash.status_code == 422, clash.text
    assert clash.json()["detail"]["field"] == "client_mutation_id"
    assert len(await _events(account_id)) == 1


async def test_the_same_key_used_for_a_different_decision_is_refused(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    payload = await _manager(app_client, token)
    primary = payload["primary"]

    assert (await _respond(app_client, token, primary, "override", key="mut-shared")).status_code == 200
    following = (await _manager(app_client, token))["primary"]
    assert following["decision_key"] != primary["decision_key"]

    clash = await _respond(app_client, token, following, "accept", key="mut-shared")
    assert clash.status_code == 422
    assert clash.json()["detail"]["field"] == "client_mutation_id"
    assert len(await _events(account_id)) == 1


# ---------------------------------------------------------------------------
# Stale requests fail closed
# ---------------------------------------------------------------------------


async def test_a_forged_fingerprint_changes_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = dict((await _manager(app_client, token))["primary"])
    primary["decision_fingerprint"] = "f" * 64

    response = await _respond(app_client, token, primary, "accept")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["field"] == "decision_fingerprint"
    assert await _is_paused(item_id) is False
    assert await _events(account_id) == []


async def test_a_decision_key_the_server_would_not_produce_changes_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = dict((await _manager(app_client, token))["primary"])
    primary["decision_key"] = f"{rules_engine.RULE_ALLERGY}:item:{item_id}"

    response = await _respond(app_client, token, primary, "accept")

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "decision_key"
    assert await _is_paused(item_id) is False
    assert await _events(account_id) == []


async def test_a_product_deleted_between_reading_and_answering_changes_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    deleted = await app_client.delete(f"/api/v2/inventory/items/{item_id}", headers=auth(token))
    assert deleted.status_code in (200, 204), deleted.text

    response = await _respond(app_client, token, primary, "accept")

    assert response.status_code == 422, response.text
    assert await _events(account_id) == []


async def test_correcting_the_expiry_between_reading_and_answering_changes_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    await _set_expiry(app_client, token, item_id, TODAY - timedelta(days=200))

    response = await _respond(app_client, token, primary, "accept")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["field"] == "decision_fingerprint"
    assert await _is_paused(item_id) is False
    assert await _events(account_id) == []


async def test_confirming_an_ingredient_between_reading_and_answering_changes_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(
        app_client, token, name="Mystery Cleanser", product_type="cleanser",
        expiry=TODAY + timedelta(days=400),
    )
    await _add(
        app_client, token, name="A Moisturiser", product_type="moisturiser",
        expiry=TODAY + timedelta(days=400),
    )
    await _add(
        app_client, token, name="A Sunscreen", product_type="sunscreen",
        expiry=TODAY + timedelta(days=400),
    )
    async with get_sessionmaker()() as session:
        session.add(ProductIngredient(
            account_id=account_id, item_id=uuid.UUID(item_id), ingredient_key="fragrance",
            matched_text="parfum", confidence=0.4, source="photo_extracted",
            needs_confirmation=True, confirmed_at=None,
        ))
        await session.commit()

    payload = await _manager(app_client, token)
    unconfirmed = next(
        row for row in [payload["primary"]] if row["rule_id"] == rules_engine.RULE_UNCONFIRMED
    )

    async with get_sessionmaker()() as session:
        row = (await session.execute(select(ProductIngredient).where(
            ProductIngredient.item_id == uuid.UUID(item_id),
        ))).scalar_one()
        row.confidence = 1.0
        row.source = "user_declared"
        row.needs_confirmation = False
        row.confirmed_at = utcnow()
        await session.commit()

    response = await _respond(app_client, token, unconfirmed, "accept")

    assert response.status_code == 422, response.text
    assert await _events(account_id) == []


async def test_pausing_a_product_yourself_first_makes_the_decision_stale(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]

    paused = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert paused.status_code == 200, paused.text

    response = await _respond(app_client, token, primary, "accept")

    assert response.status_code == 422, response.text
    assert await _events(account_id) == []


async def test_one_account_cannot_answer_another_accounts_decision(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _expired_shelf(app_client, token_a)
    primary_a = (await _manager(app_client, token_a))["primary"]

    response = await _respond(app_client, token_b, primary_a, "accept")

    assert response.status_code == 422, response.text
    assert await _is_paused(item_a) is False
    assert await _events(account_a) == []
    assert await _events(account_b) == []


async def test_an_override_by_one_person_never_quietens_another_persons_decision(
    app_client, db_clean, registered_supabase_user,
):
    """Some decision keys are identical across accounts, by design.

    "No moisturiser recorded" for Skin Care is the same key for everybody who
    is missing one — it names a routine step, not a product. If the stored
    answers were ever read without the account filter, one person saying "not
    now" would silence it for everyone else who had it.
    """
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    await _seed(app_client)
    for token in (token_a, token_b):
        await _add(app_client, token, name="A Cleanser", product_type="cleanser",
                   expiry=TODAY + timedelta(days=400))

    shared_key = f"{rules_engine.RULE_MISSING_SLOT}:beauty:moisturiser"
    before_a = next(
        row for row in [(await _manager(app_client, token_a))["primary"]]
        if row["decision_key"] == shared_key
    )
    before_b = (await _manager(app_client, token_b))["primary"]
    assert before_b["decision_key"] == shared_key
    assert before_a["decision_fingerprint"] == before_b["decision_fingerprint"]

    assert (await _respond(
        app_client, token_a, before_a, "override", key="mut-shared-key-a",
    )).status_code == 200

    after_a = await _manager(app_client, token_a)
    after_b = await _manager(app_client, token_b)

    assert after_a["primary"]["decision_key"] != shared_key
    assert after_a["counts"]["overridden"] == 1
    assert after_b["primary"]["decision_key"] == shared_key
    assert after_b["counts"]["overridden"] == 0


async def test_one_accounts_shelf_is_invisible_to_another(
    app_client, db_clean, registered_supabase_user,
):
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _expired_shelf(app_client, token_a)

    payload_b = await _manager(app_client, token_b)

    assert payload_b["primary"] is None
    assert item_a not in str(payload_b)


# ---------------------------------------------------------------------------
# Not now
# ---------------------------------------------------------------------------


async def test_saying_not_now_is_recorded_costs_nothing_and_is_remembered(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    before = await _manager(app_client, token)
    primary = before["primary"]

    response = await _respond(app_client, token, primary, "override", key="mut-notnow-01")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == {
        "choice": "overridden", "action_kind": "pause_product",
        "action_applied": False, "replayed": False,
    }
    assert await _is_paused(item_id) is False

    events = await _events(account_id)
    assert [row.choice for row in events] == ["overridden"]

    after = await _manager(app_client, token)
    assert after["counts"]["overridden"] == 1
    assert after["counts"]["active"] == before["counts"]["active"] - 1
    assert after["primary"] is None or after["primary"]["decision_key"] != primary["decision_key"]

    # Nothing was marked missed, and no consistency record was written.
    async with get_sessionmaker()() as session:
        adherence = await session.scalar(select(func.count(RoutineAdherence.id)).where(
            RoutineAdherence.account_id == account_id,
        ))
    assert adherence == 0


async def test_the_next_thing_moves_to_the_front_after_an_override(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    before = await _manager(app_client, token)
    assert before["remaining_count"] >= 1

    await _respond(app_client, token, before["primary"], "override")
    after = await _manager(app_client, token)

    assert after["primary"] is not None
    assert after["primary"]["decision_key"] != before["primary"]["decision_key"]
    assert after["remaining_count"] == before["remaining_count"] - 1


async def test_a_decision_asks_again_once_its_inputs_change(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    await _respond(app_client, token, primary, "override")
    assert primary["decision_key"] not in str(await _manager(app_client, token))

    await _set_expiry(app_client, token, item_id, TODAY - timedelta(days=300))

    revived = await _manager(app_client, token)
    assert revived["primary"]["decision_key"] == primary["decision_key"]
    assert revived["primary"]["decision_fingerprint"] != primary["decision_fingerprint"]


# ---------------------------------------------------------------------------
# A navigation resolves nothing
# ---------------------------------------------------------------------------


async def test_accepting_a_navigation_records_the_tap_and_settles_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Undated Cleanser", product_type="cleanser")
    await _add(app_client, token, name="Dated Moisturiser", product_type="moisturiser",
               expiry=TODAY + timedelta(days=400))
    await _add(app_client, token, name="Dated Sunscreen", product_type="sunscreen",
               expiry=TODAY + timedelta(days=400))
    primary = (await _manager(app_client, token))["primary"]
    assert primary["rule_id"] == rules_engine.RULE_NO_EXPIRY
    assert primary["action"]["kind"] == manager.ACTION_RECORD_DATE
    assert primary["action"]["mutates"] is False

    response = await _respond(app_client, token, primary, "accept", key="mut-route-01")

    assert response.status_code == 200, response.text
    assert response.json()["applied"]["action_applied"] is False
    assert [row.choice for row in await _events(account_id)] == ["accepted"]

    # Opening a screen is not recording a date. The decision is still there.
    after = await _manager(app_client, token)
    assert after["primary"]["decision_key"] == primary["decision_key"]
    assert await _is_paused(item_id) is False


async def test_recording_the_date_is_what_actually_settles_it(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Undated Cleanser", product_type="cleanser")
    await _add(app_client, token, name="Dated Moisturiser", product_type="moisturiser",
               expiry=TODAY + timedelta(days=400))
    await _add(app_client, token, name="Dated Sunscreen", product_type="sunscreen",
               expiry=TODAY + timedelta(days=400))
    primary = (await _manager(app_client, token))["primary"]
    await _respond(app_client, token, primary, "accept")

    await _set_expiry(app_client, token, item_id, TODAY + timedelta(days=400))

    after = await _manager(app_client, token)
    assert primary["decision_key"] not in str(after)


# ---------------------------------------------------------------------------
# Giving it back
# ---------------------------------------------------------------------------


async def _pause_through_the_manager(app_client, token: str) -> tuple[str, dict]:
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    assert primary["action"]["kind"] == manager.ACTION_PAUSE_PRODUCT
    response = await _respond(app_client, token, primary, "accept", key=f"mut-{uuid.uuid4().hex[:10]}")
    assert response.status_code == 200, response.text
    return item_id, primary


async def test_a_product_the_manager_paused_is_offered_back_when_the_reason_goes(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id, paused_decision = await _pause_through_the_manager(app_client, token)
    assert await _is_paused(item_id) is True

    # The date was wrong. Corrected, nothing is expired any more.
    await _set_expiry(app_client, token, item_id, TODAY + timedelta(days=400))

    payload = await _manager(app_client, token)
    primary = payload["primary"]

    assert primary["kind"] == manager.KIND_GIVE_BACK
    assert primary["decision"] == manager.DECISION_BRING_IT_BACK
    assert primary["reason"] == "Expired Cleanser can return."
    assert primary["action"]["kind"] == manager.ACTION_RESUME_PRODUCT
    assert primary["action"]["inventory_item_id"] == item_id
    assert primary["override"]["label"] == manager.OVERRIDE_KEEP_PAUSED
    assert primary["rule_id"] == paused_decision["rule_id"]
    assert payload["counts"]["give_back"] == 1


async def test_bringing_it_back_resumes_it_and_the_offer_goes_away(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id, _ = await _pause_through_the_manager(app_client, token)
    await _set_expiry(app_client, token, item_id, TODAY + timedelta(days=400))
    offer = (await _manager(app_client, token))["primary"]

    response = await _respond(app_client, token, offer, "accept", key="mut-giveback-01")

    assert response.status_code == 200, response.text
    assert response.json()["applied"] == {
        "choice": "restored", "action_kind": "resume_product",
        "action_applied": True, "replayed": False,
    }
    assert await _is_paused(item_id) is False
    assert [row.choice for row in await _events(account_id)] == ["accepted", "restored"]

    after = await _manager(app_client, token)
    assert after["counts"]["give_back"] == 0


async def test_keeping_it_paused_is_recorded_and_not_asked_again(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id, _ = await _pause_through_the_manager(app_client, token)
    await _set_expiry(app_client, token, item_id, TODAY + timedelta(days=400))
    offer = (await _manager(app_client, token))["primary"]

    response = await _respond(app_client, token, offer, "override", key="mut-keeppaused-01")

    assert response.status_code == 200, response.text
    assert response.json()["applied"]["choice"] == "restore_overridden"
    assert await _is_paused(item_id) is True
    assert [row.choice for row in await _events(account_id)] == ["accepted", "restore_overridden"]

    after = await _manager(app_client, token)
    assert after["counts"]["give_back"] == 0
    assert after["counts"]["overridden"] >= 1


async def test_nothing_is_offered_back_while_it_is_still_expired(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id, _ = await _pause_through_the_manager(app_client, token)

    payload = await _manager(app_client, token)

    assert payload["counts"]["give_back"] == 0
    assert await _is_paused(item_id) is True


async def test_resuming_it_yourself_leaves_nothing_to_give_back(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id, _ = await _pause_through_the_manager(app_client, token)
    await _set_expiry(app_client, token, item_id, TODAY + timedelta(days=400))

    resumed = await app_client.post(
        f"/api/v2/routines/products/{item_id}/resume", headers=auth(token),
    )
    assert resumed.status_code == 200, resumed.text

    payload = await _manager(app_client, token)
    assert payload["counts"]["give_back"] == 0


async def test_pausing_it_again_yourself_is_your_choice_not_ours_to_undo(
    app_client, db_clean, registered_supabase_user,
):
    """The manager must not offer to undo something the person did afterwards."""
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    item_id, _ = await _pause_through_the_manager(app_client, token)
    await _set_expiry(app_client, token, item_id, TODAY + timedelta(days=400))

    # They resume it, decide they preferred it paused, and pause it themselves.
    assert (await app_client.post(
        f"/api/v2/routines/products/{item_id}/resume", headers=auth(token),
    )).status_code == 200
    assert (await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )).status_code == 200

    payload = await _manager(app_client, token)

    assert payload["counts"]["give_back"] == 0
    assert await _is_paused(item_id) is True


async def test_a_give_back_offer_is_not_visible_to_another_account(
    app_client, db_clean, registered_supabase_user,
):
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    await _seed(app_client)
    item_a, _ = await _pause_through_the_manager(app_client, token_a)
    await _set_expiry(app_client, token_a, item_a, TODAY + timedelta(days=400))
    offer = (await _manager(app_client, token_a))["primary"]
    assert offer["kind"] == manager.KIND_GIVE_BACK

    assert (await _manager(app_client, token_b))["counts"]["give_back"] == 0
    response = await _respond(app_client, token_b, offer, "accept")
    assert response.status_code == 422
    assert await _is_paused(item_a) is True


async def test_the_give_back_log_is_read_per_account_even_when_asked_wrongly(
    app_client, db_clean, registered_supabase_user,
):
    """Defence in depth, asserted directly.

    In normal use an inventory item belongs to exactly one account, so one
    person's log could never name another's product. The account filter on the
    log is the second lock, and a second lock is only worth having if somebody
    checks it is closed.
    """
    from app.domains.routines import shelf

    token_a, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    await _seed(app_client)
    item_id, _ = await _pause_through_the_manager(app_client, token_a)
    await _set_expiry(app_client, token_a, item_id, TODAY + timedelta(days=400))

    async with get_sessionmaker()() as session:
        context = await shelf.gather(session, account_id=account_a)
        products = {
            product.id: product
            for category in manager.MANAGER_CATEGORIES
            for product in shelf.build(context, category)
        }
        paused = frozenset({uuid.UUID(item_id)})
        mine = await manager.give_back_candidates(
            session, account_id=account_a, products=products, paused_item_ids=paused,
        )
        theirs = await manager.give_back_candidates(
            session, account_id=account_b, products=products, paused_item_ids=paused,
        )

    assert [str(row.item_id) for row in mine] == [item_id]
    assert theirs == []


# ---------------------------------------------------------------------------
# The mutation table
# ---------------------------------------------------------------------------


def test_every_state_changing_action_is_wired_to_the_care_authority_that_owns_it():
    """No action that changes state may be applied by anything but Care."""
    from app.domains.routines import service as routines_service

    assert set(routines_service._MANAGER_MUTATIONS) == manager.MUTATING_ACTION_KINDS
    assert {
        "pause_product": routines_service.pause_care_product,
        "resume_product": routines_service.resume_care_product,
        "prefer_product": routines_service.prefer_care_product,
        "unprefer_product": routines_service.unprefer_care_product,
    } == routines_service._MANAGER_MUTATIONS


def test_the_request_and_the_column_agree_on_how_long_a_decision_key_can_be():
    from app.domains.routines.schemas import ShelfManagerRespondRequest

    field = ShelfManagerRespondRequest.model_fields["decision_key"]
    limit = next(row.max_length for row in field.metadata if hasattr(row, "max_length"))
    column = ShelfManagerDecisionEvent.__table__.columns["decision_key"].type.length

    assert limit == manager.DECISION_KEY_MAX_LENGTH == column


async def test_an_action_the_care_authority_refuses_records_nothing(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """The answer and the change land together or not at all."""
    from app.domains.routines import service as routines_service
    from app.shared.errors.exceptions import ValidationFailedError

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    assert primary["action"]["kind"] == manager.ACTION_PAUSE_PRODUCT

    async def _refuse(*args, **kwargs):
        raise ValidationFailedError("Not right now.", field="item_id")

    monkeypatch.setitem(
        routines_service._MANAGER_MUTATIONS, manager.ACTION_PAUSE_PRODUCT, _refuse,
    )

    response = await _respond(app_client, token, primary, "accept", key="mut-refused-01")

    assert response.status_code == 422, response.text
    assert await _events(account_id) == []
    assert await _is_paused(item_id) is False


# ---------------------------------------------------------------------------
# It is their data
# ---------------------------------------------------------------------------


async def test_the_log_is_exported_with_the_account_that_owns_it(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token_a)
    await _expired_shelf(app_client, token_b)
    primary_a = (await _manager(app_client, token_a))["primary"]
    await _respond(app_client, token_a, primary_a, "accept", key="mut-export-a")

    async with get_sessionmaker()() as session:
        export_a = await build_export(session, account_a)
        export_b = await build_export(session, account_b)

    rows_a = export_a["domains"]["routines"]["shelf_manager_decision_events"]
    rows_b = export_b["domains"]["routines"]["shelf_manager_decision_events"]
    assert len(rows_a) == 1
    assert rows_a[0]["decision_key"] == primary_a["decision_key"]
    assert rows_a[0]["choice"] == "accepted"
    assert rows_b == []


async def test_the_log_holds_identifiers_and_state_and_nothing_written(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    await _respond(app_client, token, primary, "accept", key="mut-shape-01")

    columns = {column.name for column in ShelfManagerDecisionEvent.__table__.columns}
    assert columns == {
        "id", "created_at", "updated_at", "account_id", "decision_key",
        "decision_fingerprint", "choice", "action_kind",
        "target_inventory_item_id", "client_mutation_id",
    }
    # No note, no payload, no reason, nothing a person or a label wrote.
    assert not columns & {"note", "payload", "reason", "detail", "text", "headline"}

    event = (await _events(account_id))[0]
    assert event.decision_fingerprint == primary["decision_fingerprint"]


async def test_deleting_the_account_takes_its_log_and_leaves_everyone_elses(
    app_client, db_clean, registered_supabase_user, fake_admin, media_root,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token_a)
    await _expired_shelf(app_client, token_b)
    for token in (token_a, token_b):
        primary = (await _manager(app_client, token))["primary"]
        await _respond(app_client, token, primary, "accept", key=f"mut-del-{uuid.uuid4().hex[:8]}")
    assert len(await _events(account_a)) == 1
    assert len(await _events(account_b)) == 1

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.request_deletion(session, account_a)
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        surviving = (await session.execute(select(ShelfManagerDecisionEvent))).scalars().all()
    assert [row.account_id for row in surviving] == [account_b]
    # And nothing is left pointing at an account that no longer exists.
    assert all(row.account_id is not None for row in surviving)


async def test_deleting_the_product_takes_the_row_that_named_it(
    app_client, db_clean, registered_supabase_user,
):
    """Proved behaviourally: autogenerate does not compare delete rules."""
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    await _respond(app_client, token, primary, "accept", key="mut-cascade-01")
    assert len(await _events(account_id)) == 1

    async with get_sessionmaker()() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        await session.delete(item)
        await session.commit()

    assert await _events(account_id) == []


async def test_the_step_10a_deletion_lesson_is_not_regressed(
    app_client, db_clean, registered_supabase_user, fake_admin, media_root,
):
    """Adding this table must not change how a scan's provenance is erased."""
    from app.domains.product.models import LabelSnapshot, ScanEvent

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    primary = (await _manager(app_client, token))["primary"]
    await _respond(app_client, token, primary, "accept", key="mut-10a-01")

    async with get_sessionmaker()() as session:
        before_snapshots = await session.scalar(select(func.count(LabelSnapshot.id)))
        before_scans = await session.scalar(select(func.count(ScanEvent.id)))

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        assert await session.scalar(select(func.count(LabelSnapshot.id))) == before_snapshots
        assert await session.scalar(select(func.count(ScanEvent.id))) == before_scans
        orphans = await session.scalar(select(func.count(ScanEvent.id)).where(
            ScanEvent.account_id == account_id,
        ))
    assert orphans == 0
    assert await _events(account_id) == []


# ---------------------------------------------------------------------------
# The rest of the shelf is untouched
# ---------------------------------------------------------------------------


async def test_the_existing_shelf_report_is_unchanged_by_the_manager(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)

    before = await app_client.get("/api/v2/shelf/summary", headers=auth(token))
    primary = (await _manager(app_client, token))["primary"]
    await _respond(app_client, token, primary, "override", key="mut-untouched-01")
    after = await app_client.get("/api/v2/shelf/summary", headers=auth(token))

    assert before.status_code == 200 and after.status_code == 200
    # Saying "not now" changes nothing about what the shelf reports.
    assert before.json() == after.json()


async def test_drafts_are_still_counted_and_still_not_decided_about(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)

    async with get_sessionmaker()() as session:
        draft = InventoryItem(
            account_id=(await session.execute(select(InventoryItem.account_id).limit(1))).scalar_one(),
            category="beauty", display_name="Unconfirmed Serum", subcategory="treatment",
            source="photo_extracted", verification_state="draft", confidence=0.4,
            currency="INR", condition="good",
        )
        session.add(draft)
        await session.commit()
        draft_id = str(draft.id)

    summary = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()
    payload = await _manager(app_client, token)

    assert summary["counts"]["drafts"] == 1
    assert draft_id not in str(payload)
