"""Progressive onboarding state and recovery."""
from tests.conftest import auth


async def test_partial_onboarding_resumes(app_client, make_user):
    _, token = await make_user()
    initial = await app_client.get("/api/v2/onboarding/status", headers=auth(token))
    assert initial.status_code == 200
    assert initial.json()["current_step"] == "goal"
    saved = await app_client.post(
        "/api/v2/onboarding/step",
        json={"step": "goal", "data": {"current_goal": "Work"}},
        headers=auth(token),
    )
    assert saved.status_code == 200
    resumed = await app_client.get("/api/v2/onboarding/status", headers=auth(token))
    assert resumed.json()["answers"]["goal"]["current_goal"] == "Work"
    assert resumed.json()["current_step"] == "style_preferences"


async def test_optional_steps_can_be_skipped_and_minimum_flow_returns_result(app_client, make_user):
    _, token = await make_user()
    await app_client.post("/api/v2/onboarding/step", json={"step": "goal", "data": {"current_goal": "Everyday confidence"}}, headers=auth(token))
    for step in ("style_preferences", "fit_preferences", "lifestyle", "appearance_photo", "colour_palette", "confirm_attributes", "preview"):
        response = await app_client.post("/api/v2/onboarding/step", json={"step": step, "skipped": True}, headers=auth(token))
        assert response.status_code == 200, response.text
    completed = await app_client.post("/api/v2/onboarding/complete", headers=auth(token))
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["weight_required"] is False
    assert completed.json()["first_result"]["headline"]


async def test_goal_is_concrete_and_cannot_be_skipped(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post("/api/v2/onboarding/step", json={"step": "goal", "skipped": True}, headers=auth(token))
    assert response.status_code == 422


async def test_onboarding_state_never_accepts_photo_bytes_or_weight(app_client, make_user):
    _, token = await make_user()
    photo = await app_client.post("/api/v2/onboarding/step", json={"step": "appearance_photo", "data": {"image_base64": "secret-face"}}, headers=auth(token))
    assert photo.status_code == 422
    weight = await app_client.post("/api/v2/onboarding/step", json={"step": "fit_preferences", "data": {"weight_kg": 65}}, headers=auth(token))
    assert weight.status_code == 422
