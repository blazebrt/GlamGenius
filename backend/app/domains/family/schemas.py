"""Family Circle boundary schemas. No free-text member fields."""
from __future__ import annotations
import uuid
from typing import Literal
from pydantic import BaseModel

ProfileRelation = Literal["adult", "child", "other"]
class FamilyProfileCreate(BaseModel):
    relation: ProfileRelation
class FamilyProfilePatch(BaseModel):
    active: bool
class FamilyProfileResponse(BaseModel):
    id: uuid.UUID
    position: int
    relation: str
    label: str
    active: bool
class FamilyCircleResponse(BaseModel):
    enabled: bool
    max_profiles: int
    profiles: list[FamilyProfileResponse]
    shared_shelf: bool
    shared_verdicts: bool
