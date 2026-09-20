"""Step 11E: persisted Care execution belongs to one household subject.

The account still owns the physical shelf. These tests prove that persisted
routine identity, steps through that routine, adherence, wash cadence and
compiler provenance no longer become household-global merely because the
products are shared.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.domains.family.decision_subject import canonicalize_decision_subject
from app.domains.family.subject import (
    AGE_BAND_NOT_STATED,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
)
from app.domains.inventory.models import InventoryItem
from app.domains.planning import notifications
from app.domains.planning.models import NotificationDelivery
from app.domains.routines import adherence
from app.domains.routines.models import (
    Routine,
    RoutineAdherence,
    RoutineRecommendationRun,
    RoutineStep,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_domain_routines_api import _seeded_shelf
from tests.test_step11d_subject_care_authority import (
    _answer,
    _expired_shelf,
    _member,
    _queue,
    _self_subject_id,
)

pytestmark = pytest.mark.asyncio


async def _generate(client, token: str, subject_id: str | None = None):
    url = "/api/v2/routines/generate"
    if subject_id is not None:
        url += f"?subject_id={subject_id}"
    response = await client.post(
        url,
        headers=auth(token),
        json={"kinds": ["morning", "evening", "wash_day"], "explain": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _morning_by_subject(account_id: uuid.UUID):
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Routine)
            .where(Routine.account_id == account_id, Routine.kind == "morning")
            .order_by(Routine.created_at, Routine.id)
        )).scalars().all()
        return rows


async def _step_for_subject(
    account_id: uuid.UUID, subject_id: uuid.UUID, *, slot: str = "cleanser",
) -> tuple[Routine, RoutineStep]:
    async with get_sessionmaker()() as session:
        routine = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == subject_id,
                Routine.kind == "morning",
            )
        )).scalar_one()
        step = (await session.execute(
            select(RoutineStep).where(
                RoutineStep.routine_id == routine.id,
                RoutineStep.slot == slot,
            )
        )).scalar_one()
        return routine, step


async def test_legacy_self_routine_adopts_in_place_and_keeps_adherence(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    first = await _generate(app_client, token)
    legacy_morning = next(row for row in first["routines"] if row["kind"] == "morning")
    legacy_routine_id = uuid.UUID(legacy_morning["id"])
    cleanser = next(
        row for row in legacy_morning["steps"] if row["slot"] == "cleanser"
    )
    done_on = date(2026, 9, 20)
    completed = await app_client.post(
        f"/api/v2/routines/steps/{cleanser['id']}/complete",
        headers=auth(token),
        json={"done_on": done_on.isoformat(), "completed": True},
    )
    assert completed.status_code == 200, completed.text

    await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    second = await _generate(app_client, token)
    assert second["subject"] == {
        "household_subject_id": str(self_id),
        "is_account_holder": True,
    }

    async with get_sessionmaker()() as session:
        mornings = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "morning",
            )
        )).scalars().all()
        assert len(mornings) == 1
        assert mornings[0].id == legacy_routine_id
        assert mornings[0].household_subject_id == self_id
        history = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.routine_id == legacy_routine_id,
                RoutineAdherence.done_on == done_on,
            )
        )).scalar_one()
        assert history.completed is True


async def test_self_and_two_members_can_hold_same_kind_without_cloning_products(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    self_result = await _generate(app_client, token)
    a_result = await _generate(app_client, token, member_a)
    b_result = await _generate(app_client, token, member_b)

    assert self_result["subject"]["household_subject_id"] == str(self_id)
    assert a_result["subject"]["household_subject_id"] == member_a
    assert b_result["subject"]["household_subject_id"] == member_b

    rows = await _morning_by_subject(account_id)
    assert {row.household_subject_id for row in rows} == {
        self_id, uuid.UUID(member_a), uuid.UUID(member_b),
    }
    assert len(rows) == 3

    async with get_sessionmaker()() as session:
        cleanser_item_ids = []
        for routine in rows:
            step = (await session.execute(
                select(RoutineStep).where(
                    RoutineStep.routine_id == routine.id,
                    RoutineStep.slot == "cleanser",
                )
            )).scalar_one()
            cleanser_item_ids.append(step.inventory_item_id)
    assert len(set(cleanser_item_ids)) == 1, (
        "subjects may share the physical product without cloning inventory"
    )


async def test_completion_and_consistency_are_isolated_by_selected_subject(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    await _generate(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)

    routine_a, step_a = await _step_for_subject(
        account_id, uuid.UUID(member_a),
    )
    _routine_b, _step_b = await _step_for_subject(
        account_id, uuid.UUID(member_b),
    )

    done = await app_client.post(
        f"/api/v2/routines/steps/{step_a.id}/complete?subject_id={member_a}",
        headers=auth(token),
        json={"completed": True},
    )
    assert done.status_code == 200, done.text
    assert done.json()["subject"]["household_subject_id"] == member_a

    wrong_subject = await app_client.post(
        f"/api/v2/routines/steps/{step_a.id}/complete?subject_id={member_b}",
        headers=auth(token),
        json={"completed": True},
    )
    assert wrong_subject.status_code == 404

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].routine_id == routine_a.id

    a_consistency = await app_client.get(
        f"/api/v2/routines/consistency?days=1&subject_id={member_a}",
        headers=auth(token),
    )
    b_consistency = await app_client.get(
        f"/api/v2/routines/consistency?days=1&subject_id={member_b}",
        headers=auth(token),
    )
    assert a_consistency.status_code == b_consistency.status_code == 200
    assert a_consistency.json()["steps_completed"] == 1
    assert b_consistency.json()["steps_completed"] == 0


async def test_wash_anchor_uses_only_the_selected_subject_history(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)

    async with get_sessionmaker()() as session:
        routines = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "wash_day",
            )
        )).scalars().all()
        by_subject = {row.household_subject_id: row for row in routines}
        wash_a = by_subject[uuid.UUID(member_a)]
        shampoo_a = (await session.execute(
            select(RoutineStep).where(
                RoutineStep.routine_id == wash_a.id,
                RoutineStep.slot == "shampoo",
            )
        )).scalar_one()

    anchor = date(2026, 9, 19)
    response = await app_client.post(
        f"/api/v2/routines/steps/{shampoo_a.id}/complete?subject_id={member_a}",
        headers=auth(token),
        json={"done_on": anchor.isoformat(), "completed": True},
    )
    assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        claim_a = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER,
            account_id=account_id,
            subject_id=uuid.UUID(member_a),
            relation="adult",
            age_band=AGE_BAND_NOT_STATED,
        )
        claim_b = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER,
            account_id=account_id,
            subject_id=uuid.UUID(member_b),
            relation="adult",
            age_band=AGE_BAND_NOT_STATED,
        )
        checked_a = await canonicalize_decision_subject(
            session, principal_account_id=account_id, decision_subject=claim_a,
        )
        checked_b = await canonicalize_decision_subject(
            session, principal_account_id=account_id, decision_subject=claim_b,
        )
        assert await adherence.last_completed_wash_on(
            session,
            account_id=account_id,
            through=anchor,
            decision_subject=checked_a,
        ) == anchor
        assert await adherence.last_completed_wash_on(
            session,
            account_id=account_id,
            through=anchor,
            decision_subject=checked_b,
        ) is None


async def test_new_recommendation_runs_record_subject_without_rewriting_legacy(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)

    async with get_sessionmaker()() as session:
        runs = (await session.execute(
            select(RoutineRecommendationRun)
            .where(RoutineRecommendationRun.account_id == account_id)
            .order_by(RoutineRecommendationRun.created_at)
        )).scalars().all()
    assert runs[0].household_subject_id is None
    assert runs[-1].household_subject_id == uuid.UUID(member)


async def _versions(account_id: uuid.UUID) -> dict[uuid.UUID | None, dict[str, int]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Routine).where(Routine.account_id == account_id)
        )).scalars().all()
    out: dict[uuid.UUID | None, dict[str, int]] = {}
    for row in rows:
        out.setdefault(row.household_subject_id, {})[row.kind] = row.version
    return out


async def test_member_direct_pause_reconciles_only_that_members_routines(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    items = await _seeded_shelf(app_client, token)
    cleanser_id = uuid.UUID(items[0])
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    await _generate(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)
    before = await _versions(account_id)
    async with get_sessionmaker()() as session:
        item_before = await session.get(InventoryItem, cleanser_id)
        physical_version = item_before.version

    response = await app_client.post(
        f"/api/v2/routines/products/{cleanser_id}/pause?subject_id={member_a}",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True

    after = await _versions(account_id)
    assert after[uuid.UUID(member_a)] != before[uuid.UUID(member_a)]
    assert after[uuid.UUID(member_b)] == before[uuid.UUID(member_b)]
    assert after[self_id] == before[self_id]

    async with get_sessionmaker()() as session:
        item_after = await session.get(InventoryItem, cleanser_id)
        assert item_after.version == physical_version


async def test_improve_returns_only_the_selected_members_persisted_routines(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)

    response = await app_client.get(
        f"/api/v2/routines/improve?subject_id={member_a}",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["subject"]["household_subject_id"] == member_a

    async with get_sessionmaker()() as session:
        a_ids = set((await session.execute(
            select(Routine.id).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == uuid.UUID(member_a),
            )
        )).scalars().all())
        b_ids = set((await session.execute(
            select(Routine.id).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == uuid.UUID(member_b),
            )
        )).scalars().all())
    returned = {uuid.UUID(row["id"]) for row in payload["routines"]}
    assert returned == a_ids
    assert not returned & b_ids


async def test_deactivated_and_under_12_members_cannot_use_persisted_routine_surfaces(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    child_response = await app_client.post(
        "/api/v2/family-circle/profiles",
        headers=auth(token),
        json={"relation": "adult", "age_band": "under_12"},
    )
    assert child_response.status_code == 201, child_response.text
    child = child_response.json()["id"]
    refused = await app_client.post(
        f"/api/v2/routines/generate?subject_id={child}",
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )
    assert refused.status_code == 422
    async with get_sessionmaker()() as session:
        assert (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == uuid.UUID(child),
            )
        )).scalars().all() == []

    member = await _member(app_client, token)
    await _generate(app_client, token, member)
    deactivated = await app_client.patch(
        f"/api/v2/family-circle/profiles/{member}",
        headers=auth(token),
        json={"active": False},
    )
    assert deactivated.status_code == 200, deactivated.text
    today = await app_client.get(
        f"/api/v2/routines/today?subject_id={member}",
        headers=auth(token),
    )
    assert today.status_code == 404


async def test_member_routine_step_is_not_valid_self_only_experience_feedback(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)
    _routine, step = await _step_for_subject(account_id, uuid.UUID(member))

    response = await app_client.post(
        "/api/v2/routines/experience-feedback",
        headers=auth(token),
        json={
            "subject_type": "routine_step",
            "subject_id": str(step.id),
            "dimension": "overall_experience",
            "sentiment": "positive",
        },
    )
    assert response.status_code == 404


async def test_notification_action_cannot_complete_a_members_step(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)
    _routine, step = await _step_for_subject(account_id, uuid.UUID(member))
    done_on = date(2026, 9, 20)

    async with get_sessionmaker()() as session:
        delivery = NotificationDelivery(
            account_id=account_id,
            plan_date=done_on,
            notification_key=f"routine_due:{step.id}",
            dedup_hash="a" * 64,
            title="Routine",
            body="Routine step",
            status="queued",
            source_kind="routine_due",
            source_id=str(step.id),
            destination_params={
                "routine_action": "complete_step",
                "step_id": str(step.id),
                "done_on": done_on.isoformat(),
            },
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    response = await app_client.post(
        f"/api/v2/routines/notifications/{delivery_id}/action",
        headers=auth(token),
        json={"action": "done"},
    )
    assert response.status_code == 404

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
                RoutineAdherence.routine_id == _routine.id,
            )
        )).scalars().all()
        assert rows == []



async def test_member_manager_acceptance_reconciles_only_that_members_routine(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    # Add one Manager-worthy product after the ordinary Care shelf exists.
    await _expired_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    await _generate(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)
    before = await _versions(account_id)

    primary = (await _queue(app_client, token, member_a))["primary"]
    assert primary is not None
    response = await _answer(
        app_client, token, primary, "accept", subject_id=member_a,
    )
    assert response.status_code == 200, response.text
    after = await _versions(account_id)

    assert after[uuid.UUID(member_a)] != before[uuid.UUID(member_a)]
    assert after[uuid.UUID(member_b)] == before[uuid.UUID(member_b)]
    assert after[self_id] == before[self_id]


async def test_member_simplification_changes_only_that_members_profile_and_routines(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    await _generate(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)
    before = await _versions(account_id)

    response = await app_client.post(
        f"/api/v2/routines/simplify?subject_id={member_a}",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["subject"]["household_subject_id"] == member_a
    assert response.json()["changed"] is True

    after = await _versions(account_id)
    assert after[uuid.UUID(member_a)] != before[uuid.UUID(member_a)]
    assert after[uuid.UUID(member_b)] == before[uuid.UUID(member_b)]
    assert after[self_id] == before[self_id]


async def test_protocol_notification_worker_never_queues_a_member_only_routine(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)

    # Only the named member has persisted routines. Notification delivery is
    # still account-holder/self-only in Step 11E.
    await _generate(app_client, token, member)

    async with get_sessionmaker()() as session:
        delivery = await notifications.queue_for_protocol_day(
            session,
            account_id=account_id,
            plan_date=date(2026, 9, 20),
            timezone_name="Asia/Kolkata",
        )
        assert delivery is None


async def test_an_unadopted_legacy_routine_is_never_visible_to_a_member(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """A household can exist for a long time before its holder writes again.

    Until they do, the pre-household routine sits there with no subject on it.
    It is structurally theirs — no route could have written a member's routine
    before this slice — and a member must not pick it up in the meantime. This
    is the state adoption has not yet reached, which is where a subject
    predicate that quietly matches everything stops being visible: once the
    legacy row has been adopted there is nothing left for a loose predicate to
    wrongly match.
    """
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)
    member = await _member(app_client, token)

    async with get_sessionmaker()() as session:
        legacy_kinds = set((await session.execute(
            select(Routine.kind).where(
                Routine.account_id == account_id,
                Routine.household_subject_id.is_(None),
            )
        )).scalars().all())
    assert legacy_kinds, "the pre-household routine should still be unadopted"

    # The member has written nothing, so they have nothing.
    member_today = await app_client.get(
        f"/api/v2/routines/today?subject_id={member}", headers=auth(token),
    )
    assert member_today.status_code == 200, member_today.text
    assert member_today.json()["routines"] == []

    member_improve = await app_client.get(
        f"/api/v2/routines/improve?subject_id={member}", headers=auth(token),
    )
    assert member_improve.status_code == 200, member_improve.text
    assert member_improve.json()["routines"] == []

    # The account holder still sees their own, unchanged and still unadopted.
    holder_today = await app_client.get("/api/v2/routines/today", headers=auth(token))
    assert holder_today.status_code == 200, holder_today.text
    assert holder_today.json()["subject"]["is_account_holder"] is True
    async with get_sessionmaker()() as session:
        still_legacy = set((await session.execute(
            select(Routine.kind).where(
                Routine.account_id == account_id,
                Routine.household_subject_id.is_(None),
            )
        )).scalars().all())
    assert still_legacy == legacy_kinds, "a read adopted rows; reads must not write"


async def test_one_persons_generation_never_retires_another_persons_routine(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """Reconciliation retires what *this* person no longer has, and no more.

    Building a member's morning routine says nothing about the account holder's
    evening one. A reconciliation that looked at every routine on the account
    would retire every kind the current subject did not just build — quietly
    switching off somebody else's evening routine because a different person
    asked for a different thing.
    """
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)

    everything = await app_client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"kinds": ["morning", "evening", "wash_day"], "explain": False},
    )
    assert everything.status_code == 200, everything.text
    me = await _self_subject_id(account_id)

    async def _active_kinds(subject_id) -> set[str]:
        async with get_sessionmaker()() as session:
            return set((await session.execute(
                select(Routine.kind).where(
                    Routine.account_id == account_id,
                    Routine.household_subject_id == subject_id,
                    Routine.status == "active",
                )
            )).scalars().all())

    holder_before = await _active_kinds(me)
    assert {"morning", "evening"} <= holder_before

    only_morning = await app_client.post(
        f"/api/v2/routines/generate?subject_id={member}",
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )
    assert only_morning.status_code == 200, only_morning.text

    assert await _active_kinds(me) == holder_before, (
        "generating for one person retired another person's routine"
    )
    assert await _active_kinds(uuid.UUID(member)) == {"morning"}


async def test_a_member_with_no_routine_is_told_there_is_nothing_to_measure(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """Nothing to measure is not the same as a score of zero.

    Somebody who has never built a routine has not failed at anything, and the
    product does not tell them they completed none of it. The distinction turns
    entirely on whose steps count as expected: read across the household, a
    member with no routine inherits the account holder's expectations and gets a
    real reading of zero against them.
    """
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token)

    empty = await app_client.get(
        f"/api/v2/routines/consistency?days=7&subject_id={member}", headers=auth(token),
    )
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["note"] == "Nothing to measure yet."
    assert body["steps_completed"] == 0
    assert body["subject"]["household_subject_id"] == member
    # And nothing in the sentence blames them for it.
    for banned in ("fail", "missed", "wasted", "poor", "bad"):
        assert banned not in body["note"].lower()

    # The account holder, who does have one, gets a real reading.
    mine = await app_client.get(
        "/api/v2/routines/consistency?days=7", headers=auth(token),
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["note"] != "Nothing to measure yet."


async def test_consistency_ignores_an_adherence_row_from_another_account(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """The row says it is mine; the routine it points at says otherwise.

    Adherence carries its own ``account_id`` and inherits its real owner through
    the routine. Reading one without the other means a row written against
    somebody else's routine can be counted into this account's consistency —
    a number about a stranger's mornings, shown as this person's.
    """
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seeded_shelf(app_client, token_a)
    await _seeded_shelf(app_client, token_b)
    await _generate(app_client, token_a)
    await _generate(app_client, token_b)

    async with get_sessionmaker()() as session:
        foreign_routine = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_b, Routine.kind == "morning",
            )
        )).scalar_one()
        foreign_step = (await session.execute(
            select(RoutineStep).where(RoutineStep.routine_id == foreign_routine.id).limit(1)
        )).scalar_one()
        session.add(RoutineAdherence(
            account_id=account_a,
            routine_id=foreign_routine.id,
            slot=foreign_step.slot,
            step_id=foreign_step.id,
            done_on=date.today(),
            completed=True,
        ))
        await session.commit()

    reading = await app_client.get(
        "/api/v2/routines/consistency?days=1", headers=auth(token_a),
    )
    assert reading.status_code == 200, reading.text
    assert reading.json()["steps_completed"] == 0, (
        "a completion recorded against another account's routine was counted"
    )


async def test_a_self_claim_naming_a_member_cannot_read_the_holders_own_care_state(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """The claim says "me"; the household says otherwise, and the server asks it.

    A caller-supplied subject that names a member while claiming to be the
    account holder is the sharpest forgery available, because the two halves
    change different things. The id decides which preference row is written; the
    ``is_account_holder`` flag decides whether the *pre-household* Care state is
    readable at all. Believing the flag would let a request made about a member
    read what the account holder decided before the household existed — and then
    report the member's own state as already settled on the strength of it.
    """
    from app.domains.care.product_preferences import CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY
    from app.domains.family.decision_subject import DecisionSubject
    from app.domains.family.subject import SUBJECT_ACCOUNT_HOLDER
    from app.domains.inventory.models import InventoryAttribute
    from app.domains.routines.service import _pause_care_product_for_manager

    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    async with get_sessionmaker()() as session:
        item = (await session.execute(
            select(InventoryItem).where(
                InventoryItem.account_id == account_id,
                InventoryItem.category == "beauty",
            ).limit(1)
        )).scalar_one()
        item_id = item.id
    # The account holder's own pre-household pause, in the store it lived in.
    paused = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert paused.status_code == 200, paused.text
    member = await _member(app_client, token)

    forged = DecisionSubject(
        subject=ResolvedSubject(
            kind=SUBJECT_ACCOUNT_HOLDER, account_id=account_id,
            subject_id=uuid.UUID(member), relation="self",
            age_band=AGE_BAND_NOT_STATED,
        ),
        circle_created_at=None,
    )
    async with get_sessionmaker()() as session:
        result = await _pause_care_product_for_manager(
            session, account_id=account_id, account_id_str=str(account_id),
            item_id=item_id, subject_claim=forged,
        )
        await session.commit()

    # Resolved as the member they actually are: this is a new decision for that
    # person, not a restatement of somebody else's.
    assert result["changed"] is True, (
        "the forged claim read the account holder's pre-household Care state"
    )
    assert result["subject"]["household_subject_id"] == member
    assert result["subject"]["is_account_holder"] is False

    # And the account holder's own pre-household row was not taken over.
    async with get_sessionmaker()() as session:
        legacy = (await session.execute(
            select(InventoryAttribute).where(
                InventoryAttribute.item_id == item_id,
                InventoryAttribute.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
            )
        )).scalars().all()
    assert len(legacy) == 1 and legacy[0].value is True
