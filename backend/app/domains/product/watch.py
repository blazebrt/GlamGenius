"""Step 12C — Product Watch: material notices for a pack the customer chose to watch.

The reaction layer, and deliberately nothing more. Three authorities already
know things, and Product Watch reinterprets none of them:

* **Step 12A** knows whether a confirmed label observation changed, and whether
  that comparison may be published.
* **Step 12B** knows whether an official record's own history changed, and
  whether that comparison may be published.
* **The official records matcher** knows which current FSSAI records apply to
  one exact pack — exact licence, meaningful exact lot, conflicts ruled out,
  ambiguity withheld.

Product Watch adds only what none of them can: *that the customer asked*, and
*what was already known when they did*. The division is exact::

    Existing authorities determine truth.
    Product Watch determines whether the customer asked to hear about it.
    Notification infrastructure determines whether and how it may be delivered.

A notification is itself a customer-facing claim, so it inherits every
publication boundary it rests on. When Step 12A or Step 12B withholds a
comparison from the product screen, Product Watch has nothing to say about it
either — it reads the same governed envelopes the screen reads, never the
internal fields behind them. No model is consulted anywhere in this module.

What a watch is anchored to
---------------------------
One exact pack, proven on this account's own device through the governed
current-pack authorities, never a barcode on its own and never the newest
snapshot someone else happened to publish. See :class:`ProductWatch` for why
the anchor is a capture *and* a label version rather than either alone.

Nothing here polls FSSAI, schedules anything or sends anything. The hourly
notification worker asks :func:`queue_material_notice` for at most one notice,
and the existing outbox decides whether it is delivered.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.official_records import service as official_records_service
from app.domains.official_records.source import SOURCE_URL as OFFICIAL_CURRENT_SOURCE_URL
from app.domains.planning import notification_strings as strings
from app.domains.planning import notifications
from app.domains.product import change_projection as label_projection
from app.domains.product import label_evidence, pack_context
from app.domains.product import service as product_service
from app.domains.product.models import LabelSnapshot, ProductWatch, ScanDevice, ScanEvent
from app.domains.product.personal_decision import (
    CurrentPackSnapshotUnresolved,
    resolve_current_pack_label_snapshot,
)
from app.shared.database.base import utcnow

logger = logging.getLogger(__name__)

WATCH_CONTRACT_VERSION = "step-12c-v1"
CURSOR_VERSION = 1

#: The three material notice classes. Nothing else is material to a watch.
KIND_RECORD_MATCH = "record_match"
KIND_REGULATORY_CHANGE = "regulatory_change"
KIND_LABEL_CHANGE = "label_change"
NOTICE_KINDS = (KIND_RECORD_MATCH, KIND_REGULATORY_CHANGE, KIND_LABEL_CHANGE)

#: Delivery order when more than one notice is waiting. This is a fixed order
#: among classes — which one takes the day's single notification slot — and is
#: explicitly not a severity score. Nothing here ranks one recall above another,
#: one status above another, or one ingredient above another.
NOTICE_PRIORITY: Mapping[str, int] = {
    KIND_RECORD_MATCH: 1,
    KIND_REGULATORY_CHANGE: 2,
    KIND_LABEL_CHANGE: 3,
}
_KIND_CODE: Mapping[str, str] = {
    KIND_RECORD_MATCH: "a",
    KIND_REGULATORY_CHANGE: "b",
    KIND_LABEL_CHANGE: "c",
}
_COPY: Mapping[str, tuple[str, str]] = {
    KIND_RECORD_MATCH: (strings.PRODUCT_WATCH_RECORD_MATCH_TITLE, strings.PRODUCT_WATCH_RECORD_MATCH_BODY),
    KIND_REGULATORY_CHANGE: (
        strings.PRODUCT_WATCH_REGULATORY_CHANGE_TITLE, strings.PRODUCT_WATCH_REGULATORY_CHANGE_BODY,
    ),
    KIND_LABEL_CHANGE: (strings.PRODUCT_WATCH_LABEL_CHANGE_TITLE, strings.PRODUCT_WATCH_LABEL_CHANGE_BODY),
}

DESTINATION = "/verdict"
SOURCE_KIND = "product_watch"

#: The one stable answer for "this device cannot prove an account-bound pack".
#: Deliberately a single code: telling a caller *which* of the checks failed
#: would tell them whose capture is on the device.
REASON_CONFIRMED_PACK_REQUIRED = "confirmed_pack_required"

#: Delivery outcomes that mean the customer's own preference decided the event.
#: An explicit opt-out consumes it, so turning notifications back on later does
#: not replay a backlog. Quiet hours and the daily cap are not in this set:
#: they are temporary, so the event stays eligible for a later local day.
_OPT_OUT_REASONS = frozenset({notifications.SUPPRESSED_DISABLED, notifications.SUPPRESSED_MODULE_OFF})


class WatchRefused(ValueError):
    """A governed refusal. ``code`` is stable; ``message`` is customer-safe."""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class WatchCursorInvalid(ValueError):
    """A stored cursor is not in the one shape this module writes."""


# ---------------------------------------------------------------------------
# The cursor: what was already known, and what has already been decided
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RecordBaseline:
    """One official record this watch already knows about.

    ``revisions`` holds the revision numbers Step 12B had already validated or
    that a notice has already been decided for. ``baseline_unknown`` means the
    record's history did not survive Step 12B's ledger checks when it became
    known, so no later revision can be told apart from one that already
    existed; such a record never produces a change notice for this anchor.
    """

    revisions: tuple[int, ...] = ()
    baseline_unknown: bool = False


@dataclass(frozen=True)
class WatchCursor:
    """Semantic state only. Timestamps never decide what has been seen."""

    records: Mapping[str, RecordBaseline] = field(default_factory=dict)
    label_baseline_version: int = 1
    label_notified_versions: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "v": CURSOR_VERSION,
            "records": {
                recall_id: {
                    "revisions": sorted(set(baseline.revisions)),
                    "baseline_unknown": baseline.baseline_unknown,
                }
                for recall_id, baseline in sorted(self.records.items())
            },
            "label": {
                "baseline_version": self.label_baseline_version,
                "notified_versions": sorted(set(self.label_notified_versions)),
            },
        }

    @classmethod
    def from_json(cls, raw: object) -> WatchCursor:
        """Strict: exactly the shape :meth:`to_json` writes, or refuse.

        A cursor is what stands between one event and a repeated notice, so a
        malformed one is never repaired into something plausible. The watch is
        skipped and the reason logged instead.
        """
        if not isinstance(raw, Mapping) or set(raw) != {"v", "records", "label"} or raw["v"] != CURSOR_VERSION:
            raise WatchCursorInvalid("cursor_shape")
        records_raw, label_raw = raw["records"], raw["label"]
        if not isinstance(records_raw, Mapping) or not isinstance(label_raw, Mapping):
            raise WatchCursorInvalid("cursor_shape")
        records: dict[str, RecordBaseline] = {}
        for recall_id, value in records_raw.items():
            if not isinstance(recall_id, str) or not recall_id or not isinstance(value, Mapping):
                raise WatchCursorInvalid("cursor_record")
            if set(value) != {"revisions", "baseline_unknown"}:
                raise WatchCursorInvalid("cursor_record")
            revisions, unknown = value["revisions"], value["baseline_unknown"]
            if not isinstance(unknown, bool) or not _positive_ints(revisions):
                raise WatchCursorInvalid("cursor_record")
            records[recall_id] = RecordBaseline(revisions=tuple(revisions), baseline_unknown=unknown)
        if set(label_raw) != {"baseline_version", "notified_versions"}:
            raise WatchCursorInvalid("cursor_label")
        baseline_version, notified = label_raw["baseline_version"], label_raw["notified_versions"]
        if not _positive_int(baseline_version) or not _positive_ints(notified):
            raise WatchCursorInvalid("cursor_label")
        return cls(
            records=records,
            label_baseline_version=baseline_version,
            label_notified_versions=tuple(notified),
        )


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_ints(value: object) -> bool:
    return isinstance(value, list) and all(_positive_int(item) for item in value)


# ---------------------------------------------------------------------------
# A notice candidate: structured, idempotent, and without prose
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WatchNotice:
    """One material notice this watch could give. No copy lives here.

    Two evaluations of the same stored state produce the same notice, and the
    same :attr:`notification_key`, so the outbox's dedup authority recognises
    it however many times it is proposed.
    """

    kind: str
    barcode: str
    recall_id: str | None = None
    revision: int | None = None
    label_version: int | None = None
    #: Stable ordering among notices of one class: the date the register
    #: states for a record, or the label version. Never the time we noticed.
    observation_key: str = ""

    @property
    def event_identity(self) -> str:
        return json.dumps(
            {
                "kind": self.kind, "barcode": self.barcode, "recall_id": self.recall_id,
                "revision": self.revision, "label_version": self.label_version,
            },
            sort_keys=True, separators=(",", ":"),
        )

    @property
    def notification_key(self) -> str:
        """``pw:<class>:<digest>`` — fits the outbox's 64-character key."""
        digest = hashlib.sha256(self.event_identity.encode("utf-8")).hexdigest()[:56]
        return f"pw:{_KIND_CODE[self.kind]}:{digest}"

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return (NOTICE_PRIORITY[self.kind], self.observation_key, self.barcode, self.event_identity)


@dataclass(frozen=True)
class OfficialState:
    """The official-records view of one exact pack, exactly as the screen gets it."""

    records: tuple[dict[str, Any], ...]
    heads: Mapping[str, int | None]


def current_record_source_is_openable(source_url: object) -> bool:
    """Whether a matched record carries the openable official page it is listed on.

    A current-state notice says "the register lists this", so the register's
    own page has to be openable from the destination screen. ``https`` on the
    one governed importer source, and nothing looser. This is about the
    *current* record; revision evidence is Step 12B's separate authority.
    """
    return isinstance(source_url, str) and source_url == OFFICIAL_CURRENT_SOURCE_URL


def record_governs_pack(record: object, heads: Mapping[str, int | None]) -> bool:
    """Whether one official-records row is a governed, publishable match for this pack.

    The one rule for "an official record matches this pack and may be said",
    shared by Product Watch's record-match notice and the Step 14 purchase
    guard so the two can never disagree about which records count:

    * the exact matcher resolved it to this pack (``match_state == "matched"``;
      ambiguity already published nothing);
    * its immutable Step 12B ledger validates (a positive head), because a
      record whose own history cannot be proven is not one to act on;
    * its current source is the one openable official page.

    Nothing about when it was last seen: a record omitted from a later export
    is not withdrawn, corrected or resolved, so omission never releases it.
    """
    if not isinstance(record, Mapping):
        return False
    recall_id = record.get("recall_id")
    if not isinstance(recall_id, str) or not recall_id:
        return False
    if record.get("match_state") != "matched":
        return False
    if not _positive_int(heads.get(recall_id)):
        return False
    return current_record_source_is_openable(record.get("source_url"))


def _published_revision(change: object) -> int | None:
    """The revision a *published* Step 12B change names, or ``None``.

    Only ``status == "changed"`` counts. ``unavailable`` means nothing
    customer-publishable exists — which is not "nothing changed" — and
    ``first_observed_record`` is never a change.
    """
    if not isinstance(change, Mapping) or change.get("status") != "changed":
        return None
    revision = change.get("current_revision")
    return revision if _positive_int(revision) else None


def notices_for(
    *,
    barcode: str,
    cursor: WatchCursor,
    official: OfficialState,
    label_pair: tuple[LabelSnapshot, LabelSnapshot | None] | None = None,
) -> list[WatchNotice]:
    """Every material notice this watch could give right now. Pure.

    ``label_pair`` is the Step 12A comparison to consider, supplied by the
    caller. Production supplies none — see :func:`queue_material_notice` — so
    the formula path is dormant until a background selection exists that does
    not weaken Step 12A's current-pack rule.
    """
    found: list[WatchNotice] = []
    for record in official.records:
        # Product Watch is proactive publication. A current matcher result is
        # not enough when this record's immutable Step 12B ledger is corrupt:
        # do not call attention to a record whose official history cannot be
        # validated, and do not manufacture a baseline for it.
        if not record_governs_pack(record, official.heads):
            continue
        recall_id = record["recall_id"]
        observation = str(record.get("recall_start_date") or "")
        known = cursor.records.get(recall_id)
        if known is None:
            # A record this pack did not match when the watch began, and that
            # it matches now. A current-state fact, not a claim about when the
            # regulator acted.
            found.append(WatchNotice(
                kind=KIND_RECORD_MATCH, barcode=barcode, recall_id=recall_id,
                observation_key=observation,
            ))
            continue
        if known.baseline_unknown:
            continue
        revision = _published_revision(record.get("regulatory_change"))
        if revision is None or revision in known.revisions:
            continue
        if known.revisions and revision <= max(known.revisions):
            continue
        found.append(WatchNotice(
            kind=KIND_REGULATORY_CHANGE, barcode=barcode, recall_id=recall_id,
            revision=revision, observation_key=observation,
        ))
    if label_pair is not None:
        label_notice = _label_change_notice(barcode=barcode, cursor=cursor, pair=label_pair)
        if label_notice is not None:
            found.append(label_notice)
    return found


def _label_change_notice(
    *, barcode: str, cursor: WatchCursor, pair: tuple[LabelSnapshot, LabelSnapshot | None],
) -> WatchNotice | None:
    """A Step 12A comparison, only as Step 12A would publish it."""
    current, previous = pair
    if current.barcode != barcode or (previous is not None and previous.barcode != barcode):
        return None
    try:
        projection = label_projection.project_label_change(current=current, previous=previous)
    except label_projection.LabelHistoryInvariantError as broken:
        logger.warning("product_watch_label_history_invalid reason=%s", broken.reason)
        return None
    # Both authorities, as the product screen asks them: integrity, then
    # publication. A first observation is never a change, whatever the gate.
    if not label_evidence.comparison_is_publishable(current=current, previous=previous):
        return None
    if projection.status is not label_projection.LabelChangeStatus.CHANGED:
        return None
    version = current.version_number
    if version <= cursor.label_baseline_version or version in cursor.label_notified_versions:
        return None
    return WatchNotice(
        kind=KIND_LABEL_CHANGE, barcode=barcode, label_version=version,
        observation_key=f"{version:010d}",
    )


def baseline_cursor(*, official: OfficialState, label_baseline_version: int) -> WatchCursor:
    """Everything already known becomes baseline. Nothing here is a notice."""
    return WatchCursor(
        records={
            record["recall_id"]: _record_baseline(official.heads.get(record["recall_id"]))
            for record in official.records
            if isinstance(record.get("recall_id"), str) and record.get("recall_id")
        },
        label_baseline_version=label_baseline_version,
    )


def _record_baseline(head: int | None) -> RecordBaseline:
    return RecordBaseline(revisions=(head,) if head is not None else (), baseline_unknown=head is None)


def merge_rebaseline_cursor(existing: WatchCursor, fresh: WatchCursor) -> WatchCursor:
    """Monotonically extend a valid cursor with facts observed during opt-out.

    Re-enabling delivery is not permission to forget an event this watch has
    already decided. New current facts become baseline, while every prior
    record identity, uncertainty marker and label decision remains durable.
    """
    records: dict[str, RecordBaseline] = dict(existing.records)
    for recall_id, incoming in fresh.records.items():
        prior = records.get(recall_id)
        if prior is None:
            records[recall_id] = incoming
            continue
        records[recall_id] = RecordBaseline(
            revisions=tuple(sorted({*prior.revisions, *incoming.revisions})),
            baseline_unknown=prior.baseline_unknown or incoming.baseline_unknown,
        )
    return WatchCursor(
        records=records,
        label_baseline_version=max(existing.label_baseline_version, fresh.label_baseline_version),
        label_notified_versions=tuple(sorted({
            *existing.label_notified_versions, *fresh.label_notified_versions,
        })),
    )


def cursor_after_decision(cursor: WatchCursor, notice: WatchNotice, official: OfficialState) -> WatchCursor:
    """The cursor once ``notice`` has a durable decision. Idempotent."""
    records = dict(cursor.records)
    if notice.kind == KIND_RECORD_MATCH and notice.recall_id is not None:
        if notice.recall_id not in records:
            records[notice.recall_id] = _record_baseline(official.heads.get(notice.recall_id))
        return WatchCursor(records, cursor.label_baseline_version, cursor.label_notified_versions)
    if notice.kind == KIND_REGULATORY_CHANGE and notice.recall_id is not None and notice.revision is not None:
        known = records.get(notice.recall_id, RecordBaseline())
        records[notice.recall_id] = RecordBaseline(
            revisions=tuple(sorted({*known.revisions, notice.revision})),
            baseline_unknown=known.baseline_unknown,
        )
        return WatchCursor(records, cursor.label_baseline_version, cursor.label_notified_versions)
    if notice.kind == KIND_LABEL_CHANGE and notice.label_version is not None:
        return WatchCursor(
            records, cursor.label_baseline_version,
            tuple(sorted({*cursor.label_notified_versions, notice.label_version})),
        )
    return cursor


# ---------------------------------------------------------------------------
# Anchors: the account-bound pack, now and later
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WatchablePack:
    """A pack this account's device proves, and the version it resolves to."""

    scan_event: ScanEvent
    snapshot: LabelSnapshot


@dataclass(frozen=True)
class AnchorContext:
    facts: dict[str, Any]
    snapshot: LabelSnapshot


async def resolve_watchable_pack(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, device: ScanDevice | None,
) -> WatchablePack | None:
    """The exact pack this account may watch from this device, or ``None``.

    Three existing authorities, and no new one:

    1. :func:`pack_context.current_pack` — this device's newest scan of this
       barcode, and only when it is a genuine confirmed label capture;
    2. that capture belongs to this account, on a device this account claimed
       — the Step 10A rule, because owning a device is not owning every
       capture on it;
    3. :func:`resolve_current_pack_label_snapshot` — the governed resolver for
       the label version that speaks for that capture.

    Another account's scan, an Open Food Facts record, a reference view and a
    plain barcode scan all produce ``None``.
    """
    if device is None or device.claimed_by_account_id != account_id:
        return None
    pack = await pack_context.current_pack(session, barcode=barcode, device_id=device.id)
    if not pack.is_proven or pack.scan_event is None:
        return None
    if pack.scan_event.account_id != account_id:
        return None
    try:
        snapshot = await resolve_current_pack_label_snapshot(session, pack=pack)
    except CurrentPackSnapshotUnresolved:
        # A proven pack with no label version behind it is a broken invariant,
        # not a customer state. A fixed event name and nothing else: the
        # exception text carries a snapshot id.
        logger.warning("product_watch_snapshot_unresolved")
        return None
    return WatchablePack(scan_event=pack.scan_event, snapshot=snapshot)


async def anchor_context(session: AsyncSession, watch: ProductWatch) -> AnchorContext | None:
    """Re-prove a stored anchor before it is used. Fails closed, never repairs."""
    # Re-read, never trusted from the identity map: the notification worker
    # shares one session across a whole batch, and a capture withdrawn by an
    # account deletion committed meanwhile must be seen as withdrawn.
    event = await session.get(ScanEvent, watch.anchor_scan_event_id, populate_existing=True)
    snapshot = await session.get(LabelSnapshot, watch.anchor_label_snapshot_id, populate_existing=True)
    reason = None
    if event is None or not pack_context.is_confirmed_label_capture(event):
        reason = "anchor_capture_invalid"
    elif event.account_id != watch.account_id or event.barcode != watch.barcode:
        reason = "anchor_capture_not_owned"
    elif (
        snapshot is None
        or snapshot.barcode != watch.barcode
        or snapshot.version_number != watch.anchor_label_version
    ):
        reason = "anchor_version_invalid"
    if reason is not None:
        logger.warning("product_watch_anchor_invalid reason=%s", reason)
        return None
    return AnchorContext(facts=dict(event.label_facts or {}), snapshot=snapshot)


async def official_state(session: AsyncSession, facts: dict[str, Any]) -> OfficialState:
    """The official records for exactly these pack facts, via the screen's own envelope.

    The same call the verdict route makes, so the same exact matcher, the same
    ambiguity withholding, the same OFF isolation and the same Step 12B gate
    apply. Nothing is re-derived here.
    """
    envelope = await official_records_service.official_records_envelope(session, facts)
    records = tuple(row for row in (envelope.get("records") or []) if isinstance(row, dict))
    heads = await official_records_service.validated_revision_heads(
        session, [row["recall_id"] for row in records if isinstance(row.get("recall_id"), str)],
    )
    return OfficialState(records=records, heads=heads)


async def _label_baseline_version(session: AsyncSession, *, barcode: str, anchor_version: int) -> int:
    """Every label version that already existed is baseline, not a change."""
    head = await product_service.latest_label_snapshot(session, barcode)
    return max(anchor_version, head.version_number if head is not None else anchor_version)


async def _fresh_baseline(session: AsyncSession, *, barcode: str, facts: dict[str, Any], anchor_version: int) -> WatchCursor:
    official = await official_state(session, facts)
    return baseline_cursor(
        official=official,
        label_baseline_version=await _label_baseline_version(session, barcode=barcode, anchor_version=anchor_version),
    )


# ---------------------------------------------------------------------------
# The customer's API
# ---------------------------------------------------------------------------
def watchable_barcode(barcode: str) -> bool:
    """One rule with the deep link, so every watch can open its own product."""
    return bool(notifications.VERDICT_BARCODE.fullmatch(barcode))


async def _watch_row(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, lock: bool,
) -> ProductWatch | None:
    statement = select(ProductWatch).where(
        ProductWatch.account_id == account_id, ProductWatch.barcode == barcode,
    )
    if lock:
        # A locked read must see the row as it is now, not as an earlier read
        # in this session left it in memory.
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(statement)).scalar_one_or_none()


async def _delivery_state(session: AsyncSession, account_id: uuid.UUID) -> dict[str, bool]:
    """Watching and being told are different consents; report both, conflate neither."""
    from app.domains.planning.models import NotificationPreference

    preference = (await session.execute(select(NotificationPreference).where(
        NotificationPreference.account_id == account_id,
    ))).scalar_one_or_none()
    if preference is None:
        return {"notifications_enabled": True, "product_watch_enabled": True, "native_push_enabled": False}
    return {
        "notifications_enabled": bool(preference.enabled),
        "product_watch_enabled": _topic_enabled(preference),
        "native_push_enabled": bool(preference.native_push_enabled),
    }


def _topic_enabled(preference: Any) -> bool:
    return bool((preference.topics or {}).get(
        notifications.PRODUCT_WATCH_TOPIC,
        notifications.DEFAULT_TOPIC_NOTIFICATIONS[notifications.PRODUCT_WATCH_TOPIC],
    ))


def listening(preference: Any) -> bool:
    """Whether this account currently wants to hear about watched products."""
    return bool(preference.enabled) and _topic_enabled(preference)


def _public_state(
    *, barcode: str, watch: ProductWatch | None, pack: WatchablePack | None, delivery: dict[str, bool],
) -> dict[str, Any]:
    """Public watch state. No watch, snapshot, capture, record or account id; no cursor."""
    watching = watch is not None and watch.active
    return {
        "contract_version": WATCH_CONTRACT_VERSION,
        "barcode": barcode,
        "watching": watching,
        "label_version": watch.anchor_label_version if watching else None,
        "started_at": watch.started_at.isoformat() if watching else None,
        "watchable": pack is not None,
        "anchorable_label_version": pack.snapshot.version_number if pack is not None else None,
        "watching_this_pack": bool(
            watching and pack is not None and watch.anchor_scan_event_id == pack.scan_event.id
        ),
        "reason": None if pack is not None else REASON_CONFIRMED_PACK_REQUIRED,
        "delivery": delivery,
    }


async def watch_state(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, device: ScanDevice | None,
) -> dict[str, Any]:
    watch = await _watch_row(session, account_id=account_id, barcode=barcode, lock=False)
    pack = (
        await resolve_watchable_pack(session, account_id=account_id, barcode=barcode, device=device)
        if watchable_barcode(barcode) else None
    )
    return _public_state(
        barcode=barcode, watch=watch, pack=pack, delivery=await _delivery_state(session, account_id),
    )


async def start_watch(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, label_version: int,
    device: ScanDevice | None, now: datetime | None = None,
) -> dict[str, Any]:
    """Watch, or re-anchor to, the exact pack this device proves. Idempotent.

    Creating a watch sends nothing. Whatever is already true of this pack —
    matching official records, their current revisions, existing label
    versions — becomes the baseline, and only what becomes available after it
    can ever produce a notice.
    """
    if not watchable_barcode(barcode):
        raise WatchRefused(
            "barcode_not_watchable", "This product cannot be watched.", status_code=422,
        )
    pack = await resolve_watchable_pack(session, account_id=account_id, barcode=barcode, device=device)
    if pack is None:
        raise WatchRefused(
            REASON_CONFIRMED_PACK_REQUIRED,
            "Confirm this pack's label on this device to watch it.",
            status_code=409,
        )
    if pack.snapshot.version_number != label_version:
        raise WatchRefused(
            "watch_context_changed",
            "This pack's details changed. Refresh and try again.",
            status_code=409,
        )
    now = now or utcnow()
    watch = await _watch_row(session, account_id=account_id, barcode=barcode, lock=True)
    if watch is None:
        cursor = await _fresh_baseline(
            session, barcode=barcode, facts=dict(pack.scan_event.label_facts or {}),
            anchor_version=pack.snapshot.version_number,
        )
        try:
            # The (account, barcode) constraint is the final authority. A
            # savepoint keeps a same-key concurrent insert from rolling back
            # the whole request before its winner can be read.
            async with session.begin_nested():
                watch = ProductWatch(
                    account_id=account_id, barcode=barcode,
                    anchor_scan_event_id=pack.scan_event.id,
                    anchor_label_snapshot_id=pack.snapshot.id,
                    anchor_label_version=pack.snapshot.version_number,
                    active=True, started_at=now, stopped_at=None,
                    notice_cursor=cursor.to_json(),
                )
                session.add(watch)
                await session.flush()
        except IntegrityError:
            watch = await _watch_row(session, account_id=account_id, barcode=barcode, lock=True)
            if watch is None:
                raise
    if not (
        watch.active
        and watch.anchor_scan_event_id == pack.scan_event.id
        and watch.anchor_label_snapshot_id == pack.snapshot.id
    ):
        # A different pack, or a stopped watch: an explicit customer action
        # re-anchors it, and the baseline is rebuilt for the new context so
        # nothing already known is replayed as news.
        cursor = await _fresh_baseline(
            session, barcode=barcode, facts=dict(pack.scan_event.label_facts or {}),
            anchor_version=pack.snapshot.version_number,
        )
        watch.anchor_scan_event_id = pack.scan_event.id
        watch.anchor_label_snapshot_id = pack.snapshot.id
        watch.anchor_label_version = pack.snapshot.version_number
        watch.active = True
        watch.stopped_at = None
        watch.started_at = now
        watch.notice_cursor = cursor.to_json()
        await session.flush()
    return _public_state(
        barcode=barcode, watch=watch, pack=pack, delivery=await _delivery_state(session, account_id),
    )


async def stop_watch(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Stop watching. Idempotent, and it erases no delivery history."""
    watch = await _watch_row(session, account_id=account_id, barcode=barcode, lock=True)
    if watch is not None and watch.active:
        watch.active = False
        watch.stopped_at = now or utcnow()
        await session.flush()
    return _public_state(
        barcode=barcode, watch=watch, pack=None, delivery=await _delivery_state(session, account_id),
    )


async def rebaseline_account(session: AsyncSession, account_id: uuid.UUID) -> int:
    """Everything true now becomes baseline for every active watch of this account.

    Called when the customer turns notifications or the Product Watch topic
    back on. While they had opted out, nothing was decided on their behalf and
    nothing was queued; this is what keeps turning it back on from delivering a
    backlog of facts that became available while they had asked not to hear.
    """
    watches = await _active_watches(session, account_id)
    for watch in watches:
        try:
            existing = WatchCursor.from_json(watch.notice_cursor)
        except WatchCursorInvalid as broken:
            # A preference toggle must never launder corrupt semantic history.
            logger.warning("product_watch_cursor_invalid reason=%s", broken)
            continue
        anchor = await anchor_context(session, watch)
        if anchor is None:
            continue
        fresh = await _fresh_baseline(
            session, barcode=watch.barcode, facts=anchor.facts, anchor_version=watch.anchor_label_version,
        )
        watch.notice_cursor = merge_rebaseline_cursor(existing, fresh).to_json()
    await session.flush()
    return len(watches)


async def _active_watches(session: AsyncSession, account_id: uuid.UUID) -> Sequence[ProductWatch]:
    """Every active watch, locked, in one deterministic order."""
    return (await session.execute(
        select(ProductWatch)
        .where(ProductWatch.account_id == account_id, ProductWatch.active.is_(True))
        .order_by(ProductWatch.barcode)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().all()


# ---------------------------------------------------------------------------
# The worker's door
# ---------------------------------------------------------------------------
def _consumes_event(delivery: Any) -> bool:
    """Whether a delivery decision settles the event for this watch's cursor.

    Queued, claimed, accepted or failed at the provider: decided — the outbox
    owns what happens next, and a provider failure is not retried by a second
    system. An explicit opt-out: decided. Quiet hours and the daily cap: not
    decided, so the event stays eligible on a later local day. The outbox's
    per-day dedup key bounds that to one decision row per event per day.
    """
    if delivery.status != notifications.STATUS_SUPPRESSED:
        return True
    return delivery.suppressed_reason in _OPT_OUT_REASONS


async def queue_material_notice(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> Any:
    """At most one material notice for this account in this cycle.

    Lock order is the notification preference first and the watches second,
    the same order as the preferences route, so the two can never deadlock.
    The watch rows stay locked from evaluation until the caller commits the
    decision: a watch stopped or re-anchored concurrently is either seen before
    evaluation or waits until the decision is durable — never in between.

    The cursor moves in the same transaction as the outbox row it records, so
    a rollback loses both together and a commit keeps both together.
    """
    preference = await notifications.preferences_for(session, account_id, timezone_name, lock=True)
    # The worker read this row before taking the lock, and a locked SELECT of
    # an object already in the session does not overwrite what is in memory.
    # With the lock now held, re-read it: an opt-out committed in between must
    # win, and nobody can change it again until this decision is committed.
    await session.refresh(preference)
    if not listening(preference):
        # An explicit opt-out: nothing is evaluated, nothing is queued, and the
        # day's single slot is left to the ordinary reminders. Re-enabling
        # re-baselines the watches (:func:`rebaseline_account`).
        return None
    candidates: list[tuple[WatchNotice, ProductWatch, WatchCursor, OfficialState]] = []
    for watch in await _active_watches(session, account_id):
        try:
            cursor = WatchCursor.from_json(watch.notice_cursor)
        except WatchCursorInvalid as broken:
            logger.warning("product_watch_cursor_invalid reason=%s", broken)
            continue
        anchor = await anchor_context(session, watch)
        if anchor is None:
            continue
        official = await official_state(session, anchor.facts)
        # The formula path is dormant in production: no label pair is
        # supplied. Choosing one in the background would mean picking a label
        # observation this account's device did not make and presenting it as
        # news about their pack, which Step 12A's current-pack rule forbids.
        for notice in notices_for(barcode=watch.barcode, cursor=cursor, official=official):
            candidates.append((notice, watch, cursor, official))
    if not candidates:
        return None
    notice, watch, cursor, official = min(candidates, key=lambda item: item[0].sort_key)
    title, body = _COPY[notice.kind]
    delivery = await notifications.queue(
        session, account_id=account_id, plan_date=plan_date,
        notification_key=notice.notification_key, title=title, body=body,
        module=notifications.PRODUCT_WATCH_TOPIC, topic=notifications.PRODUCT_WATCH_TOPIC,
        timezone_name=timezone_name, moment=moment,
        deep_link=DESTINATION, destination_params={"barcode": notice.barcode},
        source_kind=SOURCE_KIND, source_id=notice.barcode, scheduled_for=moment,
    )
    if _consumes_event(delivery):
        watch.notice_cursor = cursor_after_decision(cursor, notice, official).to_json()
        if delivery.status != notifications.STATUS_SUPPRESSED:
            watch.last_notified_at = utcnow()
        await session.flush()
    return delivery


__all__ = [
    "CURSOR_VERSION",
    "DESTINATION",
    "KIND_LABEL_CHANGE",
    "KIND_RECORD_MATCH",
    "KIND_REGULATORY_CHANGE",
    "NOTICE_PRIORITY",
    "REASON_CONFIRMED_PACK_REQUIRED",
    "WATCH_CONTRACT_VERSION",
    "OfficialState",
    "RecordBaseline",
    "WatchCursor",
    "WatchCursorInvalid",
    "WatchNotice",
    "WatchRefused",
    "anchor_context",
    "baseline_cursor",
    "current_record_source_is_openable",
    "record_governs_pack",
    "cursor_after_decision",
    "listening",
    "merge_rebaseline_cursor",
    "notices_for",
    "official_state",
    "queue_material_notice",
    "rebaseline_account",
    "resolve_watchable_pack",
    "start_watch",
    "stop_watch",
    "watch_state",
    "watchable_barcode",
]
