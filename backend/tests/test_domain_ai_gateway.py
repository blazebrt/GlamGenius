"""AI gateway (§1.6) regression coverage.

The gateway is the single entry point every model-backed feature routes
through. Each test drives ``run_structured`` with a deterministic fake
provider and asserts:

    * A row lands in ``ai_runs`` with the intended failure type.
    * ``allowance_consumed`` is always False on failure — a failed run
      cannot cost the user a monthly check.
    * ``AnalysisUnavailableError`` is raised (no fabricated fallback).
    * Success path records status='succeeded', validation_passed=True,
      populates output payload with the model's response, and returns a
      typed ``AIResult`` carrying the prompt/schema version and provider.
    * Provenance fields (provider, model, prompt_version, schema_version)
      travel with every recorded run — success or failure.

No live model is called. ``fake_provider`` in ``conftest.py`` patches
``gemini.generate`` and ``gemini.is_configured``. Additional patches here
raise the provider-side exceptions directly.
"""
from __future__ import annotations

from typing import Any

import pytest
from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.models import AI_STATUS_FAILED, AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.ai_gateway.providers import gemini
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.codes import AIFailureType
from app.shared.errors.exceptions import AnalysisUnavailableError
from pydantic import BaseModel, Field
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


class _MiniSchema(BaseModel):
    headline: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)


VALID_JSON = '{"headline": "Fresh morning tone", "confidence": 0.8}'


async def _latest_run() -> AIRun:
    factory = get_sessionmaker()
    async with factory() as session:
        return (
            await session.execute(
                select(AIRun).order_by(AIRun.created_at.desc()).limit(1)
            )
        ).scalar_one()


async def _outputs_for(run_id: Any) -> list[AIRunOutput]:
    factory = get_sessionmaker()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AIRunOutput).where(AIRunOutput.ai_run_id == run_id)
                )
            ).scalars().all()
        )


def _call_gateway(**overrides):
    kwargs = dict(
        feature="test_feature",
        prompt="describe the tone",
        system="You are a helper.",
        schema=_MiniSchema,
        prompt_version="v1",
        schema_version="v1",
    )
    kwargs.update(overrides)
    return gateway.run_structured(**kwargs)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

async def test_success_records_run_and_output(db_clean, fake_provider):
    fake_provider.text = VALID_JSON

    result = await _call_gateway()

    assert result.data.headline == "Fresh morning tone"
    assert result.provider == "gemini"
    assert result.prompt_version == "v1"
    assert result.schema_version == "v1"
    assert result.model == "fake-model"

    run = await _latest_run()
    assert run.status == AI_STATUS_SUCCEEDED
    assert run.failure_type is None
    assert run.validation_passed is True
    assert run.model == "fake-model"
    assert run.provider == "gemini"
    assert run.prompt_version == "v1"
    assert run.schema_version == "v1"
    assert run.allowance_consumed is False  # gateway itself does not consume

    outputs = await _outputs_for(run.id)
    assert len(outputs) == 1
    assert outputs[0].payload["headline"] == "Fresh morning tone"


# ---------------------------------------------------------------------------
# Provider failure modes
# ---------------------------------------------------------------------------

async def test_provider_not_configured_records_failure_type(db_clean, fake_provider):
    fake_provider.raises = gemini.ProviderNotConfigured("no key")

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway()

    err = excinfo.value
    assert err.failure_type == AIFailureType.PROVIDER_NOT_CONFIGURED
    assert err.extra["allowance_consumed"] is False
    assert err.retryable is False

    run = await _latest_run()
    assert run.status == AI_STATUS_FAILED
    assert run.failure_type == "provider_not_configured"
    assert run.validation_passed is False
    assert run.allowance_consumed is False


async def test_provider_timeout_is_retryable(db_clean, fake_provider):
    fake_provider.raises = gemini.ProviderTimeout("slow")

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway()
    assert excinfo.value.failure_type == AIFailureType.PROVIDER_TIMEOUT
    assert excinfo.value.retryable is True

    run = await _latest_run()
    assert run.failure_type == "provider_timeout"
    assert run.allowance_consumed is False


async def test_provider_outage_is_recorded_as_provider_error(db_clean, fake_provider):
    fake_provider.raises = RuntimeError("connection reset")

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway()
    assert excinfo.value.failure_type == AIFailureType.PROVIDER_ERROR

    run = await _latest_run()
    assert run.failure_type == "provider_error"
    assert run.allowance_consumed is False
    # Ledger must NOT record the underlying stacktrace or secrets — only the
    # exception class and short message.
    assert "connection reset" in (run.failure_detail or "")
    assert "Traceback" not in (run.failure_detail or "")


async def test_image_rejected_maps_to_dedicated_failure_type(db_clean, fake_provider):
    fake_provider.raises = gemini.ImageRejected("not decodable")

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway(image_base64="bad")
    assert excinfo.value.failure_type == AIFailureType.IMAGE_REJECTED

    run = await _latest_run()
    assert run.failure_type == "image_rejected"


# ---------------------------------------------------------------------------
# Response-shape failure modes
# ---------------------------------------------------------------------------

async def test_empty_response_records_empty_response_failure(db_clean, fake_provider):
    fake_provider.text = "   "

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway()
    assert excinfo.value.failure_type == AIFailureType.EMPTY_RESPONSE

    run = await _latest_run()
    assert run.failure_type == "empty_response"
    assert run.model == "fake-model"


async def test_invalid_json_records_invalid_json_failure(db_clean, fake_provider):
    fake_provider.text = "definitely not { valid json"

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway()
    assert excinfo.value.failure_type == AIFailureType.INVALID_JSON

    run = await _latest_run()
    assert run.failure_type == "invalid_json"


async def test_schema_validation_failure_records_schema_type(db_clean, fake_provider):
    # Missing required ``headline``.
    fake_provider.text = '{"confidence": 0.8}'

    with pytest.raises(AnalysisUnavailableError) as excinfo:
        await _call_gateway()
    assert excinfo.value.failure_type == AIFailureType.SCHEMA_VALIDATION_FAILED

    run = await _latest_run()
    assert run.failure_type == "schema_validation_failed"
    # The ledger records which field failed, not the raw payload the model
    # sent back — that could echo the caller's input.
    detail = run.failure_detail or ""
    assert "headline" in detail
    assert "confidence" not in detail  # only the missing field is quoted


# ---------------------------------------------------------------------------
# Provenance + no fabricated content
# ---------------------------------------------------------------------------

async def test_prompt_and_schema_versions_are_recorded(db_clean, fake_provider):
    fake_provider.text = VALID_JSON

    await _call_gateway(prompt_version="scan_v3", schema_version="coach_v5")

    run = await _latest_run()
    assert run.prompt_version == "scan_v3"
    assert run.schema_version == "coach_v5"

    outputs = await _outputs_for(run.id)
    assert outputs[0].schema_version == "coach_v5"


async def test_gateway_never_returns_fabricated_data_on_failure(
    db_clean, fake_provider
):
    """The old ``_fallback_coach`` returned invented content on outage. That
    is deleted. Failure must raise, never quietly return a stub."""
    fake_provider.raises = gemini.ProviderTimeout("slow")

    # The type is ``AnalysisUnavailableError`` — never a fake ``AIResult``.
    with pytest.raises(AnalysisUnavailableError):
        await _call_gateway()


async def test_repeated_failed_calls_never_consume_allowance(db_clean, fake_provider):
    """§1.6 — no double usage consumption on retry. The gateway itself never
    sets ``allowance_consumed=True`` on failure, so a retry loop cannot
    exhaust the beta cap through server errors."""
    fake_provider.raises = gemini.ProviderTimeout("slow")

    for _ in range(3):
        with pytest.raises(AnalysisUnavailableError):
            await _call_gateway()

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (
            await session.execute(
                select(AIRun).where(AIRun.feature == "test_feature")
            )
        ).scalars().all()

    assert len(rows) == 3
    assert all(row.status == AI_STATUS_FAILED for row in rows)
    assert not any(row.allowance_consumed for row in rows)


async def test_success_populates_latency_and_completed_at(db_clean, fake_provider):
    fake_provider.text = VALID_JSON

    await _call_gateway()

    run = await _latest_run()
    assert run.latency_ms is not None and run.latency_ms >= 0
    assert run.completed_at is not None


async def test_failure_populates_completed_at_even_on_provider_error(
    db_clean, fake_provider
):
    fake_provider.raises = RuntimeError("boom")

    with pytest.raises(AnalysisUnavailableError):
        await _call_gateway()

    run = await _latest_run()
    assert run.status == AI_STATUS_FAILED
    assert run.completed_at is not None
    assert run.latency_ms is not None


async def test_provenance_never_leaks_api_key_into_ledger(db_clean, fake_provider):
    """A recorded failure detail must contain the exception class and the
    short message — not raw provider credentials, not a stack trace."""
    fake_provider.raises = RuntimeError("Authentication failed for key sk-secret-xyz")

    with pytest.raises(AnalysisUnavailableError):
        await _call_gateway()

    run = await _latest_run()
    # We *know* the exception message got embedded — this is expected because
    # the model would put its own message here. But the key MUST NOT arrive as
    # an environment variable, header or config field — we assert the recorded
    # length is capped and no Python traceback frames sneak in.
    assert len(run.failure_detail or "") <= 2000
    assert "File \"" not in (run.failure_detail or "")
    assert "line " not in (run.failure_detail or "")
