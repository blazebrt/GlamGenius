"""Load the food reference sets into the authoring tool, as drafts.

Three subject types, so the queue can be filtered one set at a time:
``nutrient_threshold``, ``food_additive`` and ``culinary_ingredient``.

Nothing here approves or publishes. A threshold whose figure could not be read
loads as ``not_enough_information`` with a note naming what to transcribe, and
the authoring tool's gate then keeps it a draft until somebody supplies a
source URL that opens — which is the correct resting state for it.

Run with ``python -m app.domains.nutrition.food_reference_loader`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence import authoring
from app.domains.evidence.models import EvidenceClaim
from app.domains.nutrition.food_reference import (
    ADDITIVES,
    CULINARY_INGREDIENTS,
    THRESHOLDS,
    TIER_NOT_ENOUGH,
    Additive,
    CulinaryIngredient,
    Threshold,
)
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)

SUBJECT_THRESHOLD = "nutrient_threshold"
SUBJECT_ADDITIVE = "food_additive"
SUBJECT_CULINARY = "culinary_ingredient"
LOADER_AUTHOR = "food_reference_loader"

_UNREAD = (
    "This citation has NOT been opened by the system that loaded it, which has no "
    "network access. Open the source and confirm the figure before approving."
)


def _threshold_subject(threshold: Threshold) -> str:
    return f"{threshold.nutrient} ({threshold.basis}, {threshold.source.identifier})"


def _threshold_claim(threshold: Threshold) -> str:
    if threshold.low_max is None and threshold.high_min is None:
        return (
            f"The per-100 {'ml' if threshold.basis == 'drink' else 'g'} threshold for "
            f"{threshold.nutrient} is not carried here. {threshold.note or ''}".strip()
        )
    unit = threshold.unit
    return (
        f"{threshold.nutrient.capitalize()} is low at {threshold.low_max} or less and high "
        f"above {threshold.high_min}, measured in {unit}."
    )


def _threshold_notes(threshold: Threshold) -> str:
    lines = [f"Basis: per 100 {'ml' if threshold.basis == 'drink' else 'g'} ({threshold.basis})."]
    if threshold.note:
        lines.append(threshold.note)
    if threshold.disagreement:
        lines.append(f"Sources disagree: {threshold.disagreement}")
    lines.append(_UNREAD)
    return "\n".join(lines)


def _additive_claim(additive: Additive) -> str:
    ins = f"INS {additive.ins}" if additive.ins else "No INS number"
    return f"{additive.name} ({ins}). {additive.function} Treated as {additive.tier}."


def _additive_notes(additive: Additive) -> str:
    lines = [f"Risk tier: {additive.tier}."]
    if additive.note:
        lines.append(additive.note)
    if additive.disagreement:
        lines.append(f"Sources disagree: {additive.disagreement}")
    lines.append(_UNREAD)
    return "\n".join(lines)


def _culinary_claim(ingredient: CulinaryIngredient) -> str:
    return f"{ingredient.name} is never given a letter grade. {ingredient.why_never_graded}"


def _culinary_notes(ingredient: CulinaryIngredient) -> str:
    lines = [f"Label spellings: {', '.join(ingredient.aliases)}."]
    if ingredient.daily_guidance:
        lines.append(f"Daily guidance: {ingredient.daily_guidance}")
    else:
        lines.append("No daily quantity guidance is carried for this one.")
    if ingredient.disagreement:
        lines.append(f"Sources disagree: {ingredient.disagreement}")
    if ingredient.guidance_source:
        lines.append(_UNREAD)
    return "\n".join(lines)


async def _draft_once(
    session: AsyncSession, *, subject_type: str, subject: str, claim: str,
    source_name: str, source_url: str, tier: str, notes: str, author: str,
    value: str | None = None, unit: str | None = None,
) -> bool:
    """Create the draft unless one already exists for this subject."""
    existing = (await session.execute(
        select(EvidenceClaim).where(
            EvidenceClaim.subject_type == subject_type,
            EvidenceClaim.subject_key == subject,
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return False
    await authoring.create_draft(
        session,
        authoring.EntryInput(
            subject_type=subject_type, subject_key=subject, claim=claim,
            value=value, unit=unit, source_name=source_name, source_url=source_url,
            evidence_tier=tier, notes=notes, domain="nutrition",
        ),
        author=author,
    )
    return True


async def load(session: AsyncSession, *, author: str = LOADER_AUTHOR) -> dict[str, Any]:
    created = {SUBJECT_THRESHOLD: 0, SUBJECT_ADDITIVE: 0, SUBJECT_CULINARY: 0}

    for threshold in THRESHOLDS:
        if await _draft_once(
            session, subject_type=SUBJECT_THRESHOLD, subject=_threshold_subject(threshold),
            claim=_threshold_claim(threshold),
            value=str(threshold.high_min) if threshold.high_min is not None else None,
            unit=threshold.unit if threshold.high_min is not None else None,
            source_name=threshold.source.name, source_url=threshold.source.url,
            tier=threshold.tier, notes=_threshold_notes(threshold), author=author,
        ):
            created[SUBJECT_THRESHOLD] += 1

    for additive in ADDITIVES:
        subject = f"{additive.name} ({additive.ins})" if additive.ins else additive.name
        if await _draft_once(
            session, subject_type=SUBJECT_ADDITIVE, subject=subject,
            claim=_additive_claim(additive),
            source_name=additive.source.name, source_url=additive.source.url,
            tier=TIER_NOT_ENOUGH if additive.confidence.value == "low" else "clinically_studied",
            notes=_additive_notes(additive), author=author,
        ):
            created[SUBJECT_ADDITIVE] += 1

    for ingredient in CULINARY_INGREDIENTS:
        source = ingredient.guidance_source
        if await _draft_once(
            session, subject_type=SUBJECT_CULINARY, subject=ingredient.name,
            claim=_culinary_claim(ingredient),
            source_name=source.name if source else "Product rule, not an external source",
            source_url=source.url if source else "",
            # Never grading a cooking ingredient is our own rule, not a finding.
            tier=TIER_NOT_ENOUGH,
            notes=_culinary_notes(ingredient), author=author,
        ):
            created[SUBJECT_CULINARY] += 1

    return {
        "thresholds": len(THRESHOLDS),
        "additives": len(ADDITIVES),
        "culinary_ingredients": len(CULINARY_INGREDIENTS),
        "drafts_created": created,
    }


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    factory = get_sessionmaker()
    async with factory() as session:
        summary = await load(session)
        await session.commit()
    logger.info("food_reference_loaded %s", summary)


if __name__ == "__main__":
    asyncio.run(main())
