"""Admin-only Step 8G personal-applicability evidence authoring routes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.admin import require_admin
from app.domains.evidence import authoring as evidence_authoring
from app.domains.personal_applicability import authoring
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount

router = APIRouter(prefix="/admin/personal-applicability", tags=["admin-personal-applicability"])


class ConditionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str = Field(min_length=1, max_length=160)
    values: list[str] = Field(min_length=1, max_length=30)


class ExistingSourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["existing"]
    source_key: str = Field(min_length=1, max_length=160)
    locator: str | None = Field(default=None, max_length=512)


class NewSourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["new"]
    source_type: str = Field(min_length=1, max_length=48)
    title: str = Field(min_length=1, max_length=512)
    publisher: str = Field(min_length=1, max_length=256)
    canonical_url: str = Field(min_length=1, max_length=2000)
    license_or_use_note: str = Field(min_length=1, max_length=8000)
    locator: str | None = Field(default=None, max_length=512)
    publication_date: date | None = None
    version_or_revision: str | None = Field(default=None, max_length=160)
    jurisdiction: str | None = Field(default=None, max_length=128)


SourceBody = Annotated[ExistingSourceBody | NewSourceBody, Field(discriminator="mode")]


class PersonalApplicabilityEntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: PersonalApplicabilityCategory
    substance_key: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=8000)
    scope: str = Field(min_length=1, max_length=8000)
    evidence_strength: str = Field(min_length=1, max_length=32)
    strength_rationale: str = Field(min_length=1, max_length=8000)
    conditions: list[ConditionBody] = Field(min_length=1, max_length=4)
    sources: list[SourceBody] = Field(min_length=1, max_length=5)

    def to_input(self) -> authoring.PersonalApplicabilityDraftInput:
        source_inputs: list[authoring.AuthoringSourceInput] = []
        for source in self.sources:
            if isinstance(source, ExistingSourceBody):
                source_inputs.append(authoring.ExistingSourceInput(
                    source_key=source.source_key,
                    locator=source.locator,
                ))
            else:
                source_inputs.append(authoring.NewSourceInput(
                    source_type=source.source_type,
                    title=source.title,
                    publisher=source.publisher,
                    canonical_url=source.canonical_url,
                    license_or_use_note=source.license_or_use_note,
                    locator=source.locator,
                    publication_date=source.publication_date,
                    version_or_revision=source.version_or_revision,
                    jurisdiction=source.jurisdiction,
                ))
        return authoring.PersonalApplicabilityDraftInput(
            category=self.category,
            substance_key=self.substance_key,
            summary=self.summary,
            scope=self.scope,
            evidence_strength=self.evidence_strength,
            strength_rationale=self.strength_rationale,
            conditions=tuple(
                authoring.AuthoringConditionInput(
                    fact_key=condition.fact_key,
                    values=tuple(condition.values),
                )
                for condition in self.conditions
            ),
            sources=tuple(source_inputs),
        )


class PublicationVerificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_opened: bool
    founder_verified_fact: bool
    claude_review_completed: bool
    codex_review_completed: bool
    independent_reviews_agree: bool
    adversarial_review_passed: bool
    unresolved_doubt: bool = False

    def to_input(self) -> evidence_authoring.VerificationInput:
        return evidence_authoring.VerificationInput(**self.model_dump())


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


def _actor(current: CurrentAccount) -> str:
    return current.account_id_str


@router.get("/vocabulary")
async def vocabulary(_: CurrentAccount = Depends(require_admin)):
    return authoring.personal_applicability_vocabulary()


@router.get("/entries")
async def list_entries(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.list_personal_applicability_entries(
        session,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: uuid.UUID,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.get_personal_applicability_entry(session, entry_id)


@router.get("/entries/{entry_id}/versions")
async def versions(
    entry_id: uuid.UUID,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.versions_of_personal_applicability_entry(session, entry_id)


@router.post("/entries", status_code=status.HTTP_201_CREATED)
async def create_entry(
    body: PersonalApplicabilityEntryBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.create_personal_applicability_draft(
        session,
        body.to_input(),
        author=_actor(current),
    )
    await session.commit()
    return result


@router.put("/entries/{entry_id}")
async def edit_entry(
    entry_id: uuid.UUID,
    body: PersonalApplicabilityEntryBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.edit_personal_applicability_entry(
        session,
        entry_id,
        body.to_input(),
        author=_actor(current),
    )
    await session.commit()
    return result


@router.post("/entries/{entry_id}/approve")
async def approve_entry(
    entry_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.approve_personal_applicability_entry(
        session,
        entry_id,
        reviewer=_actor(current),
    )
    await session.commit()
    return result


@router.post("/entries/{entry_id}/publication-verification")
async def record_publication_verification(
    entry_id: uuid.UUID,
    body: PublicationVerificationBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.record_personal_applicability_publication_verification(
        session,
        entry_id,
        verification=body.to_input(),
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.post("/entries/{entry_id}/publish")
async def publish_entry(
    entry_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.publish_personal_applicability_entry(
        session,
        entry_id,
        publisher=_actor(current),
    )
    await session.commit()
    return result


@router.post("/entries/{entry_id}/reject")
async def reject_entry(
    entry_id: uuid.UUID,
    body: RejectBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.reject_personal_applicability_entry(
        session,
        entry_id,
        reviewer=_actor(current),
        reason=body.reason,
    )
    await session.commit()
    return result


__all__ = ["router"]
