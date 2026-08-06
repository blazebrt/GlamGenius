"""API and AI schemas for the appearance digital twin."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AttributeUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: Any


class ProfilePatch(BaseModel):
    attributes: list[AttributeUpdate] = Field(min_length=1, max_length=40)


class ObservationEdit(BaseModel):
    value: Any
    verification_state: Optional[Literal["unverified", "not_sure"]] = None


class BaselineRequest(BaseModel):
    image_base64: str = Field(min_length=20)


class BaselineObservation(BaseModel):
    key: Literal[
        "skin_tone", "undertone", "face_shape", "visible_skin_characteristics",
        "hair_type", "hair_texture", "hair_density"
    ]
    value: Any
    confidence: float = Field(ge=0, le=1)
    why: str = Field(min_length=1, max_length=500)


class PaletteColour(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    hex: Optional[str] = Field(default=None, max_length=16)
    why: str = Field(min_length=1, max_length=300)


class BaselineAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    image_quality: Literal["low", "usable", "good"]
    image_quality_notes: str = Field(max_length=300)
    observations: list[BaselineObservation] = Field(default_factory=list, max_length=12)
    colour_palette: list[PaletteColour] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=500)
    disclaimer: str = "Style guidance from visible photo details, not medical advice."

    @model_validator(mode="after")
    def useful_when_readable(self):
        if self.image_quality != "low" and not self.colour_palette:
            raise ValueError("A readable baseline needs a colour palette")
        return self


class OnboardingStepRequest(BaseModel):
    step: Literal[
        "goal", "style_preferences", "fit_preferences", "lifestyle",
        "appearance_photo", "colour_palette", "confirm_attributes", "preview"
    ]
    data: dict[str, Any] = Field(default_factory=dict)
    skipped: bool = False

    @model_validator(mode="after")
    def never_accept_photo_bytes(self):
        if any(key in self.data for key in ("image", "image_base64", "photo", "photo_base64")):
            raise ValueError("Onboarding step state cannot store photo data")
        return self
