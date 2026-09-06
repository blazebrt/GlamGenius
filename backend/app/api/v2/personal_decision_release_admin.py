"""Admin-only Step 8H governed decision release management routes.

There is deliberately no customer route here. Step 8H builds the machinery
that makes reviewed decision knowledge activatable; the surface that shows a
person a BUY / WAIT / SKIP is a later milestone, and shipping it alongside the
release mechanism would mean the first real release had somewhere to appear
before anyone had reviewed what appearing means.

``release_key`` is never accepted from a request. One production series exists
and its key is a constant: a client-supplied key would let an unreviewed
series be created and activated beside the reviewed one, leaving the runtime
loader to choose between them.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.admin import require_admin
from app.domains.personal_decision_release import authoring
from app.domains.personal_decision_release.runtime import (
    load_active_personal_decision_release,
)
from app.domains.personal_decision_release.validation import ReleaseVerification
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount

router = APIRouter(
    prefix="/admin/personal-decision-releases",
    tags=["admin-personal-decision-releases"],
)


class ManifestBody(BaseModel):
    """The manifest arrives as an opaque document and is parsed by the domain.

    Deliberately not modelled field by field in Pydantic. The manifest schema
    is owned by ``personal_decision_release.manifest``, which also parses
    persisted rows; a second definition here would be the one that drifts, and
    a request would then be accepted under rules the database read never
    applies.
    """

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]


class ReviewVerificationBody(BaseModel):
    """The named human attestations. Every field is required and explicit.

    No default is provided for any checkpoint. A default would mean an
    attestation nobody made, which is the one thing this record exists to make
    impossible.
    """

    model_config = ConfigDict(extra="forbid")

    founder_review_completed: StrictBool
    claude_review_completed: StrictBool
    codex_review_completed: StrictBool
    independent_reviews_agree: StrictBool
    adversarial_review_passed: StrictBool
    unresolved_doubt: StrictBool

    def to_verification(self) -> ReleaseVerification:
        return ReleaseVerification(
            founder_review_completed=self.founder_review_completed,
            claude_review_completed=self.claude_review_completed,
            codex_review_completed=self.codex_review_completed,
            independent_reviews_agree=self.independent_reviews_agree,
            adversarial_review_passed=self.adversarial_review_passed,
            unresolved_doubt=self.unresolved_doubt,
        )


def _actor(current: CurrentAccount) -> str:
    return current.account_id_str


@router.get("")
async def list_releases(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.list_personal_decision_releases(
        session,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/active")
async def active_release(
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The active release as the runtime actually loads it, or ``None``.

    Goes through the runtime loader rather than a plain query so an admin sees
    what production sees, including a refusal if the stored manifest no longer
    matches its hash.
    """
    release = await load_active_personal_decision_release(session)
    if release is None:
        return None
    return {
        "release_id": str(release.release_id),
        "release_version": release.release_version,
        "content_hash": release.content_hash,
        "counts": {
            "semantic_rules": len(release.semantic_rules),
            "policy_rules": len(release.policy_rules),
            "explanation_rules": len(release.explanation_rules),
        },
    }


@router.get("/{release_id}")
async def get_release(
    release_id: uuid.UUID,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.get_personal_decision_release(session, release_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_release(
    body: ManifestBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.create_personal_decision_release_draft(
        session,
        body.manifest,
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.put("/{release_id}")
async def edit_release(
    release_id: uuid.UUID,
    body: ManifestBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Edit a draft. Never a way to create a new version of a reviewed release.

    An approved, active or retired release is immutable and this returns a
    conflict for all three. Superseding one is an explicit clone.
    """
    result = await authoring.edit_personal_decision_release_draft(
        session,
        release_id,
        body.manifest,
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.post("/{release_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_release(
    release_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.clone_personal_decision_release(
        session,
        release_id,
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.post("/{release_id}/review-verification")
async def record_review_verification(
    release_id: uuid.UUID,
    body: ReviewVerificationBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.record_personal_decision_release_verification(
        session,
        release_id,
        verification=body.to_verification(),
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.post("/{release_id}/validate")
async def validate_release(
    release_id: uuid.UUID,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Run the full readiness check and change nothing."""
    return await authoring.validate_personal_decision_release(session, release_id)


@router.post("/{release_id}/approve")
async def approve_release(
    release_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await authoring.approve_personal_decision_release(
        session,
        release_id,
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.post("/{release_id}/activate")
async def activate_release(
    release_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Revalidate against today's evidence, then switch atomically."""
    result = await authoring.activate_personal_decision_release(
        session,
        release_id,
        actor=_actor(current),
    )
    await session.commit()
    return result


@router.post("/{release_id}/deactivate")
async def deactivate_release(
    release_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Emergency stop. Leaves zero active releases, which is a safe state."""
    result = await authoring.deactivate_personal_decision_release(
        session,
        release_id,
        actor=_actor(current),
    )
    await session.commit()
    return result
