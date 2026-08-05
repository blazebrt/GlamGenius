"""§1.2 + §1.3 — consent and profile/onboarding through the real routes.

``test_domain_consent.py`` and ``test_domain_profile.py`` exercise the services
directly. This file covers the same ground through the API, plus the parts
neither reaches: consent source and history as the client sees them, revocation
actually blocking a later analysis, attribute validation at the boundary, and a
user correcting an observation the app made about them.

What this protects against
--------------------------
* A photo being analysed after the user withdrew consent.
* Consent being recorded without the version or the source, so it cannot be
  proved later what was agreed to and where.
* A repeated grant stacking rows or resetting history.
* An unknown consent type being accepted and silently ignored.
* An attribute the profile does not support being written anyway.
* An observation the app inferred outranking the user's own correction.
* One account reading or editing another's profile.
"""
from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import func, select

from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS, Consent
from app.domains.profile.models import AttributeObservation
from app.shared.database.sql import get_sessionmaker
from tests.conftest import auth, png_bytes


pytestmark = pytest.mark.asyncio


def _image() -> str:
    return base64.b64encode(png_bytes()).decode("ascii")


async def _set_consent(client, token, granted: bool):
    return await client.post(
        "/api/v2/consent",
        headers=auth(token),
        json={"consent_type": CONSENT_PHOTO_ANALYSIS, "granted": granted},
    )


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

async def test_consent_summary_starts_ungranted(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    body = (await app_client.get("/api/v2/consent", headers=auth(token))).json()

    assert body["photo_analysis"]["granted"] is False
    assert body["consent_version"], "the version being asked about must be stated"
    assert body["required"] is True


async def test_grant_records_version_and_source(
    app_client, db_clean, registered_supabase_user
):
    token, uid = await registered_supabase_user()

    resp = await _set_consent(app_client, token, True)

    assert resp.status_code == 200, resp.text
    assert resp.json()["photo_analysis"]["granted"] is True

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(Consent).where(Consent.account_id == uid)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].granted is True
    assert rows[0].version, "a consent without a version cannot be audited"
    assert rows[0].source == "app"
    assert rows[0].recorded_at is not None


async def test_consent_history_is_append_only(
    app_client, db_clean, registered_supabase_user
):
    """Grant, revoke, grant again: three rows, never an edit. The history is
    the evidence of what was agreed and when."""
    token, uid = await registered_supabase_user()

    for granted in (True, False, True):
        assert (await _set_consent(app_client, token, granted)).status_code == 200

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(Consent).where(Consent.account_id == uid).order_by(Consent.recorded_at)
        )).scalars().all()

    assert [row.granted for row in rows] == [True, False, True]
    summary = (await app_client.get("/api/v2/consent", headers=auth(token))).json()
    assert summary["photo_analysis"]["granted"] is True


async def test_revoked_consent_blocks_a_later_scan(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The whole point of the gate: withdrawing consent has to stop the next
    analysis, not just change a display flag."""
    token, _ = await registered_supabase_user()
    await _set_consent(app_client, token, True)

    allowed = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image(), "scan_type": "face"},
    )
    assert allowed.status_code == 201, allowed.text

    await _set_consent(app_client, token, False)

    refused = await app_client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": _image(), "scan_type": "face"},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "consent_missing"


async def test_repeated_grant_is_idempotent_for_the_caller(
    app_client, db_clean, registered_supabase_user
):
    """Tapping "I agree" twice must not change what the user sees, even though
    the ledger keeps both entries."""
    token, _ = await registered_supabase_user()

    first = (await _set_consent(app_client, token, True)).json()
    second = (await _set_consent(app_client, token, True)).json()

    assert first["photo_analysis"]["granted"] is True
    assert second["photo_analysis"]["granted"] is True
    assert second["consent_version"] == first["consent_version"]


async def test_unknown_consent_type_is_refused(
    app_client, db_clean, registered_supabase_user
):
    token, uid = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/consent",
        headers=auth(token),
        json={"consent_type": "sell_my_data", "granted": True},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "consent_type"

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(Consent.id)).where(Consent.account_id == uid)
        )).scalar_one()
    assert count == 0, "an unrecognised consent type must not be stored at all"


async def test_consent_is_scoped_to_the_account(
    app_client, db_clean, registered_supabase_user
):
    granting_token, _ = await registered_supabase_user()
    other_token, _ = await registered_supabase_user()
    await _set_consent(app_client, granting_token, True)

    other = (await app_client.get("/api/v2/consent", headers=auth(other_token))).json()

    assert other["photo_analysis"]["granted"] is False


async def test_consent_requires_authentication(app_client, db_clean):
    assert (await app_client.get("/api/v2/consent")).status_code == 401


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

async def test_profile_is_created_on_first_read_and_is_stable(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    first = (await app_client.get("/api/v2/profile", headers=auth(token))).json()
    second = (await app_client.get("/api/v2/profile", headers=auth(token))).json()

    assert first["id"] == second["id"], "reading twice must not fork the profile"
    assert "attributes" in first
    assert "change_history" in first


async def test_profile_update_persists_and_records_the_change(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    resp = await app_client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [
            {"key": "skin_tone", "value": "medium"},
            {"key": "favourite_colours", "value": ["deep teal", "ivory"]},
        ]},
    )

    assert resp.status_code == 200, resp.text
    stored = {row["key"]: row["value"] for row in resp.json()["attributes"]}
    assert stored["skin_tone"] == "medium"
    assert stored["favourite_colours"] == ["deep teal", "ivory"]

    history = (await app_client.get("/api/v2/profile", headers=auth(token))).json()
    assert history["change_history"], "an edit to yourself must be traceable"


async def test_updating_the_same_attribute_overwrites_rather_than_duplicates(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    await app_client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "skin_tone", "value": "medium"}]},
    )
    resp = await app_client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "skin_tone", "value": "deep"}]},
    )

    values = [row for row in resp.json()["attributes"] if row["key"] == "skin_tone"]
    assert len(values) == 1
    assert values[0]["value"] == "deep"


async def test_unsupported_profile_attribute_is_refused(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    resp = await app_client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [{"key": "body_fat_percentage", "value": 18}]},
    )

    assert resp.status_code == 422
    assert "body_fat_percentage" in resp.json()["detail"]["message"]


async def test_empty_attribute_list_is_refused(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.patch(
        "/api/v2/profile", headers=auth(token), json={"attributes": []}
    )
    assert resp.status_code == 422


async def test_attribute_registry_is_published_with_readiness(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    body = (await app_client.get(
        "/api/v2/profile/attributes", headers=auth(token)
    )).json()

    assert body["registry"], "the app must be told which attributes exist"
    assert {"key", "label", "section", "kind"} <= set(body["registry"][0])
    assert "readiness" in body
    assert body["weight_required"] is False, "weight is deliberately not asked for"


# ---------------------------------------------------------------------------
# Baseline observations and user correction
# ---------------------------------------------------------------------------

async def test_baseline_analysis_requires_consent(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/profile/baseline-analysis",
        headers=auth(token),
        json={"image_base64": _image()},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CONSENT_REQUIRED"
    assert resp.json()["detail"]["consent_type"] == CONSENT_PHOTO_ANALYSIS


async def test_user_correction_outranks_an_inferred_observation(
    app_client, db_clean, registered_supabase_user
):
    """The user is the authority on themselves. An observation the app made
    from a photo must yield to what they say."""
    token, uid = await registered_supabase_user()
    profile = (await app_client.get("/api/v2/profile", headers=auth(token))).json()

    factory = get_sessionmaker()
    async with factory() as session:
        observation = AttributeObservation(
            profile_id=uuid.UUID(profile["id"]),
            key="skin_tone",
            proposed_value="deep",
            confidence=0.55,
            why="Observed from the baseline photo.",
            source="photo_observed",
            verification_state="unverified",
        )
        session.add(observation)
        await session.commit()
        observation_id = observation.id

    listed = (await app_client.get(
        "/api/v2/profile/observations", headers=auth(token)
    )).json()
    assert any(row["id"] == str(observation_id) for row in listed["observations"])

    corrected = await app_client.patch(
        f"/api/v2/profile/observations/{observation_id}",
        headers=auth(token),
        json={"value": "medium"},
    )

    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["value"] == "medium"
    assert corrected.json()["source"] == "user_declared", (
        "a corrected observation must be attributed to the user, not the photo"
    )


async def test_observation_can_be_confirmed_or_rejected(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    profile = (await app_client.get("/api/v2/profile", headers=auth(token))).json()

    factory = get_sessionmaker()
    async with factory() as session:
        rows = [
            AttributeObservation(
                profile_id=uuid.UUID(profile["id"]),
                key=key,
                proposed_value=value,
                confidence=0.6,
                why="Observed from the baseline photo.",
                source="photo_observed",
                verification_state="unverified",
            )
            for key, value in (("hair_type", "wavy"), ("undertone", "cool"))
        ]
        session.add_all(rows)
        await session.commit()
        confirm_id, reject_id = rows[0].id, rows[1].id

    confirmed = await app_client.post(
        f"/api/v2/profile/observations/{confirm_id}/confirm", headers=auth(token)
    )
    rejected = await app_client.post(
        f"/api/v2/profile/observations/{reject_id}/reject", headers=auth(token)
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["verification_state"] == "confirmed"
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["verification_state"] == "rejected"

    # A confirmed observation becomes a profile attribute; a rejected one does not.
    attributes = {
        row["key"]: row["value"]
        for row in (await app_client.get(
            "/api/v2/profile", headers=auth(token)
        )).json()["attributes"]
    }
    assert attributes.get("hair_type") == "wavy"
    assert "undertone" not in attributes


async def test_another_accounts_observation_is_not_reachable(
    app_client, db_clean, registered_supabase_user
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    profile = (await app_client.get("/api/v2/profile", headers=auth(owner_token))).json()

    factory = get_sessionmaker()
    async with factory() as session:
        observation = AttributeObservation(
            profile_id=uuid.UUID(profile["id"]),
            key="skin_tone",
            proposed_value="deep",
            confidence=0.5,
            why="Observed from the baseline photo.",
            source="photo_observed",
            verification_state="unverified",
        )
        session.add(observation)
        await session.commit()
        observation_id = observation.id

    for method, path in (
        ("patch", f"/api/v2/profile/observations/{observation_id}"),
        ("post", f"/api/v2/profile/observations/{observation_id}/confirm"),
        ("post", f"/api/v2/profile/observations/{observation_id}/reject"),
    ):
        kwargs = {"headers": auth(intruder_token)}
        if method == "patch":
            kwargs["json"] = {"value": "light"}
        resp = await getattr(app_client, method)(path, **kwargs)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"


# ---------------------------------------------------------------------------
# Progressive onboarding
# ---------------------------------------------------------------------------

async def test_onboarding_saves_partially_and_resumes_where_it_stopped(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    started = (await app_client.get(
        "/api/v2/onboarding/status", headers=auth(token)
    )).json()
    assert started["status"] != "completed"
    assert started["current_step"] == started["steps"][0]

    await app_client.post(
        "/api/v2/onboarding/step",
        headers=auth(token),
        json={"step": "goal", "data": {"current_goal": "look_polished_at_work"}},
    )

    resumed = (await app_client.get(
        "/api/v2/onboarding/status", headers=auth(token)
    )).json()
    assert resumed["id"] == started["id"], "resuming must not start a new session"
    assert "goal" in resumed["completed_steps"]
    assert resumed["current_step"] != "goal"
    assert resumed["answers"]["goal"]["current_goal"] == "look_polished_at_work"


async def test_onboarding_refuses_fields_that_do_not_belong_to_the_step(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    resp = await app_client.post(
        "/api/v2/onboarding/step",
        headers=auth(token),
        json={"step": "goal", "data": {"height_cm": 175}},
    )

    assert resp.status_code == 422
    assert "height_cm" in resp.json()["detail"]["message"]


async def test_onboarding_cannot_complete_without_the_goal_step(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()

    resp = await app_client.post("/api/v2/onboarding/complete", headers=auth(token))

    assert resp.status_code == 422
    assert "goal" in resp.json()["detail"]["message"].lower()


async def test_completed_onboarding_refuses_further_steps(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    await app_client.post(
        "/api/v2/onboarding/step",
        headers=auth(token),
        json={"step": "goal", "data": {"current_goal": "look_polished_at_work"}},
    )
    completed = await app_client.post(
        "/api/v2/onboarding/complete", headers=auth(token)
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["first_result"], (
        "finishing onboarding must show the user something for their effort"
    )

    resp = await app_client.post(
        "/api/v2/onboarding/step",
        headers=auth(token),
        json={"step": "lifestyle", "data": {"city": "Mumbai"}},
    )
    assert resp.status_code == 422


async def test_onboarding_is_scoped_to_the_account(
    app_client, db_clean, registered_supabase_user
):
    first_token, _ = await registered_supabase_user()
    second_token, _ = await registered_supabase_user()
    await app_client.post(
        "/api/v2/onboarding/step",
        headers=auth(first_token),
        json={"step": "goal", "data": {"current_goal": "look_polished_at_work"}},
    )

    other = (await app_client.get(
        "/api/v2/onboarding/status", headers=auth(second_token)
    )).json()

    assert other["completed_steps"] == []
    assert other["answers"] == {}
