"""Step 10B: what happens when two answers arrive at once.

The rest of the manager suite proves the sequential contract — a retry replays,
a reused key with a different answer conflicts. Neither of those touches the
path the implementation actually depends on, which is PostgreSQL deciding who
goes first. This file races real requests against a real database.

Three races, and they are not the same race:

* **the same submission key, the same answer** — a retry that overlapped the
  original. It must cost nothing: one event, one change, and the loser told it
  was a replay;
* **the same submission key, opposite answers** — a mistake on the client's
  part. One wins, the other is refused, and the product's state must match
  whichever answer was actually recorded;
* **two different submission keys, the same decision** — a second device, or an
  app that decided a retry deserved a new key. Key uniqueness cannot help here
  because the keys differ, and both requests compile the same decision before
  either has changed anything. Without a lock on the product both would apply
  the same Care change and the second would hit the unique constraint on the
  preference attribute: a 500 on a button somebody pressed twice.

The overlap is arranged with a barrier at the point both requests compile the
queue, not with sleeps. Both are released together and then race for real.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from app.domains.care.product_preferences import CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY
from app.domains.inventory.models import InventoryAttribute, InventoryEvent
from app.domains.routines import manager
from app.domains.routines import rules as rules_engine
from app.domains.routines.models import ShelfManagerDecisionEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_step10b_shelf_manager_api import _add, _manager
from tests.test_v3_03_3_integration import _seed

pytestmark = pytest.mark.asyncio

TODAY = date.today()
RESPOND = "/api/v2/shelf/manager/respond"


def _release_both_at_the_first_compile(monkeypatch, barrier: asyncio.Barrier) -> None:
    """Hold each racing request at its first queue compile, then release both.

    Every answer compiles the queue before it does anything else, whether or
    not it goes on to take a row lock, so this is the one point both racers
    reach. Waiting only on each task's *first* compile leaves the recompiles
    that happen later — under the lock, and for the response — running at full
    speed, which is where the contention we are trying to observe lives.
    """
    original = manager.build_queue
    gated: set[object] = set()

    async def _build_queue(session, *, account_id, today=None):
        task = asyncio.current_task()
        # Exactly the two racers, and only their first compile. Anything the
        # test does afterwards runs normally rather than waiting for a partner
        # that is never coming.
        if len(gated) < 2 and task not in gated:
            gated.add(task)
            await asyncio.wait_for(barrier.wait(), timeout=60)
        return await original(session, account_id=account_id, today=today)

    monkeypatch.setattr(manager, "build_queue", _build_queue)


async def _pausing_primary(client, token: str) -> dict:
    """A shelf whose front decision changes stored state."""
    item_id = await _add(
        client, token, name="Expired Cleanser", product_type="cleanser",
        expiry=TODAY - timedelta(days=30),
    )
    await _add(client, token, name="Good Moisturiser", product_type="moisturiser",
               expiry=TODAY + timedelta(days=400))
    await _add(client, token, name="Good Sunscreen", product_type="sunscreen",
               expiry=TODAY + timedelta(days=400))
    await _add(client, token, name="Undated Toner", product_type="toner")

    primary = (await _manager(client, token))["primary"]
    assert primary["rule_id"] == rules_engine.RULE_EXPIRED
    assert primary["action"]["kind"] == manager.ACTION_PAUSE_PRODUCT
    assert primary["action"]["inventory_item_id"] == item_id
    return primary


def _body(primary: dict, *, choice: str, key: str) -> dict:
    return {
        "decision_key": primary["decision_key"],
        "decision_fingerprint": primary["decision_fingerprint"],
        "choice": choice,
        "client_mutation_id": key,
    }


async def _state(account_id: uuid.UUID, item_id: str) -> dict:
    """Everything one pause should have produced, counted."""
    async with get_sessionmaker()() as session:
        events = (await session.execute(
            select(ShelfManagerDecisionEvent).where(
                ShelfManagerDecisionEvent.account_id == account_id,
            )
        )).scalars().all()
        attributes = await session.scalar(select(func.count(InventoryAttribute.id)).where(
            InventoryAttribute.item_id == uuid.UUID(item_id),
            InventoryAttribute.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
        ))
        pauses = await session.scalar(select(func.count(InventoryEvent.id)).where(
            InventoryEvent.item_id == uuid.UUID(item_id),
            InventoryEvent.event_type == "care_routine_paused",
        ))
    return {
        "events": events,
        "choices": [row.choice for row in events],
        "paused_attributes": attributes,
        "pause_events": pauses,
    }


def _no_server_errors(*responses) -> None:
    for response in responses:
        assert response.status_code != 500, response.text
        assert "IntegrityError" not in response.text
        assert "UniqueViolation" not in response.text


# ---------------------------------------------------------------------------
# The same key, the same answer
# ---------------------------------------------------------------------------


async def test_two_identical_answers_racing_apply_the_change_exactly_once(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    primary = await _pausing_primary(app_client, token)
    item_id = primary["action"]["inventory_item_id"]
    body = _body(primary, choice="accept", key="mut-race-identical")

    _release_both_at_the_first_compile(monkeypatch, asyncio.Barrier(2))
    first, second = await asyncio.gather(
        app_client.post(RESPOND, headers=auth(token), json=body),
        app_client.post(RESPOND, headers=auth(token), json=body),
    )

    _no_server_errors(first, second)
    assert sorted([first.status_code, second.status_code]) == [200, 200]
    # One of them applied it; the other was told it was a replay.
    applied = [response.json()["applied"] for response in (first, second)]
    assert sorted(row["replayed"] for row in applied) == [False, True]
    assert sorted(row["action_applied"] for row in applied) == [False, True]
    assert all(row["choice"] == "accepted" for row in applied)

    state = await _state(account_id, item_id)
    assert state["choices"] == ["accepted"]
    assert state["paused_attributes"] == 1
    assert state["pause_events"] == 1


# ---------------------------------------------------------------------------
# The same key, opposite answers
# ---------------------------------------------------------------------------


async def test_opposite_answers_under_one_key_leave_the_product_matching_the_winner(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """Whichever answer is recorded is the one the product must reflect.

    An event saying "overridden" beside a paused product, or "accepted" beside
    one that is not, would be a log that lies about somebody's own shelf.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    primary = await _pausing_primary(app_client, token)
    item_id = primary["action"]["inventory_item_id"]
    key = "mut-race-opposite"

    _release_both_at_the_first_compile(monkeypatch, asyncio.Barrier(2))
    accepted, overridden = await asyncio.gather(
        app_client.post(RESPOND, headers=auth(token), json=_body(primary, choice="accept", key=key)),
        app_client.post(RESPOND, headers=auth(token), json=_body(primary, choice="override", key=key)),
    )

    _no_server_errors(accepted, overridden)
    assert sorted([accepted.status_code, overridden.status_code]) == [200, 422]
    refused = accepted if accepted.status_code == 422 else overridden
    assert refused.json()["detail"]["field"] == "client_mutation_id"

    state = await _state(account_id, item_id)
    assert len(state["events"]) == 1
    winner = state["choices"][0]
    assert winner in ("accepted", "overridden")
    if winner == "accepted":
        assert state["paused_attributes"] == 1
        assert state["pause_events"] == 1
    else:
        assert state["paused_attributes"] == 0
        assert state["pause_events"] == 0


# ---------------------------------------------------------------------------
# Two different keys, one decision
# ---------------------------------------------------------------------------


async def test_two_different_keys_answering_one_decision_change_it_once(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """The race key uniqueness cannot see.

    Both requests are well formed, both name the current primary, and their
    submission keys genuinely differ — a second device, or an app that treated
    a retry as a new attempt. Only a lock on the product makes the second one
    wait long enough to find out the decision has already been settled.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    primary = await _pausing_primary(app_client, token)
    item_id = primary["action"]["inventory_item_id"]

    _release_both_at_the_first_compile(monkeypatch, asyncio.Barrier(2))
    first, second = await asyncio.gather(
        app_client.post(RESPOND, headers=auth(token), json=_body(primary, choice="accept", key="mut-race-k1")),
        app_client.post(RESPOND, headers=auth(token), json=_body(primary, choice="accept", key="mut-race-k2")),
    )

    _no_server_errors(first, second)
    assert sorted([first.status_code, second.status_code]) == [200, 422]
    loser = first if first.status_code == 422 else second
    # Not a malformed request: the decision it named is simply no longer current.
    assert loser.json()["detail"]["field"] in ("decision_key", "decision_fingerprint")

    state = await _state(account_id, item_id)
    assert state["choices"] == ["accepted"]
    assert state["paused_attributes"] == 1
    assert state["pause_events"] == 1

    # And the shelf is coherent afterwards: nothing is half-applied.
    after = await _manager(app_client, token)
    assert after["primary"] is None or after["primary"]["decision_key"] != primary["decision_key"]


async def test_a_racing_loser_can_still_answer_whatever_is_in_front_afterwards(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """Losing a race must not strand the person on a dead submission key."""
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    primary = await _pausing_primary(app_client, token)

    _release_both_at_the_first_compile(monkeypatch, asyncio.Barrier(2))
    await asyncio.gather(
        app_client.post(RESPOND, headers=auth(token), json=_body(primary, choice="accept", key="mut-after-k1")),
        app_client.post(RESPOND, headers=auth(token), json=_body(primary, choice="accept", key="mut-after-k2")),
    )
    # No need to undo the gate: it admits two tasks and then stands aside.

    following = (await _manager(app_client, token))["primary"]
    assert following is not None
    assert following["decision_key"] != primary["decision_key"]

    response = await app_client.post(
        RESPOND, headers=auth(token),
        json=_body(following, choice="override", key="mut-after-next"),
    )

    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as session:
        recorded = (await session.execute(
            select(ShelfManagerDecisionEvent.decision_key, ShelfManagerDecisionEvent.choice)
            .where(ShelfManagerDecisionEvent.account_id == account_id)
            .order_by(ShelfManagerDecisionEvent.created_at, ShelfManagerDecisionEvent.id)
        )).all()
    assert [row[1] for row in recorded] == ["accepted", "overridden"]
    assert recorded[1][0] == following["decision_key"]
