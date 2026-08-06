"""Observation inbox and explicit verification workflow."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile import service
from app.domains.profile.models import AppearanceProfile, AttributeObservation
from app.domains.profile.registry import ALLOWED_SOURCES, ATTRIBUTE_REGISTRY, validate_attribute
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

MINIMUM_OBSERVATION_CONFIDENCE = 0.35


async def create(
    session: AsyncSession,
    profile: AppearanceProfile,
    *, key: str, value: Any, source: str, confidence: float, why: str,
    source_ai_run_id: uuid.UUID | None = None,
) -> AttributeObservation | None:
    if source not in ALLOWED_SOURCES or source == "user_declared":
        raise ValueError("Observations require an inference or verification source")
    spec = ATTRIBUTE_REGISTRY.get(key)
    if spec is None or (source == "photo_observed" and not spec.ai_observable):
        raise ValueError(f"{key} cannot be inferred from this source")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if confidence < MINIMUM_OBSERVATION_CONFIDENCE:
        return None
    cleaned = validate_attribute(key, value)
    if source_ai_run_id:
        duplicate = (await session.execute(
            select(AttributeObservation).where(
                AttributeObservation.profile_id == profile.id,
                AttributeObservation.source_ai_run_id == source_ai_run_id,
                AttributeObservation.key == key,
            )
        )).scalar_one_or_none()
        if duplicate:
            return duplicate
    row = AttributeObservation(
        profile_id=profile.id, key=key, proposed_value=cleaned, source=source,
        confidence=confidence, why=why.strip()[:2000], source_ai_run_id=source_ai_run_id,
    )
    session.add(row)
    await session.flush()
    return row


async def list_for_profile(session: AsyncSession, profile_id: uuid.UUID) -> list[AttributeObservation]:
    stmt = select(AttributeObservation).where(AttributeObservation.profile_id == profile_id).order_by(AttributeObservation.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def owned(session: AsyncSession, profile_id: uuid.UUID, observation_id: uuid.UUID) -> AttributeObservation:
    row = (await session.execute(select(AttributeObservation).where(AttributeObservation.id == observation_id, AttributeObservation.profile_id == profile_id))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Observation not found.")
    return row


async def confirm(session: AsyncSession, profile: AppearanceProfile, row: AttributeObservation) -> None:
    if row.verification_state == "rejected":
        raise ValidationFailedError("A rejected observation is kept as history and cannot be confirmed.")
    source = "user_declared" if row.source == "user_declared" else row.source
    await service.apply_attributes(
        session, profile, [{"key": row.key, "value": row.proposed_value}],
        source=source, confidence=row.confidence, verification_state="confirmed",
        source_ai_run_id=row.source_ai_run_id, reason="observation_confirmed",
    )
    row.verification_state = "confirmed"
    row.reviewed_at = utcnow()


def reject(row: AttributeObservation) -> None:
    row.verification_state = "rejected"
    row.reviewed_at = utcnow()


def edit(row: AttributeObservation, value: Any, state: str | None = None) -> None:
    if row.verification_state == "rejected":
        raise ValidationFailedError("A rejected observation is kept unchanged as history.")
    row.proposed_value = validate_attribute(row.key, value)
    row.source = "user_declared"
    row.confidence = 1.0
    row.why = "Edited by you before confirmation."
    row.verification_state = state or "unverified"
    row.reviewed_at = utcnow() if state == "not_sure" else None


def serialize(row: AttributeObservation) -> dict[str, Any]:
    return {
        "id": str(row.id), "key": row.key, "label": ATTRIBUTE_REGISTRY[row.key].label,
        "value": row.proposed_value, "source": row.source, "confidence": row.confidence,
        "why": row.why, "verification_state": row.verification_state,
        "source_ai_run_id": str(row.source_ai_run_id) if row.source_ai_run_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }
