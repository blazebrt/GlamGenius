"""Step 11E privacy export: one persisted routine graph per human."""
from __future__ import annotations

import json
import uuid

import pytest
from app.domains.privacy import EXPORT_SCHEMA_VERSION
from app.domains.privacy.export import build_export
from app.domains.routines.models import Routine
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

    assert export["schema_version"] == EXPORT_SCHEMA_VERSION == "1.4"
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
