"""V3-03.13 explicit Care experience feedback contract."""
from __future__ import annotations

import uuid

import pytest
from app.domains.inventory.models import InventoryAttribute
from app.domains.profile.models import ProfileAttribute
from app.domains.progress.models import FeedbackEvent, MemoryFact
from app.domains.routines.models import RoutineAdherence, RoutineRecommendationRun
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


async def _account_counts(account_id: uuid.UUID) -> dict[str, int]:
    factory = get_sessionmaker()
    models = {
        "inventory_attributes": InventoryAttribute,
        "profile_attributes": ProfileAttribute,
        "adherence": RoutineAdherence,
        "recommendation_runs": RoutineRecommendationRun,
        "memory": MemoryFact,
        "progress_feedback": FeedbackEvent,
    }
    async with factory() as session:
        return {
            name: int((await session.execute(
                select(func.count()).select_from(model).where(model.account_id == account_id)
            )).scalar_one())
            for name, model in models.items()
            if hasattr(model, "account_id")
        }


async def test_explicit_feedback_is_scoped_taxonomized_and_non_adaptive(
    app_client, db_clean, registered_supabase_user, fake_provider, storage,
):
    token, account_id, created = await _seeded_account(app_client, registered_supabase_user)
    beauty_id = created["inventory"]["beauty"][0]
    hair_id = created["inventory"]["hair"][0]
    wardrobe_id = created["inventory"]["wardrobe"][0]
    before = await _account_counts(account_id)

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

    after = await _account_counts(account_id)
    assert after == before


async def test_routine_step_provenance_get_delete_and_privacy_are_account_scoped(
    app_client, db_clean, registered_supabase_user, fake_provider, storage,
):
    token_a, account_a, created = await _seeded_account(app_client, registered_supabase_user)
    routine = created["routines"]["routines"][0]
    step_id = routine["steps"][0]["id"]
    saved = ok(await _post(
        app_client, token_a, subject_type="routine_step", subject_id=step_id,
        dimension="ease_of_use", sentiment="neutral", note="A step note",
    ))
    assert saved["routine_kind"] == routine["kind"]
    assert saved["routine_slot"] == routine["steps"][0]["slot"]

    token_b, account_b = await registered_supabase_user()
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

    listing = ok(await app_client.get(
        "/api/v2/routines/experience-feedback?subject_type=routine_step",
        headers=auth(token_a),
    ))
    assert [row["id"] for row in listing["feedback"]] == [saved["id"]]
    exported = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token_a)))
    feedback = exported["domains"]["routines"]["experience_feedback"]
    assert len(feedback) == 1
    assert feedback[0]["note"] == "A step note"
    assert feedback[0]["account_id"] == str(account_a)
    assert feedback_b["id"] not in {row["id"] for row in feedback}
    assert str(account_b) not in repr(feedback)

    assert ok(await app_client.delete(
        f"/api/v2/routines/experience-feedback/{saved['id']}", headers=auth(token_a),
    ))["deleted"] is True
    assert ok(await app_client.get(
        "/api/v2/routines/experience-feedback", headers=auth(token_a),
    ))["feedback"] == []


async def test_product_cross_account_subject_is_not_revealed(
    app_client, db_clean, registered_supabase_user, fake_provider, storage,
):
    token_a, _, created = await _seeded_account(app_client, registered_supabase_user)
    product_id = created["inventory"]["beauty"][0]
    token_b, _ = await registered_supabase_user()
    response = await _post(app_client, token_b, subject_id=product_id)
    assert response.status_code == 404
    assert ok(await app_client.get(
        "/api/v2/routines/experience-feedback", headers=auth(token_a),
    ))["feedback"] == []
