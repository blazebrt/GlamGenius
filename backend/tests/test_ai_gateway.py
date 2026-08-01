"""The trust reset.

Every test here exists because the old code did the opposite: it invented a
result, showed it as real, and charged the user for it.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app.domains.ai_gateway.models import AI_STATUS_FAILED, AI_STATUS_SUCCEEDED, AIRun
from app.domains.ai_gateway.providers import gemini
from app.shared.database import sql
from tests.conftest import auth, png_bytes, valid_analysis_payload

import base64

IMAGE_B64 = base64.b64encode(png_bytes()).decode()


def test_prompt_does_not_request_unversioned_appearance_scores():
    from ai import COACH_JSON_SCHEMA

    assert "wellness_scores" not in COACH_JSON_SCHEMA
    assert "overall_score" not in COACH_JSON_SCHEMA


async def _scans_used(user_id: str) -> int:
    from database import db

    doc = await db.users.find_one({"id": user_id})
    return int((doc or {}).get("scans_used_this_month") or 0)


async def _latest_run() -> AIRun | None:
    factory = sql.get_sessionmaker()
    async with factory() as session:
        stmt = select(AIRun).order_by(AIRun.created_at.desc()).limit(1)
        return (await session.execute(stmt)).scalar_one_or_none()


# --- Failure never fabricates ----------------------------------------------


@pytest.mark.parametrize(
    "failure,expected_type",
    [
        (gemini.ProviderNotConfigured("no key"), "provider_not_configured"),
        (gemini.ProviderTimeout("too slow"), "provider_timeout"),
        (gemini.ProviderCallFailed("upstream 500"), "provider_error"),
    ],
)
async def test_provider_failure_returns_structured_error(
    app_client, make_user, fake_provider, failure, expected_type
):
    user, token = await make_user()
    fake_provider.raises = failure

    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "ANALYSIS_UNAVAILABLE"
    assert detail["allowance_consumed"] is False
    assert detail["failure_type"] == expected_type
    assert detail["guidance"], "the user must be told what to do next"

    # The whole point: no invented analysis anywhere in the response.
    body = response.text.lower()
    for invented in ("wheatish", "deep teal", "mustard", "combination"):
        assert invented not in body


async def test_invalid_json_from_provider_is_a_failure(
    app_client, make_user, fake_provider
):
    user, token = await make_user()
    fake_provider.text = "Sure! Here is your analysis: not actually JSON {{{"

    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["failure_type"] == "invalid_json"


async def test_schema_violating_response_is_a_failure(
    app_client, make_user, fake_provider
):
    """Well-formed JSON that is missing the substance must not reach the user."""
    payload = valid_analysis_payload()
    payload["observations"] = []  # no observations is not an analysis
    payload["fashion"]["best_clothing_colors"] = []  # nor is no colour advice
    fake_provider.text = json.dumps(payload)

    user, token = await make_user()
    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["failure_type"] == "schema_validation_failed"


async def test_allowance_is_not_consumed_when_the_provider_fails(
    app_client, make_user, fake_provider
):
    """The single most important test in this phase."""
    user, token = await make_user()
    before = await _scans_used(user["id"])

    fake_provider.raises = gemini.ProviderCallFailed("upstream exploded")
    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )
    assert response.status_code == 503

    after = await _scans_used(user["id"])
    assert after == before, "a failed analysis must not cost the user a check"


async def test_profile_is_not_written_when_analysis_fails(
    app_client, make_user, fake_provider
):
    """Invented facts used to be saved onto the user permanently."""
    from database import db

    user, token = await make_user()
    fake_provider.raises = gemini.ProviderTimeout("slow")

    await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    doc = await db.users.find_one({"id": user["id"]})
    assert not doc.get("skin_tone")
    assert not doc.get("undertone")
    assert not doc.get("face_shape")


async def test_no_scan_row_is_stored_on_failure(app_client, make_user, fake_provider):
    from database import db

    user, token = await make_user()
    fake_provider.raises = gemini.ProviderCallFailed("nope")

    await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    assert await db.scans.count_documents({"user_id": user["id"]}) == 0


# --- Failures are recorded --------------------------------------------------


async def test_failed_run_is_recorded_with_its_reason(
    app_client, make_user, fake_provider
):
    user, token = await make_user()
    fake_provider.raises = gemini.ProviderTimeout("too slow")

    await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    run = await _latest_run()
    assert run is not None
    assert run.status == AI_STATUS_FAILED
    assert run.failure_type == "provider_timeout"
    assert run.allowance_consumed is False
    assert run.validation_passed is False
    assert run.prompt_version
    assert run.schema_version


# --- Success path -----------------------------------------------------------


async def test_valid_response_succeeds_and_is_recorded(
    app_client, make_user, fake_provider
):
    fake_provider.text = json.dumps(valid_analysis_payload())
    user, token = await make_user()

    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    assert response.status_code == 200, response.text
    analysis = response.json()["analysis"]

    provenance = analysis["meta"]["provenance"]
    assert provenance["model_version"] == "fake-model"
    assert provenance["prompt_version"]
    assert provenance["schema_version"]
    assert provenance["verification_status"] == "unverified"
    assert provenance["confidence"] == pytest.approx(0.8)
    assert provenance["ai_run_id"]

    run = await _latest_run()
    assert run.status == AI_STATUS_SUCCEEDED
    assert run.validation_passed is True
    assert run.latency_ms is not None
    assert run.input_tokens == 1200
    assert run.output_tokens == 800
    assert run.estimated_cost_usd is not None and run.estimated_cost_usd > 0

    assert await _scans_used(user["id"]) == 1


async def test_usage_ledger_records_the_consumed_check(
    app_client, make_user, fake_provider
):
    from app.domains.entitlements.models import UsageLedgerEntry

    fake_provider.text = json.dumps(valid_analysis_payload())
    user, token = await make_user()

    await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(UsageLedgerEntry).where(
                        UsageLedgerEntry.feature == "scan_analyze"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(r.quantity == 1 and r.reason == "analysis_succeeded" for r in rows)


async def test_out_of_vocabulary_profile_values_are_not_stored(
    app_client, make_user, fake_provider
):
    """An odd one-off answer is shown but must not become a stored fact."""
    from database import db

    payload = valid_analysis_payload()
    payload["profile"]["skin_tone"] = "iridescent lavender"
    payload["profile"]["undertone"] = "warm"
    fake_provider.text = json.dumps(payload)

    user, token = await make_user()
    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": IMAGE_B64, "scan_type": "face"},
        headers=auth(token),
    )
    assert response.status_code == 200

    # Still shown to the user...
    assert (
        response.json()["analysis"]["profile"]["skin_tone"] == "iridescent lavender"
    )
    # ...but not written to the profile, while the valid one is.
    doc = await db.users.find_one({"id": user["id"]})
    assert doc.get("skin_tone") is None
    assert doc.get("undertone") == "warm"


# --- Oversized input --------------------------------------------------------


async def test_oversized_image_payload_is_rejected_before_the_provider(
    app_client, make_user, fake_provider, monkeypatch
):
    import routes.scan as scan_routes

    monkeypatch.setattr(scan_routes, "MAX_IMAGE_BASE64_CHARS", 100)
    user, token = await make_user()

    response = await app_client.post(
        "/api/scan/analyze",
        json={"image_base64": "A" * 500, "scan_type": "face"},
        headers=auth(token),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert fake_provider.calls == 0, "we must not pay to reject an oversized upload"


# --- Unit-level gateway behaviour ------------------------------------------


async def test_parse_json_tolerates_code_fences():
    from app.domains.ai_gateway.gateway import parse_json

    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('```\n{"a": 2}\n```') == {"a": 2}
    assert parse_json('{"a": 3}') == {"a": 3}


async def test_parse_json_rejects_a_bare_array():
    from app.domains.ai_gateway.gateway import parse_json

    with pytest.raises(ValueError):
        parse_json("[1, 2, 3]")


async def test_provider_timeout_is_raised_not_swallowed(monkeypatch):
    """A hanging provider must surface as a timeout, not hang the request."""
    from app.config import AI_TIMEOUT_SECONDS  # noqa: F401

    async def _never_returns(*args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(gemini, "get_client", lambda: object())
    monkeypatch.setattr("app.domains.ai_gateway.providers.gemini.asyncio.to_thread", _never_returns)
    monkeypatch.setattr("app.domains.ai_gateway.providers.gemini.AI_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(gemini.ProviderTimeout):
        await gemini.generate("prompt", "system")
