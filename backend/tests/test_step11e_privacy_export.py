"""Step 11E privacy export: one persisted routine graph per human."""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from app.domains.privacy import EXPORT_SCHEMA_VERSION
from app.domains.privacy.export import build_export
from app.domains.routines.models import Routine, RoutineAdherence, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_domain_routines_api import _seeded_shelf
from tests.test_step11d_subject_care_authority import _member, _self_subject_id

pytestmark = pytest.mark.asyncio


async def _generate(client, token: str, subject_id: str | None = None):
    url = "/api/v2/routines/generate"
    if subject_id is not None:
        url += f"?subject_id={subject_id}"
    response = await client.post(
        url,
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )
    assert response.status_code == 200, response.text


async def test_export_1_4_groups_routine_graph_by_subject_and_legacy_self(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    # Pre-household persisted history is structurally account-holder history.
    await _generate(app_client, token)
    member = await _member(app_client, token)
    self_id = await _self_subject_id(account_id)
    await _generate(app_client, token, member)

    async with get_sessionmaker()() as session:
        export = await build_export(session, account_id)

    # The 1.4 routine grouping, unchanged in 1.5 (Lane E: export coverage)
    # and in 1.6 (Step 15: the growth domain's referral history).
    assert export["schema_version"] == EXPORT_SCHEMA_VERSION == "1.6"
    history = export["domains"]["routines"]["routine_history"]
    assert set(history["by_subject"]) == {str(self_id), member}
    assert history["by_subject"][member]["routines"]
    assert history["by_subject"][member]["recommendation_runs"]
    assert history["account_holder_legacy"]["routines"]
    assert history["account_holder_legacy"]["recommendation_runs"]
    assert history["unattributed"]["routines"] == []


async def test_export_strips_foreign_subject_from_a_owned_routine_everywhere(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a = await registered_supabase_user()
    token_b, _account_b = await registered_supabase_user()
    await _seeded_shelf(app_client, token_a)
    await _generate(app_client, token_a)
    foreign_member = await _member(app_client, token_b)

    async with get_sessionmaker()() as session:
        routine = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_a,
                Routine.kind == "morning",
            )
        )).scalar_one()
        routine.household_subject_id = uuid.UUID(foreign_member)
        await session.commit()

    async with get_sessionmaker()() as session:
        export = await build_export(session, account_a)

    encoded = json.dumps(export, sort_keys=True)
    assert foreign_member not in encoded
    history = export["domains"]["routines"]["routine_history"]
    assert len(history["unattributed"]["routines"]) == 1
    assert history["unattributed"]["routines"][0]["household_subject_id"] is None
    assert history["unattributed"]["routines"][0]["invariant"] == (
        "routine_subject_ownership_invalid"
    )



async def test_dual_self_routine_is_governed_on_regular_read_but_export_still_completes(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)
    await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    async with get_sessionmaker()() as session:
        legacy = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "morning",
                Routine.household_subject_id.is_(None),
            )
        )).scalar_one()
        session.add(Routine(
            account_id=account_id,
            household_subject_id=self_id,
            kind="morning",
            label=legacy.label,
            frequency=legacy.frequency,
        ))
        await session.commit()

    # 503, not 422. Nothing about the request was wrong: the stored state is in
    # a shape no route could have produced, which is the governed identity
    # failure the rest of Step 11 already answers this way. A 422 on
    # ``subject_id`` would blame a field the person cannot fix.
    regular = await app_client.get("/api/v2/routines/today", headers=auth(token))
    assert regular.status_code == 503, regular.text
    assert regular.json()["detail"]["message"] == "This result is not available right now."
    # The reason is for the log, never the customer.
    assert "routine_dual_self_state" not in regular.text

    async with get_sessionmaker()() as session:
        export = await build_export(session, account_id)
    errors = export["domains"]["routines"]["invariant_errors"]
    assert {
        "routine_kind": "morning",
        "error": "account_has_dual_self_routines",
    } in errors


async def test_export_of_foreign_routine_adherence_strips_every_foreign_reference(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seeded_shelf(app_client, token_a)
    await _seeded_shelf(app_client, token_b)
    await _generate(app_client, token_a)
    await _generate(app_client, token_b)

    async with get_sessionmaker()() as session:
        foreign_routine = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_b,
                Routine.kind == "morning",
            )
        )).scalar_one()
        foreign_step = (await session.execute(
            select(RoutineStep).where(
                RoutineStep.routine_id == foreign_routine.id,
            ).limit(1)
        )).scalar_one()
        malformed = RoutineAdherence(
            account_id=account_a,
            routine_id=foreign_routine.id,
            slot=foreign_step.slot,
            step_id=foreign_step.id,
            done_on=date(2026, 9, 20),
            completed=True,
        )
        session.add(malformed)
        await session.commit()

    async with get_sessionmaker()() as session:
        export = await build_export(session, account_a)

    encoded = json.dumps(export, sort_keys=True)
    assert str(foreign_routine.id) not in encoded
    assert str(foreign_step.id) not in encoded
    history = export["domains"]["routines"]["routine_history"]
    row = next(
        item for item in history["unattributed"]["adherence"]
        if item["account_id"] == str(account_a)
    )
    assert row["routine_id"] is None
    assert row["step_id"] is None
    assert row["invariant"] == "adherence_routine_ownership_invalid"


async def test_export_is_pure_read_and_inactive_member_history_is_retained(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)

    deactivated = await app_client.patch(
        f"/api/v2/family-circle/profiles/{member}",
        headers=auth(token),
        json={"active": False},
    )
    assert deactivated.status_code == 200, deactivated.text

    async with get_sessionmaker()() as session:
        legacy_before = (await session.execute(
            select(Routine.id, Routine.household_subject_id).where(
                Routine.account_id == account_id,
                Routine.household_subject_id.is_(None),
            )
        )).all()
        export = await build_export(session, account_id)
        legacy_after = (await session.execute(
            select(Routine.id, Routine.household_subject_id).where(
                Routine.account_id == account_id,
                Routine.household_subject_id.is_(None),
            )
        )).all()

    assert legacy_after == legacy_before
    assert export["domains"]["routines"]["routine_history"]["by_subject"][member]["routines"]
