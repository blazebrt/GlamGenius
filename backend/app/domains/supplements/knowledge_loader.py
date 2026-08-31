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

Idempotent: running it twice updates the knowledge rows in place and does not
duplicate the drafts.

Run with ``python -m app.domains.supplements.knowledge_loader`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence import authoring
from app.domains.supplements.knowledge import COMPOUNDS, Compound, Verification
from app.domains.supplements.models import SupplementComponentKnowledge
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)

SUBJECT_TYPE = "supplement_component"
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
    if compound.absorption and compound.absorption.disagreement:
        lines.append(f"Sources disagree: {compound.absorption.disagreement}")
    lines.append(
        "The elemental percentage is arithmetic on atomic weights and needs no source. "
        "Any absorption figure comes from the cited study and has NOT been opened and "
        "confirmed by the system that loaded it."
    )
    return "\n".join(lines)


async def _upsert_knowledge(
    session: AsyncSession, compound: Compound, claim_id: Any | None,
) -> SupplementComponentKnowledge:
    row = (await session.execute(
        select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.canonical_component_key == compound.key,
            SupplementComponentKnowledge.compound_form == compound.form,
        )
    )).scalar_one_or_none()
    if row is None:
        row = SupplementComponentKnowledge(
            canonical_component_key=compound.key, compound_form=compound.form,
        )
        session.add(row)

    absorption = compound.absorption
    row.nutrient = compound.nutrient
    row.elemental_percent = compound.elemental_percent
    row.percent_kind = compound.percent_kind
    row.hydration_note = compound.hydration or compound.equivalent_note
    row.absorption_summary = absorption.summary if absorption else None
    row.absorption_value = absorption.value_text if absorption else None
    row.absorption_unit = absorption.unit if absorption else None
    row.disagreement = absorption.disagreement if absorption else None
    row.source_name = absorption.source_name if absorption else None
    row.source_url = absorption.source_url if absorption else None
    row.source_identifier = absorption.source_identifier if absorption else None
    row.confidence = str(absorption.confidence) if absorption else None
    row.evidence_tier = compound.tier
    # Never anything else on load: no source here has been opened by this system.
    row.verification = Verification.UNVERIFIED.value
    row.notes = compound.note
    if claim_id is not None:
        row.evidence_claim_id = claim_id
    await session.flush()
    return row


async def load(session: AsyncSession, *, author: str = LOADER_AUTHOR) -> dict[str, Any]:
    from app.domains.evidence.models import EvidenceClaim

    created_drafts = 0
    reused_drafts = 0
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

        await _upsert_knowledge(session, compound, claim_id)

    return {
        "compounds": len(COMPOUNDS),
        "drafts_created": created_drafts,
        "drafts_reused": reused_drafts,
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
