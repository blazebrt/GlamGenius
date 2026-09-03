"""Whether the server can prove this device is holding this packet.

One resolver, in one place, because every layer that speaks about *this pack*
must agree on what "this pack" is. Three of them ask — the official-record
match, the shopper-observation batch signal, and the verdict screen's own
``physical_pack_context`` flag — and three separate answers would eventually
disagree in a way nobody noticed until a stranger's recall appeared on
somebody's screen.

**The claim is not the client's to make.** ``physical_pack_context=true`` is a
request, not evidence. A client sets it by default, an old build sets it always,
and a hostile one sets it deliberately; none of that tells the server anything
about what is in somebody's hand. So the request is treated as a *ceiling* — it
can only ever withhold authority, never grant it — and the authority itself is
established here, from rows this server wrote.

**The newest scan, and never one before it.** Authority comes from the newest
``ScanEvent`` this device made of this barcode, and only when that event is
itself a confirmed label capture. Reaching backwards to an older capture when
the newest event is a plain scan would be the same mistake in slower motion: a
plain scan of the same barcode means a different physical packet is in this
person's hand now, and its lot is unknown until they capture it. Searching
backwards would attach last month's lot to today's packet, and would keep
showing a shopper a signal about a pack they put back on the shelf.

**Server time, never the client's.** Ordering is by ``created_at`` and then by
``id`` to break a tie deterministically. ``scanned_at`` is a client-supplied
value that an offline queue may legitimately backdate and a hostile client may
simply choose, so it decides nothing here.

**The global latest snapshot is not this pack.** ``latest_label_snapshot``
answers "what is the newest thing anybody has published about this barcode",
which is the right question for the product science and the wrong one for
everything in this module: that row may be a stranger's photograph of a
stranger's packet, and Step 3 deduplicates identical label content into a
single snapshot owned by whoever captured it first, so owning it was never the
question either. What this module returns is the label content of *this
device's own newest capture*.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.models import ScanEvent
from app.domains.product.service import OUTCOME_LABEL


def is_confirmed_label_capture(event: ScanEvent | None) -> bool:
    """Is this row a genuine confirmed label capture?

    The one definition of confirmation provenance, on the three counts spelled
    out in :func:`current_pack`: the canonical label outcome, a non-empty
    ``label_facts`` object, and an ``ai_run_id`` proving the row came through
    the server-authorised confirmation route.

    Lives here, and is imported rather than restated, because every layer that
    treats a row as "a confirmed pack label" has to mean the same thing by it.
    A second, looser copy elsewhere is exactly how a forged ``found_local`` row
    carrying a hand-written ``mrp_text`` ends up being read as a capture.
    """
    if event is None:
        return False
    facts = event.label_facts
    return (
        event.outcome == OUTCOME_LABEL
        and isinstance(facts, dict)
        and bool(facts)
        and event.ai_run_id is not None
    )


def confirmed_label_capture_clauses() -> list[ColumnElement[bool]]:
    """The same test, as SQL. Literally the same — that is the whole point.

    ``label_facts`` is JSONB and a plain scan stores JSON ``null`` in it rather
    than SQL ``NULL``, so ``IS NOT NULL`` would read every plain scan as a
    capture. The type is asserted explicitly, and so is non-emptiness.

    **Why non-emptiness has to be here and not only in Python.** A caller that
    selects the newest matching row and *then* applies
    :func:`is_confirmed_label_capture` gets the wrong answer if the two
    disagree: an ineligible newer row consumes the ``LIMIT 1``, fails the
    row-level check, and hides an older row that was genuinely eligible. The
    query has to select the newest *eligible* capture, not the newest row that
    might turn out to be one.

    **Why this expression and not** ``jsonb_object_length``. That function
    rejects non-object JSON outright, and PostgreSQL does not promise to
    evaluate ``WHERE`` conjuncts left to right, so pairing it with a
    ``jsonb_typeof`` guard would be relying on an evaluation order the planner
    is free to ignore — an error waiting for the row that triggers it.
    Comparing against an explicitly typed empty object is total: ``<>`` on
    JSONB is defined for every JSON shape and raises on none of them. SQL
    ``NULL`` yields ``NULL`` and the row is excluded, which is also correct.

    Verified against ``null``, ``[]``, ``""``, ``{}``, a populated object and
    SQL ``NULL``: only the populated object qualifies.
    """
    return [
        ScanEvent.outcome == OUTCOME_LABEL,
        ScanEvent.ai_run_id.is_not(None),
        func.jsonb_typeof(ScanEvent.label_facts) == "object",
        ScanEvent.label_facts != cast({}, JSONB),
    ]


@dataclass(frozen=True)
class CurrentPack:
    """What this server can prove about the packet in this device's hand."""

    #: This device's newest scan of this barcode, whatever kind it was.
    scan_event: ScanEvent | None = None
    #: The confirmed label content of that scan — present only when the newest
    #: scan *is* a label capture. Never facts from an older capture, and never
    #: facts from another device's.
    label_facts: dict[str, Any] | None = None

    @property
    def has_scan(self) -> bool:
        """Has this device scanned this barcode at all?"""
        return self.scan_event is not None

    @property
    def is_proven(self) -> bool:
        """May a layer speak about *this packet* to this caller?

        The single question every caller should ask. True only when this
        device's newest scan of this barcode captured the label, which is the
        strongest statement the server is in a position to make about what
        somebody is holding.
        """
        return self.label_facts is not None


async def current_pack_event(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> ScanEvent | None:
    """The newest scan this device made of this barcode. Not the newest capture.

    A device that has never scanned this barcode — and a caller with no device
    at all — gets nothing rather than somebody else's row.
    """
    if device_id is None:
        return None
    return (await session.execute(
        select(ScanEvent)
        .where(ScanEvent.barcode == barcode, ScanEvent.device_id == device_id)
        .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
        .limit(1)
    )).scalars().first()


async def current_pack(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> CurrentPack:
    """Resolve the pack this device is holding, as far as the server can tell.

    Fails closed at every step. A pack is proven only when this device's newest
    scan of this barcode is a genuine confirmed label capture, which the server
    can attest to on three counts that must all hold:

    * ``outcome == OUTCOME_LABEL``. Only the ``/scan/label/confirm`` route writes
      that outcome, so a plain barcode read — or an event of any other kind —
      does not qualify however its ``label_facts`` happen to look. A row whose
      ``label_facts`` were set on a non-label event is not a capture; it is at
      best a mislabelled row and at worst a forged one, and either way it cannot
      speak for this packet.
    * ``label_facts`` is a non-empty object. A plain scan stores JSON ``null``
      here rather than SQL ``NULL``, so a bare "is not null" check would read
      every plain scan as a capture — the type is checked, not just presence.
    * ``ai_run_id`` is present. Every legitimate capture is written by the
      confirmation route from a validated ``AIRun`` (the only production writer
      of ``OUTCOME_LABEL``), so its absence means the row did not come through
      the server-authorised path. Requiring it proves provenance rather than
      mere resemblance.

    Any of the three missing yields a scan context with no proven pack, so the
    caller shows the product's science and withholds every physical-pack layer.
    """
    event = await current_pack_event(session, barcode=barcode, device_id=device_id)
    if event is None:
        return CurrentPack()
    if not is_confirmed_label_capture(event):
        # A plain scan, a non-label event, or a row without confirmation
        # provenance: a packet the server cannot vouch for.
        return CurrentPack(scan_event=event)
    return CurrentPack(scan_event=event, label_facts=event.label_facts)


__all__ = [
    "CurrentPack",
    "confirmed_label_capture_clauses",
    "current_pack",
    "current_pack_event",
    "is_confirmed_label_capture",
]
