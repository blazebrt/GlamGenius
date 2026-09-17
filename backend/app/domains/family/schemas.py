"""Family Circle boundary schemas. No free-text member fields."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

ProfileRelation = Literal["adult", "child", "other"]
# The bands the product asks for. ``self`` is not offered because the signed-in
# person is not created through this route, and a date of birth is not offered
# because the only line that matters is under 12.
ProfileAgeBand = Literal["under_12", "teen_12_17", "adult_18_plus", "not_stated"]


class FamilyProfileCreate(BaseModel):
    """A new member: a relation, and optionally how old they are.

    Unknown fields are refused here for the same reason they are on the patch,
    and the create direction is the more dangerous of the two. Somebody adding
    a child and mistyping the key would otherwise be answered 201 with a member
    stored as ``not_stated`` — a child the server has no reason to hand over
    for, created by a request that said it was one.
    """

    model_config = ConfigDict(extra="forbid")

    relation: ProfileRelation
    age_band: ProfileAgeBand = "not_stated"


class FamilyProfilePatch(BaseModel):
    """A partial update: send only what is actually changing.

    ``active`` used to be the single required field, so a body that named
    nothing was refused. Both halves of that stay true. A client that only
    knows how to send ``active`` still behaves exactly as it did, and a request
    that changes nothing is still refused rather than answered with a cheerful
    200 that did not do anything.

    Neither field accepts ``null``. Elsewhere in this application a null in a
    patch means "leave it alone", and for a field that decides whether the
    product hands somebody to a clinician, quietly ignoring what the caller
    wrote is the wrong way to fail. Clearing a stored age is spelled
    ``not_stated``, which is a value in the vocabulary rather than an absence.

    ``extra="forbid"`` for the same reason: ``{"age_bands": "adult_18_plus"}``
    is a typo that must not look like a successful correction.
    """

    model_config = ConfigDict(extra="forbid")

    active: StrictBool | None = None
    age_band: ProfileAgeBand | None = None

    @model_validator(mode="after")
    def _names_a_real_change(self) -> FamilyProfilePatch:
        if not self.model_fields_set:
            raise ValueError("send at least one of 'active' or 'age_band'")
        for field in sorted(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(
                    f"'{field}' cannot be null; omit it to leave it unchanged"
                )
        return self


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
