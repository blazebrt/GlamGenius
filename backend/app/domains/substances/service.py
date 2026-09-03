"""Resolving an exact reviewed name to a canonical substance identity.

The whole contract is one question: *what exact entity does this exact name
denote?* Not whether it is safe, what it does, or how much of it is present.

Three answers, and no fourth:

* ``RESOLVED``   — exactly one eligible entity.
* ``AMBIGUOUS``  — two or more, and we refuse to choose between them.
* ``UNRESOLVED`` — none.

There is no score, no probability and no confidence percentage, because there is
nothing to be uncertain about: either a reviewed, published claim records this
exact name for this entity, or it does not.

**Ambiguity is never broken.** Not by popularity, source count, evidence
strength, the old Care family, product category, ingredient position,
alphabetical order, row order, a heuristic, or a model. Every one of those would
be the system inventing an answer a reviewer never gave. Two entities that share
a printed name is a real state of the world; reporting it is the honest thing.

**Eligibility is decided before anything is narrowed.** Step 6B shipped a
LIMIT-1 defect where the newest row was selected and only then found invalid,
so an ineligible row could permanently hide a valid one behind it. Here the
bounded candidate set for a name is read in full, each candidate is validated,
and only then is the answer decided. An invalid row can cost nothing but itself.

**Constant queries, whatever the batch size.** A formula has many ingredients,
so this is built as a batch resolver from the start rather than a loop that will
be discovered to be N+1 later. Two reads serve any allowed batch.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.enums import ClaimType, EvidenceDomain, EvidenceTier, SourceType
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.evidence.service import (
    claim_is_public_knowledge_path,
    source_path_is_public_knowledge,
)
from app.domains.substances.enums import SubstanceStatus
from app.domains.substances.identity_schema import parse_identity
from app.domains.substances.models import Substance, SubstanceName
from app.domains.substances.normalization import normalize_name

#: The subject_type every identity claim carries.
IDENTITY_SUBJECT_TYPE = "substance"

#: Source types that may establish that a name denotes an entity.
#:
#: Conservative on purpose, and narrower than the full ``SourceType`` list. A
#: register, a government reference, an ingredient reference database or a
#: manufacturer's own technical document are the kinds of document that *record
#: nomenclature*. Notably excluded: ``other`` (which is what the authoring tool
#: assigns when nobody has classified the source, so accepting it would let an
#: unclassified link establish canonical identity), ``manufacturer_claim`` and
#: ``manufacturer_label`` (marketing copy, where a trade name standing in for a
#: molecule is the norm), and the research/consensus types, which speak about
#: what a substance *does* rather than what it *is*.
IDENTITY_SOURCE_TYPES: frozenset[str] = frozenset({
    SourceType.OFFICIAL_REGULATION.value,
    SourceType.GOVERNMENT_REFERENCE.value,
    SourceType.INGREDIENT_REFERENCE_DATABASE.value,
    SourceType.MANUFACTURER_TECHNICAL_DOCUMENT.value,
})

#: One formula's ingredient list is the realistic upper bound for a batch.
#: Larger inputs are refused rather than truncated: silently dropping the tail
#: would return "unresolved" for names nobody actually looked at.
MAX_BATCH_NAMES = 128


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class SubstanceResolution:
    """The answer for one queried name."""

    query: str
    normalized_name: str | None
    status: ResolutionStatus
    substance_id: uuid.UUID | None = None
    substance_key: str | None = None
    entity_kind: str | None = None
    #: Every distinct entity the name could denote. One when RESOLVED, two or
    #: more when AMBIGUOUS, empty when UNRESOLVED. Carried so a caller can say
    #: *which* entities were confused rather than only that something was.
    candidate_substance_keys: tuple[str, ...] = ()


def _unresolved(query: str, normalized: str | None) -> SubstanceResolution:
    return SubstanceResolution(
        query=query, normalized_name=normalized, status=ResolutionStatus.UNRESOLVED,
    )


def _claim_matches_identity_subject(claim: EvidenceClaim, substance: Substance) -> bool:
    """Is this claim actually an identity claim about *this* substance?

    Checked at read time rather than trusted from the foreign key. A row can
    drift — a hand-edited database, a bad historical import, a claim edited into
    a different subject — and a ``SubstanceName`` pointing at a claim is not by
    itself evidence that the claim says anything about this substance.
    """
    return (
        claim.domain == EvidenceDomain.SUBSTANCE.value
        and claim.subject_type == IDENTITY_SUBJECT_TYPE
        and claim.subject_key == substance.substance_key
        and claim.claim_type == ClaimType.SUBSTANCE_IDENTITY.value
        and claim.evidence_tier == EvidenceTier.REFERENCE_DATA.value
    )


def _name_row_agrees_with_claim(row: SubstanceName, claim: EvidenceClaim, substance: Substance) -> bool:
    """Does the claim's own payload still record exactly this name row?

    The materialised index is only as trustworthy as its agreement with the
    evidence it was built from. Re-parsing the payload and requiring the exact
    row to appear in it is what makes manual drift fail closed instead of
    resolving to something no reviewer wrote.
    """
    identity = parse_identity(claim.structured_value)
    if identity is None:
        return False
    if identity.entity_kind != substance.entity_kind:
        return False
    for name in identity.names:
        if (
            name.normalized_name == row.normalized_name
            and name.namespace == row.namespace
            and name.is_preferred == row.is_preferred
        ):
            return True
    return False


async def resolve_names(
    session: AsyncSession, names: Sequence[str],
) -> list[SubstanceResolution]:
    """Resolve many exact names in a bounded, constant number of queries.

    One entry is returned per input, in input order, including for inputs that
    normalise to nothing. Duplicate inputs get identical answers.

    **This takes one candidate name at a time — never an ingredient list.**
    ``"Water, Niacinamide, Glycerin"`` does not resolve to niacinamide: there is
    no tokenisation, no substring search and no longest-alias matching here.
    Splitting a printed list into candidate names is Step 7B's problem, and doing
    it implicitly would mean guessing where one name ends and the next begins.
    """
    if len(names) > MAX_BATCH_NAMES:
        raise ValueError(f"at most {MAX_BATCH_NAMES} names may be resolved at once")

    normalized: list[str | None] = [normalize_name(name) for name in names]
    lookup_keys = sorted({key for key in normalized if key is not None})
    if not lookup_keys:
        return [_unresolved(str(q), n) for q, n in zip(names, normalized, strict=True)]

    # Query 1 — every candidate row for every key at once, joined to the entity
    # and the claim so eligibility needs no further read. Bounded by the batch
    # size and by how many entities may share a printed name.
    candidates = (await session.execute(
        select(SubstanceName, Substance, EvidenceClaim)
        .join(Substance, Substance.id == SubstanceName.substance_id)
        .join(EvidenceClaim, EvidenceClaim.id == SubstanceName.identity_claim_id)
        .where(
            SubstanceName.normalized_name.in_(lookup_keys),
            Substance.status == SubstanceStatus.ACTIVE.value,
        )
    )).all()

    claim_ids = {claim.id for _row, _substance, claim in candidates}
    source_paths: dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]] = {}
    if claim_ids:
        # Query 2 — the source paths for exactly those claims, in one read.
        for link, source in (await session.execute(
            select(EvidenceClaimSource, EvidenceSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaimSource.claim_id.in_(claim_ids))
        )).all():
            source_paths.setdefault(link.claim_id, []).append((link, source))

    # Validate every candidate before anything is narrowed, then group by the
    # entity it establishes. Grouping by substance is what makes two eligible
    # paths to the same entity a single RESOLVED answer rather than ambiguity.
    eligible: dict[str, dict[uuid.UUID, Substance]] = {key: {} for key in lookup_keys}
    for row, substance, claim in candidates:
        if not _claim_matches_identity_subject(claim, substance):
            continue
        if not claim_is_public_knowledge_path(claim):
            continue
        if not _name_row_agrees_with_claim(row, claim, substance):
            continue
        if not any(
            source_path_is_public_knowledge(
                link, source, allowed_source_types=IDENTITY_SOURCE_TYPES,
            )
            for link, source in source_paths.get(claim.id, ())
        ):
            continue
        eligible[row.normalized_name][substance.id] = substance

    results: list[SubstanceResolution] = []
    for query, key in zip(names, normalized, strict=True):
        if key is None:
            results.append(_unresolved(str(query), None))
            continue
        found = eligible.get(key) or {}
        # Sorted so the reported candidate set is deterministic. This orders a
        # *reported* set; it never picks a winner out of one.
        keys = tuple(sorted(substance.substance_key for substance in found.values()))
        if not found:
            results.append(_unresolved(str(query), key))
        elif len(found) == 1:
            substance = next(iter(found.values()))
            results.append(SubstanceResolution(
                query=str(query),
                normalized_name=key,
                status=ResolutionStatus.RESOLVED,
                substance_id=substance.id,
                substance_key=substance.substance_key,
                entity_kind=substance.entity_kind,
                candidate_substance_keys=keys,
            ))
        else:
            results.append(SubstanceResolution(
                query=str(query),
                normalized_name=key,
                status=ResolutionStatus.AMBIGUOUS,
                candidate_substance_keys=keys,
            ))
    return results


async def resolve_name(session: AsyncSession, name: str) -> SubstanceResolution:
    """Resolve one exact name. Delegates, so both paths share one definition."""
    return (await resolve_names(session, [name]))[0]


__all__ = [
    "IDENTITY_SOURCE_TYPES",
    "IDENTITY_SUBJECT_TYPE",
    "MAX_BATCH_NAMES",
    "ResolutionStatus",
    "SubstanceResolution",
    "resolve_name",
    "resolve_names",
]
