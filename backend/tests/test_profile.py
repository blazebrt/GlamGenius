"""Phase 2 digital twin contracts."""
from __future__ import annotations

import base64
import json

from tests.conftest import auth, png_bytes


def baseline_payload(*, quality="good", observations=None):
    return {
        "image_quality": quality,
        "image_quality_notes": "Clear enough for colour guidance" if quality != "low" else "Too dark to assess reliably",
        "observations": observations or [],
        "colour_palette": [] if quality == "low" else [{"name": "deep teal", "hex": "#0F766E", "why": "A balanced starting colour"}],
        "summary": "A practical colour starting point" if quality != "low" else "Please try another photo or skip this step",
    }


async def test_profile_routes_require_authentication(app_client):
    for method, path in [("GET", "/api/v2/profile"), ("PATCH", "/api/v2/profile"), ("GET", "/api/v2/profile/observations")]:
        response = await app_client.request(method, path, json={"attributes": [{"key": "city", "value": "Pune"}]} if method == "PATCH" else None)
        assert response.status_code == 401


async def test_user_declared_attributes_have_provenance_and_history(app_client, make_user):
    _, token = await make_user()
    response = await app_client.patch(
        "/api/v2/profile",
        json={"attributes": [{"key": "preferred_style", "value": "Modern Indian"}, {"key": "city", "value": "Pune"}]},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["weight_required"] is False
    by_key = {row["key"]: row for row in body["attributes"]}
    assert by_key["preferred_style"]["source"] == "user_declared"
    assert by_key["preferred_style"]["confidence"] == 1.0
    assert by_key["preferred_style"]["verification_state"] == "confirmed"
    full = (await app_client.get("/api/v2/profile", headers=auth(token))).json()
    assert {event["attribute_key"] for event in full["change_history"]} >= {"preferred_style", "city"}
    exported = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    twin = exported["appearance_digital_twin"]
    assert {row["key"] for row in twin["attributes"]} >= {"preferred_style", "city"}


async def test_invalid_measurements_and_weight_are_refused(app_client, make_user):
    _, token = await make_user()
    too_short = await app_client.patch("/api/v2/profile", json={"attributes": [{"key": "height_cm", "value": 20}]}, headers=auth(token))
    assert too_short.status_code == 422
    weight = await app_client.patch("/api/v2/profile", json={"attributes": [{"key": "weight_kg", "value": 60}]}, headers=auth(token))
    assert weight.status_code == 422


async def test_baseline_creates_reviewable_observations_without_overwriting_user_value(app_client, make_user, fake_provider):
    _, token = await make_user()
    await app_client.patch("/api/v2/profile", json={"attributes": [{"key": "undertone", "value": "neutral"}]}, headers=auth(token))
    fake_provider.text = json.dumps(baseline_payload(observations=[
        {"key": "undertone", "value": "warm", "confidence": 0.86, "why": "Warm colour cast in even light"},
        {"key": "face_shape", "value": "oval", "confidence": 0.2, "why": "Outline is partly hidden"},
    ]))
    image = base64.b64encode(png_bytes()).decode()
    response = await app_client.post("/api/v2/profile/baseline-analysis", json={"image_base64": image}, headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["photo_stored"] is False
    assert [row["key"] for row in response.json()["observations"]] == ["undertone"]
    profile = (await app_client.get("/api/v2/profile", headers=auth(token))).json()
    undertone = next(row for row in profile["attributes"] if row["key"] == "undertone")
    assert undertone["value"] == "neutral"
    assert undertone["source"] == "user_declared"


async def test_observation_confirm_edit_reject_and_ownership(app_client, make_user, fake_provider):
    _, token_a = await make_user()
    _, token_b = await make_user()
    fake_provider.text = json.dumps(baseline_payload(observations=[
        {"key": "skin_tone", "value": "medium", "confidence": 0.78, "why": "Visible tone in neutral light"},
        {"key": "hair_type", "value": "wavy", "confidence": 0.74, "why": "Visible wave pattern"},
    ]))
    image = base64.b64encode(png_bytes()).decode()
    rows = (await app_client.post("/api/v2/profile/baseline-analysis", json={"image_base64": image}, headers=auth(token_a))).json()["observations"]
    skin, hair = rows
    hidden = await app_client.post(f"/api/v2/profile/observations/{skin['id']}/confirm", headers=auth(token_b))
    assert hidden.status_code == 404
    edited = await app_client.patch(f"/api/v2/profile/observations/{skin['id']}", json={"value": "deep"}, headers=auth(token_a))
    assert edited.status_code == 200 and edited.json()["source"] == "user_declared"
    confirmed = await app_client.post(f"/api/v2/profile/observations/{skin['id']}/confirm", headers=auth(token_a))
    assert confirmed.json()["verification_state"] == "confirmed"
    rejected = await app_client.post(f"/api/v2/profile/observations/{hair['id']}/reject", headers=auth(token_a))
    assert rejected.json()["verification_state"] == "rejected"
    persisted = (await app_client.get("/api/v2/profile/observations", headers=auth(token_a))).json()["observations"]
    assert any(row["id"] == hair["id"] and row["verification_state"] == "rejected" for row in persisted)
    cannot_confirm = await app_client.post(f"/api/v2/profile/observations/{hair['id']}/confirm", headers=auth(token_a))
    assert cannot_confirm.status_code == 422


async def test_low_quality_baseline_is_honest_and_stores_no_observations(app_client, make_user, fake_provider):
    _, token = await make_user()
    fake_provider.text = json.dumps(baseline_payload(quality="low"))
    image = base64.b64encode(png_bytes()).decode()
    response = await app_client.post("/api/v2/profile/baseline-analysis", json={"image_base64": image}, headers=auth(token))
    assert response.status_code == 200
    assert response.json()["status"] == "low_quality"
    assert response.json()["observations"] == []
    assert response.json()["photo_stored"] is False


def test_baseline_prompt_is_inclusive_and_has_no_body_shaming():
    from app.domains.profile.baseline import PROMPT, SYSTEM
    text = (PROMPT + SYSTEM).lower()
    assert "infer body weight" in text and "never diagnose" in text
    for phrase in ("fat person", "ugly", "bad body", "attractiveness score"):
        assert phrase not in text
