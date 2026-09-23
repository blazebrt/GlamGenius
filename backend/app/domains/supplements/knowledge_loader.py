"""Load the absorption knowledge base into the database, as drafts.

Two things happen per compound:

* a row in ``supplement_component_knowledge`` holding the structured numbers,
  keyed on ``canonical_component_key``; and
* a **draft** entry in the authoring tool, so a person reviews it before any of
  it can be published.

Nothing here publishes, and nothing here approves. The authoring tool's own
gate still applies, which means an entry cannot be approved until somebody
supplies a source URL that opens — and for entries marked
``not_enough_information`` that is the correct permanent state.

Step 13 adds one thing to each draft: a **binding** in its ``structured_value``
recording exactly which structured row values the draft is evidence for (see
``knowledge_reader``). The customer-facing reader shows a row only while the
published claim's binding still equals the row, so a row changed after review
is withheld instead of shown under someone else's approval. A binding is only
ever written onto a *draft* that has no recorded verification yet; once a
reviewer has started verifying a draft, or it has moved past draft, the loader
leaves it alone and any disagreement surfaces as a withheld entry.

Idempotent: running it twice updates unreviewed rows in place and does not
duplicate the drafts.

**Reviewed authority is never rewritten.** A row is updated from the file only
while it is ``unverified`` and every claim it touches is a pristine draft. Once
a person has confirmed or disputed a row, or approved, rejected, published or
started verifying its claim, the loader leaves the row, its verification state,
its claim link and the claim exactly as they are. If the file has since changed,
the difference is logged as ``reviewed_row_drift`` and counted in the summary
(``reviewed_rows_preserved``, ``reviewed_row_drift``) for a person to act on.

Run with ``python -m app.domains.supplements.knowledge_loader`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence import authoring
from app.domains.evidence.enums import ReviewStatus
from app.domains.supplements.knowledge import COMPOUNDS, Compound, Verification
from app.domains.supplements.knowledge_reader import BINDING_KEY, SUBJECT_TYPE, binding_for, row_binding_values
from app.domains.supplements.models import SupplementComponentKnowledge
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)

# SUBJECT_TYPE is imported from the reader so the writer and the reader of these
# claims can never disagree about what they are filed under.
LOADER_AUTHOR = "knowledge_loader"


def _claim_text(compound: Compound) -> str:
    """One sentence stating what is known, and saying so when little is."""
    parts: list[str] = []
    percent = compound.elemental_percent
    if percent is not None:
        kind = "elemental" if compound.percent_kind == "elemental_by_weight" else "equivalent"
        parts.append(f"{compound.form} is {percent}% {compound.nutrient.lower()} by weight ({kind}).")
    if compound.absorption is not None:
        parts.append(compound.absorption.summary)
    else:
        parts.append(
            f"There is not enough information this system could source on how well "
            f"{compound.form} is absorbed."
        )
    return " ".join(parts)


def _notes(compound: Compound) -> str:
    lines: list[str] = []
    if compound.hydration:
        lines.append(f"Hydration: {compound.hydration}")
    if compound.equivalent_note:
        lines.append(compound.equivalent_note)
    if compound.note:
        lines.append(compound.note)
    if compound.absorption is not None:
        # The figure a reviewer must confirm, stated where the reviewer reads.
        lines.append(
            f"Absorption figure carried: {compound.absorption.value_text} "
            f"({compound.absorption.unit}); author's confidence rating: {compound.absorption.confidence}."
        )
    if compound.absorption and compound.absorption.disagreement:
        lines.append(f"Sources disagree: {compound.absorption.disagreement}")
    lines.append(
        "The elemental percentage is arithmetic on atomic weights and needs no source. "
        "Any absorption figure comes from the cited study and has NOT been opened and "
        "confirmed by the system that loaded it."
    )
    return "\n".join(lines)


#: Row fields the loader writes from the knowledge file. A reviewed row keeps
#: every one of them exactly as it was reviewed.
LOADED_FIELDS: tuple[str, ...] = (
    "nutrient", "elemental_percent", "percent_kind", "hydration_note",
    "absorption_summary", "absorption_value", "absorption_unit", "disagreement",
    "source_name", "source_url", "source_identifier", "confidence", "evidence_tier", "notes",
)

#: Stable operator-only reason when the file and a reviewed row differ.
REVIEWED_ROW_DRIFT = "reviewed_row_drift"


def _candidate(compound: Compound) -> dict[str, Any]:
    """What the knowledge file would write for this compound."""
    absorption = compound.absorption
    return {
        "nutrient": compound.nutrient,
        "elemental_percent": compound.elemental_percent,
        "percent_kind": compound.percent_kind,
        "hydration_note": compound.hydration or compound.equivalent_note,
        "absorption_summary": absorption.summary if absorption else None,
        "absorption_value": absorption.value_text if absorption else None,
        "absorption_unit": absorption.unit if absorption else None,
        "disagreement": absorption.disagreement if absorption else None,
        "source_name": absorption.source_name if absorption else None,
        "source_url": absorption.source_url if absorption else None,
        "source_identifier": absorption.source_identifier if absorption else None,
        "confidence": str(absorption.confidence) if absorption else None,
        "evidence_tier": compound.tier,
        "notes": compound.note,
    }


def claim_is_pristine(claim: Any | None) -> bool:
    """Has no person touched this claim's review yet?

    A draft that nobody has approved, rejected, published or attested to. A
    claim that went back to draft after a rejection has been looked at, and is
    not pristine.
    """
    if claim is None:
        return True
    return (
        claim.review_status == ReviewStatus.DRAFT.value
        and not claim.reviewed_by and claim.reviewed_at is None
        and not claim.published_by and claim.published_at is None
        and not (claim.structured_value or {}).get("publication_verification")
    )


async def _upsert_knowledge(
    session: AsyncSession, compound: Compound, claim_id: Any | None,
) -> tuple[SupplementComponentKnowledge, bool, list[str]]:
    """Create or update one row, but only while nobody has reviewed anything.

    Returns ``(row, written, drifted_fields)``. The loader is release-owned: it
    may keep an unreviewed draft in step with the file, and nothing else. A row
    is rewritten only while it is ``unverified`` *and* both the claim it is
    linked to and the claim it would be linked to are pristine drafts. Anything
    else — a ``confirmed`` or ``disputed`` row, or a claim somebody approved,
    rejected, published or started verifying — is left exactly as it is: no
    value, no verification state and no claim link changes. Where the file now
    says something different from such a row, the difference is reported and
    logged, never applied.
    """
    from app.domains.evidence.models import EvidenceClaim

    candidate = _candidate(compound)
    row = (await session.execute(
        select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.canonical_component_key == compound.key,
            SupplementComponentKnowledge.compound_form == compound.form,
        )
    )).scalar_one_or_none()
    target = await session.get(EvidenceClaim, claim_id) if claim_id is not None else None

    if row is not None:
        linked = await session.get(EvidenceClaim, row.evidence_claim_id) if row.evidence_claim_id else None
        pristine = (
            row.verification == Verification.UNVERIFIED.value
            and claim_is_pristine(linked)
            and claim_is_pristine(target)
        )
        if not pristine:
            drifted = [field for field in LOADED_FIELDS if getattr(row, field) != candidate[field]]
            if claim_id is not None and row.evidence_claim_id != claim_id:
                drifted.append("evidence_claim_id")
            if drifted:
                logger.warning(
                    "supplement_knowledge_reviewed_row_drift",
                    extra={
                        "reason": REVIEWED_ROW_DRIFT,
                        "knowledge_row_id": str(row.id),
                        "compound_form": row.compound_form,
                        "verification": row.verification,
                        "fields": ",".join(drifted),
                    },
                )
            return row, False, drifted
    else:
        row = SupplementComponentKnowledge(
            canonical_component_key=compound.key, compound_form=compound.form,
        )
        session.add(row)

    for field, value in candidate.items():
        setattr(row, field, value)
    # Never anything else on load: no source here has been opened by this system.
    row.verification = Verification.UNVERIFIED.value
    if claim_id is not None:
        row.evidence_claim_id = claim_id
    await session.flush()
    return row, True, []


def _bind_draft(claim: Any, row: SupplementComponentKnowledge) -> bool:
    """Record on a draft which row values it is evidence for. Drafts only.

    Returns whether anything was written. Never touches a claim that has left
    draft, or a draft somebody has started verifying: rewriting the numbers
    under a recorded attestation would make the attestation about something
    else.
    """
    if claim.review_status != ReviewStatus.DRAFT.value:
        return False
    current = dict(claim.structured_value or {})
    if current.get("publication_verification"):
        return False
    binding = binding_for(row_binding_values(row))
    if current.get(BINDING_KEY) == binding:
        return False
    current[BINDING_KEY] = binding
    claim.structured_value = current
    return True


async def load(session: AsyncSession, *, author: str = LOADER_AUTHOR) -> dict[str, Any]:
    from app.domains.evidence.models import EvidenceClaim

    created_drafts = 0
    reused_drafts = 0
    bound_drafts = 0
    reviewed_rows_preserved = 0
    reviewed_row_drift = 0
    for compound in COMPOUNDS:
        # One draft per compound form, found by the subject it describes.
        existing = (await session.execute(
            select(EvidenceClaim).where(
                EvidenceClaim.subject_type == SUBJECT_TYPE,
                EvidenceClaim.subject_key == compound.form,
            ).order_by(EvidenceClaim.claim_version.desc()).limit(1)
        )).scalar_one_or_none()

        if existing is None:
            absorption = compound.absorption
            entry = await authoring.create_draft(
                session,
                authoring.EntryInput(
                    subject_type=SUBJECT_TYPE,
                    subject_key=compound.form,
                    claim=_claim_text(compound),
                    value=str(compound.elemental_percent) if compound.elemental_percent is not None else None,
                    unit="% by weight" if compound.elemental_percent is not None else None,
                    source_name=absorption.source_name if absorption else "No source found",
                    source_url=absorption.source_url if absorption else "",
                    evidence_tier=compound.tier,
                    notes=_notes(compound),
                    domain="supplements",
                ),
                author=author,
            )
            claim_id = entry["id"]
            created_drafts += 1
        else:
            claim_id = existing.id
            reused_drafts += 1

        row, written, drifted = await _upsert_knowledge(session, compound, claim_id)
        if not written:
            # Reviewed authority: neither the row nor its claim is touched.
            reviewed_rows_preserved += 1
            reviewed_row_drift += 1 if drifted else 0
            continue
        claim = await session.get(EvidenceClaim, row.evidence_claim_id) if row.evidence_claim_id else None
        if claim is not None and _bind_draft(claim, row):
            bound_drafts += 1
            await session.flush()

    return {
        "compounds": len(COMPOUNDS),
        "drafts_created": created_drafts,
        "drafts_reused": reused_drafts,
        "drafts_bound": bound_drafts,
        "reviewed_rows_preserved": reviewed_rows_preserved,
        "reviewed_row_drift": reviewed_row_drift,
        "with_absorption": sum(1 for c in COMPOUNDS if c.absorption),
        "not_enough_information": sum(1 for c in COMPOUNDS if not c.absorption),
    }


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    factory = get_sessionmaker()
    async with factory() as session:
        summary = await load(session)
        await session.commit()
    logger.info("supplement_knowledge_loaded %s", summary)


if __name__ == "__main__":
    asyncio.run(main())
