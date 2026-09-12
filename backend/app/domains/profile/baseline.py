"""Optional, transient-photo baseline analysis."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.gateway import run_structured
from app.domains.ai_gateway.models import AIRun
from app.domains.profile import observations
from app.domains.profile.models import AppearanceProfile
from app.domains.profile.schemas import BaselineAnalysis
from app.domains.routines.safety import narrative_is_safe

logger = logging.getLogger(__name__)

PROMPT_VERSION = "appearance_baseline.v1"
SCHEMA_VERSION = "appearance_baseline.v1"

SYSTEM = """You are a respectful personal style assistant. Describe only visible,
non-medical appearance details that are useful for colour and styling. Never diagnose,
score attractiveness, infer body weight, ethnicity, health, age, gender identity or other
sensitive traits. Use inclusive, neutral language. If the image is not clear enough, set
image_quality to low and do not guess. Return JSON only."""

PROMPT = """Review this optional onboarding photo. Return exactly:
{
  "image_quality": "low|usable|good",
  "image_quality_notes": "short neutral explanation",
  "observations": [
    {"key": "skin_tone|undertone|face_shape|visible_skin_characteristics|hair_type|hair_texture|hair_density", "value": "short value or string list", "confidence": 0.0, "why": "visible evidence only"}
  ],
  "colour_palette": [{"name": "colour", "hex": "#RRGGBB", "why": "style reason"}],
  "summary": "one useful, constructive style summary",
  "disclaimer": "Style guidance from visible photo details, not medical advice."
}
Every observation is a suggestion for the user to confirm, edit or reject. Do not include
medical terms, attractiveness judgments, body-shape judgments, weight, or measurements."""

# The disclaimer is ours, not the model's. The schema carries a ``disclaimer``
# field so a model that returns one does not trip ``extra="forbid"``, but the
# value below is what the user sees — a model must never be able to soften,
# shorten or empty it.
BASELINE_DISCLAIMER = "Style guidance from visible photo details, not medical advice."

# Shown in place of a summary that failed the language sweep. States that the
# text is missing rather than guessing at a safe rewrite of it.
SUMMARY_WITHHELD = "No written summary for this photo. The observations below are still yours to confirm or reject."

LOW_QUALITY_GUIDANCE = [
    "Use soft light facing you rather than light from behind",
    "Keep your face and hair in focus and inside the frame",
    "You can skip the photo and continue onboarding at any time",
]


def _observation_is_safe(suggestion: Any) -> bool:
    """Every string the model wrote for one observation, swept together.

    ``visible_skin_characteristics`` is a free-text list in the registry, so
    nothing below this point stops the model writing a condition name into a
    persisted profile attribute. This does. It fails closed: an observation
    that trips the sweep is dropped, never rewritten.
    """
    parts: list[str] = [suggestion.why]
    value = suggestion.value
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, (list, tuple)):
        parts.extend(str(item) for item in value)
    return all(narrative_is_safe(part) for part in parts)


async def analyse(session: AsyncSession, profile: AppearanceProfile, *, account_id_str: str, image_base64: str) -> dict[str, Any]:
    result = await run_structured(
        feature="appearance_baseline", prompt=PROMPT, system=SYSTEM,
        schema=BaselineAnalysis, prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION, account_id_str=account_id_str,
        image_base64=image_base64,
    )
    data = result.data
    # The gateway deliberately lets a successful user response survive a
    # telemetry outage. Only attach a foreign key when its ledger row exists.
    recorded_run = await session.get(AIRun, result.run_id)
    run_id = result.run_id if recorded_run else None
    profile.baseline_ai_run_id = run_id
    if data.image_quality == "low":
        profile.baseline_status = "needs_better_photo"
        return {
            "status": "low_quality", "image_quality": data.image_quality,
            "message": "We could not see enough detail to make reliable suggestions.",
            "guidance": LOW_QUALITY_GUIDANCE, "observations": [], "colour_palette": [],
            "ai_run_id": str(run_id) if run_id else None, "photo_stored": False,
        }

    created = []
    for suggestion in data.observations:
        if not _observation_is_safe(suggestion):
            # The key is safe to log; the wording that tripped the sweep is not.
            logger.warning("appearance_baseline_rejected_observation key=%s", suggestion.key)
            continue
        row = await observations.create(
            session, profile, key=suggestion.key, value=suggestion.value,
            source="photo_observed", confidence=suggestion.confidence,
            why=suggestion.why, source_ai_run_id=run_id,
        )
        if row is not None:
            created.append(observations.serialize(row))
    palette = [
        item for item in data.colour_palette
        if narrative_is_safe(item.name) and narrative_is_safe(item.why)
    ]
    if len(palette) != len(data.colour_palette):
        logger.warning(
            "appearance_baseline_rejected_palette dropped=%d", len(data.colour_palette) - len(palette)
        )
    summary = data.summary
    if not narrative_is_safe(summary):
        logger.warning("appearance_baseline_rejected_summary")
        summary = SUMMARY_WITHHELD
    profile.baseline_status = "observations_ready"
    return {
        "status": "observations_ready", "image_quality": data.image_quality,
        "message": summary, "observations": created,
        "colour_palette": [item.model_dump() for item in palette],
        "ai_run_id": str(run_id) if run_id else None, "photo_stored": False,
        "disclaimer": BASELINE_DISCLAIMER,
    }
