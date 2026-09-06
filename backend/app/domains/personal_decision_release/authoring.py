"""The release lifecycle: draft, edit, clone, verify, approve, activate, retire.

Every transition here is deliberate and one-way. A reviewed bundle is
immutable from approval onward, activation revalidates everything against
today's evidence before it changes anything, and the switch from one active
release to another happens in a single transaction or not at all.

Two rules shape most of the code below.

**Re-read before deciding.** Approval and activation validate the persisted
row, not the request that asked for them. A manifest can be edited directly in
the database, and a check that trusts what the API was handed would bless
something else entirely. That is also why the content hash is recomputed at
every gate rather than only when the manifest is written.

**Never choose.** Where more than one thing could be the active release, or a
release could plausibly be revived, this module raises instead of picking. The
whole point of a governed release is that a human decided; a tie-break here
would be an unreviewed decision wearing the costume of an implementation
detail.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.personal_decision_release.enums import (
    ALLOWED_RELEASE_TRANSITIONS,
    PERSONAL_DECISION_RELEASE_KEY,
    PersonalDecisionReleaseStatus,
    PersonalDecisionReleaseValidationCode,
)
from app.domains.personal_decision_release.manifest import (
    PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
    PersonalDecisionReleaseManifest,
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.validation import (
    PersonalDecisionReleaseValidationError,
    ReleaseEvidenceReport,
    ReleaseVerification,
    assert_manifest_carries_no_personal_data,
    assert_verification_permits_approval,
    parse_release_verification,
    validate_release_manifest,
    validate_release_structure,
)
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import ConflictError, NotFoundError


def _conflict(code: PersonalDecisionReleaseValidationCode, message: str, release) -> ConflictError:
    error = ConflictError(message, current_version=release.release_version)
    error.extra["reason"] = code.value
    return error


async def _release(session: AsyncSession, release_id: uuid.UUID) -> PersonalDecisionRelease:
    release = await session.get(PersonalDecisionRelease, release_id)
    if release is None:
        raise NotFoundError("Decision release not found.")
    return release


def _assert_transition(
    release: PersonalDecisionRelease,
    target: PersonalDecisionReleaseStatus,
) -> None:
    current = PersonalDecisionReleaseStatus(release.status)
    if target not in ALLOWED_RELEASE_TRANSITIONS[current]:
        raise _conflict(
            PersonalDecisionReleaseValidationCode.RELEASE_NOT_EDITABLE,
            f"A {current.value} release cannot become {target.value}.",
            release,
        )


def persisted_manifest(release: PersonalDecisionRelease) -> PersonalDecisionReleaseManifest:
    """Parse the stored manifest and prove it is still the reviewed one.

    The hash is recomputed from the canonical form of what is actually in the
    column. A stored manifest that no longer hashes to its stored hash was
    changed outside the reviewed path, and the only safe answer is to refuse
    it -- repairing either value would make the other one a lie.
    """
    manifest = parse_release_manifest(release.manifest)
    if manifest_content_hash(manifest) != release.content_hash:
        raise PersonalDecisionReleaseValidationError(
            PersonalDecisionReleaseValidationCode.RELEASE_CONTENT_HASH_MISMATCH,
            "The stored manifest does not match its recorded content hash.",
        )
    return manifest


def release_view(release: PersonalDecisionRelease) -> dict[str, Any]:
    """Everything an admin needs to inspect one release. No customer data."""
    manifest = release.manifest or {}
    return {
        "id": str(release.id),
        "release_key": release.release_key,
        "release_version": release.release_version,
        "status": release.status,
        "manifest_schema_version": release.manifest_schema_version,
        "content_hash": release.content_hash,
        "counts": {
            "semantic_rules": len(manifest.get("semantic_rules") or []),
            "policy_rules": len(manifest.get("policy_rules") or []),
            "explanation_rules": len(manifest.get("explanation_rules") or []),
        },
        "manifest": manifest,
        "review_verification": release.review_verification,
        "created_by": release.created_by,
        "created_at": release.created_at,
        "approved_by": release.approved_by,
        "approved_at": release.approved_at,
        "activated_by": release.activated_by,
        "activated_at": release.activated_at,
        "retired_by": release.retired_by,
        "retired_at": release.retired_at,
        "supersedes_release_id": (
            str(release.supersedes_release_id) if release.supersedes_release_id else None
        ),
    }


async def _next_release_version(session: AsyncSession) -> int:
    current = (
        await session.execute(
            select(func.max(PersonalDecisionRelease.release_version)).where(
                PersonalDecisionRelease.release_key == PERSONAL_DECISION_RELEASE_KEY
            )
        )
    ).scalar_one_or_none()
    return int(current or 0) + 1


async def _new_draft(
    session: AsyncSession,
    manifest: PersonalDecisionReleaseManifest,
    *,
    actor: str,
    supersedes_release_id: uuid.UUID | None,
) -> PersonalDecisionRelease:
    release = PersonalDecisionRelease(
        release_key=PERSONAL_DECISION_RELEASE_KEY,
        release_version=await _next_release_version(session),
        manifest_schema_version=PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
        manifest=canonical_manifest(manifest),
        content_hash=manifest_content_hash(manifest),
        status=PersonalDecisionReleaseStatus.DRAFT.value,
        review_verification=None,
        created_by=actor,
        supersedes_release_id=supersedes_release_id,
    )
    session.add(release)
    await session.flush()
    return release


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------


async def create_personal_decision_release_draft(
    session: AsyncSession,
    manifest_document: Any,
    *,
    actor: str,
    source_release_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Start a new draft from a manifest document.

    A draft is allowed to be incomplete -- a semantic rule typed before its
    policy exists is normal mid-review -- so only the structural rules that
    always hold are applied here. The completeness rules run at approval,
    which is where an incomplete bundle would actually become dangerous.
    """
    assert_manifest_carries_no_personal_data(manifest_document)
    manifest = parse_release_manifest(manifest_document)
    validate_release_structure(manifest, require_complete=False)
    release = await _new_draft(
        session, manifest, actor=actor, supersedes_release_id=source_release_id
    )
    return release_view(release)


async def edit_personal_decision_release_draft(
    session: AsyncSession,
    release_id: uuid.UUID,
    manifest_document: Any,
    *,
    actor: str,
) -> dict[str, Any]:
    """Replace a draft's manifest, and forget every review of the old one.

    Clearing ``review_verification`` is the point of this function as much as
    the new manifest is. An attestation says a named person read *these*
    rules; carrying it across an edit would let changed rules inherit the
    approval of rules nobody compared them to.
    """
    release = await _release(session, release_id)
    if release.status != PersonalDecisionReleaseStatus.DRAFT.value:
        raise _conflict(
            PersonalDecisionReleaseValidationCode.RELEASE_NOT_EDITABLE,
            f"A {release.status} release is immutable; clone it into a new draft instead.",
            release,
        )

    assert_manifest_carries_no_personal_data(manifest_document)
    manifest = parse_release_manifest(manifest_document)
    validate_release_structure(manifest, require_complete=False)

    release.manifest = canonical_manifest(manifest)
    release.manifest_schema_version = PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION
    release.content_hash = manifest_content_hash(manifest)
    release.review_verification = None
    release.created_by = actor
    await session.flush()
    return release_view(release)


async def clone_personal_decision_release(
    session: AsyncSession,
    release_id: uuid.UUID,
    *,
    actor: str,
) -> dict[str, Any]:
    """Copy any release into a fresh draft for independent review.

    This is the only route "back". A retired release is never reactivated and
    an approved one is never reopened: both would put rules into production
    that nobody looked at against today's evidence. Cloning keeps the exact
    manifest and drops everything else -- verification, approval, activation
    and retirement all start empty, and the clone must earn its own.
    """
    source = await _release(session, release_id)
    manifest = persisted_manifest(source)
    release = await _new_draft(session, manifest, actor=actor, supersedes_release_id=source.id)
    return release_view(release)


async def record_personal_decision_release_verification(
    session: AsyncSession,
    release_id: uuid.UUID,
    *,
    verification: ReleaseVerification,
    actor: str,
) -> dict[str, Any]:
    """Record the named attestations. This does not approve anything."""
    del actor
    release = await _release(session, release_id)
    if release.status != PersonalDecisionReleaseStatus.DRAFT.value:
        raise _conflict(
            PersonalDecisionReleaseValidationCode.RELEASE_NOT_EDITABLE,
            f"Review attestations may only be recorded on a draft, not on a {release.status} "
            "release.",
            release,
        )
    release.review_verification = verification.as_dict()
    await session.flush()
    return release_view(release)


# ---------------------------------------------------------------------------
# Validation, approval, activation
# ---------------------------------------------------------------------------


async def validate_personal_decision_release(
    session: AsyncSession,
    release_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the full readiness check without changing anything.

    Deliberately available on its own so a reviewer can see what a release
    would fail on before pressing approve, and so the same check can be re-run
    later against evidence that has moved.
    """
    release = await _release(session, release_id)
    manifest = persisted_manifest(release)
    report = await validate_release_manifest(session, manifest, require_complete=True)
    verification = parse_release_verification(release.review_verification)
    return {
        "id": str(release.id),
        "release_version": release.release_version,
        "status": release.status,
        "content_hash": release.content_hash,
        "ready": True,
        "verification_recorded": verification is not None,
        "semantic_evidence_checked": report.semantic_evidence_checked,
        "policies_checked": report.policies_checked,
        "explanations_checked": report.explanations_checked,
    }


async def approve_personal_decision_release(
    session: AsyncSession,
    release_id: uuid.UUID,
    *,
    actor: str,
) -> dict[str, Any]:
    """Approve a draft that survives the complete readiness check.

    Approval is not activation. It records that this exact bundle was reviewed
    and holds together; whether production should start using it is a separate
    decision, taken later and revalidated then.
    """
    release = await _release(session, release_id)
    _assert_transition(release, PersonalDecisionReleaseStatus.APPROVED)

    manifest = persisted_manifest(release)
    assert_verification_permits_approval(release.review_verification)
    await validate_release_manifest(session, manifest, require_complete=True)

    release.status = PersonalDecisionReleaseStatus.APPROVED.value
    release.approved_by = actor
    release.approved_at = utcnow()
    await session.flush()
    return release_view(release)


async def _locked_series(session: AsyncSession) -> list[PersonalDecisionRelease]:
    """Lock every row in the series, in a deterministic order.

    Two admins pressing activate at the same moment is the case the
    application checks cannot handle on their own: both would read "one active
    release", both would retire it, and both would insert their own. Locking
    the whole series serialises them, and the partial unique index is the
    backstop if the lock is ever lost.
    """
    return list(
        (
            await session.execute(
                select(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.release_key == PERSONAL_DECISION_RELEASE_KEY)
                .order_by(PersonalDecisionRelease.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def activate_personal_decision_release(
    session: AsyncSession,
    release_id: uuid.UUID,
    *,
    actor: str,
) -> dict[str, Any]:
    """Make an approved release the active one, atomically, or not at all.

    The full cross-validation runs again here against current evidence, and
    that repetition is the point. Evidence moves between review and
    activation: a claim can be superseded by a new version, a source retired,
    a URL removed, a licence note blanked. A release that was coherent at
    approval may name evidence that no longer exists, and activating it would
    put a citation in front of a customer that nobody can open.

    Nothing changes until every check has passed. If validation fails the
    candidate stays APPROVED and the current active release stays ACTIVE --
    there is no half-switched state in which production has neither.
    """
    series = await _locked_series(session)
    by_id = {row.id: row for row in series}
    release = by_id.get(release_id)
    if release is None:
        raise NotFoundError("Decision release not found.")

    _assert_transition(release, PersonalDecisionReleaseStatus.ACTIVE)

    manifest = persisted_manifest(release)
    await validate_release_manifest(session, manifest, require_complete=True)

    currently_active = [
        row for row in series if row.status == PersonalDecisionReleaseStatus.ACTIVE.value
    ]
    if len(currently_active) > 1:
        # Never pick one. Two active releases means production has already been
        # answering with knowledge nobody chose, and the honest response is to
        # stop rather than to quietly settle it.
        raise _conflict(
            PersonalDecisionReleaseValidationCode.MULTIPLE_ACTIVE_DECISION_RELEASES,
            "More than one release is active; refusing to choose between them.",
            release,
        )

    moment = utcnow()
    supersedes = release.supersedes_release_id
    for previous in currently_active:
        previous.status = PersonalDecisionReleaseStatus.RETIRED.value
        previous.retired_by = actor
        previous.retired_at = moment
        if supersedes is None:
            supersedes = previous.id

    # Retire first, in its own statement. The partial unique index is a plain
    # unique index and is therefore checked per statement, not at commit -- so
    # if the unit of work happened to write the new ACTIVE row before the old
    # one stopped being active, a correct replacement would fail on its own
    # invariant. Both statements are still in one transaction: the switch is
    # atomic, it is only the order within it that has to be explicit.
    await session.flush()

    release.supersedes_release_id = supersedes
    release.status = PersonalDecisionReleaseStatus.ACTIVE.value
    release.activated_by = actor
    release.activated_at = moment
    await session.flush()
    return release_view(release)


async def deactivate_personal_decision_release(
    session: AsyncSession,
    release_id: uuid.UUID,
    *,
    actor: str,
) -> dict[str, Any]:
    """Emergency stop: retire the active release and activate nothing.

    Zero active releases is a safe state, not a broken one. Production falls
    back to no reviewed rules at all and therefore emits no BUY / WAIT / SKIP,
    which is exactly what should happen when the reviewed knowledge is in
    doubt. The previous release is deliberately not revived: rolling back is
    itself a decision, and it is made by cloning, reviewing and activating.
    """
    release = await _release(session, release_id)
    _assert_transition(release, PersonalDecisionReleaseStatus.RETIRED)

    release.status = PersonalDecisionReleaseStatus.RETIRED.value
    release.retired_by = actor
    release.retired_at = utcnow()
    await session.flush()
    return release_view(release)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def get_personal_decision_release(
    session: AsyncSession,
    release_id: uuid.UUID,
) -> dict[str, Any]:
    return release_view(await _release(session, release_id))


async def list_personal_decision_releases(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query = select(PersonalDecisionRelease).where(
        PersonalDecisionRelease.release_key == PERSONAL_DECISION_RELEASE_KEY
    )
    if status is not None:
        query = query.where(PersonalDecisionRelease.status == status)
    query = (
        query.order_by(PersonalDecisionRelease.release_version.desc())
        .limit(max(1, min(limit, 200)))
        .offset(max(0, offset))
    )
    return [release_view(row) for row in (await session.execute(query)).scalars().all()]


__all__ = [
    "ReleaseEvidenceReport",
    "activate_personal_decision_release",
    "approve_personal_decision_release",
    "clone_personal_decision_release",
    "create_personal_decision_release_draft",
    "deactivate_personal_decision_release",
    "edit_personal_decision_release_draft",
    "get_personal_decision_release",
    "list_personal_decision_releases",
    "persisted_manifest",
    "record_personal_decision_release_verification",
    "release_view",
    "validate_personal_decision_release",
]
