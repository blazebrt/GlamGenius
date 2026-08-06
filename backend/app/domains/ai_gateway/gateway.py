"""The AI gateway.

**One way in, one way out.** Nothing in the codebase calls a model directly any
more. Every call comes through :func:`run_structured`, which:

1. records the attempt in ``ai_runs`` before anything can go wrong
2. calls the provider with a timeout
3. parses the JSON
4. validates it against a versioned Pydantic schema
5. stores the validated payload in ``ai_run_outputs`` with its provenance
6. returns it — or raises :class:`AnalysisUnavailableError`

**It never invents a result.** The old ``_fallback_coach`` returned a fixed
"wheatish / warm / deep teal" analysis whenever the provider was missing or
threw, and the caller could not tell. That is deleted. Failure raises, the raise
carries specific guidance, and the caller's allowance is untouched.

**Recording uses its own database session** rather than the request's. An AI
call happened whether or not the surrounding request goes on to succeed, and the
ledger should say so. It also means V1 routes, which have no Postgres session,
need no plumbing changes to be recorded.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import AI_COST_PER_1K_INPUT_USD, AI_COST_PER_1K_OUTPUT_USD
from app.domains.ai_gateway.models import (
    AI_STATUS_FAILED,
    AI_STATUS_SUCCEEDED,
    VERIFICATION_UNVERIFIED,
    AIRun,
    AIRunOutput,
)
from app.domains.ai_gateway.providers import gemini
from app.domains.identity import service as identity
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.codes import AIFailureType
from app.shared.errors.exceptions import (
    DEFAULT_PHOTO_GUIDANCE,
    PROVIDER_DOWN_GUIDANCE,
    AnalysisUnavailableError,
)
from app.shared.observability.request_id import get_request_id

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class AIResult(Generic[T]):
    """A validated result and everything needed to trace where it came from."""

    data: T
    run_id: uuid.UUID
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    confidence: float | None
    latency_ms: int
    estimated_cost_usd: Decimal | None

    def provenance(self) -> dict[str, Any]:
        """The provenance envelope attached to anything shown to the user."""
        return {
            "source": self.provider,
            "model_version": self.model,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "confidence": self.confidence,
            "verification_status": VERIFICATION_UNVERIFIED,
            "ai_run_id": str(self.run_id),
            "created_at": utcnow().isoformat(),
        }


# What to tell the user for each way a run can fail. Keyed by failure type so
# the message and the recorded reason can never drift apart.
_FAILURE_PRESENTATION: dict[AIFailureType, dict[str, Any]] = {
    AIFailureType.PROVIDER_NOT_CONFIGURED: {
        "message": (
            "Photo analysis is temporarily switched off. Nothing was charged to "
            "your account."
        ),
        "guidance": PROVIDER_DOWN_GUIDANCE,
        "retryable": False,
    },
    AIFailureType.PROVIDER_TIMEOUT: {
        "message": "That took too long to analyse. Please try again.",
        "guidance": PROVIDER_DOWN_GUIDANCE,
        "retryable": True,
    },
    AIFailureType.PROVIDER_ERROR: {
        "message": "We could not analyse this image reliably.",
        "guidance": PROVIDER_DOWN_GUIDANCE,
        "retryable": True,
    },
    AIFailureType.EMPTY_RESPONSE: {
        "message": "We could not analyse this image reliably.",
        "guidance": DEFAULT_PHOTO_GUIDANCE,
        "retryable": True,
    },
    AIFailureType.INVALID_JSON: {
        "message": "We could not read the result clearly enough to trust it.",
        "guidance": PROVIDER_DOWN_GUIDANCE,
        "retryable": True,
    },
    AIFailureType.SCHEMA_VALIDATION_FAILED: {
        "message": "We could not read the result clearly enough to trust it.",
        "guidance": PROVIDER_DOWN_GUIDANCE,
        "retryable": True,
    },
    AIFailureType.IMAGE_REJECTED: {
        "message": "We could not read that photo. Please take another one.",
        "guidance": DEFAULT_PHOTO_GUIDANCE,
        "retryable": True,
    },
}


def _estimate_cost(
    input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    if input_tokens is None and output_tokens is None:
        return None
    cost = Decimal("0")
    if input_tokens:
        cost += Decimal(str(AI_COST_PER_1K_INPUT_USD)) * Decimal(input_tokens) / 1000
    if output_tokens:
        cost += Decimal(str(AI_COST_PER_1K_OUTPUT_USD)) * Decimal(output_tokens) / 1000
    return cost.quantize(Decimal("0.000001"))


def parse_json(text: str) -> dict[str, Any]:
    """Parse a model's JSON reply, tolerating markdown code fences."""
    clean = (text or "").strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    parsed = json.loads(clean.strip())
    if not isinstance(parsed, dict):
        raise ValueError("Model returned JSON that is not an object")
    return parsed


async def _record_run(**fields: Any) -> uuid.UUID | None:
    """Write one ai_runs row. Never raises.

    A ledger that can break the feature it is measuring is worse than no ledger,
    so a recording failure is logged and swallowed.
    """
    try:
        factory = get_sessionmaker()
        async with factory() as session:
            account_id = None
            account_id_str = fields.pop("account_id_str", None)
            if account_id_str:
                account = await identity.get_account(session, account_id_str)
                account_id = account.id

            output_payload = fields.pop("output_payload", None)
            output_confidence = fields.pop("output_confidence", None)

            run = AIRun(account_id=account_id, request_id=get_request_id(), **fields)
            session.add(run)
            await session.flush()

            if output_payload is not None:
                session.add(
                    AIRunOutput(
                        ai_run_id=run.id,
                        schema_version=run.schema_version,
                        payload=output_payload,
                        confidence=output_confidence,
                        verification_status=VERIFICATION_UNVERIFIED,
                    )
                )
            await session.commit()
            return run.id
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("ai_run_recording_failed feature=%s", fields.get("feature"))
        return None


def _fail(
    failure_type: AIFailureType, run_id: uuid.UUID | None
) -> AnalysisUnavailableError:
    presentation = _FAILURE_PRESENTATION[failure_type]
    return AnalysisUnavailableError(
        presentation["message"],
        failure_type=failure_type,
        guidance=presentation["guidance"],
        retryable=presentation["retryable"],
        ai_run_id=str(run_id) if run_id else None,
    )


async def run_structured(
    *,
    feature: str,
    prompt: str,
    system: str,
    schema: type[T],
    prompt_version: str,
    schema_version: str,
    account_id_str: str | None = None,
    image_base64: str | None = None,
) -> AIResult[T]:
    """Call the provider and return a validated result, or raise.

    Raises:
        AnalysisUnavailableError: for every failure mode. The error carries the
            failure type, user-facing guidance, and ``allowance_consumed: False``.
    """
    started = time.perf_counter()

    base_record: dict[str, Any] = {
        "account_id_str": account_id_str,
        "feature": feature,
        "provider": gemini.PROVIDER_NAME,
        "model": gemini.GEMINI_MODEL or "unknown",
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "allowance_consumed": False,
    }

    async def record_failure(
        failure_type: AIFailureType, detail: str, model: str | None = None
    ) -> uuid.UUID | None:
        elapsed = int((time.perf_counter() - started) * 1000)
        return await _record_run(
            **{
                **base_record,
                "model": model or base_record["model"],
                "status": AI_STATUS_FAILED,
                "failure_type": failure_type.value,
                # Type and short message only — never a stack trace, never a key.
                "failure_detail": detail[:2000],
                "latency_ms": elapsed,
                "validation_passed": False,
                "completed_at": utcnow(),
            }
        )

    # --- 1. Call the provider -------------------------------------------------
    try:
        response = await gemini.generate(prompt, system, image_base64=image_base64)
    except gemini.ProviderNotConfigured as exc:
        run_id = await record_failure(AIFailureType.PROVIDER_NOT_CONFIGURED, str(exc))
        logger.warning("ai_provider_not_configured feature=%s", feature)
        raise _fail(AIFailureType.PROVIDER_NOT_CONFIGURED, run_id) from exc
    except gemini.ProviderTimeout as exc:
        run_id = await record_failure(AIFailureType.PROVIDER_TIMEOUT, str(exc))
        logger.warning("ai_provider_timeout feature=%s", feature)
        raise _fail(AIFailureType.PROVIDER_TIMEOUT, run_id) from exc
    except gemini.ImageRejected as exc:
        run_id = await record_failure(AIFailureType.IMAGE_REJECTED, str(exc))
        raise _fail(AIFailureType.IMAGE_REJECTED, run_id) from exc
    except Exception as exc:  # noqa: BLE001 - anything else is a provider error
        run_id = await record_failure(
            AIFailureType.PROVIDER_ERROR, f"{type(exc).__name__}: {exc}"
        )
        logger.warning(
            "ai_provider_error feature=%s type=%s", feature, type(exc).__name__
        )
        raise _fail(AIFailureType.PROVIDER_ERROR, run_id) from exc

    if not response.text.strip():
        run_id = await record_failure(
            AIFailureType.EMPTY_RESPONSE, "Provider returned no text", response.model
        )
        raise _fail(AIFailureType.EMPTY_RESPONSE, run_id)

    # --- 2. Parse -------------------------------------------------------------
    try:
        payload = parse_json(response.text)
    except (json.JSONDecodeError, ValueError) as exc:
        run_id = await record_failure(
            AIFailureType.INVALID_JSON, f"{type(exc).__name__}: {exc}", response.model
        )
        logger.warning("ai_invalid_json feature=%s", feature)
        raise _fail(AIFailureType.INVALID_JSON, run_id) from exc

    # --- 3. Validate ----------------------------------------------------------
    try:
        validated = schema.model_validate(payload)
    except ValidationError as exc:
        # Log which fields failed, not the payload — it can contain the user's
        # own details echoed back.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}"
            for err in exc.errors()[:10]
        )
        run_id = await record_failure(
            AIFailureType.SCHEMA_VALIDATION_FAILED, problems, response.model
        )
        logger.warning(
            "ai_schema_validation_failed feature=%s problems=%s", feature, problems
        )
        raise _fail(AIFailureType.SCHEMA_VALIDATION_FAILED, run_id) from exc

    # --- 4. Record the success ------------------------------------------------
    latency_ms = int((time.perf_counter() - started) * 1000)
    estimated_cost = _estimate_cost(response.input_tokens, response.output_tokens)
    confidence = _confidence_of(validated)

    run_id = await _record_run(
        **{
            **base_record,
            "model": response.model,
            "status": AI_STATUS_SUCCEEDED,
            "latency_ms": latency_ms,
            "validation_passed": True,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "estimated_cost_usd": estimated_cost,
            "completed_at": utcnow(),
            "output_payload": validated.model_dump(mode="json"),
            "output_confidence": confidence,
        }
    )

    logger.info(
        "ai_run_succeeded feature=%s model=%s latency_ms=%s cost_usd=%s",
        feature,
        response.model,
        latency_ms,
        estimated_cost,
    )

    return AIResult(
        data=validated,
        run_id=run_id or uuid.uuid4(),
        provider=gemini.PROVIDER_NAME,
        model=response.model,
        prompt_version=prompt_version,
        schema_version=schema_version,
        confidence=confidence,
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost,
    )


def _confidence_of(validated: BaseModel) -> float | None:
    meta = getattr(validated, "meta", None)
    value = getattr(meta, "confidence", None)
    return float(value) if isinstance(value, (int, float)) else None
