"""V3-03.13 explicit Care experience feedback contract."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from app.domains.identity.models import Account
from app.domains.inventory.models import InventoryAttribute, InventoryItem
from app.domains.profile.models import AppearanceProfile, ProfileAttribute, UserConstraint
from app.domains.progress.models import FeedbackEvent, MemoryFact
from app.domains.routines.models import CareExperienceFeedback, RoutineAdherence, RoutineRecommendationRun
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.journey import ok
from tests.test_domain_privacy_integration import _seeded_account

pytestmark = pytest.mark.asyncio


async def _post(client, token, *, subject_id: str, subject_type: str = "product", **values):
    body = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "dimension": values.pop("dimension", "comfort"),
        "sentiment": values.pop("sentiment", "negative"),
        **values,
    }
    return await client.post(
        "/api/v2/routines/experience-feedback", headers=auth(token), json=body,
    )


async def _care_side_effect_state(account_id: uuid.UUID) -> dict[str, object]:
    """Snapshot Care state through each model's real ownership relationship."""
    factory = get_sessionmaker()
    async with factory() as session:
        inventory_controls = (await session.execute(
            select(InventoryAttribute)
            .join(InventoryItem, InventoryItem.id == InventoryAttribute.item_id)
            .where(
                InventoryItem.account_id == account_id,
                InventoryAttribute.key.in_(("care_routine_paused", "care_routine_preferred")),
            )
            .order_by(InventoryAttribute.item_id, InventoryAttribute.key)
        )).scalars().all()
        care_effort = (await session.execute(
            select(ProfileAttribute)
            .join(AppearanceProfile, AppearanceProfile.id == ProfileAttribute.profile_id)
            .where(
                AppearanceProfile.account_id == account_id,
                ProfileAttribute.key == "care_routine_effort",
            )
            .order_by(ProfileAttribute.profile_id, ProfileAttribute.key)
        )).scalars().all()
        constraints = (await session.execute(
            select(UserConstraint)
            .join(AppearanceProfile, AppearanceProfile.id == UserConstraint.profile_id)
            .where(AppearanceProfile.account_id == account_id)
            .order_by(UserConstraint.kind, UserConstraint.value, UserConstraint.id)
        )).scalars().all()

        async def count(model) -> int:
            return int((await session.execute(
                select(func.count()).select_from(model).where(model.account_id == account_id)
            )).scalar_one())

        return {
            "inventory_controls": tuple(
                (
                    str(row.item_id), row.key, json.dumps(row.value, sort_keys=True),
                    row.source, row.confidence, row.verification_state,
                )
                for row in inventory_controls
            ),
            "care_effort": tuple(
                (
                    str(row.profile_id), row.key, json.dumps(row.value, sort_keys=True),
                    row.source, row.confidence, row.verification_state,
                )
                for row in care_effort
            ),
            "user_constraints": tuple(
                (row.kind, row.value, row.notes, row.active) for row in constraints
            ),
            "adherence": await count(RoutineAdherence),
            "recommendation_runs": await count(RoutineRecommendationRun),
            "memory": await count(MemoryFact),
            "progress_feedback": await count(FeedbackEvent),
        }


async def test_explicit_feedback_is_scoped_taxonomized_and_non_adaptive(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id, created = await _seeded_account(app_client, registered_supabase_user)
    beauty_id = created["inventory"]["beauty"][0]
    hair_id = created["inventory"]["hair"][0]
    wardrobe_id = created["inventory"]["wardrobe"][0]
    before = await _care_side_effect_state(account_id)

    response = await _post(
        app_client, token, subject_id=beauty_id,
        dimension="comfort", sentiment="negative",
        experienced_on="2026-08-14", note="  It felt different — verbatim.  ",
    )
    saved = ok(response, 200, 201)
    assert saved["feedback_version"] == "v3-03.13"
    assert saved["note"] == "  It felt different — verbatim.  "
    assert saved["experienced_on"] == "2026-08-14"
    assert saved["routine_kind"] is None and saved["routine_slot"] is None
    assert saved["affects_recommendations"] is False
    assert saved["creates_memory"] is False
    assert saved["changes_care_safety"] is False
    assert await _care_side_effect_state(account_id) == before

    hair = ok(await _post(
        app_client, token, subject_id=hair_id,
        dimension="overall_experience", sentiment="positive",
    ))
    assert hair["subject_id"] == hair_id
    assert ok(await _post(
        app_client, token, subject_id=beauty_id,
        dimension="routine_fit", sentiment="neutral",
    ))["id"] != saved["id"]

    assert (await _post(app_client, token, subject_id=wardrobe_id)).status_code == 404
    assert (await _post(
        app_client, token, subject_id=beauty_id, experienced_on="2099-01-01",
    )).status_code == 422
    assert (await app_client.post(
        "/api/v2/routines/experience-feedback", headers=auth(token), json={
            "subject_type": "product", "subject_id": beauty_id,
            "dimension": "comfort", "sentiment": "negative", "account_id": str(account_id),
        },
    )).status_code == 422
    assert (await _post(
        app_client, token, subject_id=beauty_id, dimension="unsafe",
    )).status_code == 422
    assert (await _post(
        app_client, token, subject_id=beauty_id, subject_type="wardrobe",
    )).status_code == 422
    assert (await _post(
        app_client, token, subject_id=beauty_id, sentiment="uncertain",
    )).status_code == 422

    after = await _care_side_effect_state(account_id)
    assert after == before


async def test_routine_step_provenance_get_delete_and_privacy_are_account_scoped(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a, created = await _seeded_account(app_client, registered_supabase_user)
    routine = created["routines"]["routines"][0]
    step_id = routine["steps"][0]["id"]
    assert not CareExperienceFeedback.__table__.c.subject_id.foreign_keys
    saved = ok(await _post(
        app_client, token_a, subject_type="routine_step", subject_id=step_id,
        dimension="ease_of_use", sentiment="neutral", note="A step note",
    ))
    assert saved["routine_kind"] == routine["kind"]
    assert saved["routine_slot"] == routine["steps"][0]["slot"]

    token_b, account_b = await registered_supabase_user()
    assert (await _post(
        app_client, token_b, subject_type="routine_step", subject_id=step_id,
    )).status_code == 404
    assert (await _post(app_client, token_b, subject_id=created["inventory"]["beauty"][0])).status_code == 404
    item_b = ok(await app_client.post(
        "/api/v2/inventory/items", headers=auth(token_b), json={
            "category": "beauty", "display_name": "B-owned cleanser",
            "subcategory": "cleanser", "details": {"product_type": "cleanser"},
        },
    ))
    feedback_b = ok(await _post(
        app_client, token_b, subject_id=item_b["id"], note="B-owned feedback",
    ))
    assert (await app_client.delete(
        f"/api/v2/routines/experience-feedback/{saved['id']}", headers=auth(token_b),
    )).status_code == 404

    await asyncio.sleep(0.01)
    newer = ok(await _post(
        app_client, token_a, subject_type="routine_step", subject_id=step_id,
        dimension="routine_fit", sentiment="positive", note="A newer step note",
    ))
    listing = ok(await app_client.get(
        f"/api/v2/routines/experience-feedback?subject_type=routine_step&subject_id={step_id}",
        headers=auth(token_a),
    ))
    rows = listing["feedback"]
    assert {row["id"] for row in rows} == {newer["id"], saved["id"]}
    assert newer["created_at"] >= saved["created_at"]
    assert [(row["created_at"], row["id"]) for row in rows] == sorted(
        ((row["created_at"], row["id"]) for row in rows), reverse=True,
    )
    exported = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token_a)))
    feedback = exported["domains"]["routines"]["experience_feedback"]
    assert len(feedback) == 2
    assert {row["note"] for row in feedback} == {"A step note", "A newer step note"}
    assert {row["account_id"] for row in feedback} == {str(account_a)}
    assert feedback_b["id"] not in {row["id"] for row in feedback}
    assert str(account_b) not in repr(feedback)

    before_delete = await _care_side_effect_state(account_a)
    assert ok(await app_client.delete(
        f"/api/v2/routines/experience-feedback/{saved['id']}", headers=auth(token_a),
    ))["deleted"] is True
    assert await _care_side_effect_state(account_a) == before_delete
    assert ok(await app_client.delete(
        f"/api/v2/routines/experience-feedback/{newer['id']}", headers=auth(token_a),
    ))["deleted"] is True
    assert ok(await app_client.get(
        "/api/v2/routines/experience-feedback", headers=auth(token_a),
    ))["feedback"] == []


async def test_product_cross_account_subject_is_not_revealed(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, _, created = await _seeded_account(app_client, registered_supabase_user)
    product_id = created["inventory"]["beauty"][0]
    token_b, _ = await registered_supabase_user()
    response = await _post(app_client, token_b, subject_id=product_id)
    assert response.status_code == 404
    assert ok(await app_client.get(
        "/api/v2/routines/experience-feedback", headers=auth(token_a),
    ))["feedback"] == []


async def test_feedback_account_delete_cascades_from_database(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id, created = await _seeded_account(app_client, registered_supabase_user)
    saved = ok(await _post(
        app_client, token, subject_id=created["inventory"]["beauty"][0], note="cascade me",
    ))
    factory = get_sessionmaker()
    async with factory() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        await session.delete(account)
        await session.commit()
    async with factory() as session:
        remaining = await session.get(CareExperienceFeedback, uuid.UUID(saved["id"]))
        assert remaining is None
