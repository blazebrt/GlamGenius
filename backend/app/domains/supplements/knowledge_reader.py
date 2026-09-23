"""The one reader that may put supplement form knowledge in front of a customer.

Step 13. ``supplement_component_knowledge`` holds structured numbers about one
compound form; ``evidence_claims`` holds the review state. Neither is enough on
its own:

* ``SupplementComponentKnowledge.verification == "confirmed"`` says a person
  opened the source. It is not publication, and a row can be edited after the
  fact.
* A published ``EvidenceClaim`` says a claim passed review. It does not say the
  structured row still carries what was reviewed.
* ``confidence`` is the author's rating of a finding. It is never verification
  and never publication.
* A ``source_url`` string is a citation. It is not proof anybody opened it.

So a knowledge entry reaches a customer only when **one** check establishes all
of the following together, and it is withheld when any one fails:

1. It was asked for by an exact (nutrient, compound form) pair from
   ``forms.resolve_form`` — never by nutrient key alone.
2. The row exists for exactly that pair, is ``confirmed`` (not ``unverified``
   and not ``disputed``), carries an absorption figure, a confidence and an
   openable source URL.
3. The row points at a claim, and that claim is about exactly this form: domain
   ``supplements``, subject type ``supplement_component``, subject key equal to
   the row's ``compound_form``, a clinically-studied tier matching the row, not
   AI-generated, and a graded strength this reader accepts.
4. The claim is published public knowledge (``claim_is_public_knowledge_path``:
   published by a named person, approved before that, supported, graded with a
   rationale, every verification checkpoint recorded and no doubt left).
5. The claim's own binding (``structured_value["supplement_form_knowledge"]``,
   written when the draft was created) records exactly the row's current
   values, and the reviewed claim text contains the sentence a customer will
   read. A row edited after review therefore stops agreeing with its claim and
   is withheld rather than shown.
6. At least one source path is public-knowledge grade
   (``source_path_is_public_knowledge``) for an allowed source type, and its
   URL is the row's URL. The authoring tool files new sources as ``other``,
   which is not allowed here: a person has to classify the source first.

There is no publication path for ``disputed`` in the evidence contract
(``claim_is_public_knowledge_path`` requires a *supported* claim), so a disputed
row is withheld like any other failure. Disagreement between sources that a
reviewer published *inside* a supported claim is carried in ``disagreement``.

Nothing here repairs anything. A disagreement between the two sides is logged
for operators under a stable reason code and the entry is withheld.

Two queries, whatever the batch size.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.enums import EvidenceDomain, EvidenceStrength, EvidenceTier, SourceType
from app.domains.evidence.grading import Verification
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.evidence.service import claim_is_public_knowledge_path, source_path_is_public_knowledge
from app.domains.evidence.urls import openable_url
from app.domains.supplements.models import SupplementComponentKnowledge

logger = logging.getLogger(__name__)

READER_VERSION = "step-13-knowledge-v1"

#: The subject type the loader files every supplement form claim under.
SUBJECT_TYPE = "supplement_component"
#: The key inside ``EvidenceClaim.structured_value`` that binds a claim to a row.
BINDING_KEY = "supplement_form_knowledge"
BINDING_VERSION = "step-13-binding-v1"

#: The structured row fields a binding must record, exactly.
BOUND_FIELDS: tuple[str, ...] = (
    "canonical_component_key", "compound_form", "absorption_summary", "absorption_value",
    "absorption_unit", "disagreement", "source_url", "confidence",
)

#: Kinds of document that may carry an empirical claim about absorption.
#: ``other`` is excluded: it is what the authoring tool assigns when nobody has
#: classified a source, and an unclassified link must not carry a public claim.
#: Manufacturer documents are excluded because they are a party to the claim.
SUPPLEMENT_KNOWLEDGE_SOURCE_TYPES: frozenset[str] = frozenset({
    SourceType.PEER_REVIEWED_RESEARCH.value,
    SourceType.SYSTEMATIC_REVIEW.value,
    SourceType.OFFICIAL_GUIDELINE.value,
    SourceType.GOVERNMENT_REFERENCE.value,
    SourceType.PROFESSIONAL_CONSENSUS.value,
})

#: Graded strengths that may carry a customer-visible empirical statement.
SUPPLEMENT_KNOWLEDGE_STRENGTHS: frozenset[str] = frozenset({
    EvidenceStrength.STRONG.value,
    EvidenceStrength.MODERATE.value,
    EvidenceStrength.LIMITED.value,
})


class KnowledgeStatus(StrEnum):
    PUBLISHED = "published"
    NOT_ENOUGH_INFORMATION = "not_enough_information"


class WithheldBecause(StrEnum):
    """Stable operator reason codes. Never shown to a customer."""

    NO_ROW = "no_row"
    ROW_UNVERIFIED = "row_unverified"
    ROW_DISPUTED = "row_disputed"
    ROW_HAS_NO_FIGURE = "row_has_no_figure"
    ROW_SOURCE_NOT_OPENABLE = "row_source_not_openable"
    NO_CLAIM = "no_claim"
    CLAIM_SUBJECT_MISMATCH = "claim_subject_mismatch"
    CLAIM_TIER_MISMATCH = "claim_tier_mismatch"
    CLAIM_AI_GENERATED = "claim_ai_generated"
    CLAIM_NOT_PUBLISHED = "claim_not_published"
    CLAIM_STRENGTH_NOT_ACCEPTED = "claim_strength_not_accepted"
    BINDING_MISSING = "binding_missing"
    BINDING_MISMATCH = "binding_mismatch"
    CLAIM_TEXT_MISMATCH = "claim_text_mismatch"
    NO_PUBLIC_SOURCE = "no_public_source"


#: Reasons that mean the two authorities disagree, which an operator must see.
#: A plain unreviewed draft is the normal state of every entry today and is not
#: logged: it is not a fault, it is what "dormant" looks like.
DISAGREEMENT_REASONS: frozenset[WithheldBecause] = frozenset({
    WithheldBecause.ROW_HAS_NO_FIGURE,
    WithheldBecause.ROW_SOURCE_NOT_OPENABLE,
    WithheldBecause.NO_CLAIM,
    WithheldBecause.CLAIM_SUBJECT_MISMATCH,
    WithheldBecause.CLAIM_TIER_MISMATCH,
    WithheldBecause.CLAIM_AI_GENERATED,
    WithheldBecause.CLAIM_NOT_PUBLISHED,
    WithheldBecause.CLAIM_STRENGTH_NOT_ACCEPTED,
    WithheldBecause.BINDING_MISSING,
    WithheldBecause.BINDING_MISMATCH,
    WithheldBecause.CLAIM_TEXT_MISMATCH,
    WithheldBecause.NO_PUBLIC_SOURCE,
})


@dataclass(frozen=True)
class PublishedSource:
    name: str
    publisher: str
    url: str


@dataclass(frozen=True)
class FormKnowledge:
    """What the reader decided for one exact (nutrient, form) pair."""

    canonical_component_key: str
    compound_form: str
    status: KnowledgeStatus
    summary: str | None = None
    value: str | None = None
    unit: str | None = None
    disagreement: str | None = None
    evidence_strength: str | None = None
    source: PublishedSource | None = None
    #: Operator-only. Never serialised for a customer.
    withheld_because: WithheldBecause | None = None

    def customer_dict(self) -> dict[str, Any]:
        """The customer payload. No row id, claim id, reviewer or review metadata."""
        if self.status is not KnowledgeStatus.PUBLISHED or self.source is None:
            return {"status": KnowledgeStatus.NOT_ENOUGH_INFORMATION.value}
        return {
            "status": KnowledgeStatus.PUBLISHED.value,
            "summary": self.summary,
            "value": self.value,
            "unit": self.unit,
            "disagreement": self.disagreement,
            "evidence_strength": self.evidence_strength,
            "source": {"name": self.source.name, "publisher": self.source.publisher, "url": self.source.url},
        }


def binding_for(row_values: dict[str, Any]) -> dict[str, Any]:
    """The binding a claim records for a row. Used by the loader and the reader alike."""
    binding = {field: row_values.get(field) for field in BOUND_FIELDS}
    binding["binding_version"] = BINDING_VERSION
    return binding


def row_binding_values(row: SupplementComponentKnowledge) -> dict[str, Any]:
    return {field: getattr(row, field) for field in BOUND_FIELDS}


def _withheld(key: str, form: str, reason: WithheldBecause, *, row: SupplementComponentKnowledge | None = None,
              claim: EvidenceClaim | None = None) -> FormKnowledge:
    if reason in DISAGREEMENT_REASONS:
        logger.warning(
            "supplement_knowledge_withheld",
            extra={
                "reason": reason.value,
                "reader_version": READER_VERSION,
                "canonical_component_key": key,
                "compound_form": form,
                "knowledge_row_id": str(row.id) if row is not None else None,
                "evidence_claim_id": str(claim.id) if claim is not None else None,
            },
        )
    return FormKnowledge(key, form, KnowledgeStatus.NOT_ENOUGH_INFORMATION, withheld_because=reason)


def _decide(
    key: str,
    form: str,
    row: SupplementComponentKnowledge | None,
    claim: EvidenceClaim | None,
    paths: list[tuple[EvidenceClaimSource, EvidenceSource]],
) -> FormKnowledge:
    if row is None:
        return _withheld(key, form, WithheldBecause.NO_ROW)

    published = claim is not None and claim_is_public_knowledge_path(claim)
    if row.verification == Verification.DISPUTED.value:
        return _withheld(key, form, WithheldBecause.ROW_DISPUTED, row=row, claim=claim)
    if row.verification != Verification.CONFIRMED.value:
        # A published claim over an unconfirmed row is a disagreement worth
        # an operator's eye; an unconfirmed row over a draft is just dormant.
        if published:
            logger.warning(
                "supplement_knowledge_withheld",
                extra={
                    "reason": WithheldBecause.ROW_UNVERIFIED.value,
                    "reader_version": READER_VERSION,
                    "canonical_component_key": key,
                    "compound_form": form,
                    "knowledge_row_id": str(row.id),
                    "evidence_claim_id": str(claim.id) if claim is not None else None,
                },
            )
        return FormKnowledge(key, form, KnowledgeStatus.NOT_ENOUGH_INFORMATION,
                             withheld_because=WithheldBecause.ROW_UNVERIFIED)
    if not (row.absorption_value or "").strip() or not (row.absorption_summary or "").strip() or not row.confidence:
        return _withheld(key, form, WithheldBecause.ROW_HAS_NO_FIGURE, row=row, claim=claim)
    row_url = openable_url(row.source_url)
    if row_url is None:
        return _withheld(key, form, WithheldBecause.ROW_SOURCE_NOT_OPENABLE, row=row, claim=claim)
    if claim is None:
        return _withheld(key, form, WithheldBecause.NO_CLAIM, row=row)

    if (
        claim.domain != EvidenceDomain.SUPPLEMENTS.value
        or claim.subject_type != SUBJECT_TYPE
        or claim.subject_key != row.compound_form
        or row.compound_form != form
        or row.canonical_component_key != key
    ):
        return _withheld(key, form, WithheldBecause.CLAIM_SUBJECT_MISMATCH, row=row, claim=claim)
    if (
        claim.evidence_tier != EvidenceTier.CLINICALLY_STUDIED.value
        or row.evidence_tier != claim.evidence_tier
    ):
        return _withheld(key, form, WithheldBecause.CLAIM_TIER_MISMATCH, row=row, claim=claim)
    if claim.ai_generated is not False:
        return _withheld(key, form, WithheldBecause.CLAIM_AI_GENERATED, row=row, claim=claim)
    if not published:
        return _withheld(key, form, WithheldBecause.CLAIM_NOT_PUBLISHED, row=row, claim=claim)
    if claim.evidence_strength not in SUPPLEMENT_KNOWLEDGE_STRENGTHS:
        return _withheld(key, form, WithheldBecause.CLAIM_STRENGTH_NOT_ACCEPTED, row=row, claim=claim)

    binding = (claim.structured_value or {}).get(BINDING_KEY)
    if not isinstance(binding, dict):
        return _withheld(key, form, WithheldBecause.BINDING_MISSING, row=row, claim=claim)
    if binding != binding_for(row_binding_values(row)):
        return _withheld(key, form, WithheldBecause.BINDING_MISMATCH, row=row, claim=claim)
    # The sentence a customer reads must be one the reviewer read.
    if row.absorption_summary.strip() not in (claim.summary or ""):
        return _withheld(key, form, WithheldBecause.CLAIM_TEXT_MISMATCH, row=row, claim=claim)

    sources = sorted(
        (
            source for link, source in paths
            if source_path_is_public_knowledge(link, source, allowed_source_types=SUPPLEMENT_KNOWLEDGE_SOURCE_TYPES)
            and openable_url(source.canonical_url) == row_url
        ),
        key=lambda source: (source.source_key, str(source.id)),
    )
    if not sources:
        return _withheld(key, form, WithheldBecause.NO_PUBLIC_SOURCE, row=row, claim=claim)
    source = sources[0]
    return FormKnowledge(
        key, form, KnowledgeStatus.PUBLISHED,
        summary=row.absorption_summary.strip(),
        value=row.absorption_value.strip(),
        unit=(row.absorption_unit or "").strip() or None,
        disagreement=(row.disagreement or "").strip() or None,
        evidence_strength=claim.evidence_strength,
        source=PublishedSource(name=source.title.strip(), publisher=source.publisher.strip(), url=row_url),
    )


async def read_form_knowledge(
    session: AsyncSession, pairs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], FormKnowledge]:
    """Decide every requested exact (nutrient key, compound form) pair.

    One answer per distinct pair. A pair with no row, or any failed check, is
    ``not_enough_information``. The row is fetched by the pair, never by key.
    """
    wanted = sorted({(str(key), str(form)) for key, form in pairs})
    if not wanted:
        return {}
    rows = list((await session.execute(
        select(SupplementComponentKnowledge).where(or_(*(
            and_(
                SupplementComponentKnowledge.canonical_component_key == key,
                SupplementComponentKnowledge.compound_form == form,
            )
            for key, form in wanted
        )))
    )).scalars().all())
    row_by_pair = {(row.canonical_component_key, row.compound_form): row for row in rows}

    claim_ids: set[uuid.UUID] = {row.evidence_claim_id for row in rows if row.evidence_claim_id is not None}
    claims: dict[uuid.UUID, EvidenceClaim] = {}
    paths: dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]] = {}
    if claim_ids:
        for claim, link, source in (await session.execute(
            select(EvidenceClaim, EvidenceClaimSource, EvidenceSource)
            .outerjoin(EvidenceClaimSource, EvidenceClaimSource.claim_id == EvidenceClaim.id)
            .outerjoin(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaim.id.in_(claim_ids))
        )).all():
            claims[claim.id] = claim
            if link is not None and source is not None:
                paths.setdefault(claim.id, []).append((link, source))

    decided: dict[tuple[str, str], FormKnowledge] = {}
    for key, form in wanted:
        row = row_by_pair.get((key, form))
        claim = claims.get(row.evidence_claim_id) if row is not None and row.evidence_claim_id else None
        decided[(key, form)] = _decide(key, form, row, claim, paths.get(claim.id, []) if claim is not None else [])
    return decided


__all__ = [
    "BINDING_KEY",
    "BINDING_VERSION",
    "BOUND_FIELDS",
    "DISAGREEMENT_REASONS",
    "READER_VERSION",
    "SUBJECT_TYPE",
    "SUPPLEMENT_KNOWLEDGE_SOURCE_TYPES",
    "SUPPLEMENT_KNOWLEDGE_STRENGTHS",
    "FormKnowledge",
    "KnowledgeStatus",
    "PublishedSource",
    "WithheldBecause",
    "binding_for",
    "read_form_knowledge",
    "row_binding_values",
]
