"""Controlled registry for every Phase 2 profile attribute.

Values may be extensible, but keys are not. This prevents the digital twin
from becoming an ungoverned JSON document and gives validation, provenance and
readiness calculations one shared vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class AttributeSpec:
    key: str
    section: str
    label: str
    kind: str = "text"  # text | number | list
    choices: tuple[str, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    ai_observable: bool = False


def _spec(key: str, section: str, label: str, **kwargs: Any) -> AttributeSpec:
    return AttributeSpec(key=key, section=section, label=label, **kwargs)


ATTRIBUTE_REGISTRY: dict[str, AttributeSpec] = {
    # Appearance: observations remain unverified until the user acts.
    "skin_tone": _spec("skin_tone", "appearance", "Skin tone", ai_observable=True),
    "undertone": _spec("undertone", "appearance", "Undertone", ai_observable=True),
    "face_shape": _spec("face_shape", "appearance", "Face shape", ai_observable=True),
    "visible_skin_characteristics": _spec(
        "visible_skin_characteristics", "appearance", "Visible skin characteristics", kind="list", ai_observable=True
    ),
    "hair_type": _spec("hair_type", "appearance", "Hair type", ai_observable=True),
    "hair_texture": _spec("hair_texture", "appearance", "Hair texture", ai_observable=True),
    "hair_density": _spec("hair_density", "appearance", "Visible hair density", ai_observable=True),
    "facial_hair_preference": _spec("facial_hair_preference", "appearance", "Facial hair preference"),
    # Style.
    "preferred_style": _spec("preferred_style", "style", "Preferred style"),
    "favourite_colours": _spec("favourite_colours", "style", "Favourite colours", kind="list"),
    "disliked_colours": _spec("disliked_colours", "style", "Colours to avoid", kind="list"),
    "brand_preferences": _spec("brand_preferences", "style", "Brand preferences", kind="list"),
    "formality_preference": _spec("formality_preference", "style", "Formality preference"),
    "style_experimentation": _spec(
        "style_experimentation", "style", "Style approach", choices=("classic", "balanced", "experimental")
    ),
    "indian_categories": _spec("indian_categories", "style", "Indian categories", kind="list"),
    "western_categories": _spec("western_categories", "style", "Western categories", kind="list"),
    "fusion_categories": _spec("fusion_categories", "style", "Fusion categories", kind="list"),
    # Fit. Weight is intentionally absent.
    "height_cm": _spec("height_cm", "fit", "Height", kind="number", minimum=100, maximum=250),
    "usual_top_size": _spec("usual_top_size", "fit", "Usual top size"),
    "usual_bottom_size": _spec("usual_bottom_size", "fit", "Usual bottom size"),
    "shoulder_fit": _spec("shoulder_fit", "fit", "Shoulder fit preference"),
    "waist_fit": _spec("waist_fit", "fit", "Waist fit preference"),
    "hip_fit": _spec("hip_fit", "fit", "Hip fit preference"),
    "preferred_coverage": _spec("preferred_coverage", "fit", "Preferred coverage"),
    "sleeve_preferences": _spec("sleeve_preferences", "fit", "Sleeve preferences", kind="list"),
    "neckline_preferences": _spec("neckline_preferences", "fit", "Neckline preferences", kind="list"),
    "footwear_comfort": _spec("footwear_comfort", "fit", "Footwear comfort"),
    "silhouettes_liked": _spec("silhouettes_liked", "fit", "Silhouettes liked", kind="list"),
    "silhouettes_avoided": _spec("silhouettes_avoided", "fit", "Silhouettes avoided", kind="list"),
    # Lifestyle.
    "city": _spec("city", "lifestyle", "City"),
    "climate": _spec("climate", "lifestyle", "Climate"),
    "work_context": _spec("work_context", "lifestyle", "Work context"),
    "routine": _spec("routine", "lifestyle", "Routine"),
    "activity_level": _spec("activity_level", "lifestyle", "Activity level"),
    "travel_frequency": _spec("travel_frequency", "lifestyle", "Travel frequency"),
    "budget": _spec("budget", "lifestyle", "Budget"),
    "calendar_patterns": _spec("calendar_patterns", "lifestyle", "Calendar patterns", kind="list"),
    # Goals and user-entered constraints/wellness context.
    "current_goal": _spec("current_goal", "goals", "Current goal"),
    "appearance_goals": _spec("appearance_goals", "goals", "Appearance goals", kind="list"),
    "allergies": _spec("allergies", "constraints", "Allergies you entered", kind="list"),
    "sleep_pattern": _spec("sleep_pattern", "wellness", "Sleep pattern"),
    "hydration_habits": _spec("hydration_habits", "wellness", "Hydration habits"),
    "stress_level": _spec("stress_level", "wellness", "Stress level"),
    "workout_frequency": _spec("workout_frequency", "wellness", "Workout frequency"),
}


ALLOWED_SOURCES = {
    "user_declared",
    "photo_observed",
    "inventory_inferred",
    "behavior_inferred",
    "integration",
    "stylist_verified",
}

VERIFICATION_STATES = {"unverified", "confirmed", "rejected", "not_sure", "superseded"}


def validate_attribute(key: str, value: Any) -> Any:
    spec = ATTRIBUTE_REGISTRY.get(key)
    if spec is None:
        raise ValueError(f"Unknown profile attribute: {key}")
    if value is None:
        raise ValueError(f"{spec.label} cannot be empty")
    if spec.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{spec.label} must be a number")
        number = float(value)
        if spec.minimum is not None and number < spec.minimum:
            raise ValueError(f"{spec.label} must be at least {spec.minimum:g}")
        if spec.maximum is not None and number > spec.maximum:
            raise ValueError(f"{spec.label} must be at most {spec.maximum:g}")
        return int(number) if number.is_integer() else number
    if spec.kind == "list":
        if not isinstance(value, list) or len(value) > 30:
            raise ValueError(f"{spec.label} must be a list of up to 30 items")
        cleaned = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 100:
                raise ValueError(f"Every {spec.label.lower()} item must be short text")
            if item.strip() not in cleaned:
                cleaned.append(item.strip())
        return cleaned
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 300:
        raise ValueError(f"{spec.label} must be short text")
    cleaned = value.strip()
    if spec.choices and cleaned.lower() not in spec.choices:
        raise ValueError(f"{spec.label} must be one of: {', '.join(spec.choices)}")
    return cleaned
