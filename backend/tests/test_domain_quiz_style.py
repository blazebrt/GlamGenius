"""Quiz + occasion styling (§1.8) regression coverage.

Covers the deterministic non-AI parts of §1.8: quiz submission, quiz
history, and occasion CRUD. AI-driven look generation is exercised in
the critical journey — this file focuses on the ownership guards and
schema-level assertions that must never be weakened.

Assertions:

    * ``GET /quiz/questions`` returns a versioned bank and every question
      declares an id, prompt and non-empty options list.
    * ``POST /quiz/submit`` rejects incomplete answer sets with 400 and
      lists exactly the missing / unexpected question ids.
    * ``POST /quiz/submit`` persists the submission with the current
      schema version and derived_style_vibe.
    * ``GET /quiz/latest`` reflects the newest submission per account.
    * ``GET /quiz/history`` is scoped: account B never sees A's history.
    * ``POST /occasions`` rejects unsupported occasion_keys and unknown
      dress codes.
    * ``GET /occasions/{id}`` returns 404 for another account's occasion
      (no cross-account probing).
    * ``PATCH`` updates fields in place and preserves ownership.
    * ``PATCH ... status=archived`` and ``GET /occasions`` default hides
      archived; ``?include_archived=true`` includes them.
"""
from __future__ import annotations

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------

async def test_quiz_questions_are_versioned_and_have_options(app_client, db_clean):
    resp = await app_client.get("/api/v2/quiz/questions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "quiz.v1"
    assert len(body["questions"]) >= 3
    for question in body["questions"]:
        assert question["id"] and question["prompt"]
        assert isinstance(question["options"], list) and len(question["options"]) >= 2
        for option in question["options"]:
            assert option["value"] and option["label"]


ALL_ANSWERS = {
    "vibe": "polished",
    "occasion_focus": "work",
    "colour_energy": "neutral",
    "silhouette": "structured",
    "care_time": "under_20",
}


async def test_quiz_submit_rejects_incomplete_answers(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token),
        json={"answers": {"vibe": "polished"}},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "quiz_answers_invalid"
    # All the missing question ids must be named — vague error is not enough.
    assert set(detail["missing"]) == {
        "occasion_focus",
        "colour_energy",
        "silhouette",
        "care_time",
    }
    assert detail["unexpected"] == []


async def test_quiz_submit_rejects_unexpected_answers(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    payload = dict(ALL_ANSWERS)
    payload["stray_key"] = "hello"
    resp = await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token),
        json={"answers": payload},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["unexpected"] == ["stray_key"]


async def test_quiz_submit_persists_and_derives_style_vibe(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token),
        json={"answers": ALL_ANSWERS},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["schema_version"] == "quiz.v1"
    assert body["derived_style_vibe"] == "polished"
    assert body["answers"] == ALL_ANSWERS


async def test_quiz_latest_returns_most_recent(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token),
        json={"answers": ALL_ANSWERS},
    )
    second = dict(ALL_ANSWERS)
    second["vibe"] = "classic"
    await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token),
        json={"answers": second},
    )
    resp = await app_client.get("/api/v2/quiz/latest", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["submission"]["derived_style_vibe"] == "classic"


async def test_quiz_history_is_scoped_to_account(
    app_client, db_clean, registered_supabase_user
):
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()

    await app_client.post(
        "/api/v2/quiz/submit",
        headers=auth(token_a),
        json={"answers": ALL_ANSWERS},
    )
    hist_a = await app_client.get("/api/v2/quiz/history", headers=auth(token_a))
    hist_b = await app_client.get("/api/v2/quiz/history", headers=auth(token_b))

    assert len(hist_a.json()["submissions"]) == 1
    assert hist_b.json()["submissions"] == []


async def test_quiz_submit_without_token_is_unauthorised(app_client, db_clean):
    resp = await app_client.post(
        "/api/v2/quiz/submit",
        json={"answers": ALL_ANSWERS},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Occasions
# ---------------------------------------------------------------------------

async def test_create_occasion_rejects_unsupported_key(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/occasions",
        headers=auth(token),
        json={"occasion_key": "underwater_ball", "title": "Silly"},
    )
    # Pydantic validator emits 422 for unknown occasion_key.
    assert resp.status_code == 422


async def test_create_occasion_rejects_unknown_dress_code(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/occasions",
        headers=auth(token),
        json={
            "occasion_key": "office",
            "title": "Board meeting",
            "dress_code": "steampunk",
        },
    )
    assert resp.status_code == 422


async def test_owned_occasion_returns_404_for_other_account(
    app_client, db_clean, registered_supabase_user
):
    token_a, _ = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/occasions",
        headers=auth(token_a),
        json={"occasion_key": "office", "title": "Morning meeting"},
    )
    assert resp.status_code == 200, resp.text
    occasion_id = resp.json()["id"]

    resp_b = await app_client.get(
        f"/api/v2/occasions/{occasion_id}", headers=auth(token_b)
    )
    assert resp_b.status_code == 404


async def test_patch_occasion_updates_and_archives(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    create = await app_client.post(
        "/api/v2/occasions",
        headers=auth(token),
        json={"occasion_key": "everyday", "title": "Walk"},
    )
    occasion_id = create.json()["id"]

    updated = await app_client.patch(
        f"/api/v2/occasions/{occasion_id}",
        headers=auth(token),
        json={"title": "Sunday market", "notes": "farmer's market"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Sunday market"
    assert body["notes"] == "farmer's market"

    # Archive.
    await app_client.patch(
        f"/api/v2/occasions/{occasion_id}",
        headers=auth(token),
        json={"status": "archived"},
    )

    # Default listing hides the archived occasion.
    active = await app_client.get("/api/v2/occasions", headers=auth(token))
    ids_active = [row["id"] for row in active.json()["occasions"]]
    assert occasion_id not in ids_active

    including = await app_client.get(
        "/api/v2/occasions?include_archived=true", headers=auth(token)
    )
    ids_included = [row["id"] for row in including.json()["occasions"]]
    assert occasion_id in ids_included


async def test_occasion_types_catalogue_is_stable(app_client, db_clean):
    resp = await app_client.get("/api/v2/style/occasion-types")
    assert resp.status_code == 200
    body = resp.json()
    keys = {row["key"] for row in body["occasions"]}
    # The canonical occasion set must include everyday + office at minimum.
    assert {"everyday", "office"}.issubset(keys)
    # Dress codes are exposed with formality levels.
    for row in body["dress_codes"]:
        assert row["key"] and isinstance(row["formality"], int)
