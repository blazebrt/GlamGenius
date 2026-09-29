"""A confirmed capture whose personal observation privacy erasure has withdrawn.

A semantic :class:`LabelSnapshot` keeps its first capture as ``scan_event_id``
for good, and every later identical capture — this account's or anybody
else's — resolves to that same row. When the first confirmer erases their
account, :func:`app.domains.privacy.deletion_service._withdraw_scan_observations`
clears the capture's ``label_facts`` on purpose, and the account cascade severs
the capture and its AI run from the person. That is privacy working, and none
of it is undone here.

What must not follow is that everybody else loses the version. The resolver
used to re-prove a historical source by reading the source's own facts, which
erasure has deliberately removed, so one person's deletion silently broke the
pack authority of every independent capture of the same label.

**What erasure leaves, and why it is proof.** No personal field survives, but
four things the genuine path writes do, and together they are a signature no
other path produces:

* the capture row itself: the confirmed-label outcome, its barcode, its device
  and its ``ai_run_id``, with ``label_facts`` and ``account_id`` now empty and
  ``account_attachment_allowed`` false. Both confirmation routes are signed in
  and always write an account, so an empty account on a label capture means
  the account is gone — and erasure marks every row it withdraws as never
  attachable to anyone again;
* the AI run ledger row (``ai_runs``), which erasure keeps deliberately as the
  non-personal cost and provenance record: a successful, schema-validated run
  of a label transcription workflow, created before the capture. The
  confirmation route required that run to belong to the confirming account,
  so an empty account on it means the same erasure;
* the *absence* of that run's output. The gateway writes a successful run and
  its output in one transaction, and confirmation reads the output, so a
  successful run with no output means ``_delete_ai_outputs`` removed it — the
  only code that ever does;
* the snapshot row, written by that same confirmation: in its transaction (the
  two ``created_at`` values are one transaction timestamp), for its device, of
  the kind of label that workflow reads.

A row that merely *looks* withdrawn — empty facts and account on a label
capture — without every one of those is not a withdrawn confirmation. It is a
malformed or forged row, and it proves nothing.

**What this never does.** It never makes the erased capture a current pack:
the current pack is still only a device's newest event, and that event must be
a live confirmed capture (:func:`pack_context.is_confirmed_label_capture`).
It never restores or reads erased facts: the content proof is the *current*
capture's own facts against the snapshot's stored content, done by the
resolver. And it never reaches a model: it reads two ledger tables through
their ORM models and imports nothing that can call a provider.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.product.service import OUTCOME_LABEL

#: ``extraction.FEATURE`` — the food label transcription behind ``/scan/label/confirm``.
FOOD_LABEL_WORKFLOW = "product_label_transcribe"
#: ``care_extraction.FEATURE`` — behind ``/scan/skin-care/label/confirm``.
SKIN_CARE_LABEL_WORKFLOW = "skin_care_label_transcribe"

#: Every schema version each confirmation route accepts or has accepted.
#:
#: Append-only, and deliberately not the live ``CONFIRMABLE_SCHEMA_VERSIONS``:
#: retiring a schema from new confirmations does not make the confirmations it
#: produced any less genuine. Tests pin that each live set is contained here,
#: and pin this table itself, so adding a version is a conscious act. The
#: constants are restated rather than imported because both extraction modules
#: import the AI gateway client, and nothing on the resolver's path may.
CONFIRMATION_WORKFLOW_SCHEMAS: Mapping[str, frozenset[str]] = {
    FOOD_LABEL_WORKFLOW: frozenset({"scan-label.v1", "scan-label.v2"}),
    SKIN_CARE_LABEL_WORKFLOW: frozenset({"skin-care-label.v1"}),
}

#: ``care_capture.CATEGORY_FACT_KEY`` / ``SKIN_CARE_CATEGORY``: a skin-care
#: confirmation stores this pair; a food transcription schema cannot carry it.
CATEGORY_FACT_KEY = "product_category"
SKIN_CARE_CATEGORY = "skin_care"


@dataclass(frozen=True)
class RetainedRun:
    """The ledger row erasure keeps, and whether its output is still stored."""

    run: AIRun
    output_retained: bool


async def retained_runs(
    session: AsyncSession, run_ids: Iterable[uuid.UUID | None],
) -> dict[uuid.UUID, RetainedRun]:
    """The ledger rows for these runs, in one statement, or nothing to read."""
    ids = {run_id for run_id in run_ids if run_id is not None}
    if not ids:
        return {}
    output_retained = exists().where(AIRunOutput.ai_run_id == AIRun.id)
    rows = (await session.execute(
        select(AIRun, output_retained.label("output_retained")).where(AIRun.id.in_(ids))
    )).all()
    return {
        run.id: RetainedRun(run=run, output_retained=bool(retained))
        for run, retained in rows
    }


def _workflow_for(snapshot: LabelSnapshot) -> str:
    facts = snapshot.facts if isinstance(snapshot.facts, dict) else {}
    if facts.get(CATEGORY_FACT_KEY) == SKIN_CARE_CATEGORY:
        return SKIN_CARE_LABEL_WORKFLOW
    return FOOD_LABEL_WORKFLOW


def proves_withdrawn_confirmation(
    *, snapshot: LabelSnapshot, source: ScanEvent, retained: RetainedRun | None, barcode: str,
) -> bool:
    """Did ``source`` confirm ``snapshot`` before erasure withdrew its observation?

    Every count below is something the genuine confirmation and the genuine
    erasure both leave behind; each one missing is a reason to refuse. This
    speaks only to the historical source. The caller still requires the
    current capture's own facts to match the snapshot's content exactly, and
    both rows to have existed before that capture.
    """
    # The capture: a confirmed-label row for this product, withdrawn and severed.
    if (
        source.outcome != OUTCOME_LABEL
        or source.barcode != barcode
        or snapshot.barcode != barcode
        or snapshot.scan_event_id != source.id
        or source.label_facts is not None
        or source.account_id is not None
        # Erasure marks every row it withdraws non-attachable, and a signed-in
        # confirmation never was attachable; a row still open to a device
        # claim is anonymous history, not a withdrawn confirmation.
        or source.account_attachment_allowed is not False
        or source.ai_run_id is None
        or source.device_id is None
    ):
        return False
    # The ledger: a successful, validated transcription by a confirmation
    # workflow, severed from its account, whose output erasure removed.
    if retained is None:
        return False
    run = retained.run
    workflow = _workflow_for(snapshot)
    if (
        run.id != source.ai_run_id
        or run.account_id is not None
        or retained.output_retained
        or run.status != AI_STATUS_SUCCEEDED
        or run.validation_passed is not True
        or run.feature != workflow
        or run.schema_version not in CONFIRMATION_WORKFLOW_SCHEMAS[workflow]
        or run.created_at > source.created_at
    ):
        return False
    # The snapshot was written by that confirmation, not attached to it later.
    return (
        snapshot.created_at == source.created_at
        and snapshot.device_id == source.device_id
    )


__all__ = [
    "CATEGORY_FACT_KEY",
    "CONFIRMATION_WORKFLOW_SCHEMAS",
    "FOOD_LABEL_WORKFLOW",
    "SKIN_CARE_CATEGORY",
    "SKIN_CARE_LABEL_WORKFLOW",
    "RetainedRun",
    "proves_withdrawn_confirmation",
    "retained_runs",
]
