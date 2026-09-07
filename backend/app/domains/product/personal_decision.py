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
future observations rewriting past provenance.

So a fallback candidate has to earn the whole chain, not resemble part of it:

```
current physical capture
    ↓ exact same canonical content
eligible historical LabelSnapshot   (barcode, fingerprint, canonical facts)
    ↓ existed already                (snapshot created_at <= this capture)
its source ScanEvent                 (same barcode, same canonical facts)
    ↓ genuine confirmed capture      (pack_context.is_confirmed_label_capture)
    ↓ happened already               ((created_at, id) <= this capture's)
```

Two of those links are easy to miss and were. **The row itself must have
existed**: checking only the *source event's* time lets a snapshot inserted
later point at an older capture and pass as historical. And **the source must
be a real confirmed capture**: the snapshot names an event id and nothing
stopped that event being a plain scan, a forged ``label_captured`` row with no
``ai_run_id``, or a capture of a different product entirely.

Ordering is the repository's server ordering — ``created_at`` then ``id`` —
never ``scanned_at``, which a client chooses, and never version number alone.

**The newest *eligible* candidate, not the newest candidate.** Ordering by
version and validating the winner afterwards is a different rule: a forged
high-version row would take the ORDER BY, fail validation, and hide the
legitimate older version behind it. Eligibility is part of the choice.

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

from app.domains.product import pack_context
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


def _eligible_source(source: ScanEvent, *, barcode: str, facts: dict) -> bool:
    """Is the capture this candidate names a genuine source for these facts?

    A snapshot is only as trustworthy as the capture behind it, and the row is
    free to name any event at all. Three things must hold, and the first is
    borrowed rather than restated:

    * :func:`pack_context.is_confirmed_label_capture` — the one definition of
      confirmation provenance in this codebase. A plain scan, an event with
      empty or malformed ``label_facts``, and a ``label_captured`` row with no
      ``ai_run_id`` all fail it. Writing a looser copy here is precisely how a
      forged row ends up being read as a capture.
    * The same barcode. Provenance never crosses products.
    * The same *canonical* content. Raw JSON equality would reject a capture
      whose whitespace differs while carrying identical semantic content — the
      same normalisation the fingerprint is built from, so the comparison
      agrees with the fingerprint instead of contradicting it.
    """
    if not pack_context.is_confirmed_label_capture(source):
        return False
    if source.barcode != barcode:
        return False
    return canonical_label_facts(source.label_facts) == canonical_label_facts(facts)


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
    # already held it is this pack's version — provided the whole chain behind
    # it is genuine and it existed by the time this pack was confirmed.
    #
    # One query for the bounded candidate set, then deterministic filtering in
    # memory. Selecting the newest row and *then* validating it would be a
    # different rule: a forged high-version snapshot would win the ORDER BY,
    # fail validation, and hide the legitimate older version behind it. The
    # newest *eligible* candidate is the answer, so eligibility has to be part
    # of the choice rather than a check applied after it.
    source = aliased(ScanEvent)
    rows = (await session.execute(
        select(LabelSnapshot, source)
        .join(source, LabelSnapshot.scan_event_id == source.id)
        .where(
            LabelSnapshot.barcode == event.barcode,
            LabelSnapshot.content_fingerprint == fingerprint,
            # The row itself must have existed. A snapshot inserted later that
            # merely *points* at an older capture is a backfill, and letting it
            # answer would be exactly the rewriting of history this guards.
            LabelSnapshot.created_at <= event.created_at,
            source.barcode == event.barcode,
            tuple_(source.created_at, source.id) <= tuple_(event.created_at, event.id),
        )
        .order_by(LabelSnapshot.version_number.desc())
    )).all()

    for candidate, source_event in rows:
        if not _eligible_source(source_event, barcode=event.barcode, facts=facts):
            continue
        if not _matches(candidate, barcode=event.barcode, facts=facts, fingerprint=fingerprint):
            continue
        return candidate
    raise CurrentPackSnapshotUnresolved(
        "no legitimate label version existed for this pack's confirmed content "
        "at the time it was confirmed"
    )


__all__ = ["CurrentPackSnapshotUnresolved", "resolve_current_pack_label_snapshot"]
