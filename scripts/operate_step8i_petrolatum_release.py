#!/usr/bin/env python3
"""Install and activate the exact reviewed Step 8I petrolatum pack, and nothing else.

This is the operator path for one specific already-reviewed knowledge pack. It is
deliberately not a knowledge-management framework: there is no ``--action``, no
``--reason``, no ``--policy``, no ``--signal``, no release chooser and no "latest".
The only things an operator supplies are operational identity -- who is acting, and
which exact release, named by its id and by the exact content hash they expect it to
have. Everything the customer would eventually be told is fixed in
``app.knowledge_packs.petrolatum_dry_skin_v1`` and in the reviewed copy catalogue,
and this script can only carry those forward or refuse.

**It lives in ``scripts/`` on purpose.** ``backend/app/**`` must stay free of any
import of ``app.knowledge_packs``, so that normal application runtime -- the API,
startup, bootstrap, migrations, background jobs, the Step 8K decision path -- cannot
reach the pack even by accident. A static test enforces that. Putting this tool in
``backend/app/commands`` would break it.

**It does not replace human review.** ``prepare`` writes drafts. ``compile`` writes a
draft release. Neither records an attestation, approves anything, or publishes
anything: those are separate governance events performed by people through the
existing evidence and release lifecycles. Source code equalling the pack is not a
review of the pack; it is only evidence that nobody has edited the file.

**It never runs itself.** Nothing here is wired into CI, deployment, startup,
migration, bootstrap, seeding or cron. Production activation is a deliberate
operator event, performed once, after this implementation has itself been reviewed.

**What the output may and may not contain.** Output is JSON on stdout so an operator
and a test can both read it. It may include explicit actor attribution -- a successful
state-changing operation deliberately echoes the `--actor` it recorded, because a
governed write nobody can be traced to is worse than a noisy one -- along with governed
release and evidence metadata: ids, versions, statuses, content hashes, reason and
verdict keys, and the reviewed BUY label. It must never include credentials, tokens,
passwords, connection strings, raw unexpected-exception messages, or customer or
personal data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.content.for_you_copy import (  # noqa: E402
    FOR_YOU_COPY_VERSION,
    reason_text,
    verdict_text,
)
from app.domains.evidence.enums import (  # noqa: E402
    ClaimType,
    ReviewStatus,
)
from app.domains.evidence.models import (  # noqa: E402
    EvidenceClaim,
    EvidenceClaimSource,
    EvidenceSource,
)
from app.domains.personal_applicability import authoring as applicability_authoring  # noqa: E402
from app.domains.personal_applicability.enums import (  # noqa: E402
    PersonalApplicabilityCategory,
)

# The one place BUY / WAIT / SKIP keys are written down. Imported rather than
# restated: a copy here would be a verdict key this script invented, which is
# exactly what the pack's governance forbids. It is private to Step 8F because
# no *production* line may name a verdict; an operator check that the reviewed
# label still resolves is the one legitimate reader outside that module.
from app.domains.personal_decision_explanation.service import (  # noqa: E402
    _VERDICT_KEYS as VERDICT_KEYS_BY_ACTION,
)
from app.domains.personal_decision_release import authoring as release_authoring  # noqa: E402
from app.domains.personal_decision_release.enums import (  # noqa: E402
    PERSONAL_DECISION_RELEASE_KEY,
    PersonalDecisionReleaseStatus,
)
from app.domains.personal_decision_release.manifest import (  # noqa: E402
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease  # noqa: E402
from app.domains.personal_decision_release.runtime import (  # noqa: E402
    load_active_personal_decision_release,
)
from app.domains.substances import authoring as substance_authoring  # noqa: E402
from app.domains.substances.identity_schema import (  # noqa: E402
    IDENTITY_PAYLOAD_KEY,
    parse_identity,
)
from app.domains.substances.models import Substance  # noqa: E402
from app.domains.substances.service import IDENTITY_SUBJECT_TYPE  # noqa: E402
from app.knowledge_packs import petrolatum_dry_skin_v1 as pack  # noqa: E402
from app.shared.database.sql import dispose_engine, get_sessionmaker  # noqa: E402
from app.shared.errors.exceptions import AppError  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

OPERATIONS = ("status", "prepare", "compile", "activate", "deactivate")

#: Every state-changing operation names a real person or a real service account.
#: There is no default and no fallback: a governed write whose actor is "system"
#: records nothing about who decided it.
_ACTOR_MAX = 160

#: What an operator is told when something failed that this tool did not
#: anticipate. Fixed wording on purpose -- see the boundary in `main`.
UNEXPECTED_ERROR_MESSAGE = (
    "The operator command failed unexpectedly. No changes were committed."
)


class OperatorRefusal(Exception):
    """A refusal with a machine-readable code. Never a partial write.

    Every refusal in this tool is deliberate and final for that invocation. None
    of them is recoverable by retrying, and none is repaired automatically: an
    operator has to look at what the database actually holds.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_result(self, operation: str) -> dict[str, Any]:
        return {
            "ok": False,
            "operation": operation,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _domain_refusal(code: str, error: AppError, **details: Any) -> OperatorRefusal:
    """Surface a governed refusal without reinterpreting it.

    The domain's own reason travels through untouched. This tool never decides
    that a Step 8H validation failure was really fine, and never translates one
    kind of failure into another that happens to read better.
    """
    reason = getattr(error, "reason", None)
    return OperatorRefusal(
        code,
        str(error),
        domain_reason=getattr(reason, "value", reason),
        **details,
    )


# ---------------------------------------------------------------------------
# Recognising the exact pack, without restating any of it
# ---------------------------------------------------------------------------
def _identity_names(claim: EvidenceClaim) -> list[dict[str, Any]] | None:
    identity = parse_identity(claim.structured_value or {})
    if identity is None:
        return None
    return [
        {
            "name": entry.name,
            "namespace": entry.namespace,
            "language_tag": entry.language_tag,
            "is_preferred": entry.is_preferred,
        }
        for entry in identity.names
    ]


def _identity_matches_pack(claim: EvidenceClaim, sources: Sequence[EvidenceSource]) -> bool:
    """Is this exactly the reviewed Step 8I identity, name and source included?

    Compared against the pack's own constants rather than a local copy of them.
    A petrolatum identity claim that differs in any of these is a *different*
    authority for the same key, which is a conflict for a human to resolve --
    never something to overwrite and never something to author beside.
    """
    payload = (claim.structured_value or {}).get(IDENTITY_PAYLOAD_KEY)
    if not isinstance(payload, Mapping):
        return False
    if payload.get("entity_kind") != pack.IDENTITY_ENTITY_KIND:
        return False
    names = _identity_names(claim)
    if names is None or len(names) != 1:
        return False
    name = names[0]
    if (
        name["name"] != pack.IDENTITY_NAME
        or name["namespace"] != pack.IDENTITY_NAME_NAMESPACE
        or name["is_preferred"] is not pack.IDENTITY_NAME_PREFERRED
    ):
        return False
    return any(_identity_source_matches_pack(source) for source in sources)


def _identity_source_matches_pack(source: EvidenceSource) -> bool:
    return (
        source.source_type == pack.IDENTITY_SOURCE_TYPE
        and source.title == pack.IDENTITY_SOURCE_TITLE
        and source.publisher == pack.IDENTITY_SOURCE_PUBLISHER
        and source.canonical_url == pack.IDENTITY_SOURCE_URL
        and source.license_or_use_note == pack.IDENTITY_SOURCE_USE_NOTE
    )


def _reviewed_source_expectations() -> dict[str, dict[str, Any]]:
    """The two reviewed source paths, read off the pack.

    Only the fields the pack itself pins. Anything absent here is something the
    pack does not govern, and inventing an expectation for it would refuse
    perfectly valid records.
    """
    return {
        "aad": {
            "source_type": pack.AAD_SOURCE_TYPE,
            "title": pack.AAD_SOURCE_TITLE,
            "publisher": pack.AAD_SOURCE_PUBLISHER,
            "canonical_url": pack.AAD_SOURCE_URL,
            "publication_date": pack.AAD_SOURCE_PUBLICATION_DATE,
            "version_or_revision": pack.AAD_SOURCE_VERSION,
            "jurisdiction": pack.AAD_SOURCE_JURISDICTION,
            "license_or_use_note": pack.AAD_SOURCE_USE_NOTE,
            "locator": pack.AAD_SOURCE_LOCATOR,
        },
        "pubmed": {
            "source_type": pack.PUBMED_SOURCE_TYPE,
            "title": pack.PUBMED_SOURCE_TITLE,
            "publisher": pack.PUBMED_SOURCE_PUBLISHER,
            "canonical_url": pack.PUBMED_SOURCE_URL,
            "publication_date": pack.PUBMED_SOURCE_PUBLICATION_DATE,
            "version_or_revision": pack.PUBMED_SOURCE_VERSION,
            "jurisdiction": pack.PUBMED_SOURCE_JURISDICTION,
            "license_or_use_note": pack.PUBMED_SOURCE_USE_NOTE,
            "locator": pack.PUBMED_SOURCE_LOCATOR,
        },
    }


def _source_conflicts(source: EvidenceSource, expected: Mapping[str, Any]) -> list[str]:
    """Which authority-bearing fields disagree with the reviewed metadata."""
    observed = {
        "source_type": source.source_type,
        "title": source.title,
        "publisher": source.publisher,
        "canonical_url": source.canonical_url,
        "publication_date": (
            source.publication_date.isoformat() if source.publication_date else None
        ),
        "version_or_revision": source.version_or_revision,
        "jurisdiction": source.jurisdiction,
        "license_or_use_note": source.license_or_use_note,
    }
    return sorted(
        field
        for field, value in observed.items()
        if field in expected and expected[field] != value
    )


def _entry_matches_pack(entry: Mapping[str, Any]) -> bool:
    """Would the Step 8I compiler accept this entry's *content*?

    The pack's own validator is the judge, so there is exactly one definition of
    "matches the pack" in the repository and this file holds no second copy of
    it. Review status is normalised first, because a draft that is otherwise
    identical to the reviewed pack is precisely what ``prepare`` must recognise
    and reuse rather than duplicate. Publication is checked separately, by
    reading the real review status, and is never implied by content.
    """
    probe = dict(entry)
    probe["review_status"] = "published"
    probe["claim_status"] = "supported"
    try:
        pack.build_release_manifest_from_published_entry(probe)
    except pack.FirstProductionKnowledgePackError:
        return False
    return True


# ---------------------------------------------------------------------------
# Reading what is actually in the database
# ---------------------------------------------------------------------------
async def _paths(
    session: AsyncSession, claim_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]]:
    found: dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]] = {
        claim_id: [] for claim_id in claim_ids
    }
    if not claim_ids:
        return found
    rows = (
        await session.execute(
            select(EvidenceClaimSource, EvidenceSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaimSource.claim_id.in_(list(claim_ids)))
        )
    ).all()
    for link, source in rows:
        found[link.claim_id].append((link, source))
    return found


async def _substance(session: AsyncSession) -> Substance | None:
    return (
        await session.execute(
            select(Substance).where(Substance.substance_key == pack.SUBSTANCE_KEY)
        )
    ).scalar_one_or_none()


async def _identity_claims(session: AsyncSession) -> list[EvidenceClaim]:
    return list(
        (
            await session.execute(
                select(EvidenceClaim)
                .where(
                    EvidenceClaim.claim_type == ClaimType.SUBSTANCE_IDENTITY.value,
                    EvidenceClaim.subject_type == IDENTITY_SUBJECT_TYPE,
                    EvidenceClaim.subject_key == pack.SUBSTANCE_KEY,
                    EvidenceClaim.review_status != ReviewStatus.REJECTED.value,
                )
                .order_by(EvidenceClaim.id)
            )
        )
        .scalars()
        .all()
    )


async def _applicability_claims(session: AsyncSession) -> list[EvidenceClaim]:
    return list(
        (
            await session.execute(
                select(EvidenceClaim)
                .where(
                    EvidenceClaim.claim_type
                    == ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value,
                    EvidenceClaim.subject_key == pack.SUBSTANCE_KEY,
                    EvidenceClaim.review_status.notin_(
                        [ReviewStatus.REJECTED.value, ReviewStatus.SUPERSEDED.value]
                    ),
                )
                .order_by(EvidenceClaim.id)
            )
        )
        .scalars()
        .all()
    )


async def _source_by_url(session: AsyncSession, url: str) -> EvidenceSource | None:
    return (
        await session.execute(
            select(EvidenceSource).where(EvidenceSource.canonical_url == url)
        )
    ).scalar_one_or_none()


async def _releases(session: AsyncSession) -> list[PersonalDecisionRelease]:
    return list(
        (
            await session.execute(
                select(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.release_key == PERSONAL_DECISION_RELEASE_KEY)
                .order_by(PersonalDecisionRelease.release_version)
            )
        )
        .scalars()
        .all()
    )


def _release_summary(release: PersonalDecisionRelease) -> dict[str, Any]:
    return {
        "id": str(release.id),
        "release_version": release.release_version,
        "status": release.status,
        "content_hash": release.content_hash,
    }


async def _serialized_applicability(
    session: AsyncSession, claim: EvidenceClaim
) -> dict[str, Any]:
    """The published-entry shape the Step 8I compiler expects, from Step 8G itself."""
    return await applicability_authoring.get_personal_applicability_entry(session, claim.id)


# ---------------------------------------------------------------------------
# Classifying, and refusing on ambiguity
# ---------------------------------------------------------------------------
async def _classify_identity(session: AsyncSession) -> dict[str, Any]:
    claims = await _identity_claims(session)
    paths = await _paths(session, [claim.id for claim in claims])
    matching, conflicting = [], []
    for claim in claims:
        sources = [source for _, source in paths[claim.id]]
        (matching if _identity_matches_pack(claim, sources) else conflicting).append(claim)
    return {
        "state": (
            "absent"
            if not claims
            else "ambiguous"
            if len(matching) > 1
            else "conflict"
            if not matching
            else matching[0].review_status
        ),
        "matches_pack": len(matching) == 1,
        "claim_id": str(matching[0].id) if len(matching) == 1 else None,
        "review_status": matching[0].review_status if len(matching) == 1 else None,
        "matching_candidates": [str(claim.id) for claim in matching],
        "conflicting_candidates": [str(claim.id) for claim in conflicting],
    }


async def _classify_applicability(session: AsyncSession) -> dict[str, Any]:
    claims = await _applicability_claims(session)
    matching, conflicting = [], []
    for claim in claims:
        entry = await _serialized_applicability(session, claim)
        (matching if _entry_matches_pack(entry) else conflicting).append(claim)
    published = [
        claim for claim in matching if claim.review_status == ReviewStatus.PUBLISHED.value
    ]
    return {
        "state": (
            "absent"
            if not claims
            else "ambiguous"
            if len(matching) > 1
            else "conflict"
            if not matching
            else matching[0].review_status
        ),
        "matches_pack": len(matching) == 1,
        "entry_id": str(matching[0].id) if len(matching) == 1 else None,
        "review_status": matching[0].review_status if len(matching) == 1 else None,
        "published_count": len(published),
        "matching_candidates": [str(claim.id) for claim in matching],
        "conflicting_candidates": [str(claim.id) for claim in conflicting],
    }


async def _authoritative_published_entry(session: AsyncSession) -> EvidenceClaim:
    """The one published entry the pack accepts, or a refusal naming why not.

    "Exactly one" is the whole point. Two published records that both look like
    the reviewed pack mean somebody authored a second authority for the same
    knowledge, and picking the newer one would install knowledge nobody chose.
    """
    claims = await _applicability_claims(session)
    matching = []
    for claim in claims:
        if claim.review_status != ReviewStatus.PUBLISHED.value:
            continue
        entry = await _serialized_applicability(session, claim)
        if _entry_matches_pack(entry):
            matching.append(claim)
    if not matching:
        raise OperatorRefusal(
            "APPLICABILITY_NOT_PUBLISHED",
            "No published personal-applicability entry matches the reviewed Step 8I pack. "
            "Complete the Step 8G review, approval and publication first.",
            candidates=[str(claim.id) for claim in claims],
        )
    if len(matching) > 1:
        raise OperatorRefusal(
            "APPLICABILITY_AMBIGUOUS",
            "More than one published entry matches the reviewed Step 8I pack; refusing to "
            "choose between them.",
            candidates=sorted(str(claim.id) for claim in matching),
        )
    return matching[0]


# ---------------------------------------------------------------------------
# Reviewed customer copy
# ---------------------------------------------------------------------------
def _verdict_key_for(action: str) -> str:
    key = VERDICT_KEYS_BY_ACTION.get(action)
    if key is None:
        raise OperatorRefusal(
            "VERDICT_KEY_UNKNOWN",
            f"Step 8F has no verdict key for action {action!r}.",
            action=action,
        )
    return key


def _copy_state(action: str | None) -> dict[str, Any]:
    reason = reason_text(pack.REASON_KEY)
    verdict_key = VERDICT_KEYS_BY_ACTION.get(action) if action else None
    return {
        "copy_version": FOR_YOU_COPY_VERSION,
        "reason_key": pack.REASON_KEY,
        "reason_copy_present": reason is not None,
        "reason_copy_matches_pack_intent": reason == pack.FUTURE_REASON_INTENT,
        "verdict_key": verdict_key,
        "verdict_text": verdict_text(verdict_key) if verdict_key else None,
    }


def _assert_reviewed_copy(action: str) -> dict[str, Any]:
    """The second presentation gate, checked before anything becomes active.

    An activated release whose reason has no reviewed sentence would put the
    customer in front of a decision nobody proofread -- or, because Step 8K
    fails closed, in front of nothing at all while the operator believed the
    activation worked. Both are failures, and both are cheaper to catch here.
    Substituting a different reason would be worse than either.
    """
    reason = reason_text(pack.REASON_KEY)
    if reason is None:
        raise OperatorRefusal(
            "REASON_COPY_MISSING",
            f"No reviewed customer sentence exists for {pack.REASON_KEY}.",
            reason_key=pack.REASON_KEY,
        )
    if reason != pack.FUTURE_REASON_INTENT:
        raise OperatorRefusal(
            "REASON_COPY_MISMATCH",
            f"The reviewed sentence for {pack.REASON_KEY} is not the pack's reviewed "
            "intent; refusing to activate rather than substituting another reason.",
            reason_key=pack.REASON_KEY,
        )
    verdict_key = _verdict_key_for(action)
    label = verdict_text(verdict_key)
    if label is None:
        raise OperatorRefusal(
            "VERDICT_COPY_MISSING",
            f"No reviewed display label exists for {verdict_key}.",
            verdict_key=verdict_key,
        )
    return {"reason_key": pack.REASON_KEY, "verdict_key": verdict_key, "verdict_text": label}


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
async def run_status(session: AsyncSession) -> dict[str, Any]:
    """Report exactly what the database holds. Zero writes, ever.

    Where the state is ambiguous this says so rather than resolving it. There
    is no "latest" anywhere in this function: an operator being shown a single
    confident answer that the tool picked for them is how the wrong authority
    gets activated.
    """
    substance = await _substance(session)
    identity = await _classify_identity(session)
    applicability = await _classify_applicability(session)

    expectations = _reviewed_source_expectations()
    sources: dict[str, Any] = {}
    for name, expected in expectations.items():
        source = await _source_by_url(session, str(expected["canonical_url"]))
        if source is None:
            sources[name] = {"present": False, "matches_pack": False, "conflicts": []}
            continue
        conflicts = _source_conflicts(source, expected)
        sources[name] = {
            "present": True,
            "source_key": source.source_key,
            "status": source.status,
            "matches_pack": not conflicts,
            "conflicts": conflicts,
        }

    compiled: dict[str, Any] = {"available": False, "content_hash": None, "reason": None}
    action: str | None = None
    try:
        claim = await _authoritative_published_entry(session)
        entry = await _serialized_applicability(session, claim)
        manifest = pack.build_release_manifest_from_published_entry(entry)
        parsed = parse_release_manifest(manifest)
        compiled = {
            "available": True,
            "content_hash": manifest_content_hash(parsed),
            "reason": None,
            "published_entry_id": str(claim.id),
        }
        action = str(manifest["policy_rules"][0]["action"])
    except OperatorRefusal as refusal:
        compiled["reason"] = refusal.code
    except pack.FirstProductionKnowledgePackError as error:
        compiled["reason"] = f"PACK_REJECTED_ENTRY: {error}"

    releases = await _releases(session)
    expected_hash = compiled["content_hash"]
    matching = [r for r in releases if expected_hash and r.content_hash == expected_hash]
    active = [r for r in releases if r.status == PersonalDecisionReleaseStatus.ACTIVE.value]

    return {
        "ok": True,
        "operation": "status",
        "pack_id": pack.PACK_ID,
        "substance": {
            "present": substance is not None,
            "substance_key": pack.SUBSTANCE_KEY,
            "entity_kind": substance.entity_kind if substance else None,
            "matches_pack": (
                substance is not None
                and substance.entity_kind == pack.IDENTITY_ENTITY_KIND
            ),
        },
        "identity": identity,
        "sources": sources,
        "applicability": applicability,
        "compiled": compiled,
        "releases": {
            "total": len(releases),
            "matching": [_release_summary(r) for r in matching],
            "active": [_release_summary(r) for r in active],
            "step8i_release_active": bool(
                expected_hash
                and any(
                    r.status == PersonalDecisionReleaseStatus.ACTIVE.value
                    and r.content_hash == expected_hash
                    for r in releases
                )
            ),
        },
        "copy": _copy_state(action),
        "ambiguities": sorted(
            code
            for code, present in (
                ("IDENTITY_AMBIGUOUS", identity["state"] == "ambiguous"),
                ("IDENTITY_CONFLICT", identity["state"] == "conflict"),
                ("APPLICABILITY_AMBIGUOUS", applicability["state"] == "ambiguous"),
                ("APPLICABILITY_CONFLICT", applicability["state"] == "conflict"),
                ("MULTIPLE_ACTIVE_RELEASES", len(active) > 1),
                ("MULTIPLE_MATCHING_RELEASES", len(matching) > 1),
            )
            if present
        ),
    }


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
async def _prepare_identity(session: AsyncSession, *, actor: str) -> dict[str, Any]:
    substance = await _substance(session)
    if substance is not None and substance.entity_kind != pack.IDENTITY_ENTITY_KIND:
        raise OperatorRefusal(
            "SUBSTANCE_CONFLICT",
            f"{pack.SUBSTANCE_KEY} already exists as {substance.entity_kind}, not "
            f"{pack.IDENTITY_ENTITY_KIND}; refusing to change what the entity is.",
            observed=substance.entity_kind,
        )

    state = await _classify_identity(session)
    if state["state"] == "ambiguous":
        raise OperatorRefusal(
            "IDENTITY_AMBIGUOUS",
            "More than one identity claim matches the reviewed Step 8I pack; refusing to "
            "choose between them.",
            candidates=state["matching_candidates"],
        )
    if state["state"] == "conflict":
        raise OperatorRefusal(
            "IDENTITY_CONFLICT",
            f"An identity claim already exists for {pack.SUBSTANCE_KEY} whose governed "
            "identity or source metadata disagrees with the reviewed pack. Refusing to "
            "overwrite it and refusing to author a competing authority beside it.",
            candidates=state["conflicting_candidates"],
        )
    if state["matches_pack"]:
        return {"created": False, "claim_id": state["claim_id"],
                "review_status": state["review_status"]}

    result = await substance_authoring.create_identity_draft(
        session,
        substance_key=pack.SUBSTANCE_KEY,
        entity_kind=pack.IDENTITY_ENTITY_KIND,
        names=[
            {
                "name": pack.IDENTITY_NAME,
                "namespace": pack.IDENTITY_NAME_NAMESPACE,
                "language_tag": "und",
                "is_preferred": pack.IDENTITY_NAME_PREFERRED,
            }
        ],
        summary=(
            f"The reviewed government reference records the exact identity name "
            f"{pack.IDENTITY_NAME}."
        ),
        scope="Identity and nomenclature only.",
        evidence_strength="strong",
        strength_rationale=(
            "The named government reference records this identity directly, under external "
            f"identifier {pack.IDENTITY_SOURCE_EXTERNAL_ID}."
        ),
        source_title=pack.IDENTITY_SOURCE_TITLE,
        source_publisher=pack.IDENTITY_SOURCE_PUBLISHER,
        source_type=pack.IDENTITY_SOURCE_TYPE,
        source_url=pack.IDENTITY_SOURCE_URL,
        license_or_use_note=pack.IDENTITY_SOURCE_USE_NOTE,
        author=actor,
    )
    return {
        "created": True,
        "claim_id": result["claim_id"],
        "review_status": result["review_status"],
    }


async def _source_inputs(
    session: AsyncSession,
) -> tuple[applicability_authoring.AuthoringSourceInput, ...]:
    """Reuse an existing reviewed source only when its metadata matches exactly.

    A source row that already carries the reviewed canonical URL but disagrees
    about who published it, what it is called, or what its licence permits is a
    different source wearing the same address. Reusing it would attach the
    reviewed claim to unreviewed provenance; authoring a second row for the same
    URL would create two authorities for one document. Both are refused.
    """
    inputs: list[applicability_authoring.AuthoringSourceInput] = []
    for name, expected in _reviewed_source_expectations().items():
        url = str(expected["canonical_url"])
        existing = await _source_by_url(session, url)
        if existing is not None:
            conflicts = _source_conflicts(existing, expected)
            if conflicts:
                raise OperatorRefusal(
                    "SOURCE_CONFLICT",
                    f"The existing {name} source at {url} disagrees with the reviewed pack "
                    f"on: {', '.join(conflicts)}. Refusing to reuse it and refusing to "
                    "author a competing source for the same URL.",
                    source=name,
                    source_key=existing.source_key,
                    conflicts=conflicts,
                )
            inputs.append(
                applicability_authoring.ExistingSourceInput(
                    source_key=existing.source_key, locator=str(expected["locator"]),
                )
            )
            continue
        published = expected["publication_date"]
        inputs.append(
            applicability_authoring.NewSourceInput(
                source_type=str(expected["source_type"]),
                title=str(expected["title"]),
                publisher=str(expected["publisher"]),
                canonical_url=url,
                license_or_use_note=str(expected["license_or_use_note"]),
                locator=str(expected["locator"]),
                publication_date=date.fromisoformat(published) if published else None,
                version_or_revision=expected["version_or_revision"],
                jurisdiction=expected["jurisdiction"],
            )
        )
    return tuple(inputs)


async def _prepare_applicability(session: AsyncSession, *, actor: str) -> dict[str, Any]:
    state = await _classify_applicability(session)
    if state["state"] == "ambiguous":
        raise OperatorRefusal(
            "APPLICABILITY_AMBIGUOUS",
            "More than one personal-applicability entry matches the reviewed Step 8I pack; "
            "refusing to choose between them.",
            candidates=state["matching_candidates"],
        )
    if state["state"] == "conflict":
        raise OperatorRefusal(
            "APPLICABILITY_CONFLICT",
            f"A personal-applicability entry already exists for {pack.SUBSTANCE_KEY} whose "
            "governed content disagrees with the reviewed pack. Refusing to overwrite it and "
            "refusing to author a competing authority beside it.",
            candidates=state["conflicting_candidates"],
        )
    if state["matches_pack"]:
        return {"created": False, "entry_id": state["entry_id"],
                "review_status": state["review_status"]}

    view = await applicability_authoring.create_personal_applicability_draft(
        session,
        applicability_authoring.PersonalApplicabilityDraftInput(
            category=PersonalApplicabilityCategory(pack.CATEGORY),
            substance_key=pack.SUBSTANCE_KEY,
            summary=pack.EVIDENCE_SUMMARY,
            scope=pack.EVIDENCE_SCOPE,
            evidence_strength=pack.EVIDENCE_STRENGTH,
            strength_rationale=pack.EVIDENCE_STRENGTH_RATIONALE,
            conditions=(
                applicability_authoring.AuthoringConditionInput(
                    fact_key=pack.FACT_KEY, values=pack.FACT_VALUES,
                ),
            ),
            sources=await _source_inputs(session),
        ),
        author=actor,
    )
    return {"created": True, "entry_id": view["id"], "review_status": view["review_status"]}


async def run_prepare(session: AsyncSession, *, actor: str) -> dict[str, Any]:
    """Create only the missing DRAFT authority for the exact pack.

    Nothing here records an attestation, approves, publishes, or touches a
    release. The material this writes is worth exactly as much as an unreviewed
    draft, which is what it is: a file agreeing with itself is not a review.
    """
    identity = await _prepare_identity(session, actor=actor)
    applicability = await _prepare_applicability(session, actor=actor)
    return {
        "ok": True,
        "operation": "prepare",
        "pack_id": pack.PACK_ID,
        "actor": actor,
        "identity": identity,
        "applicability": applicability,
        "next_step": (
            "Human review: verify, approve and publish the identity claim and the "
            "personal-applicability entry through the existing governed lifecycles."
        ),
    }


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------
async def run_compile(session: AsyncSession, *, actor: str) -> dict[str, Any]:
    """Turn the one published entry into the reviewed manifest, as a DRAFT release.

    The manifest is produced by the pack's compiler and by nothing else: no
    action, signal, policy, reason or citation is chosen here. Re-running is
    safe and deliberately boring -- an exact match that already exists is
    reported, never duplicated, because a shelf of near-identical release
    versions is how the wrong one eventually gets activated.
    """
    claim = await _authoritative_published_entry(session)
    entry = await _serialized_applicability(session, claim)
    manifest = pack.build_release_manifest_from_published_entry(entry)
    parsed = parse_release_manifest(manifest)
    content_hash = manifest_content_hash(parsed)

    existing = [r for r in await _releases(session) if r.content_hash == content_hash]
    if len(existing) > 1:
        raise OperatorRefusal(
            "RELEASE_AMBIGUOUS",
            "More than one release already carries this exact content hash; refusing to "
            "choose between them.",
            content_hash=content_hash,
            candidates=[_release_summary(r) for r in existing],
        )
    if existing:
        release = existing[0]
        if release.status == PersonalDecisionReleaseStatus.RETIRED.value:
            raise OperatorRefusal(
                "RELEASE_RETIRED",
                "A retired release already carries this exact content hash. It is not "
                "revived automatically and no replacement is authored automatically: "
                "reinstating retired knowledge is a human decision.",
                content_hash=content_hash,
                release=_release_summary(release),
            )
        return {
            "ok": True,
            "operation": "compile",
            "pack_id": pack.PACK_ID,
            "actor": actor,
            "created": False,
            "reused": True,
            "published_entry_id": str(claim.id),
            "release": _release_summary(release),
            "already_active": (
                release.status == PersonalDecisionReleaseStatus.ACTIVE.value
            ),
            "next_step": (
                "Already active."
                if release.status == PersonalDecisionReleaseStatus.ACTIVE.value
                else "Human review: record release verification, validate, then approve."
            ),
        }

    draft = await release_authoring.create_personal_decision_release_draft(
        session, manifest, actor=actor,
    )
    return {
        "ok": True,
        "operation": "compile",
        "pack_id": pack.PACK_ID,
        "actor": actor,
        "created": True,
        "reused": False,
        "published_entry_id": str(claim.id),
        "release": {
            "id": draft["id"],
            "release_version": draft["release_version"],
            "status": draft["status"],
            "content_hash": draft["content_hash"],
        },
        "already_active": False,
        "next_step": (
            "Human review: record release verification, validate, then approve."
        ),
    }


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------
async def _release_or_refuse(
    session: AsyncSession, release_id: uuid.UUID
) -> PersonalDecisionRelease:
    release = await session.get(PersonalDecisionRelease, release_id)
    if release is None or release.release_key != PERSONAL_DECISION_RELEASE_KEY:
        raise OperatorRefusal(
            "RELEASE_NOT_FOUND",
            "No decision release exists with that id.",
            release_id=str(release_id),
        )
    return release


def _assert_expected_hash(release: PersonalDecisionRelease, expected: str) -> None:
    if release.content_hash != expected:
        raise OperatorRefusal(
            "CONTENT_HASH_MISMATCH",
            "The release does not carry the expected content hash. The operator is naming "
            "a release they have not seen.",
            release_id=str(release.id),
            expected=expected,
            observed=release.content_hash,
        )


def _assert_manifest_self_consistent(release: PersonalDecisionRelease) -> Any:
    """The stored manifest must still hash to the stored hash.

    Checked here as well as inside the domain because the operator's expected
    hash is only meaningful if the column it is compared against is still telling
    the truth about the JSON beside it.
    """
    try:
        parsed = parse_release_manifest(release.manifest)
    except Exception as error:  # noqa: BLE001 - any parse failure is the same refusal
        raise OperatorRefusal(
            "MANIFEST_UNPARSEABLE",
            "The persisted manifest does not parse under the reviewed schema.",
            release_id=str(release.id),
            error=str(error),
        ) from error
    recomputed = manifest_content_hash(parsed)
    if recomputed != release.content_hash:
        raise OperatorRefusal(
            "MANIFEST_HASH_INCONSISTENT",
            "The persisted manifest does not hash to the persisted content hash; it was "
            "changed outside the reviewed path.",
            release_id=str(release.id),
            recorded=release.content_hash,
            recomputed=recomputed,
        )
    return parsed


def _manifest_is_the_step8i_pack(manifest: Mapping[str, Any]) -> bool:
    """Are these the reviewed Step 8I rule identities, exactly?

    Identity only -- the rule, policy, explanation, action and reason the pack
    names. This deliberately does not re-derive the claim key or source key,
    which come from whatever the governed evidence generated, so an emergency
    stop still works when the underlying evidence has moved on.
    """
    semantic = manifest.get("semantic_rules") or []
    policy = manifest.get("policy_rules") or []
    explanation = manifest.get("explanation_rules") or []
    if (len(semantic), len(policy), len(explanation)) != (1, 1, 1):
        return False
    return (
        semantic[0].get("rule_id") == pack.SEMANTIC_RULE_ID
        and semantic[0].get("rule_version") == pack.SEMANTIC_RULE_VERSION
        and semantic[0].get("signal") == pack.SEMANTIC_SIGNAL
        and semantic[0].get("substance_key") == pack.SUBSTANCE_KEY
        and policy[0].get("policy_id") == pack.POLICY_ID
        and policy[0].get("policy_version") == pack.POLICY_VERSION
        and policy[0].get("action") == pack.POLICY_ACTION
        and explanation[0].get("explanation_id") == pack.EXPLANATION_ID
        and explanation[0].get("explanation_version") == pack.EXPLANATION_VERSION
        and explanation[0].get("reason_key") == pack.REASON_KEY
        and explanation[0].get("source_locator") == pack.AAD_SOURCE_LOCATOR
    )


async def run_activate(
    session: AsyncSession,
    *,
    release_id: uuid.UUID,
    expected_content_hash: str,
    actor: str,
) -> dict[str, Any]:
    """Make one exact, already-approved release active -- or refuse and change nothing.

    Every check below is independent of the operator's belief. The release id
    and the expected hash are the only things they supply, and the hash is what
    makes the id safe: naming a release without naming what it should contain is
    how a different release gets activated by a correct-looking command.
    """
    release = await _release_or_refuse(session, release_id)
    _assert_expected_hash(release, expected_content_hash)
    parsed = _assert_manifest_self_consistent(release)
    canonical = canonical_manifest(parsed)
    if not _manifest_is_the_step8i_pack(canonical):
        raise OperatorRefusal(
            "NOT_THE_STEP8I_PACK",
            "That release is not the reviewed Step 8I pack. This operator path installs one "
            "exact pack and refuses anything else.",
            release_id=str(release.id),
        )

    active = [
        r
        for r in await _releases(session)
        if r.status == PersonalDecisionReleaseStatus.ACTIVE.value
    ]
    if any(r.id != release.id for r in active):
        # The domain supports atomic replacement. This first production path
        # deliberately does not use it: deciding that reviewed knowledge already
        # serving customers should stop is its own decision, and there is no
        # --force here to make it look like a detail of this one.
        raise OperatorRefusal(
            "DIFFERENT_RELEASE_ACTIVE",
            "A different decision release is already active. This V1 operator path never "
            "replaces one; retiring the active release is a separate, governed decision.",
            active=[_release_summary(r) for r in active],
            requested=str(release.id),
        )

    action = str(canonical["policy_rules"][0]["action"])
    copy_state = _assert_reviewed_copy(action)

    if release.status == PersonalDecisionReleaseStatus.ACTIVE.value:
        loaded = await load_active_personal_decision_release(session)
        return {
            "ok": True,
            "operation": "activate",
            "pack_id": pack.PACK_ID,
            "actor": actor,
            "changed": False,
            "already_active": True,
            "release": _release_summary(release),
            "runtime": _runtime_summary(loaded),
            "copy": copy_state,
        }

    if release.status != PersonalDecisionReleaseStatus.APPROVED.value:
        raise OperatorRefusal(
            "RELEASE_NOT_APPROVED",
            f"The release is {release.status}; only an approved release may be activated. "
            "Review verification, validation and approval are separate governance events "
            "and this tool performs none of them.",
            release_id=str(release.id),
            status=release.status,
        )

    # Independently rebuild from the governed evidence that exists right now. A
    # release approved last week against evidence that has since been revised is
    # still approved; it is simply no longer the pack.
    claim = await _authoritative_published_entry(session)
    entry = await _serialized_applicability(session, claim)
    rebuilt = pack.build_release_manifest_from_published_entry(entry)
    rebuilt_hash = manifest_content_hash(parse_release_manifest(rebuilt))
    if rebuilt != canonical or rebuilt_hash != release.content_hash:
        raise OperatorRefusal(
            "MANIFEST_DIVERGED_FROM_PACK",
            "Recompiling the reviewed pack from currently published evidence does not "
            "reproduce this release. The evidence moved after approval.",
            release_id=str(release.id),
            recorded=release.content_hash,
            recompiled=rebuilt_hash,
        )

    # Evidence eligibility, cross-validation and the transition itself all stay
    # in Step 8H. None of it is reimplemented here.
    try:
        validation = await release_authoring.validate_personal_decision_release(
            session, release.id,
        )
        activated = await release_authoring.activate_personal_decision_release(
            session, release.id, actor=actor,
        )
    except AppError as error:
        raise _domain_refusal(
            "RELEASE_VALIDATION_FAILED", error, release_id=str(release.id),
        ) from error

    loaded = await load_active_personal_decision_release(session)
    if loaded is None or loaded.release_id != release.id:
        raise OperatorRefusal(
            "RUNTIME_MISMATCH",
            "After activation the runtime loader does not return the requested release.",
            release_id=str(release.id),
            loaded=str(loaded.release_id) if loaded else None,
        )
    if loaded.content_hash != expected_content_hash:
        raise OperatorRefusal(
            "RUNTIME_HASH_MISMATCH",
            "After activation the runtime loader reports a different content hash.",
            release_id=str(release.id),
            expected=expected_content_hash,
            observed=loaded.content_hash,
        )
    return {
        "ok": True,
        "operation": "activate",
        "pack_id": pack.PACK_ID,
        "actor": actor,
        "changed": True,
        "already_active": False,
        "release": {
            "id": activated["id"],
            "release_version": activated["release_version"],
            "status": activated["status"],
            "content_hash": activated["content_hash"],
        },
        "validation": {
            "ready": validation["ready"],
            "semantic_evidence_checked": validation["semantic_evidence_checked"],
            "policies_checked": validation["policies_checked"],
            "explanations_checked": validation["explanations_checked"],
        },
        "runtime": _runtime_summary(loaded),
        "copy": copy_state,
    }


def _runtime_summary(loaded: Any) -> dict[str, Any]:
    if loaded is None:
        return {"active": False, "release_id": None, "content_hash": None}
    return {
        "active": True,
        "release_id": str(loaded.release_id),
        "release_version": loaded.release_version,
        "content_hash": loaded.content_hash,
    }


# ---------------------------------------------------------------------------
# deactivate
# ---------------------------------------------------------------------------
async def run_deactivate(
    session: AsyncSession,
    *,
    release_id: uuid.UUID,
    expected_content_hash: str,
    actor: str,
) -> dict[str, Any]:
    """Emergency stop for this exact release, and no other.

    Zero active releases is the safe fallback: Step 8K then shows a governed
    non-decision rather than a decision nobody is sure about. Nothing is
    restored in its place -- reinstating earlier knowledge is a fresh decision
    with its own review.
    """
    release = await _release_or_refuse(session, release_id)
    _assert_expected_hash(release, expected_content_hash)
    parsed = _assert_manifest_self_consistent(release)
    if not _manifest_is_the_step8i_pack(canonical_manifest(parsed)):
        raise OperatorRefusal(
            "NOT_THE_STEP8I_PACK",
            "That release is not the reviewed Step 8I pack; this tool will not deactivate "
            "somebody else's release.",
            release_id=str(release.id),
        )

    if release.status == PersonalDecisionReleaseStatus.RETIRED.value:
        return {
            "ok": True,
            "operation": "deactivate",
            "pack_id": pack.PACK_ID,
            "actor": actor,
            "changed": False,
            "already_inactive": True,
            "release": _release_summary(release),
            "runtime": _runtime_summary(
                await load_active_personal_decision_release(session)
            ),
        }
    if release.status != PersonalDecisionReleaseStatus.ACTIVE.value:
        raise OperatorRefusal(
            "RELEASE_NOT_ACTIVE",
            f"The release is {release.status}, so it is not the release production is "
            "using. Refusing to deactivate anything else.",
            release_id=str(release.id),
            status=release.status,
        )

    try:
        result = await release_authoring.deactivate_personal_decision_release(
            session, release.id, actor=actor,
        )
    except AppError as error:
        raise _domain_refusal(
            "DEACTIVATION_REFUSED", error, release_id=str(release.id),
        ) from error
    loaded = await load_active_personal_decision_release(session)
    if loaded is not None:
        raise OperatorRefusal(
            "RUNTIME_STILL_ACTIVE",
            "After deactivation the runtime loader still reports an active release.",
            release_id=str(release.id),
            loaded=str(loaded.release_id),
        )
    return {
        "ok": True,
        "operation": "deactivate",
        "pack_id": pack.PACK_ID,
        "actor": actor,
        "changed": True,
        "already_inactive": False,
        "release": {
            "id": result["id"],
            "release_version": result["release_version"],
            "status": result["status"],
            "content_hash": result["content_hash"],
        },
        "runtime": _runtime_summary(loaded),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _actor(value: str) -> str:
    text = (value or "").strip()
    if not text or len(text) > _ACTOR_MAX:
        raise argparse.ArgumentTypeError(
            f"--actor must name an explicit operator, 1-{_ACTOR_MAX} characters."
        )
    return text


def _release_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--release-id must be a UUID.") from error


def _content_hash(value: str) -> str:
    text = (value or "").strip()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise argparse.ArgumentTypeError(
            "--expected-content-hash must be 64 lowercase hex characters."
        )
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operate the exact reviewed Step 8I petrolatum knowledge pack. This tool "
            "installs and activates one already-reviewed pack; it cannot author, choose "
            "or alter any science, policy, action, reason or citation."
        ),
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser("status", help="Report the pack's current state. Read-only.")

    for name, help_text in (
        ("prepare", "Create only the missing DRAFT authority for the exact pack."),
        ("compile", "Compile the published entry into a DRAFT decision release."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--actor", type=_actor, required=True)

    for name, help_text in (
        ("activate", "Activate one exact approved release, named by id and hash."),
        ("deactivate", "Emergency stop for one exact active release."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--release-id", type=_release_uuid, required=True)
        sub.add_argument("--expected-content-hash", type=_content_hash, required=True)
        sub.add_argument("--actor", type=_actor, required=True)
    return parser


async def _dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    """Run one operation in one session. Commit only on a successful write."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            if args.operation == "status":
                result = await run_status(session)
            elif args.operation == "prepare":
                result = await run_prepare(session, actor=args.actor)
            elif args.operation == "compile":
                result = await run_compile(session, actor=args.actor)
            elif args.operation == "activate":
                result = await run_activate(
                    session,
                    release_id=args.release_id,
                    expected_content_hash=args.expected_content_hash,
                    actor=args.actor,
                )
            else:
                result = await run_deactivate(
                    session,
                    release_id=args.release_id,
                    expected_content_hash=args.expected_content_hash,
                    actor=args.actor,
                )
        except OperatorRefusal as refusal:
            # Nothing partially written survives a refusal, including one raised
            # after the domain has already changed rows in this transaction.
            await session.rollback()
            return refusal.as_result(args.operation), False
        except AppError as error:
            # A governed refusal raised by a domain this tool called. Same
            # treatment: roll back, report the domain's own reason, change
            # nothing.
            await session.rollback()
            refusal = _domain_refusal("GOVERNED_REFUSAL", error)
            return refusal.as_result(args.operation), False
        except BaseException:
            # Not a governed refusal: this tool does not know what happened, so
            # it must neither describe the failure nor leave a decision about
            # the transaction to something further out. Roll back explicitly,
            # here, while the failure is still in scope -- closing the session
            # would discard the work too, but relying on that makes "nothing
            # was written" an accident of the context manager rather than a
            # promise this function keeps. Then re-raise unchanged: the
            # boundary in `main` turns it into a sanitized structural result,
            # and nothing in between gets to interpret it.
            await session.rollback()
            raise
        if args.operation == "status":
            await session.rollback()
        else:
            await session.commit()
        return result, True


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result, ok = asyncio.run(_wrapped(args))
    except Exception as error:  # noqa: BLE001 - the operator gets a code, not a message
        # Structural, never descriptive. An unexpected exception here comes
        # from a database driver, a connection attempt, the filesystem, a
        # configuration read or some future internal path, and any of those can
        # carry a connection string, a credential, an internal hostname or a
        # fragment of SQL in its message. This tool cannot inspect an unknown
        # exception well enough to promise it does not, so it does not print
        # one: no `str(error)`, no `repr`, no args, no traceback. The class name
        # is enough to route the incident, and the detail belongs in the
        # operator's own process logs rather than in output that gets pasted
        # into a ticket.
        #
        # `_dispatch` has already rolled the transaction back, so the message
        # below is a statement of fact rather than a hope.
        print(json.dumps(
            {
                "ok": False,
                "operation": args.operation,
                "code": "UNEXPECTED_ERROR",
                "message": UNEXPECTED_ERROR_MESSAGE,
                "error_type": type(error).__name__,
            },
            indent=2,
            sort_keys=True,
        ))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if ok else 2


async def _wrapped(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    try:
        return await _dispatch(args)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
