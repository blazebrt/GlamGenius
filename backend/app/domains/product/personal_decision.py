"""Finding the semantic label version behind the pack in somebody's hand.

Step 8B needs a real :class:`LabelSnapshot` — a versioned, fingerprinted row —
not the loose ``label_facts`` copied onto a scan event. Usually the current
pack's own confirmation created one and the two are the same thing. Sometimes
they are not, and the gap is where this module earns its place.

**Why the current event may own no snapshot.** Step 3 deduplicates identical
label content: confirming the same formula for the same barcode a second time
returns the snapshot that already holds that content rather than writing a
near-identical row. The second confirmation is a real, proven physical capture
with no snapshot pointing back at it.

**Why "the latest snapshot with this content" is the wrong repair.** Snapshots
are global to a barcode, and anybody can add one. If a stranger photographs the
same formula next week, a "latest matching" lookup would silently re-attribute
this person's capture to a row that did not exist when they confirmed it —
future observations rewriting past provenance. So a candidate is eligible only
if the capture that created it happened *at or before* the current pack's own
capture, ordered exactly as ``pack_context`` orders scans: server
``created_at``, then ``id`` to break a tie. Among those, the latest semantic
version wins.

**And why the global newest is never consulted at all.** ``latest_label_snapshot``
answers "what is the newest thing anybody published about this barcode", which
is the right question for product science and the wrong one here. This module
does not call it, and a static test says so.

**Failure is failure.** If the server has already attested that the pack is
proven but no legitimate snapshot can be resolved, that is a broken invariant,
not a customer state. It raises. Downgrading it to "you haven't confirmed a
pack" would tell the person something false and quietly hide a real defect.
"""

from __future__ import annotations

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.product.pack_context import CurrentPack
from app.domains.product.service import canonical_label_facts, label_content_fingerprint


class CurrentPackSnapshotUnresolved(RuntimeError):
    """A proven pack whose semantic label version cannot be identified.

    Deliberately not a customer-facing state. Reaching this means a confirmed
    capture exists without the label version it must have written, or with one
    that no longer matches it — a data-integrity fault the API answers with a
    fail-closed unavailability rather than a decision.
    """


def _matches(snapshot: LabelSnapshot, *, barcode: str, facts: dict, fingerprint: str) -> bool:
    """Is this row genuinely the semantic version of these confirmed facts?

    All three, because any one alone can agree by accident or by tampering: a
    fingerprint collision is not the worry, a hand-edited row is.
    """
    return (
        snapshot.barcode == barcode
        and snapshot.content_fingerprint == fingerprint
        and canonical_label_facts(snapshot.facts) == canonical_label_facts(facts)
    )


async def resolve_current_pack_label_snapshot(
    session: AsyncSession, *, pack: CurrentPack,
) -> LabelSnapshot:
    """The label version that speaks for this device's current pack.

    Raises:
        CurrentPackSnapshotUnresolved: when the pack is not proven, or when no
            snapshot that legitimately belongs to it can be found.
    """
    if not pack.is_proven or pack.scan_event is None or pack.label_facts is None:
        raise CurrentPackSnapshotUnresolved(
            "the current pack is not a confirmed capture, so it has no label version"
        )
    event = pack.scan_event
    facts = pack.label_facts
    fingerprint = label_content_fingerprint(facts)

    own = (await session.execute(
        select(LabelSnapshot).where(LabelSnapshot.scan_event_id == event.id)
    )).scalar_one_or_none()
    if own is not None:
        if not _matches(own, barcode=event.barcode, facts=facts, fingerprint=fingerprint):
            raise CurrentPackSnapshotUnresolved(
                f"label snapshot {own.id} does not match the capture that created it"
            )
        return own

    # No row of its own: the content was already stored, so the version that
    # already held it is this pack's version — provided it existed by the time
    # this pack was confirmed.
    source = aliased(ScanEvent)
    candidate = (await session.execute(
        select(LabelSnapshot)
        .join(source, LabelSnapshot.scan_event_id == source.id)
        .where(
            LabelSnapshot.barcode == event.barcode,
            LabelSnapshot.content_fingerprint == fingerprint,
            tuple_(source.created_at, source.id) <= tuple_(event.created_at, event.id),
        )
        .order_by(LabelSnapshot.version_number.desc())
        .limit(1)
    )).scalars().first()
    if candidate is None:
        raise CurrentPackSnapshotUnresolved(
            "no label version existed for this pack's confirmed content at the time it was confirmed"
        )
    if not _matches(candidate, barcode=event.barcode, facts=facts, fingerprint=fingerprint):
        raise CurrentPackSnapshotUnresolved(
            f"label snapshot {candidate.id} does not hold this pack's confirmed content"
        )
    return candidate


__all__ = ["CurrentPackSnapshotUnresolved", "resolve_current_pack_label_snapshot"]
