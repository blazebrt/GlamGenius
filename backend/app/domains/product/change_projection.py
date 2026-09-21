"""Step 12A — deterministic change facts between two explicit label versions.

This layer answers one question:

    What did two confirmed physical-label observations say differently?

It reports a fact, not a verdict. Nothing here decides whether a change is
good or bad, whether an ingredient is safer, whether concentration moved,
whether a rule now applies, or whether anybody should be told. Those are later
authorities, and a field one could be smuggled into does not exist in this
module.

**Confirmed pack observations are the only source.** A ``LabelSnapshot`` is
written when somebody photographed a physical pack. Open Food Facts refreshing
its copy of a product is not that, and can never produce a version here — see
``docs/architecture/ODBL_DATA_WALL.md``.

**Both versions are explicit inputs.** The projection never chooses "latest",
never queries and never writes. Selection stays with the caller, so the answer
for a named pair of observations is reproducible — including after a reviewer
publishes a canonical identity these two labels never mentioned.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domains.product.formula_projection import (
    FormulaEntries,
    ParseStatus,
    formula_entries_from_label_snapshot,
)
from app.domains.product.models import LabelSnapshot
from app.domains.product.service import (
    canonical_label_facts,
    label_changed_fields,
    label_content_fingerprint,
)
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError


class LabelHistoryInvariantError(AppError):
    """Stored label history is in a shape no supported write path can create.

    Raised rather than repaired. A projection that quietly picked the reading
    that still made sense would be inventing the history it could not find, and
    the corruption would never be seen again.

    The reason is for the server log only. ``AppError.to_detail()`` serialises
    the code, the message, ``retryable`` and ``extra`` — and this carries no
    ``extra`` — so naming the broken invariant cannot reach a customer.
    """

    status_code = 503
    code = ErrorCode.FEATURE_UNAVAILABLE
    retryable = False
    MESSAGE = "This product history is not available right now."

    def __init__(self, reason: str) -> None:
        super().__init__(self.MESSAGE)
        self.reason = reason


class LabelChangeStatus(StrEnum):
    #: The first confirmed observation of this product's label. Not a change:
    #: nothing was added, nothing was removed, and nothing about the pack
    #: moved. We had simply never seen this one before.
    FIRST_OBSERVED_VERSION = "first_observed_version"
    #: A later confirmed observation differed from the one before it.
    CHANGED = "changed"
    #: Stored history for this product did not survive its own invariants, so
    #: no change fact can be stated. Distinct from having no history at all.
    UNAVAILABLE = "unavailable"


class FormulaChangeStatus(StrEnum):
    #: No predecessor to compare against.
    NOT_APPLICABLE = "not_applicable"
    #: The printed ingredient list reads the same on both packs.
    UNCHANGED = "unchanged"
    #: The same entries, the same number of times, printed in a different
    #: order. Printed order is order. It is not concentration, and a change in
    #: it is not evidence that anything about the product moved.
    REORDERED_ONLY = "reordered_only"
    #: At least one entry is present a different number of times. The two
    #: sides of the difference are reported as what each label held alone.
    INGREDIENT_SET_CHANGED = "ingredient_set_changed"
    #: At least one of the two lists could not be read as a list of entries, so
    #: no difference between them can be stated. No partial answer is given.
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class IngredientCount:
    """One printed name and how many occurrences of it the delta accounts for.

    Occurrences are counted rather than flattened to a set because a label that
    prints a name twice has printed it twice, and a pack that drops one of the
    two has changed.
    """

    name: str
    occurrences: int

    def as_payload(self) -> dict[str, Any]:
        return {"name": self.name, "occurrences": self.occurrences}


@dataclass(frozen=True)
class FormulaChange:
    """What the two printed lists each held that the other did not.

    The two sides are named for where an entry was *seen*, not for what
    somebody did. "Added" and "removed" would attribute an action to a
    manufacturer, and an action is not something two photographs can establish
    — a pack printed one list and a later pack printed another. Stating the
    observation also keeps this out of the way of the official-record notice
    that may be sitting on the same screen, where that vocabulary means
    something else entirely, and means it about safety.
    """

    status: FormulaChangeStatus
    previous_parse_status: ParseStatus | None = None
    current_parse_status: ParseStatus | None = None
    only_on_current_label: tuple[IngredientCount, ...] = ()
    only_on_previous_label: tuple[IngredientCount, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "previous_parse_status": (
                None if self.previous_parse_status is None
                else self.previous_parse_status.value
            ),
            "current_parse_status": (
                None if self.current_parse_status is None
                else self.current_parse_status.value
            ),
            "only_on_current_label": [
                row.as_payload() for row in self.only_on_current_label
            ],
            "only_on_previous_label": [
                row.as_payload() for row in self.only_on_previous_label
            ],
        }


@dataclass(frozen=True)
class LabelChangeProjection:
    status: LabelChangeStatus
    current_version: int | None
    previous_version: int | None
    changed_fields: tuple[str, ...]
    formula: FormulaChange

    def as_payload(self) -> dict[str, Any]:
        """The customer-facing shape.

        ``scope`` is part of it because this is the product's confirmed label
        history and not a statement about anybody's own packet. No snapshot id,
        device, account or invariant reason appears here, and none may be
        added: this envelope is served to an anonymous device.
        """
        return {
            "scope": "confirmed_label_history",
            "status": self.status.value,
            "current_version": self.current_version,
            "previous_version": self.previous_version,
            "changed_fields": list(self.changed_fields),
            "formula": self.formula.as_payload(),
        }


#: What a caller shows when stored history failed its invariants. It states
#: that the history is not available; it does not guess at one, and it does not
#: pretend the product has never been seen before.
UNAVAILABLE_PROJECTION = LabelChangeProjection(
    status=LabelChangeStatus.UNAVAILABLE,
    current_version=None,
    previous_version=None,
    changed_fields=(),
    formula=FormulaChange(status=FormulaChangeStatus.NOT_APPLICABLE),
)


def _readable_facts(snapshot: LabelSnapshot, *, role: str) -> dict[str, Any]:
    """The stored facts, or a governed refusal if they are not facts at all.

    ``facts`` is JSONB. The column can hold an array, a bare string or a number,
    and a row in any of those shapes was not written by a supported path. The
    canonicaliser is entitled to assume an object — weakening it to accept
    anything would make every caller's contract vaguer to cover one corrupt
    row — so the shape is checked here, at the boundary that already exists to
    refuse impossible history.

    This is the difference between a product page that goes quiet about its
    label history and one that returns a 500. The route can only fail soft over
    a failure it can recognise, and an ``AttributeError`` from three frames
    down is not one.

    Removing *this* check alone changes nothing observable: the guard around
    the fingerprint computation catches what a non-object would raise and
    reports the same refusal, so a mutation that deletes it survives. It is
    kept because it says at the boundary what the boundary is for, and because
    the two together are what make the refusal total — deleting both is a
    mutation that does not survive.
    """
    if not isinstance(snapshot.facts, Mapping):
        raise LabelHistoryInvariantError(f"{role}_label_facts_invalid")
    return snapshot.facts


def _assert_snapshot_integrity(snapshot: LabelSnapshot, *, role: str) -> None:
    """The stored version identity must still describe the stored content."""
    facts = _readable_facts(snapshot, role=role)
    try:
        computed = label_content_fingerprint(facts)
    except (AttributeError, TypeError, ValueError) as unusable:
        # An object at the top level whose insides are still not facts — a
        # nested member with mixed key types, say. Same conclusion, same
        # governed refusal. Anything outside these is a bug in the
        # canonicaliser and must not be disguised as corrupt data.
        raise LabelHistoryInvariantError(f"{role}_label_facts_invalid") from unusable
    if computed != snapshot.content_fingerprint:
        raise LabelHistoryInvariantError(f"{role}_label_fingerprint_mismatch")
    if snapshot.version_number < 1:
        raise LabelHistoryInvariantError(f"{role}_label_version_invalid")


def _validate_chain(current: LabelSnapshot, previous: LabelSnapshot | None) -> None:
    """Every shape the supported write path can produce, and no other.

    Each check names a way the two rows could disagree about what they are. A
    comparison run over rows that disagree would state a change nobody
    observed, so none of these is recoverable here.
    """
    _assert_snapshot_integrity(current, role="current")

    if current.version_number == 1:
        if current.previous_snapshot_id is not None or previous is not None:
            raise LabelHistoryInvariantError("first_label_version_has_predecessor")
        if list(current.changed_fields or []) != []:
            raise LabelHistoryInvariantError("first_label_version_has_changed_fields")
        return

    if previous is None:
        raise LabelHistoryInvariantError("label_predecessor_missing")
    _assert_snapshot_integrity(previous, role="previous")
    if current.previous_snapshot_id != previous.id:
        raise LabelHistoryInvariantError("label_predecessor_identity_mismatch")
    if current.barcode != previous.barcode:
        raise LabelHistoryInvariantError("label_predecessor_barcode_mismatch")
    if previous.version_number != current.version_number - 1:
        raise LabelHistoryInvariantError("label_version_chain_non_contiguous")
    # Recomputed from the immutable facts rather than compared between the two
    # stored fingerprints, so a pair of rows that both carry a wrong identity
    # cannot agree their way past this.
    if canonical_label_facts(previous.facts) == canonical_label_facts(current.facts):
        raise LabelHistoryInvariantError("adjacent_label_versions_have_same_content")
    if list(current.changed_fields or []) != label_changed_fields(
        previous.facts, current.facts
    ):
        raise LabelHistoryInvariantError("label_changed_fields_mismatch")


def _keyed_occurrences(
    entries: FormulaEntries,
) -> tuple[tuple[str, ...], Counter[str], dict[str, str]] | None:
    """Printed order, occurrence counts, and one printed name per key.

    ``None`` when any entry has no canonical key at all, which is the only
    honest answer: an entry that cannot be keyed cannot be said to be present
    in one list and absent from the other.

    The display name kept is the first printing of that key, so the delta is
    reported in the words the pack used rather than in a normalised form no
    customer has ever seen.
    """
    sequence: list[str] = []
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for entry in entries.entries:
        if entry.normalized_name is None:
            return None
        sequence.append(entry.normalized_name)
        counts[entry.normalized_name] += 1
        display.setdefault(entry.normalized_name, entry.raw_name)
    return tuple(sequence), counts, display


def _delta_payload(
    delta: Counter[str],
    *,
    sequence: tuple[str, ...],
    display: dict[str, str],
) -> tuple[IngredientCount, ...]:
    """The delta in printed order, one row per key, never a negative count."""
    emitted: set[str] = set()
    out: list[IngredientCount] = []
    for key in sequence:
        if key in emitted or delta.get(key, 0) <= 0:
            continue
        emitted.add(key)
        out.append(IngredientCount(name=display[key], occurrences=delta[key]))
    return tuple(out)


def _formula_change(current: LabelSnapshot, previous: LabelSnapshot) -> FormulaChange:
    """Compare the two printed ingredient lists, or say that we cannot.

    Sameness is decided on the canonical label text before parseability is
    considered. Two packs that printed the same unreadable list did not change,
    and saying they were not comparable would invite a client to tell somebody
    the label moved when it did not.
    """
    previous_entries = formula_entries_from_label_snapshot(previous)
    current_entries = formula_entries_from_label_snapshot(current)
    statuses = {
        "previous_parse_status": previous_entries.status,
        "current_parse_status": current_entries.status,
    }

    if "ingredients" not in (current.changed_fields or []):
        return FormulaChange(status=FormulaChangeStatus.UNCHANGED, **statuses)

    if (
        previous_entries.status is not ParseStatus.PARSED
        or current_entries.status is not ParseStatus.PARSED
    ):
        return FormulaChange(status=FormulaChangeStatus.NOT_COMPARABLE, **statuses)

    previous_keyed = _keyed_occurrences(previous_entries)
    current_keyed = _keyed_occurrences(current_entries)
    if previous_keyed is None or current_keyed is None:
        return FormulaChange(status=FormulaChangeStatus.NOT_COMPARABLE, **statuses)

    previous_sequence, previous_counts, previous_display = previous_keyed
    current_sequence, current_counts, current_display = current_keyed

    if previous_sequence == current_sequence:
        return FormulaChange(status=FormulaChangeStatus.UNCHANGED, **statuses)
    if previous_counts == current_counts:
        return FormulaChange(status=FormulaChangeStatus.REORDERED_ONLY, **statuses)
    return FormulaChange(
        status=FormulaChangeStatus.INGREDIENT_SET_CHANGED,
        only_on_current_label=_delta_payload(
            current_counts - previous_counts,
            sequence=current_sequence,
            display=current_display,
        ),
        only_on_previous_label=_delta_payload(
            previous_counts - current_counts,
            sequence=previous_sequence,
            display=previous_display,
        ),
        **statuses,
    )


def project_label_change(
    *,
    current: LabelSnapshot,
    previous: LabelSnapshot | None,
) -> LabelChangeProjection:
    """Compare exactly the two supplied immutable label observations.

    There is no session parameter, and that is the contract rather than an
    omission: once a caller has named the two observations, Step 12A cannot
    query, write, consult a clock or ask a model. The same pair yields the same
    answer on any machine, on any day.
    """
    _validate_chain(current, previous)

    if previous is None:
        return LabelChangeProjection(
            status=LabelChangeStatus.FIRST_OBSERVED_VERSION,
            current_version=current.version_number,
            previous_version=None,
            changed_fields=(),
            formula=FormulaChange(status=FormulaChangeStatus.NOT_APPLICABLE),
        )

    return LabelChangeProjection(
        status=LabelChangeStatus.CHANGED,
        current_version=current.version_number,
        previous_version=previous.version_number,
        changed_fields=tuple(current.changed_fields or ()),
        formula=_formula_change(current, previous),
    )


__all__ = [
    "UNAVAILABLE_PROJECTION",
    "FormulaChange",
    "FormulaChangeStatus",
    "IngredientCount",
    "LabelChangeProjection",
    "LabelChangeStatus",
    "LabelHistoryInvariantError",
    "project_label_change",
]
