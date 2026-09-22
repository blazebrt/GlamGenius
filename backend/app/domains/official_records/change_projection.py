"""Step 12B — what changed in one official record's own authority.

The question is narrow and deterministic: **between two immutable observations
of the same canonical official record, which official content fields does the
source itself state differently?**

That is all. This module does not interpret. It does not decide whether a
regulator strengthened or relaxed a position, whether a product became safe,
whether a recall ended, or whether anybody should do anything. FSSAI's words
are reported as FSSAI wrote them, or nothing is reported. No model runs here.

Two authorities, and the order matters
--------------------------------------
Step 12A learned this the expensive way, and Step 12B is built with the lesson
already applied:

1. **This module** decides whether the stored revision history is internally
   valid and what the two observations say differently. It runs first, and
   unconditionally.
2. :mod:`app.domains.official_records.change_evidence` decides, separately,
   whether a valid answer may be shown to a customer.

A correct internal result may be withheld. A corrupt one is never published,
and is never left unexamined merely because publication was going to be
withheld anyway.

What this module is not
-----------------------
No watch, nothing subscribed, no notification, no polling, no schedule. Step
12B derives a change on read from a ledger that already exists; making anybody
*aware* of it is Step 12C and is deliberately absent.

(The noun form of "subscribed" is a forbidden string in backend source, and a
CI gate greps ``backend/app/`` for it, so the verb is used here deliberately.
Do not "fix" the wording back.)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError

from .models import OfficialRecord, OfficialRecordRevision, OfficialSourceFetch
from .source import stable_content_hash


class RegulatoryHistoryInvariantError(AppError):
    """Stored official history is in a shape no supported import path creates.

    Raised rather than repaired. A projection that quietly picked whichever
    revision still parsed would publish a regulatory change nobody observed and
    hide the corruption permanently.

    The reason string is for the server log. ``AppError.to_detail()`` emits only
    the code, the message and ``retryable``, and this error carries no
    ``extra``, so no invariant name, record id or revision id can reach a
    customer.
    """

    status_code = 503
    code = ErrorCode.FEATURE_UNAVAILABLE
    retryable = False
    MESSAGE = "This official record history is not available right now."

    def __init__(self, reason: str) -> None:
        super().__init__(self.MESSAGE)
        self.reason = reason


class RegulatoryChangeStatus(StrEnum):
    #: The first observation this application holds of this official record.
    #: Not a change: we had simply never downloaded it before. It emphatically
    #: does not mean the regulator published it today.
    FIRST_OBSERVED_RECORD = "first_observed_record"
    #: A later observation stated at least one official content field
    #: differently from the observation before it.
    CHANGED = "changed"
    #: History exists and is not being characterised — either it failed its
    #: invariants, or no openable source backs the comparison. Deliberately
    #: distinct from "there is no official record", which is ``None``.
    UNAVAILABLE = "unavailable"


#: The official content fields, taken from the canonical parsed row in
#: :func:`app.domains.official_records.source.canonical_row` minus the identity
#: key. These are the words FSSAI published; everything else the schema holds
#: is *our* bookkeeping about when we looked.
#:
#: Deliberately excluded, and each for the same reason — it describes an
#: observation, not a regulatory position: ``created_at``, ``updated_at``,
#: ``last_seen_at``, ``last_seen_fetch_id``, ``source_file_sha256``,
#: ``adapter_version``, ``original_filename``, ``row_count``, ``observed_at``,
#: ``revision_number``, ``content_hash``, and the record's ``first_seen_at``.
#: A repeated identical download changes several of those and changes nothing
#: about what the regulator said.
MATERIAL_FIELDS: tuple[str, ...] = (
    "fbo_name",
    "brand_name",
    "product_name",
    "licence",
    "license_type",
    "batch_lot",
    "reason",
    "nature_of_recall",
    "recall_status",
    "recall_start_date",
    "recall_termination_date",
)

#: Every key a stored revision payload must carry: the material fields plus the
#: identity the record is keyed by.
_PAYLOAD_KEYS: frozenset[str] = frozenset((*MATERIAL_FIELDS, "external_record_id"))

#: Fields that establish which physical pack an official record is about. A
#: change in any of these means the later revision may no longer be attributable
#: to the pack the earlier one was about — which is a fact worth stating and
#: never a clearance.
IDENTITY_FIELDS: tuple[str, ...] = ("licence", "batch_lot")


@dataclass(frozen=True)
class FieldChange:
    """One official field, as it was and as it now is."""

    field: str
    previous_value: str | None
    current_value: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "previous_value": self.previous_value,
            "current_value": self.current_value,
        }


@dataclass(frozen=True)
class RegulatoryChangeProjection:
    status: RegulatoryChangeStatus
    current_revision: int | None
    previous_revision: int | None
    changed_fields: tuple[str, ...]
    #: The before/after pairs. Only ever populated for a published comparison;
    #: an unavailable projection carries none, because the values themselves
    #: are the claim.
    changes: tuple[FieldChange, ...]
    #: True when the later revision states a different licence or batch from
    #: the earlier one. The pack the record is about may have changed, which is
    #: never the same statement as "your pack is cleared".
    exact_identity_changed: bool

    def as_payload(self) -> dict[str, Any]:
        """The customer-facing shape.

        ``scope`` is part of it because this is the *official record's* own
        history, not a statement about the packet in anybody's hand. No record
        id, revision id, fetch id or invariant reason appears here, and none may
        be added: this envelope is served to an anonymous device.
        """
        return {
            "scope": "official_record_history",
            "status": self.status.value,
            "current_revision": self.current_revision,
            "previous_revision": self.previous_revision,
            "changed_fields": list(self.changed_fields),
            "changes": [change.as_payload() for change in self.changes],
            "exact_identity_changed": self.exact_identity_changed,
        }


#: The one governed way to say "there is history here and we are not stating
#: what it says". Reused by the integrity failure path and by the publication
#: boundary, so a customer cannot tell the two apart — and neither reveals
#: anything.
UNAVAILABLE_PROJECTION = RegulatoryChangeProjection(
    status=RegulatoryChangeStatus.UNAVAILABLE,
    current_revision=None,
    previous_revision=None,
    changed_fields=(),
    changes=(),
    exact_identity_changed=False,
)


def _payload_of(revision: OfficialRecordRevision, role: str) -> Mapping[str, Any]:
    payload = revision.payload
    if not isinstance(payload, Mapping):
        raise RegulatoryHistoryInvariantError(f"{role}_revision_payload_invalid")
    if set(payload) != _PAYLOAD_KEYS:
        raise RegulatoryHistoryInvariantError(f"{role}_revision_payload_schema_mismatch")
    return payload


def _check_revision(
    revision: OfficialRecordRevision,
    fetch: OfficialSourceFetch,
    record: OfficialRecord,
    role: str,
) -> Mapping[str, Any]:
    """Everything that must hold of one stored revision, before it is compared."""
    if revision.record_id != record.id:
        raise RegulatoryHistoryInvariantError(f"{role}_revision_belongs_to_another_record")
    if revision.revision_number < 1:
        raise RegulatoryHistoryInvariantError(f"{role}_revision_number_invalid")
    if revision.source_fetch_id != fetch.id:
        raise RegulatoryHistoryInvariantError(f"{role}_revision_fetch_mismatch")
    # A revision derived from a refused artifact would be content the register
    # never successfully served.
    if fetch.status != "succeeded":
        raise RegulatoryHistoryInvariantError(f"{role}_revision_fetch_unsuccessful")
    if fetch.authority != record.authority or fetch.record_type != record.record_type:
        raise RegulatoryHistoryInvariantError(f"{role}_revision_fetch_authority_mismatch")
    payload = _payload_of(revision, role)
    if payload.get("external_record_id") != record.external_record_id:
        raise RegulatoryHistoryInvariantError(f"{role}_revision_identity_mismatch")
    # The stored hash must still describe the stored payload under the one
    # canonical hashing rule the importer used. Checked rather than trusted:
    # this is the only thing standing between a silently edited payload and a
    # customer-facing claim about a regulator.
    if revision.content_hash != stable_content_hash(dict(payload)):
        raise RegulatoryHistoryInvariantError(f"{role}_revision_content_hash_mismatch")
    return payload


def _canonical_value(record: OfficialRecord, field: str) -> Any:
    value = getattr(record, field)
    return value.isoformat() if hasattr(value, "isoformat") else value


def project_regulatory_change(
    *,
    record: OfficialRecord,
    current: OfficialRecordRevision,
    previous: OfficialRecordRevision | None,
    current_fetch: OfficialSourceFetch,
    previous_fetch: OfficialSourceFetch | None,
) -> RegulatoryChangeProjection:
    """Compare exactly the two supplied immutable observations of one record.

    Pure. No session, no query, no clock, no model — and **no "latest" lookup**.
    Selection belongs to the caller, so the same pair yields the same answer on
    any machine on any day, and no vague identifier can quietly re-point the
    comparison at a different revision.

    ``previous`` must be exactly ``current``'s predecessor for the same record.
    It is never inferred from timestamps, from "the nearest earlier row", or
    from another record that happens to look similar.
    """
    current_payload = _check_revision(current, current_fetch, record, "current")

    # The canonical row is a projection of the latest revision. If they
    # disagree, one of them was edited outside the import path and we cannot
    # tell which is the register's word.
    if current.revision_number == record.latest_revision:
        for field in MATERIAL_FIELDS:
            if _canonical_value(record, field) != current_payload.get(field):
                raise RegulatoryHistoryInvariantError("canonical_record_disagrees_with_latest_revision")
    elif current.revision_number > record.latest_revision:
        raise RegulatoryHistoryInvariantError("current_revision_ahead_of_record")

    if current.revision_number == 1:
        if previous is not None:
            raise RegulatoryHistoryInvariantError("first_revision_has_predecessor")
        # Never seen before is not a change. We had not downloaded it; that is a
        # fact about us, not about the regulator.
        return RegulatoryChangeProjection(
            status=RegulatoryChangeStatus.FIRST_OBSERVED_RECORD,
            current_revision=current.revision_number,
            previous_revision=None,
            changed_fields=(),
            changes=(),
            exact_identity_changed=False,
        )

    if previous is None or previous_fetch is None:
        raise RegulatoryHistoryInvariantError("revision_predecessor_missing")
    if previous.revision_number != current.revision_number - 1:
        raise RegulatoryHistoryInvariantError("revision_sequence_non_contiguous")
    previous_payload = _check_revision(previous, previous_fetch, record, "previous")
    # Source time only ever moves forward, and the importer enforces that on the
    # way in. A pair that disagrees was not written by it.
    if previous.observed_at > current.observed_at:
        raise RegulatoryHistoryInvariantError("revision_observation_order_invalid")
    if previous.content_hash == current.content_hash:
        # The importer only writes a revision when content changed, so two
        # adjacent revisions with identical content cannot have come from it.
        raise RegulatoryHistoryInvariantError("adjacent_revisions_have_same_content")

    changes = tuple(
        FieldChange(
            field=field,
            previous_value=previous_payload.get(field),
            current_value=current_payload.get(field),
        )
        for field in MATERIAL_FIELDS
        if previous_payload.get(field) != current_payload.get(field)
    )
    if not changes:
        # Different hashes with no material difference means the payload carried
        # something outside the canonical field set — the schema check above
        # should already have refused it.
        raise RegulatoryHistoryInvariantError("revision_change_outside_material_fields")
    return RegulatoryChangeProjection(
        status=RegulatoryChangeStatus.CHANGED,
        current_revision=current.revision_number,
        previous_revision=previous.revision_number,
        changed_fields=tuple(change.field for change in changes),
        changes=changes,
        exact_identity_changed=any(change.field in IDENTITY_FIELDS for change in changes),
    )


__all__ = [
    "IDENTITY_FIELDS",
    "MATERIAL_FIELDS",
    "UNAVAILABLE_PROJECTION",
    "FieldChange",
    "RegulatoryChangeProjection",
    "RegulatoryChangeStatus",
    "RegulatoryHistoryInvariantError",
    "project_regulatory_change",
]
