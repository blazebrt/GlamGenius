"""The product domain's one door to the Step 7B formula engine.

Snapshot selection is deliberately outside this adapter.  The supplied
``LabelSnapshot`` is the complete observation authority: no latest-version
lookup, product fallback, scan-event fallback, canonicalisation, or write is
performed here.

Everything the product domain needs from the formula engine arrives through
this module, and no other file under ``app/domains/product`` imports that
domain — a boundary ``tests/test_step7b_formula_resolution.py`` holds up by
name.  Two things pass through besides the snapshot projection itself:

* :data:`LINE_BOUNDARIES` and :func:`boundary_significance`, because the label
  *version* authority has to keep two observations apart whenever the
  difference between them could change what the parser concludes — and the
  parser is the only authority both on where a boundary can be and on where
  one would matter. Grouping is its grammar, not the product domain's.
* :func:`formula_entries_from_label_snapshot`, the pure comparison keying two
  stored observations need — no session, no registry, no write.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.formulas.parser import (
    LINE_BOUNDARIES,
    ParseStatus,
    boundary_significance,
)
from app.domains.formulas.service import (
    FormulaEntries,
    FormulaEntry,
    FormulaResolution,
    canonical_formula_entries,
    resolve_formula,
)
from app.domains.product.models import LabelSnapshot


def _printed_ingredients(snapshot: LabelSnapshot) -> object:
    """The exact stored ``ingredients_text``, or the absent-observation sentinel.

    A malformed in-memory ``facts`` value is treated like an absent ingredient
    observation.  Its non-string sentinel never becomes a string and is handed
    to the existing Step 7B authority unchanged.
    """
    return (
        snapshot.facts.get("ingredients_text")
        if isinstance(snapshot.facts, Mapping)
        else None
    )


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
    """Resolve the exact stored ``ingredients_text`` from ``snapshot``."""
    formula = await resolve_formula(session, _printed_ingredients(snapshot))
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


def formula_entries_from_label_snapshot(snapshot: LabelSnapshot) -> FormulaEntries:
    """Key one snapshot's printed ingredient list for comparison, purely.

    The same text :func:`project_formula_from_label_snapshot` would resolve,
    parsed and normalised by the same two authorities, with the registry
    lookup left out — so the answer for a stored observation is the same
    before and after a reviewer publishes a new canonical identity.
    """
    return canonical_formula_entries(_printed_ingredients(snapshot))


__all__ = [
    "LINE_BOUNDARIES",
    "boundary_significance",
    "FormulaEntries",
    "FormulaEntry",
    "FormulaProjectionProvenance",
    "LabelSnapshotFormulaProjection",
    "ParseStatus",
    "formula_entries_from_label_snapshot",
    "project_formula_from_label_snapshot",
]
