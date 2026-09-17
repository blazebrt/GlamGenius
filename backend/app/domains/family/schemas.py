"""Family Circle boundary schemas. No free-text member fields."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel

ProfileRelation = Literal["adult", "child", "other"]
# The bands the product asks for. ``self`` is not offered because the signed-in
# person is not created through this route, and a date of birth is not offered
# because the only line that matters is under 12.
ProfileAgeBand = Literal["under_12", "teen_12_17", "adult_18_plus", "not_stated"]


class FamilyProfileCreate(BaseModel):
    relation: ProfileRelation
    age_band: ProfileAgeBand = "not_stated"


class FamilyProfilePatch(BaseModel):
    active: bool


class FamilyProfileResponse(BaseModel):
    id: uuid.UUID
    position: int
    relation: str
    label: str
    age_band: str
    active: bool


class FamilyCircleResponse(BaseModel):
    enabled: bool
    max_profiles: int
    profiles: list[FamilyProfileResponse]
    shared_shelf: bool
    shared_verdicts: bool
