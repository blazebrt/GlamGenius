"""§1.8 — occasion styling: looks, adjustments, feedback and memory influence.

``test_domain_quiz_style.py`` covers the quiz and the occasion record. This file
covers what happens after "style me": the looks themselves, the items in them,
revising and swapping, feedback, and the rule that nothing may be recommended
that the user does not own.

What this protects against
--------------------------
* A look containing clothes the user does not own, presented as if they do.
* An empty wardrobe producing invented outfits instead of an honest "not enough
  inventory yet".
* A revise or swap silently losing the audit trail of what changed.
* Swapping in another account's item, or an item in the wrong slot.
* One account reading or adjusting another's look.
* A deleted controlled-memory fact still shaping what is recommended.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.progress import memory as memory_domain
from app.domains.recommendation.models import (
    LookAdjustment,
    LookFeedback,
    RecommendationRun,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.journey import SEVEN_CATEGORY_ITEMS, ok

pytestmark = pytest.mark.asyncio


async def _stock(client, token):
    """Enough owned inventory for the engine to build a complete look."""
    created = {}
    for body in SEVEN_CATEGORY_ITEMS:
        item = ok(await client.post(
            "/api/v2/inventory/items", headers=auth(token), json=body
        ))
        created.setdefault(body["category"], []).append(item["id"])
    return created


async def _style(client, token, **overrides):
    payload = {
        "occasion": {
            "occasion_key": "office",
            "title": "Monday at the office",
            "time_of_day": "morning",
        }
    }
    payload.update(overrides)
    return await client.post("/api/v2/style/occasion", headers=auth(token), json=payload)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

async def test_styling_produces_looks_from_owned_inventory_only(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    owned = await _stock(app_client, token)
    owned_ids = {value for ids in owned.values() for value in ids}

    resp = await _style(app_client, token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["looks"], "styling must produce at least one look"

    for look in body["looks"]:
        item_ids = {row["inventory_item_id"] for row in look["owned_items"]}
        assert item_ids, "a look with no owned items is not wearable"
        assert item_ids <= owned_ids, "a look may only use items the user owns"
        for addition in look["optional_additions"]:
            assert addition["owned"] is False
            assert "do not own" in addition["label"].lower()

    assert body["confirmed_item_count"] == len(owned_ids)
    assert body["disclaimer"], "styling output must carry its non-medical framing"


async def test_generated_look_references_the_occasion_it_was_asked_for(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _stock(app_client, token)
    occasion = ok(await app_client.post(
        "/api/v2/occasions",
        headers=auth(token),
        json={"occasion_key": "party", "title": "Friday dinner", "time_of_day": "evening"},
    ))

    body = (await _style(app_client, token, occasion_id=occasion["id"], occasion=None)).json()

    assert body["occasion"]["id"] == occasion["id"]
    assert body["occasion"]["occasion_key"] == "party"


async def test_empty_inventory_refuses_to_invent_clothes(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The most important refusal in the product: with nothing recorded, the
    honest answer is 'add some things', not an outfit the user cannot wear."""
    token, _ = await registered_supabase_user()

    body = (await _style(app_client, token)).json()

    assert body["status"] == "not_enough_inventory"
    assert body["looks"] == []
    assert body["guidance"], "a refusal must come with what to do about it"
    assert "not invent" in body["message"].lower()


async def test_draft_inventory_is_not_treated_as_owned(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    owned = await _stock(app_client, token)

    factory = get_sessionmaker()
    async with factory() as session:
        from app.domains.inventory.models import InventoryItem

        rows = (await session.execute(
            select(InventoryItem).where(InventoryItem.account_id == uid)
        )).scalars().all()
        for row in rows:
            row.verification_state = "draft"
        await session.commit()

    body = (await _style(app_client, token)).json()

    assert body["status"] == "not_enough_inventory"
    assert body["unconfirmed_draft_count"] == len(
        [v for ids in owned.values() for v in ids]
    )


async def test_styling_records_a_run_with_its_inputs(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """Every recommendation is reproducible: the run records what it considered."""
    token, uid = await registered_supabase_user()
    await _stock(app_client, token)

    body = (await _style(app_client, token)).json()

    factory = get_sessionmaker()
    async with factory() as session:
        run = (await session.execute(
            select(RecommendationRun).where(RecommendationRun.id == uuid.UUID(body["run_id"]))
        )).scalar_one()
    assert run.account_id == uid
    assert run.status == "succeeded"
    assert run.kind == "style_occasion"
    assert body["engine_version"]
    assert body["candidates_considered"] >= 1


async def test_repeated_styling_consumes_the_allowance_once_per_run(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _stock(app_client, token)

    first = (await _style(app_client, token)).json()["entitlement"]
    second = (await _style(app_client, token)).json()["entitlement"]

    assert second["used"] == first["used"] + 1
    assert second["source"] == "beta_grant"
    assert second["remaining"] == second["included"] - second["used"]


# ---------------------------------------------------------------------------
# Look adjustment
# ---------------------------------------------------------------------------

async def test_look_can_be_revised_and_the_change_is_recorded(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _stock(app_client, token)
    look_id = (await _style(app_client, token)).json()["looks"][0]["id"]

    resp = await app_client.post(
        f"/api/v2/looks/{look_id}/revise",
        headers=auth(token),
        json={"reason": "too_formal", "note": "Something easier for a Monday."},
    )

    assert resp.status_code == 200, resp.text

    detail = ok(await app_client.get(f"/api/v2/looks/{look_id}", headers=auth(token)))
    assert detail["adjustments"], "a revision must leave a trail"
    assert detail["adjustments"][0]["reason"] == "too_formal"

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(LookAdjustment.id)).where(
                LookAdjustment.look_id == uuid.UUID(look_id)
            )
        )).scalar_one()
    assert count >= 1


async def test_swapping_in_an_item_the_user_does_not_own_is_refused(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    other_token, _ = await registered_supabase_user()
    await _stock(app_client, owner_token)
    other_items = await _stock(app_client, other_token)
    look = (await _style(app_client, owner_token)).json()["looks"][0]

    resp = await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        headers=auth(owner_token),
        json={"slot": "clothing", "to_item_id": other_items["wardrobe"][0]},
    )

    assert resp.status_code == 404, resp.text


async def test_swapping_in_a_wrong_category_item_is_refused(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    owned = await _stock(app_client, token)
    look = (await _style(app_client, token)).json()["looks"][0]

    resp = await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        headers=auth(token),
        json={"slot": "clothing", "to_item_id": owned["perfumes"][0]},
    )

    assert resp.status_code == 422, resp.text


async def test_feedback_is_stored_once_per_look(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _stock(app_client, token)
    look_id = (await _style(app_client, token)).json()["looks"][0]["id"]

    for rating in ("saved", "loved"):
        resp = await app_client.post(
            f"/api/v2/looks/{look_id}/feedback",
            headers=auth(token),
            json={"rating": rating},
        )
        assert resp.status_code == 200, resp.text

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(LookFeedback).where(LookFeedback.account_id == uid)
        )).scalars().all()
    assert len(rows) == 1, "changing your mind must update the feedback, not stack it"
    assert rows[0].rating == "loved"


async def test_feedback_rejects_an_unknown_rating(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _stock(app_client, token)
    look_id = (await _style(app_client, token)).json()["looks"][0]["id"]

    resp = await app_client.post(
        f"/api/v2/looks/{look_id}/feedback",
        headers=auth(token),
        json={"rating": "hideous"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

async def test_another_accounts_look_is_not_reachable(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    await _stock(app_client, owner_token)
    look_id = (await _style(app_client, owner_token)).json()["looks"][0]["id"]

    reads = await app_client.get(f"/api/v2/looks/{look_id}", headers=auth(intruder_token))
    revise = await app_client.post(
        f"/api/v2/looks/{look_id}/revise",
        headers=auth(intruder_token),
        json={"reason": "too_formal"},
    )
    feedback = await app_client.post(
        f"/api/v2/looks/{look_id}/feedback",
        headers=auth(intruder_token),
        json={"rating": "loved"},
    )

    assert reads.status_code == 404
    assert revise.status_code == 404
    assert feedback.status_code == 404


async def test_unknown_look_id_is_404(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.get(f"/api/v2/looks/{uuid.uuid4()}", headers=auth(token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Controlled memory as a recommendation input
# ---------------------------------------------------------------------------

async def test_only_active_memory_is_eligible_to_shape_output(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """``active_facts`` is the single door anything shaping a recommendation
    reads through. A deleted fact must not come back through it."""
    token, uid = await registered_supabase_user()
    factory = get_sessionmaker()

    async with factory() as session:
        fact = await memory_domain.record(
            session,
            uid,
            category=memory_domain.CATEGORY_FAVOURITE,
            fact="You liked deep teal.",
            source=memory_domain.SOURCE_FEEDBACK,
            confidence=0.6,
        )
        await session.commit()
        fact_id = fact.id

    async with factory() as session:
        eligible = await memory_domain.active_facts(session, uid)
    assert fact_id in {row.id for row in eligible}

    ok(await app_client.delete(f"/api/v2/memory/{fact_id}", headers=auth(token)))

    async with factory() as session:
        eligible_after = await memory_domain.active_facts(session, uid)
        everything = await memory_domain.all_facts_including_deleted(session, uid)

    assert fact_id not in {row.id for row in eligible_after}, (
        "a deleted fact must stop being eligible immediately"
    )
    assert fact_id in {row.id for row in everything}, (
        "the tombstone stays, so the user can see what was forgotten"
    )


async def test_disabling_a_category_removes_it_from_recommendation_input(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    factory = get_sessionmaker()

    async with factory() as session:
        await memory_domain.record(
            session,
            uid,
            category=memory_domain.CATEGORY_FAVOURITE,
            fact="You liked deep teal.",
            source=memory_domain.SOURCE_FEEDBACK,
        )
        await session.commit()

    resp = await app_client.patch(
        f"/api/v2/memory/categories/{memory_domain.CATEGORY_FAVOURITE}",
        headers=auth(token),
        json={"category": memory_domain.CATEGORY_FAVOURITE, "enabled": False},
    )
    assert resp.status_code == 200, resp.text

    async with factory() as session:
        eligible = await memory_domain.active_facts(session, uid)
    assert not [
        row for row in eligible if row.category == memory_domain.CATEGORY_FAVOURITE
    ]

    # Re-enabling brings it back — switching a category off hides it from
    # recommendations, it does not destroy what was learned.
    assert (await app_client.patch(
        f"/api/v2/memory/categories/{memory_domain.CATEGORY_FAVOURITE}",
        headers=auth(token),
        json={"category": memory_domain.CATEGORY_FAVOURITE, "enabled": True},
    )).status_code == 200

    async with factory() as session:
        restored = await memory_domain.active_facts(session, uid)
    assert [
        row for row in restored if row.category == memory_domain.CATEGORY_FAVOURITE
    ]
