"""§1.11 — routines through the real routes: generation, steps, adherence.

``test_domain_routines.py`` checks the seeded reference data. This file checks
the behaviour built on top of it: routines generated from the shelf a user
actually owns, steps completed and un-completed, adherence that counts days
rather than streaks, and the safety wording that must never drift into
diagnosis.

What this protects against
--------------------------
* A routine recommending a product the user does not own without saying so.
* Double-tapping "done" counting a day twice, or a streak metric appearing.
* A step from another account being completable.
* Ingredient guidance drifting into diagnosis, prescription or dosage.
* The supplement and perfume surfaces losing their "general information"
  framing.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.bootstrap import run as run_seed
from app.domains.routines.models import Routine, RoutineAdherence
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.journey import ok

pytestmark = pytest.mark.asyncio


SHELF_ITEMS = [
    {
        "category": "beauty", "display_name": "Gentle Foaming Cleanser",
        "subcategory": "cleanser",
        "details": {
            "product_type": "cleanser", "purpose": "cleansing",
            "routine_position": "cleanse", "use_frequency": "twice_daily",
        },
    },
    {
        "category": "beauty", "display_name": "Daily Moisturiser",
        "subcategory": "moisturiser",
        "details": {
            "product_type": "moisturiser", "purpose": "hydration",
            "routine_position": "moisturise", "use_frequency": "twice_daily",
        },
    },
    {
        "category": "beauty", "display_name": "Broad Spectrum SPF 50",
        "subcategory": "sunscreen",
        "details": {
            "product_type": "sunscreen", "purpose": "sun_protection",
            "routine_position": "protect", "use_frequency": "daily",
            "active_ingredients": ["zinc oxide"],
        },
    },
    {
        "category": "hair", "display_name": "Hydrating Shampoo",
        "subcategory": "shampoo",
        "details": {
            "product_type": "shampoo", "purpose": "cleansing",
            "routine_position": "cleanse", "use_frequency": "twice_weekly",
        },
    },
    {
        "category": "hair", "display_name": "Daily Conditioner",
        "subcategory": "conditioner",
        "details": {
            "product_type": "conditioner", "purpose": "hydration",
            "routine_position": "condition", "use_frequency": "twice_weekly",
        },
    },
]


async def _seeded_shelf(client, token):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()
    created = []
    for body in SHELF_ITEMS:
        created.append(ok(await client.post(
            "/api/v2/inventory/items", headers=auth(token), json=body
        ))["id"])
    return created


async def _generate(client, token, **overrides):
    payload = {"kinds": ["morning", "evening", "wash_day"], "climate": "humid"}
    payload.update(overrides)
    return await client.post(
        "/api/v2/routines/generate", headers=auth(token), json=payload
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

async def test_routines_are_built_from_the_shelf_the_user_owns(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    owned = await _seeded_shelf(app_client, token)

    resp = await _generate(app_client, token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    kinds = {row["kind"] for row in body["routines"]}
    assert {"morning", "evening", "wash_day"} <= kinds

    morning = next(row for row in body["routines"] if row["kind"] == "morning")
    assert morning["steps"], "a routine with no steps is not a routine"
    used = [step["inventory_item_id"] for step in morning["steps"] if step["owned"]]
    assert used, "the shelf must actually be used"
    assert set(used) <= set(owned)

    factory = get_sessionmaker()
    async with factory() as session:
        stored = (await session.execute(
            select(func.count(Routine.id)).where(Routine.account_id == uid)
        )).scalar_one()
    assert stored == len(kinds)


async def test_a_step_with_nothing_to_use_is_labelled_a_gap(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The routine still shows the step, but it is honest that the user owns
    nothing for it rather than naming a product they do not have."""
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    body = (await _generate(app_client, token)).json()
    evening = next(row for row in body["routines"] if row["kind"] == "evening")

    gaps = [step for step in evening["steps"] if step["is_gap"]]
    for step in gaps:
        assert step["owned"] is False
        assert step["inventory_item_id"] is None
        assert step["product_name"] is None
        assert step["alternative"], "a gap must suggest what to do about it"


async def test_generation_is_stable_and_does_not_stack_routines(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    first = (await _generate(app_client, token)).json()
    second = (await _generate(app_client, token)).json()

    assert {r["kind"] for r in first["routines"]} == {r["kind"] for r in second["routines"]}

    factory = get_sessionmaker()
    async with factory() as session:
        routines = (await session.execute(
            select(func.count(Routine.id)).where(Routine.account_id == uid)
        )).scalar_one()
    assert routines == len(first["routines"]), (
        "regenerating must update the routines, not create a second set"
    )


async def test_climate_context_reaches_the_routine(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    body = (await _generate(app_client, token, climate="humid")).json()

    notes = [
        note
        for row in body["routines"]
        for note in row["climate_notes"]
    ]
    assert notes, "a stated climate must produce climate guidance"
    assert all(note["rule_id"].startswith("rule.") for note in notes)


async def test_unknown_routine_kind_is_refused(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await _generate(app_client, token, kinds=["overnight_miracle"])
    assert resp.status_code == 422


async def test_routine_output_carries_the_non_medical_disclaimer(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    body = (await _generate(app_client, token)).json()

    text = str(body).lower()
    for banned in ("diagnos", "prescri", "dosage", "cure", "treat your", "you have"):
        assert banned not in text, f"routine output must not read as '{banned}'"


# ---------------------------------------------------------------------------
# Adherence
# ---------------------------------------------------------------------------

async def test_completing_a_step_is_recorded_for_that_day(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    body = (await _generate(app_client, token)).json()
    step_id = body["routines"][0]["steps"][0]["id"]
    done_on = date(2026, 2, 16)

    resp = await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete",
        headers=auth(token),
        json={"done_on": done_on.isoformat(), "completed": True},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["completed"] is True
    assert resp.json()["done_on"] == done_on.isoformat()

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == uid)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].done_on == done_on
    assert rows[0].completed is True


async def test_repeated_completion_of_the_same_day_is_idempotent(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    step_id = (await _generate(app_client, token)).json()["routines"][0]["steps"][0]["id"]
    done_on = date(2026, 2, 16).isoformat()

    for _ in range(3):
        resp = await app_client.post(
            f"/api/v2/routines/steps/{step_id}/complete",
            headers=auth(token),
            json={"done_on": done_on, "completed": True},
        )
        assert resp.status_code == 200

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(RoutineAdherence.id)).where(
                RoutineAdherence.account_id == uid
            )
        )).scalar_one()
    assert count == 1, "one day, one adherence row, however many taps"


async def test_completion_can_be_undone_without_penalty(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    step_id = (await _generate(app_client, token)).json()["routines"][0]["steps"][0]["id"]
    done_on = date(2026, 2, 16).isoformat()

    await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete",
        headers=auth(token),
        json={"done_on": done_on, "completed": True},
    )
    resp = await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete",
        headers=auth(token),
        json={"done_on": done_on, "completed": False},
    )

    assert resp.status_code == 200
    assert resp.json()["completed"] is False
    # The wording of an unmarked day must not shame the user.
    message = resp.json()["note"].lower()
    assert "not a problem" in message
    for banned in ("failed", "broke", "streak", "lost"):
        assert banned not in message


async def test_adherence_on_separate_days_is_counted_separately(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    step_id = (await _generate(app_client, token)).json()["routines"][0]["steps"][0]["id"]
    start = date(2026, 2, 16)

    for offset in range(3):
        await app_client.post(
            f"/api/v2/routines/steps/{step_id}/complete",
            headers=auth(token),
            json={"done_on": (start + timedelta(days=offset)).isoformat()},
        )

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == uid)
        )).scalars().all()
    assert len({row.done_on for row in rows}) == 3


async def test_consistency_reports_days_not_streaks(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)

    body = ok(await app_client.get("/api/v2/routines/consistency", headers=auth(token)))

    # No streak *field* exists — the only mention of the word is the copy that
    # explicitly says streaks are not counted.
    assert not [key for key in body if "streak" in key.lower()]
    assert "days_consistent" in body or "days" in str(body)
    text = str(body).lower()
    assert "we do not count streaks" in text
    for banned in ("failed routine", "you failed", "broken", "you missed"):
        assert banned not in text


async def test_another_accounts_step_cannot_be_completed(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, owner_token)
    step_id = (
        await _generate(app_client, owner_token)
    ).json()["routines"][0]["steps"][0]["id"]

    resp = await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete",
        headers=auth(intruder_token),
        json={"completed": True},
    )
    assert resp.status_code == 404


async def test_unknown_step_id_is_404(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        f"/api/v2/routines/steps/{uuid.uuid4()}/complete",
        headers=auth(token),
        json={"completed": True},
    )
    assert resp.status_code == 404


async def test_routines_today_shows_only_what_is_due(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)

    body = ok(await app_client.get(
        "/api/v2/routines/today?on=2026-02-16", headers=auth(token)
    ))

    assert body["date"] == "2026-02-16"
    assert body["part_of_day"] in {"morning", "afternoon", "evening", "night"}
    assert body["disclaimer"], "the non-medical framing travels with the routine"
    # Monday is not a weekend, so the weekly extras must not be shown.
    assert "weekly" not in {row["kind"] for row in body["routines"]}


# ---------------------------------------------------------------------------
# Ingredient safety, perfume and supplement surfaces
# ---------------------------------------------------------------------------

async def test_ingredient_check_needs_something_to_check(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """An empty check must not answer "all clear" — that would read as a
    clean bill of health for a label nobody looked at."""
    token, _ = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()

    resp = await app_client.post(
        "/api/v2/ingredients/check", headers=auth(token), json={"against_owned": True}
    )
    assert resp.status_code == 422


async def test_unrecognised_ingredients_are_listed_not_ignored(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()

    body = ok(await app_client.post(
        "/api/v2/ingredients/check",
        headers=auth(token),
        json={"label_text": "Aqua, Retinol, Unobtainium Extract", "against_owned": False},
    ))

    resolved = {row["ingredient_key"] for row in body["identified"]}
    assert "retinol" in resolved
    assert body["note"], "the answer must state what it could and could not read"


async def test_ingredient_detail_is_about_the_ingredient_not_the_person(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()

    body = ok(await app_client.get("/api/v2/ingredients/retinol", headers=auth(token)))

    assert body["display_name"]
    text = str(body).lower()
    for banned in ("you have", "diagnos", "prescri", "dosage", "we recommend you take"):
        assert banned not in text


async def test_perfume_guidance_is_context_not_a_rule_about_people(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()

    body = ok(await app_client.get(
        "/api/v2/perfume/recommendation?occasion_key=office&weather=humid",
        headers=auth(token),
    ))

    text = str(body).lower()
    for banned in ("for men", "for women", "masculine only", "feminine only"):
        assert banned not in text, "perfume guidance must not impose gender rules"


async def test_supplement_summary_stays_general_information(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()
    ok(await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": "supplements", "display_name": "Vitamin D3",
            "details": {"supplement_name": "Vitamin D3", "use_frequency": "daily"},
        },
    ))

    body = ok(await app_client.get("/api/v2/supplements/summary", headers=auth(token)))

    import re

    text = str(body).lower()
    # Word-boundary matching: "mg" must not appear as a unit, but the brand
    # name contains those letters.
    for banned in (r"\b\d+\s*mg\b", r"\b\d+\s*iu\b", r"\btake \w+ (a day|daily)\b",
                   r"\bprescri", r"\bdiagnos", r"\bcures\b"):
        assert not re.search(banned, text), (
            f"supplement guidance must not match {banned!r}"
        )
    # Dosage may only ever appear as a refusal to give it.
    for match in re.finditer(r"dosage", text):
        window = text[max(0, match.start() - 60):match.end() + 20]
        assert "does not provide" in window or "not provide" in window, (
            "the word 'dosage' may only appear in the refusal to give one"
        )
    assert "professional" in text and "label" in text, (
        "supplement guidance must point at the label and a professional"
    )
