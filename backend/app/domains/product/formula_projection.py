"""Project one explicitly selected label snapshot through Step 7B.

Snapshot selection is deliberately outside this adapter.  The supplied
``LabelSnapshot`` is the complete observation authority: no latest-version
lookup, product fallback, scan-event fallback, canonicalisation, or write is
performed here.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.formulas.service import FormulaResolution, resolve_formula
from app.domains.product.models import LabelSnapshot


@dataclass(frozen=True)
class FormulaProjectionProvenance:
    """The immutable label-version identity from which a formula was read."""

    label_snapshot_id: uuid.UUID
    barcode: str
    version_number: int
    content_fingerprint: str
    scan_event_id: uuid.UUID


@dataclass(frozen=True)
class LabelSnapshotFormulaProjection:
    """A Step 7B result bound to the exact supplied physical-label version."""

    provenance: FormulaProjectionProvenance
    formula: FormulaResolution


async def project_formula_from_label_snapshot(
    session: AsyncSession,
    snapshot: LabelSnapshot,
) -> LabelSnapshotFormulaProjection:
    """Resolve the exact stored ``ingredients_text`` from ``snapshot``.

    A malformed in-memory ``facts`` value is treated like an absent ingredient
    observation.  Its non-string sentinel never becomes a string and is handed
    to the existing Step 7B authority unchanged.
    """
    ingredients_text: object = (
        snapshot.facts.get("ingredients_text")
        if isinstance(snapshot.facts, Mapping)
        else None
    )
    formula = await resolve_formula(session, ingredients_text)
    return LabelSnapshotFormulaProjection(
        provenance=FormulaProjectionProvenance(
            label_snapshot_id=snapshot.id,
            barcode=snapshot.barcode,
            version_number=snapshot.version_number,
            content_fingerprint=snapshot.content_fingerprint,
            scan_event_id=snapshot.scan_event_id,
        ),
        formula=formula,
    )


__all__ = [
    "FormulaProjectionProvenance",
    "LabelSnapshotFormulaProjection",
    "project_formula_from_label_snapshot",
]
