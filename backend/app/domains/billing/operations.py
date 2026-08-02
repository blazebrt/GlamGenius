"""Experiments, cost tracking, model evaluation, support and try-on.

The operational half of Phase 8. Grouped in one module because these are all
"running the business" concerns rather than product logic, and keeping them
together makes the boundary obvious.

Two limits worth stating up front.

**Experiments are for pricing and copy only.** :data:`ALLOWED_SURFACES` is a
closed list, and :func:`assign` refuses anything else. A/B testing a safety
rule, a medical boundary or a privacy default would mean deliberately giving
some users the worse version of a protection, which this product does not do.

**Virtual try-on is a seam, not a feature.** The brief says explicitly not to
build it until a provider is selected and approved, so there is an interface, a
job table, cost tracking and a flag that defaults off — and the only
implementation refuses honestly.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AIRun
from app.domains.billing import catalogue, entitlements
from app.domains.billing.models import (
    Experiment, ExperimentAssignment, FeatureCostDaily, ModelEvaluation,
    StylistReview, SupportCase, VirtualTryOnJob,
)
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

logger = logging.getLogger(__name__)

# --- Experiments ----------------------------------------------------------------------

# The only things that may be experimented on. Everything else — safety wording,
# medical boundaries, privacy defaults, consent — is the same for everyone.
SURFACE_PRICING = "pricing"
SURFACE_PAYWALL_COPY = "paywall_copy"
SURFACE_ONBOARDING_ORDER = "onboarding_order"
ALLOWED_SURFACES: Tuple[str, ...] = (SURFACE_PRICING, SURFACE_PAYWALL_COPY, SURFACE_ONBOARDING_ORDER)

FORBIDDEN_SURFACES: Tuple[str, ...] = (
    "safety", "medical_disclaimer", "consent", "privacy", "data_retention",
    "age_gate", "diagnosis", "dosage",
)


def _variant_for(account_id: uuid.UUID, experiment_key: str, variants: Sequence[str]) -> str:
    """Stable assignment from a hash.

    Deterministic so the app does not show somebody a different price each time
    they open it, and so an assignment survives a database restore.
    """
    digest = hashlib.sha256(f"{experiment_key}:{account_id}".encode("utf-8")).hexdigest()
    return variants[int(digest[:8], 16) % len(variants)]


async def assign(
    session: AsyncSession, account_id: uuid.UUID, experiment_key: str
) -> Optional[str]:
    """Which variant this account is in. Stable for the life of the experiment."""
    experiment = (await session.execute(
        select(Experiment).where(Experiment.key == experiment_key)
    )).scalar_one_or_none()
    if experiment is None or experiment.status != "running" or not experiment.variants:
        return None

    if experiment.surface in FORBIDDEN_SURFACES or experiment.surface not in ALLOWED_SURFACES:
        # Refused rather than honoured. An experiment on a protection means
        # deliberately giving somebody the weaker version of it.
        logger.warning("experiment_surface_refused key=%s surface=%s", experiment_key, experiment.surface)
        return None

    existing = (await session.execute(
        select(ExperimentAssignment).where(
            ExperimentAssignment.account_id == account_id,
            ExperimentAssignment.experiment_key == experiment_key,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing.variant

    variant = _variant_for(account_id, experiment_key, experiment.variants)
    await session.execute(
        pg_insert(ExperimentAssignment.__table__)
        .values(
            id=uuid.uuid4(), account_id=account_id,
            experiment_key=experiment_key, variant=variant,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "experiment_key"])
    )
    return variant


async def assignments_for(session: AsyncSession, account_id: uuid.UUID) -> Dict[str, str]:
    running = (await session.execute(
        select(Experiment).where(Experiment.status == "running")
    )).scalars().all()
    out: Dict[str, str] = {}
    for experiment in running:
        variant = await assign(session, account_id, experiment.key)
        if variant:
            out[experiment.key] = variant
    return out


# --- Feature-level AI cost ---------------------------------------------------------------


async def roll_up_costs(
    session: AsyncSession, *, on: Optional[date] = None
) -> List[Dict[str, Any]]:
    """Roll ``ai_runs`` into per-feature daily cost.

    Feature-level is the point. "We spent $40 today" is not actionable;
    "shopping evaluation costs eight times what styling costs" is — it tells you
    which prompt to shorten and which plan limit is mispriced.
    """
    day = on or date.today()
    start = day
    end = day + timedelta(days=1)

    rows = (await session.execute(
        select(
            AIRun.feature,
            func.count(AIRun.id),
            func.sum(func.coalesce(AIRun.input_tokens, 0)),
            func.sum(func.coalesce(AIRun.output_tokens, 0)),
            func.sum(func.coalesce(AIRun.estimated_cost_usd, 0.0)),
            func.sum(case((AIRun.status != "succeeded", 1), else_=0)),
        )
        .where(AIRun.created_at >= start, AIRun.created_at < end)
        .group_by(AIRun.feature)
    )).all()

    out: List[Dict[str, Any]] = []
    for feature, count, tokens_in, tokens_out, cost, failures in rows:
        await session.execute(
            pg_insert(FeatureCostDaily.__table__)
            .values(
                id=uuid.uuid4(), on_date=day, feature=feature, run_count=int(count or 0),
                failure_count=int(failures or 0), input_tokens=int(tokens_in or 0),
                output_tokens=int(tokens_out or 0), estimated_cost_usd=float(cost or 0.0),
            )
            .on_conflict_do_update(
                index_elements=["on_date", "feature"],
                set_={
                    "run_count": int(count or 0), "failure_count": int(failures or 0),
                    "input_tokens": int(tokens_in or 0), "output_tokens": int(tokens_out or 0),
                    "estimated_cost_usd": float(cost or 0.0),
                },
            )
        )
        out.append({
            "feature": feature, "runs": int(count or 0), "failures": int(failures or 0),
            "input_tokens": int(tokens_in or 0), "output_tokens": int(tokens_out or 0),
            "estimated_cost_usd": round(float(cost or 0.0), 6),
        })
    return out


async def cost_dashboard(
    session: AsyncSession, *, days: int = 30, today: Optional[date] = None
) -> Dict[str, Any]:
    """Spend per feature over a window, with a per-run figure.

    Cost per run is what makes a plan limit defensible: if a shopping check
    costs more than a share of the monthly price, the limit is wrong.
    """
    day = today or date.today()
    since = day - timedelta(days=days - 1)
    rows = (await session.execute(
        select(FeatureCostDaily).where(FeatureCostDaily.on_date >= since)
    )).scalars().all()

    by_feature: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        bucket = by_feature.setdefault(row.feature, {
            "feature": row.feature, "runs": 0, "failures": 0,
            "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0,
        })
        bucket["runs"] += row.run_count
        bucket["failures"] += row.failure_count
        bucket["input_tokens"] += row.input_tokens
        bucket["output_tokens"] += row.output_tokens
        bucket["estimated_cost_usd"] += row.estimated_cost_usd

    features = []
    for bucket in by_feature.values():
        bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"], 6)
        bucket["cost_per_run_usd"] = (
            round(bucket["estimated_cost_usd"] / bucket["runs"], 6) if bucket["runs"] else None
        )
        features.append(bucket)

    total = round(sum(row["estimated_cost_usd"] for row in features), 6)
    return {
        "window_days": days,
        "since": since.isoformat(),
        "features": sorted(features, key=lambda row: -row["estimated_cost_usd"]),
        "total_estimated_cost_usd": total,
        "note": (
            "Estimated from recorded token counts at configured rates. Useful for comparing "
            "features against each other; not an invoice."
        ),
    }


# --- Model quality --------------------------------------------------------------------------


@dataclass
class EvaluationSample:
    """One graded output.

    Graded against the deterministic engine and the safety sweeps — never
    against anybody's opinion of whether a person looks good.
    """

    feature: str
    sample_key: str
    passed: bool
    checks: Dict[str, Any]
    failure_reason: str = ""
    prompt_version: str = ""
    schema_version: str = ""
    model: str = ""


async def record_evaluation(session: AsyncSession, sample: EvaluationSample) -> ModelEvaluation:
    row = ModelEvaluation(
        feature=sample.feature, sample_key=sample.sample_key, passed=sample.passed,
        checks=sample.checks, failure_reason=sample.failure_reason,
        prompt_version=sample.prompt_version, schema_version=sample.schema_version,
        model=sample.model,
    )
    session.add(row)
    await session.flush()
    return row


async def evaluation_summary(
    session: AsyncSession, *, feature: Optional[str] = None, limit: int = 500
) -> Dict[str, Any]:
    statement = select(ModelEvaluation).order_by(ModelEvaluation.created_at.desc()).limit(limit)
    if feature:
        statement = statement.where(ModelEvaluation.feature == feature)
    rows = (await session.execute(statement)).scalars().all()

    by_feature: Dict[str, Dict[str, int]] = {}
    for row in rows:
        bucket = by_feature.setdefault(row.feature, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if row.passed else 0

    return {
        "features": [
            {
                "feature": key, "total": value["total"], "passed": value["passed"],
                "pass_rate": round(value["passed"] / value["total"], 4) if value["total"] else None,
            }
            for key, value in sorted(by_feature.items())
        ],
        "recent_failures": [
            {
                "feature": row.feature, "sample_key": row.sample_key,
                "reason": row.failure_reason,
                "at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows if not row.passed
        ][:20],
        "note": (
            "Graded against the deterministic engine and the safety checks. Nothing here "
            "grades how somebody looks."
        ),
    }


# --- Support --------------------------------------------------------------------------------


SUPPORT_CATEGORIES = ("billing", "account", "bug", "content", "other")

# Words that mean somebody needs a person quickly.
URGENT_TERMS = ("charged twice", "double charge", "fraud", "unauthorised", "unauthorized", "refund")


async def open_case(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    category: str,
    subject: str,
    message: str,
) -> Dict[str, Any]:
    """Record a support request with its billing context attached.

    The snapshot is the point: a billing question answered without asking the
    person to explain their own subscription back to us.
    """
    if category not in SUPPORT_CATEGORIES:
        raise ValidationFailedError(f"'{category}' is not a support category.", field="category")

    from app.domains.billing import service as billing_service

    snapshot: Dict[str, Any]
    try:
        snapshot = await billing_service.status(session, account_id)
    except Exception as exc:  # noqa: BLE001 — a broken snapshot must not block a support request
        logger.warning("support_snapshot_failed type=%s", type(exc).__name__)
        snapshot = {"error": "billing snapshot unavailable"}

    lowered = f"{subject} {message}".lower()
    severity = "high" if any(term in lowered for term in URGENT_TERMS) else "normal"

    case = SupportCase(
        account_id=account_id, category=category, subject=subject.strip()[:160],
        message=message.strip(), severity=severity, billing_snapshot=snapshot,
    )
    session.add(case)
    await session.flush()

    return {
        "id": str(case.id),
        "category": case.category,
        "severity": case.severity,
        "status": case.status,
        "message": (
            "Thanks — we have this, along with your billing details so you do not have to "
            "explain them. Anything about a charge we look at first."
            if severity == "high" else
            "Thanks — we have this and will come back to you."
        ),
    }


async def request_stylist_review(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    subject_type: str,
    subject_id: Optional[uuid.UUID],
    question: str,
) -> Dict[str, Any]:
    """Queue a request for a human to look at something.

    The seam for professional integration. It queues honestly and says a person
    has not replied yet — it does not generate an answer and label it human.
    """
    row = StylistReview(
        account_id=account_id, subject_type=subject_type, subject_id=subject_id,
        question=question.strip(),
    )
    session.add(row)
    await session.flush()
    return {
        "id": str(row.id),
        "status": row.status,
        "message": (
            "Queued for a human stylist. Nobody has looked at it yet — when they do, their "
            "answer will appear here, and it will be theirs rather than the app's."
        ),
    }


# --- Virtual try-on ------------------------------------------------------------------------------


class VirtualTryOnProvider(Protocol):
    """The interface a try-on provider would implement.

    Defined now so the rest of the app can be written against it. No
    implementation ships in this phase, because the brief says not to build the
    product until a provider is selected and approved.
    """

    name: str

    def is_configured(self) -> bool: ...

    async def create_job(
        self, *, source_media_id: uuid.UUID, item_ids: Sequence[str]
    ) -> Dict[str, Any]: ...

    async def get_status(self, provider_job_id: str) -> Dict[str, Any]: ...

    async def get_result(self, provider_job_id: str) -> Dict[str, Any]: ...

    async def cancel_job(self, provider_job_id: str) -> Dict[str, Any]: ...


class UnconfiguredTryOnProvider:
    """Refuses, and says why.

    Deliberately not a stub that returns a placeholder image. A placeholder
    would be a fabricated picture of somebody wearing something they have never
    worn, which is exactly the kind of thing this product does not produce.
    """

    name = "unconfigured"

    def is_configured(self) -> bool:
        return False

    async def create_job(self, **_: Any) -> Dict[str, Any]:
        raise NotFoundError("Virtual try-on is not available yet.")

    async def get_status(self, provider_job_id: str) -> Dict[str, Any]:
        raise NotFoundError("Virtual try-on is not available yet.")

    async def get_result(self, provider_job_id: str) -> Dict[str, Any]:
        raise NotFoundError("Virtual try-on is not available yet.")

    async def cancel_job(self, provider_job_id: str) -> Dict[str, Any]:
        raise NotFoundError("Virtual try-on is not available yet.")


_tryon_provider: Any = UnconfiguredTryOnProvider()


def set_tryon_provider(provider: Any) -> None:
    global _tryon_provider
    _tryon_provider = provider or UnconfiguredTryOnProvider()


def get_tryon_provider() -> Any:
    return _tryon_provider


def tryon_status() -> Dict[str, Any]:
    provider = get_tryon_provider()
    return {
        "provider": provider.name,
        "configured": provider.is_configured(),
        "note": (
            "The interface and the job table exist so this can be added without reshaping the "
            "app. No provider is selected, so nothing is generated — and we would rather show "
            "nothing than a made-up picture of you in clothes you have never worn."
        ),
    }
