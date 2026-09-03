"""Creating a substance identity draft, through the existing evidence workflow.

This module is an **adapter**, not a workflow. It owns no review states, no
transition table, no approval logic, no publication verification and no source
review. All of that already exists in ``app/domains/evidence`` and stays there:
a second copy would drift, and the drift would show up as knowledge that looks
reviewed on one path and is not on the other.

What it does own is the narrow, typed shape of an identity draft — the exact
domain, subject type, subject key, claim type and evidence tier that
``substances.service`` will later require, plus the structured payload — so a
caller cannot accidentally author an identity claim that can never resolve.

Everything after the draft is the ordinary evidence path, unchanged:

    evidence.authoring.record_publication_verification(...)
    evidence.authoring.approve(...)
    evidence.authoring.publish(...)

Two things are demanded explicitly rather than inferred, because inferring
either is how unlicensed or unclassified data becomes canonical knowledge:

* **the source type**, which must be one a reference actually is; and
* **the licence/use note**, which says what the publisher permits.

Neither is guessable from a URL, and no network call is made to find out.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.enums import (
    ClaimSourceRelationship,
    ClaimType,
    EvidenceDomain,
    EvidenceStrength,
    EvidenceTier,
    ReviewStatus,
    SourceStatus,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.substances.enums import ENTITY_KINDS, SubstanceStatus
from app.domains.substances.identity_schema import build_identity_payload, parse_identity
from app.domains.substances.models import Substance, SubstanceName
from app.domains.substances.normalization import normalize_name
from app.domains.substances.service import IDENTITY_SOURCE_TYPES, IDENTITY_SUBJECT_TYPE
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import ValidationFailedError

#: A substance key is a machine identifier a human chooses deliberately. It is
#: never derived from a display name: deriving it would tie the entity to one
#: spelling, so correcting that spelling later would silently create a different
#: entity and orphan everything referring to the old one.
_KEY_MAX_LENGTH = 120


def _valid_substance_key(value: str) -> str:
    key = (value or "").strip()
    if not key or len(key) > _KEY_MAX_LENGTH:
        raise ValidationFailedError(
            f"substance_key must be 1-{_KEY_MAX_LENGTH} characters.", field="substance_key",
        )
    if key != key.lower():
        raise ValidationFailedError("substance_key must be lowercase.", field="substance_key")
    if not key[0].isalnum() or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for c in key):
        raise ValidationFailedError(
            "substance_key must start alphanumeric and use only a-z, 0-9, '_', '.', '-'.",
            field="substance_key",
        )
    return key


async def get_or_create_substance(
    session: AsyncSession, *, substance_key: str, entity_kind: str,
) -> Substance:
    """Fetch the entity for this key, or create it.

    The key is immutable through this path: an existing row whose
    ``entity_kind`` disagrees with the one supplied is a conflict, not something
    to overwrite. Silently changing what kind of thing an entity is would change
    the meaning of every claim already pointing at it.
    """
    key = _valid_substance_key(substance_key)
    if entity_kind not in ENTITY_KINDS:
        raise ValidationFailedError(
            f"entity_kind must be one of: {', '.join(ENTITY_KINDS)}.", field="entity_kind",
        )
    existing = (await session.execute(
        select(Substance).where(Substance.substance_key == key)
    )).scalar_one_or_none()
    if existing is not None:
        if existing.entity_kind != entity_kind:
            raise ValidationFailedError(
                f"{key} already exists as {existing.entity_kind}; it cannot become {entity_kind}.",
                field="entity_kind",
            )
        return existing
    substance = Substance(
        substance_key=key, entity_kind=entity_kind, status=SubstanceStatus.ACTIVE.value,
    )
    session.add(substance)
    await session.flush()
    return substance


async def create_identity_draft(
    session: AsyncSession,
    *,
    substance_key: str,
    entity_kind: str,
    names: list[dict[str, Any]],
    summary: str,
    scope: str,
    evidence_strength: str,
    strength_rationale: str,
    source_title: str,
    source_publisher: str,
    source_type: str,
    source_url: str,
    license_or_use_note: str,
    author: str,
) -> dict[str, Any]:
    """Author one identity draft and its materialised (but inert) name rows.

    The claim starts as a ``draft`` and nothing here can shortcut that. The name
    rows are written immediately so authoring can show them, and they resolve
    nothing at all until the claim has been approved, verified and published —
    the resolver re-derives eligibility from the claim on every read, so an
    unpublished claim's names are simply invisible.
    """
    payload = _validated_payload(entity_kind=entity_kind, names=names)
    identity = parse_identity(payload)
    assert identity is not None  # build_identity_payload already round-tripped it

    if evidence_strength not in {s.value for s in EvidenceStrength}:
        raise ValidationFailedError("Unknown evidence_strength.", field="evidence_strength")
    if not strength_rationale.strip():
        raise ValidationFailedError("strength_rationale is required.", field="strength_rationale")
    if source_type not in IDENTITY_SOURCE_TYPES:
        raise ValidationFailedError(
            "A canonical identity source must be one of: "
            f"{', '.join(sorted(IDENTITY_SOURCE_TYPES))}.",
            field="source_type",
        )
    url = (source_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValidationFailedError(
            "A source URL somebody can open is required.", field="source_url",
        )
    note = (license_or_use_note or "").strip()
    if not note:
        raise ValidationFailedError(
            "A licence/use note is required, and is never inferred from the URL.",
            field="license_or_use_note",
        )

    substance = await get_or_create_substance(
        session, substance_key=substance_key, entity_kind=entity_kind,
    )

    claim = EvidenceClaim(
        claim_key=f"substance-identity:{substance.substance_key}:{uuid.uuid4().hex[:12]}",
        claim_version=1,
        domain=EvidenceDomain.SUBSTANCE.value,
        subject_type=IDENTITY_SUBJECT_TYPE,
        subject_key=substance.substance_key,
        claim_type=ClaimType.SUBSTANCE_IDENTITY.value,
        summary=summary.strip() or f"Identity of {substance.substance_key}.",
        scope=scope.strip() or f"Names recorded for {substance.substance_key}.",
        evidence_strength=evidence_strength,
        strength_rationale=strength_rationale.strip(),
        review_status=ReviewStatus.DRAFT.value,
        evidence_tier=EvidenceTier.REFERENCE_DATA.value,
        structured_value=payload,
    )
    session.add(claim)
    await session.flush()

    source = EvidenceSource(
        source_key=f"substance-identity:{uuid.uuid4().hex[:24]}",
        source_series_key=f"substance-identity:{source_publisher.lower()[:100]}",
        source_type=source_type,
        title=source_title[:512],
        publisher=source_publisher[:256],
        canonical_url=url,
        accessed_at=utcnow(),
        status=SourceStatus.ACTIVE.value,
        license_or_use_note=note,
    )
    session.add(source)
    await session.flush()
    session.add(EvidenceClaimSource(
        claim_id=claim.id,
        source_id=source.id,
        relationship=ClaimSourceRelationship.SUPPORTS.value,
    ))

    for entry in identity.names:
        session.add(SubstanceName(
            substance_id=substance.id,
            identity_claim_id=claim.id,
            name=entry.name,
            # Server-computed, always. A caller-supplied key would let the
            # writer choose what its own row matches.
            normalized_name=entry.normalized_name,
            namespace=entry.namespace,
            language_tag=entry.language_tag,
            is_preferred=entry.is_preferred,
        ))
    await session.flush()

    return {
        "substance_id": str(substance.id),
        "substance_key": substance.substance_key,
        "entity_kind": substance.entity_kind,
        "claim_id": str(claim.id),
        "review_status": claim.review_status,
        "names": [
            {"name": n.name, "normalized_name": n.normalized_name,
             "namespace": n.namespace, "is_preferred": n.is_preferred}
            for n in identity.names
        ],
        "authored_by": author,
    }


def _validated_payload(*, entity_kind: str, names: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return build_identity_payload(entity_kind=entity_kind, names=names)
    except ValueError as exc:
        raise ValidationFailedError(
            "These names are not a valid substance identity payload.", field="names",
        ) from exc


def normalized_preview(name: str) -> str | None:
    """What the server would store as the lookup key. Read-only, for authoring."""
    return normalize_name(name)


__all__ = [
    "create_identity_draft",
    "get_or_create_substance",
    "normalized_preview",
]
