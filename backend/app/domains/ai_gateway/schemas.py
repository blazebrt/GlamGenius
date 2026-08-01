"""Versioned response schemas.

Audit finding F23: model output went to the frontend unvalidated. Everything the
gateway returns now passes through one of these models first.

**How strict, and why.** Two failure modes matter and they pull in opposite
directions. Validate too loosely and half-empty garbage still reaches the user.
Validate too tightly and a perfectly good answer gets thrown away, which — now
that there is no fallback — means the user sees an error for no reason.

The line drawn here:

* **Structure and substance are required.** An analysis with no observations, or
  no colour suggestions, is not an analysis. It fails.
* **Free-text values are bounded, not enumerated.** Length caps and stripping,
  but no rejection for unexpected wording.
* **Unknown extra fields are kept** (``extra="allow"``). The prompt asks for a
  large nested object and the app renders much of it; dropping anything the
  schema has not caught up with would silently lose content.
* **Enum checking happens only where a value is written into the user's stored
  profile** — see ``PROFILE_VOCABULARY``. There, an unrecognised value is
  discarded rather than saved, so junk cannot enter the profile, but the rest of
  the analysis still reaches the user.

Bump ``SCHEMA_VERSION`` on any change to what is required. It is recorded on
every run, so old rows keep meaning what they meant.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION_COACH = "coach_analysis.v1"
SCHEMA_VERSION_STYLE_PLAN = "style_plan.v1"

DISCLAIMER = "General wellness and style guidance from a photo — not medical advice."


class _Block(BaseModel):
    """Base for nested blocks: keep unknown keys, strip whitespace."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class MetaBlock(_Block):
    scan_focus: str = "full"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    image_quality_notes: str = ""
    disclaimer: str = DISCLAIMER

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> Any:
        # Models sometimes answer 85 when they mean 0.85.
        if isinstance(value, (int, float)) and value > 1:
            return min(float(value) / 100.0, 1.0)
        if value is None:
            return 0.5
        return value


class ProfileBlock(_Block):
    face_shape: Optional[str] = Field(default=None, max_length=64)
    skin_type_visible: Optional[str] = Field(default=None, max_length=64)
    skin_tone: Optional[str] = Field(default=None, max_length=64)
    undertone: Optional[str] = Field(default=None, max_length=64)
    hair_type_visible: Optional[str] = Field(default=None, max_length=64)
    hair_texture: Optional[str] = Field(default=None, max_length=64)
    hair_density_visible: Optional[str] = Field(default=None, max_length=64)
    estimated_build_note: Optional[str] = Field(default=None, max_length=512)


class Observation(_Block):
    area: str = Field(max_length=64)
    what_i_see: str = Field(min_length=1, max_length=1024)
    level: Optional[str] = Field(default=None, max_length=32)
    why_it_matters: Optional[str] = Field(default=None, max_length=1024)


class ColorSuggestion(_Block):
    color: str = Field(min_length=1, max_length=64)
    hex_hint: Optional[str] = Field(default=None, max_length=16)
    why: Optional[str] = Field(default=None, max_length=512)


class FashionBlock(_Block):
    # The one hard requirement in this block. Colour advice is the core of what
    # the product promises from a photo; without it there is nothing to show.
    best_clothing_colors: List[ColorSuggestion] = Field(min_length=1)
    colors_to_go_easy_on: List[Dict[str, Any]] = Field(default_factory=list)
    silhouettes_for_you: List[Dict[str, Any]] = Field(default_factory=list)
    fits_and_proportions: List[str] = Field(default_factory=list)
    wardrobe_ideas_india: List[Dict[str, Any]] = Field(default_factory=list)
    current_trends_to_try: List[Dict[str, Any]] = Field(default_factory=list)
    metal_and_accessories: Optional[str] = Field(default=None, max_length=512)
    fabric_texture_tips: List[str] = Field(default_factory=list)


class DailyCareBlock(_Block):
    morning: List[str] = Field(default_factory=list)
    evening: List[str] = Field(default_factory=list)
    weekly: List[str] = Field(default_factory=list)
    climate_note: Optional[str] = Field(default=None, max_length=512)


class CareIngredientsBlock(_Block):
    for_skin: List[Dict[str, Any]] = Field(default_factory=list)
    for_hair: List[Dict[str, Any]] = Field(default_factory=list)
    ingredients_to_go_easy_on: List[Dict[str, Any]] = Field(default_factory=list)
    simple_shopping_rule: Optional[str] = Field(default=None, max_length=512)


class NutritionBlock(_Block):
    goal: Optional[str] = Field(default=None, max_length=512)
    ingredients: List[Dict[str, Any]] = Field(default_factory=list)
    simple_plate_ideas: List[str] = Field(default_factory=list)
    hydration: Optional[str] = Field(default=None, max_length=512)
    diet_fit: Optional[str] = Field(default=None, max_length=512)


class CoachSummaryBlock(_Block):
    headline: str = Field(min_length=1, max_length=512)
    top_3_actions_this_week: List[str] = Field(default_factory=list)
    recheck_in_days: Optional[int] = Field(default=None, ge=1, le=365)


class WellnessScoresBlock(_Block):
    """Carried over from V1 unchanged.

    These numbers are produced by a language model with no formula, no version
    and no explanation, and ``overall_score`` is a single composite appearance
    score. Both are contrary to the product direction and are scheduled for
    removal in the metrics phase, which is where the deterministic replacements
    are built. Removing them here — before anything replaces them — would break
    the History and Trends screens for no gain, so they stay for now and the
    block is optional.
    """

    skin_score: Optional[int] = Field(default=None, ge=0, le=100)
    hair_score: Optional[int] = Field(default=None, ge=0, le=100)
    style_readiness_score: Optional[int] = Field(default=None, ge=0, le=100)
    overall_score: Optional[int] = Field(default=None, ge=0, le=100)
    score_notes: Optional[str] = Field(default=None, max_length=512)


class CoachAnalysis(BaseModel):
    """A complete, trustworthy photo analysis."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    meta: MetaBlock = Field(default_factory=MetaBlock)
    profile: ProfileBlock = Field(default_factory=ProfileBlock)
    wellness_scores: Optional[WellnessScoresBlock] = None
    observations: List[Observation] = Field(min_length=1)
    fashion: FashionBlock
    style: Optional[Dict[str, Any]] = None
    daily_care: DailyCareBlock = Field(default_factory=DailyCareBlock)
    care_ingredients: CareIngredientsBlock = Field(default_factory=CareIngredientsBlock)
    nutrition: NutritionBlock = Field(default_factory=NutritionBlock)
    salon_suggestions: List[Dict[str, Any]] = Field(default_factory=list)
    coach_summary: CoachSummaryBlock


class StylePlan(BaseModel):
    """A style plan built from the profile, with no photo involved."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    headline: str = Field(min_length=1, max_length=512)
    fashion: Optional[Dict[str, Any]] = None
    style: Optional[Dict[str, Any]] = None
    daily_care: Optional[Dict[str, Any]] = None
    care_ingredients: Optional[Dict[str, Any]] = None
    nutrition: Optional[Dict[str, Any]] = None
    salon_suggestions: List[Dict[str, Any]] = Field(default_factory=list)
    top_3_actions_this_week: List[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


# Values that may be written into the stored user profile. Anything else the
# model says is shown to the user but not saved, so a one-off odd answer cannot
# quietly become a permanent "fact" about them.
PROFILE_VOCABULARY: Dict[str, set[str]] = {
    "face_shape": {"oval", "round", "square", "heart", "oblong", "diamond"},
    "skin_type_visible": {"oily", "dry", "combination", "normal"},
    "skin_tone": {"fair", "wheatish", "medium", "dusky", "deep"},
    "undertone": {"warm", "cool", "olive", "neutral"},
    "hair_type_visible": {"straight", "wavy", "curly", "coily"},
}


def profile_value(field: str, value: Optional[str]) -> Optional[str]:
    """The value to store for this profile field, or None to leave it alone.

    "unclear" is a legitimate answer from the model and correctly stores
    nothing.
    """
    if not value:
        return None
    normalised = value.strip().lower()
    allowed = PROFILE_VOCABULARY.get(field)
    if allowed is None:
        return None
    return normalised if normalised in allowed else None
