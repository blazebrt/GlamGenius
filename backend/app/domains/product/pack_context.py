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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.models import ScanEvent


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

    Fails closed at every step: no device, no scan, or a newest scan that is a
    plain barcode read all produce a context with no proven pack.

    ``label_facts`` is required to be a JSON object because a plain scan stores
    JSON ``null`` there rather than SQL ``NULL`` — a check for "not null" would
    read every plain scan as a capture.
    """
    event = await current_pack_event(session, barcode=barcode, device_id=device_id)
    if event is None:
        return CurrentPack()
    facts = event.label_facts
    if not isinstance(facts, dict) or not facts:
        # A plain scan: a new packet, and nothing proven about it until the
        # person photographs the label.
        return CurrentPack(scan_event=event)
    return CurrentPack(scan_event=event, label_facts=facts)


__all__ = ["CurrentPack", "current_pack", "current_pack_event"]
