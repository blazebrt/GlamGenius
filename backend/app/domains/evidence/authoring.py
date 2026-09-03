"""Authoring knowledge entries — the service behind the admin tool.

This is not a second evidence system. An "entry" is an ``EvidenceClaim`` plus
the ``EvidenceSource`` it cites, linked by ``EvidenceClaimSource``, exactly as
the rest of the product already reads them. The tool adds a workflow on top:

    draft ──approve──> approved ──publish──> published
      │
      └──reject──> rejected   (always with a reason)

Two rules are enforced here rather than left to the caller:

* **No approval without a source URL.** ``assert_claim_approvable`` in
  ``service.py`` already required a reviewed, active, non-background source;
  this module adds the requirement that the source has a URL somebody can open,
  which is what the evidence rule in PRODUCT_CONSTITUTION.md actually promises.
* **Editing never overwrites.** Editing a published entry writes a new row at
  the next ``claim_version``, points it at the old one through
  ``supersedes_claim_id``, and marks the old one superseded. The old version
  stays readable forever.
"""
from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.enums import (
    ClaimSourceRelationship,
    ClaimStatus,
    ClaimType,
    EvidenceDomain,
    EvidenceTier,
    ReviewStatus,
    SourceStatus,
    SourceType,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.evidence.service import publication_verification_complete
from app.domains.evidence.urls import openable_url
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import ConflictError, NotFoundError, ValidationFailedError

# The states an author can move an entry between, and what may follow what.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    ReviewStatus.DRAFT.value: frozenset({ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value}),
    ReviewStatus.APPROVED.value: frozenset({ReviewStatus.PUBLISHED.value, ReviewStatus.REJECTED.value}),
    ReviewStatus.PUBLISHED.value: frozenset(),
    ReviewStatus.REJECTED.value: frozenset({ReviewStatus.DRAFT.value}),
}

# Tiers that describe an absence rather than a finding. An entry may carry one,
# but it can never be presented as a supported claim.
NON_ASSERTIVE_TIERS = frozenset({
    EvidenceTier.NOT_ENOUGH_INFORMATION.value,
    EvidenceTier.AVOID.value,
})

CSV_COLUMNS = (
    "subject_type", "subject_key", "claim", "value", "unit",
    "source_name", "source_url", "evidence_tier", "notes",
)

_MAX_CSV_ROWS = 500


@dataclass(frozen=True)
class EntryInput:
    """One entry as the form collects it."""

    subject_type: str
    subject_key: str
    claim: str
    source_name: str
    source_url: str
    evidence_tier: str
    value: str | None = None
    unit: str | None = None
    notes: str | None = None
    domain: str = EvidenceDomain.NUTRITION.value


@dataclass(frozen=True)
class VerificationInput:
    """Named governance attestations; never inferred from a URL or an AI run."""

    source_opened: bool
    founder_verified_fact: bool
    claude_review_completed: bool
    codex_review_completed: bool
    independent_reviews_agree: bool
    adversarial_review_passed: bool
    unresolved_doubt: bool = False


def _require(value: str | None, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationFailedError(f"{field} is required.", field=field)
    return text


def _valid_url(value: str | None) -> str | None:
    """A source URL has to be something a reviewer can actually open.

    Delegates to the one shared validator so this and the public-knowledge
    reader cannot disagree about what "openable" means. The intent is unchanged
    — an absolute http(s) URL, or nothing — it is simply now checked by parsing
    rather than by looking at the first eight characters.
    """
    return openable_url(value)


def _validate_tier(tier: str) -> str:
    if tier not in {t.value for t in EvidenceTier}:
        raise ValidationFailedError(
            f"evidence_tier must be one of: {', '.join(t.value for t in EvidenceTier)}.",
            field="evidence_tier",
        )
    return tier


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def serialize(claim: EvidenceClaim, source: EvidenceSource | None = None) -> dict[str, Any]:
    return {
        "id": str(claim.id),
        "claim_key": claim.claim_key,
        "version": claim.claim_version,
        "domain": claim.domain,
        "subject_type": claim.subject_type,
        "subject": claim.subject_key,
        "claim": claim.summary,
        "value": claim.value_text,
        "unit": claim.value_unit,
        "evidence_tier": claim.evidence_tier,
        "notes": claim.notes,
        "status": claim.review_status,
        "rejection_reason": claim.rejection_reason,
        "supersedes_id": str(claim.supersedes_claim_id) if claim.supersedes_claim_id else None,
        "reviewed_by": claim.reviewed_by,
        "reviewed_at": claim.reviewed_at.isoformat() if claim.reviewed_at else None,
        "published_by": claim.published_by,
        "published_at": claim.published_at.isoformat() if claim.published_at else None,
        "created_at": claim.created_at.isoformat() if claim.created_at else None,
        "source": {
            "name": source.title,
            "url": source.canonical_url,
            "publisher": source.publisher,
        } if source is not None else None,
        "verification": (claim.structured_value or {}).get("publication_verification"),
    }


async def _source_for(session: AsyncSession, claim: EvidenceClaim) -> EvidenceSource | None:
    return (await session.execute(
        select(EvidenceSource)
        .join(EvidenceClaimSource, EvidenceClaimSource.source_id == EvidenceSource.id)
        .where(EvidenceClaimSource.claim_id == claim.id)
        .order_by(EvidenceClaimSource.created_at.asc())
        .limit(1)
    )).scalar_one_or_none()


async def get_entry(session: AsyncSession, entry_id: uuid.UUID) -> dict[str, Any]:
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")
    return serialize(claim, await _source_for(session, claim))


async def list_entries(
    session: AsyncSession,
    *,
    subject_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """The review queue, filtered by subject type and status."""
    query = select(EvidenceClaim)
    if subject_type:
        query = query.where(EvidenceClaim.subject_type == subject_type)
    if status:
        query = query.where(EvidenceClaim.review_status == status)

    total = (await session.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar_one()

    rows = list((await session.execute(
        query.order_by(EvidenceClaim.created_at.desc())
        .limit(min(max(limit, 1), 200)).offset(max(offset, 0))
    )).scalars().all())

    entries = [serialize(claim, await _source_for(session, claim)) for claim in rows]
    return {"entries": entries, "total": total, "limit": limit, "offset": offset}


async def subject_types(session: AsyncSession) -> list[str]:
    """Whatever subject types actually exist, so the filter is never a guess."""
    return sorted(
        value for value in (await session.execute(
            select(EvidenceClaim.subject_type).distinct()
        )).scalars().all() if value
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
async def _ensure_source(
    session: AsyncSession, *, name: str, url: str | None, author: str,
) -> EvidenceSource:
    """Reuse a source with the same URL rather than duplicating it."""
    if url:
        existing = (await session.execute(
            select(EvidenceSource).where(EvidenceSource.canonical_url == url).limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            return existing

    source = EvidenceSource(
        source_key=f"authored:{uuid.uuid4().hex[:24]}",
        source_series_key=f"authored:{name.lower()[:100]}",
        source_type=SourceType.OTHER.value,
        title=name[:512],
        publisher=name[:256],
        canonical_url=url,
        accessed_at=utcnow(),
        status=SourceStatus.ACTIVE.value,
        license_or_use_note=f"Added through the authoring tool by {author}.",
    )
    session.add(source)
    await session.flush()
    return source


async def create_draft(
    session: AsyncSession, entry: EntryInput, *, author: str,
) -> dict[str, Any]:
    """Add one entry. It always starts as a draft, whatever else is supplied."""
    subject_type = _require(entry.subject_type, "subject_type")
    subject_key = _require(entry.subject_key, "subject")
    summary = _require(entry.claim, "claim")
    source_name = _require(entry.source_name, "source_name")
    tier = _validate_tier(_require(entry.evidence_tier, "evidence_tier"))

    if entry.domain not in {d.value for d in EvidenceDomain}:
        raise ValidationFailedError("Unknown subject area.", field="domain")

    claim = EvidenceClaim(
        claim_key=f"authored:{subject_type}:{subject_key}:{uuid.uuid4().hex[:12]}",
        claim_version=1,
        domain=entry.domain,
        subject_type=subject_type[:64],
        subject_key=subject_key[:200],
        claim_type=ClaimType.USAGE_CONTEXT.value,
        summary=summary,
        scope=f"Authored entry for {subject_key}.",
        review_status=ReviewStatus.DRAFT.value,
        evidence_tier=tier,
        value_text=(entry.value or "").strip()[:256] or None,
        value_unit=(entry.unit or "").strip()[:64] or None,
        notes=(entry.notes or "").strip() or None,
    )
    session.add(claim)
    await session.flush()

    source = await _ensure_source(
        session, name=source_name, url=_valid_url(entry.source_url), author=author,
    )
    session.add(EvidenceClaimSource(
        claim_id=claim.id,
        source_id=source.id,
        relationship=ClaimSourceRelationship.SUPPORTS.value,
    ))
    await session.flush()
    return serialize(claim, source)


def _assert_transition(claim: EvidenceClaim, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(claim.review_status)
    if allowed is None:
        raise ConflictError(
            f"An entry that is {claim.review_status} is not part of the authoring queue.",
            current_version=claim.claim_version,
        )
    if target not in allowed:
        raise ConflictError(
            f"An entry that is {claim.review_status} cannot become {target}.",
            current_version=claim.claim_version,
        )


async def assert_has_openable_source(session: AsyncSession, claim: EvidenceClaim) -> EvidenceSource:
    """The approval gate: no source URL, no approval.

    Enforced here, in the service, so every caller goes through it — the API,
    a script, or a future importer.
    """
    rows = (await session.execute(
        select(EvidenceSource)
        .join(EvidenceClaimSource, EvidenceClaimSource.source_id == EvidenceSource.id)
        .where(
            EvidenceClaimSource.claim_id == claim.id,
            EvidenceClaimSource.relationship != ClaimSourceRelationship.BACKGROUND.value,
        )
    )).scalars().all()

    for source in rows:
        if _valid_url(source.canonical_url):
            return source
    raise ValidationFailedError(
        "This entry cannot be approved without a source URL somebody can open. "
        "Add one, or set the tier to not_enough_information and leave it as a draft.",
        field="source_url",
    )


async def approve(
    session: AsyncSession, entry_id: uuid.UUID, *, reviewer: str,
) -> dict[str, Any]:
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")
    _assert_transition(claim, ReviewStatus.APPROVED.value)

    source = await assert_has_openable_source(session, claim)

    now = utcnow()
    claim.review_status = ReviewStatus.APPROVED.value
    claim.reviewed_by = reviewer[:160]
    claim.reviewed_at = now
    claim.rejection_reason = None
    # A tier that describes an absence must not be recorded as a supported claim.
    claim.claim_status = (
        ClaimStatus.UNSUPPORTED.value if claim.evidence_tier in NON_ASSERTIVE_TIERS
        else ClaimStatus.SUPPORTED.value
    )
    for link in (await session.execute(
        select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim.id)
    )).scalars().all():
        link.reviewed_by = reviewer[:160]
        link.reviewed_at = now
    await session.flush()
    return serialize(claim, source)


async def publish(
    session: AsyncSession, entry_id: uuid.UUID, *, publisher: str,
) -> dict[str, Any]:
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")
    _assert_transition(claim, ReviewStatus.PUBLISHED.value)

    # Re-checked at publish as well as at approval: the source could have been
    # edited in between, and publishing is the step that makes it public.
    source = await assert_has_openable_source(session, claim)
    # The checkpoint list lives in service.py so publishing and every reader of
    # published knowledge apply the identical test.
    if not publication_verification_complete(claim):
        raise ValidationFailedError(
            "This entry cannot publish until every independent verification checkpoint passes "
            "and unresolved doubt is cleared.", field="verification",
        )

    claim.review_status = ReviewStatus.PUBLISHED.value
    claim.published_by = publisher[:160]
    claim.published_at = utcnow()
    await session.flush()
    return serialize(claim, source)


async def record_publication_verification(
    session: AsyncSession, entry_id: uuid.UUID, *, verification: VerificationInput, actor: str,
) -> dict[str, Any]:
    """Persist founder and independent-review attestations before publication.

    This is intentionally a separate explicit admin action.  Creating a claim,
    supplying a URL, or an automated reviewer can never imply that a founder
    opened a source or checked the relevant fact.
    """
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")
    if claim.review_status in {ReviewStatus.PUBLISHED.value, ReviewStatus.SUPERSEDED.value}:
        raise ConflictError("Published evidence cannot have its verification rewritten.")
    current = dict(claim.structured_value or {})
    current["publication_verification"] = {
        "source_opened": verification.source_opened,
        "founder_verified_fact": verification.founder_verified_fact,
        "claude_review_completed": verification.claude_review_completed,
        "codex_review_completed": verification.codex_review_completed,
        "independent_reviews_agree": verification.independent_reviews_agree,
        "adversarial_review_passed": verification.adversarial_review_passed,
        "unresolved_doubt": verification.unresolved_doubt,
        "recorded_by": actor[:160],
        "recorded_at": utcnow().isoformat(),
    }
    claim.structured_value = current
    await session.flush()
    return serialize(claim, await _source_for(session, claim))


async def reject(
    session: AsyncSession, entry_id: uuid.UUID, *, reviewer: str, reason: str,
) -> dict[str, Any]:
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")
    _assert_transition(claim, ReviewStatus.REJECTED.value)
    text = _require(reason, "reason")

    claim.review_status = ReviewStatus.REJECTED.value
    claim.rejection_reason = text
    claim.reviewed_by = reviewer[:160]
    claim.reviewed_at = utcnow()
    await session.flush()
    return serialize(claim, await _source_for(session, claim))


async def edit(
    session: AsyncSession, entry_id: uuid.UUID, entry: EntryInput, *, author: str,
) -> dict[str, Any]:
    """Edit an entry.

    A draft or rejected entry is still being written, so it is edited in place.
    Anything that has been approved or published is a decision somebody made,
    so editing writes a **new version** and leaves the old row untouched apart
    from marking it superseded.
    """
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")

    tier = _validate_tier(_require(entry.evidence_tier, "evidence_tier"))
    summary = _require(entry.claim, "claim")
    source_name = _require(entry.source_name, "source_name")

    editable_in_place = claim.review_status in {
        ReviewStatus.DRAFT.value, ReviewStatus.REJECTED.value,
    }
    source = await _ensure_source(
        session, name=source_name, url=_valid_url(entry.source_url), author=author,
    )

    if editable_in_place:
        claim.summary = summary
        claim.subject_key = _require(entry.subject_key, "subject")[:200]
        claim.subject_type = _require(entry.subject_type, "subject_type")[:64]
        claim.evidence_tier = tier
        claim.value_text = (entry.value or "").strip()[:256] or None
        claim.value_unit = (entry.unit or "").strip()[:64] or None
        claim.notes = (entry.notes or "").strip() or None
        claim.review_status = ReviewStatus.DRAFT.value
        claim.rejection_reason = None
        await _relink_source(session, claim, source)
        await session.flush()
        return serialize(claim, source)

    # A new version. The previous row keeps every value it had.
    next_version = int((await session.execute(
        select(func.max(EvidenceClaim.claim_version)).where(
            EvidenceClaim.claim_key == claim.claim_key,
        )
    )).scalar_one() or claim.claim_version) + 1

    replacement = EvidenceClaim(
        claim_key=claim.claim_key,
        claim_version=next_version,
        domain=claim.domain,
        subject_type=_require(entry.subject_type, "subject_type")[:64],
        subject_key=_require(entry.subject_key, "subject")[:200],
        claim_type=claim.claim_type,
        summary=summary,
        scope=claim.scope,
        review_status=ReviewStatus.DRAFT.value,
        evidence_tier=tier,
        value_text=(entry.value or "").strip()[:256] or None,
        value_unit=(entry.unit or "").strip()[:64] or None,
        notes=(entry.notes or "").strip() or None,
        supersedes_claim_id=claim.id,
    )
    session.add(replacement)
    await session.flush()
    session.add(EvidenceClaimSource(
        claim_id=replacement.id,
        source_id=source.id,
        relationship=ClaimSourceRelationship.SUPPORTS.value,
    ))
    claim.review_status = ReviewStatus.SUPERSEDED.value
    await session.flush()
    return serialize(replacement, source)


async def _relink_source(
    session: AsyncSession, claim: EvidenceClaim, source: EvidenceSource,
) -> None:
    existing = (await session.execute(
        select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim.id)
    )).scalars().all()
    for link in existing:
        await session.delete(link)
    await session.flush()
    session.add(EvidenceClaimSource(
        claim_id=claim.id,
        source_id=source.id,
        relationship=ClaimSourceRelationship.SUPPORTS.value,
    ))
    await session.flush()


async def versions_of(session: AsyncSession, entry_id: uuid.UUID) -> dict[str, Any]:
    """Every version of an entry, oldest first, so nothing is ever hidden."""
    claim = await session.get(EvidenceClaim, entry_id)
    if claim is None:
        raise NotFoundError("That entry does not exist.")
    rows = list((await session.execute(
        select(EvidenceClaim)
        .where(EvidenceClaim.claim_key == claim.claim_key)
        .order_by(EvidenceClaim.claim_version.asc())
    )).scalars().all())
    return {
        "claim_key": claim.claim_key,
        "versions": [serialize(row, await _source_for(session, row)) for row in rows],
    }


# ---------------------------------------------------------------------------
# CSV import — draft only, always
# ---------------------------------------------------------------------------
@dataclass
class ImportOutcome:
    created: list[dict[str, Any]]
    errors: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_count": len(self.created),
            "error_count": len(self.errors),
            # Stated rather than implied: an import can only ever add drafts.
            "status": ReviewStatus.DRAFT.value,
            "created": self.created,
            "errors": self.errors,
        }


async def import_csv(session: AsyncSession, raw: str, *, author: str) -> ImportOutcome:
    """Bulk import. Every row lands as a draft; nothing here can publish.

    A bad row is reported with its line number and skipped — one typo in a
    hundred rows should not throw away the other ninety-nine.
    """
    try:
        reader = csv.DictReader(io.StringIO(raw))
        rows = list(reader)
    except csv.Error as exc:
        raise ValidationFailedError(f"That file could not be read as CSV: {exc}", field="file")

    if reader.fieldnames is None:
        raise ValidationFailedError("That file has no header row.", field="file")
    missing = [c for c in ("subject_type", "subject_key", "claim", "source_name", "evidence_tier")
               if c not in reader.fieldnames]
    if missing:
        raise ValidationFailedError(
            f"The file is missing these columns: {', '.join(missing)}.", field="file",
        )
    if len(rows) > _MAX_CSV_ROWS:
        raise ValidationFailedError(
            f"That file has {len(rows)} rows; {_MAX_CSV_ROWS} is the most one import may carry.",
            field="file",
        )

    outcome = ImportOutcome(created=[], errors=[])
    for index, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            created = await create_draft(
                session,
                EntryInput(
                    subject_type=(row.get("subject_type") or "").strip(),
                    subject_key=(row.get("subject_key") or "").strip(),
                    claim=(row.get("claim") or "").strip(),
                    value=(row.get("value") or "").strip() or None,
                    unit=(row.get("unit") or "").strip() or None,
                    source_name=(row.get("source_name") or "").strip(),
                    source_url=(row.get("source_url") or "").strip(),
                    evidence_tier=(row.get("evidence_tier") or "").strip(),
                    notes=(row.get("notes") or "").strip() or None,
                    domain=(row.get("domain") or EvidenceDomain.NUTRITION.value).strip(),
                ),
                author=author,
            )
            outcome.created.append(created)
        except ValidationFailedError as exc:
            outcome.errors.append({"line": index, "message": str(exc)})
    return outcome
