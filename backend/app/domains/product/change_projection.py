"""Step 12A — deterministic change facts between two explicit label versions.

This layer answers one question:

    What did two confirmed physical-label observations say differently?

It does not decide why a manufacturer changed a pack, whether the change is
good or bad, whether an ingredient is safer, whether concentration changed, or
whether any regulation requires an action. Those are later authorities.

Both snapshots are explicit inputs. The projection never chooses "latest" and
never writes. That keeps version selection outside the comparison and makes the
result reproducible for the exact two observations a caller names.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domains.formulas.parser import FormulaParse, ParseStatus, parse_formula
from app.domains.product.models import LabelSnapshot
from app.domains.product.service import label_changed_fields, label_content_fingerprint
from app.domains.substances.normalization import normalize_name
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError


class LabelHistoryInvariantError(AppError):
    """Stored label history is in a shape no supported write path can create."""

    status_code = 503
    code = ErrorCode.FEATURE_UNAVAILABLE
    retryable = False
    MESSAGE = "This product history is not available right now."

    def __init__(self, reason: str) -> None:
        super().__init__(self.MESSAGE)
        self.reason = reason  # log-only; AppError serialises only extra.


class LabelChangeStatus(StrEnum):
    FIRST_OBSERVED_VERSION = "first_observed_version"
    CHANGED = "changed"


class FormulaChangeStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNCHANGED = "unchanged"
    REORDERED_ONLY = "reordered_only"
    INGREDIENT_SET_CHANGED = "ingredient_set_changed"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class IngredientCount:
    name: str
    occurrences: int

    def as_payload(self) -> dict[str, Any]:
        return {"name": self.name, "occurrences": self.occurrences}


@dataclass(frozen=True)
class FormulaChange:
    status: FormulaChangeStatus
    previous_parse_status: ParseStatus | None = None
    current_parse_status: ParseStatus | None = None
    added: tuple[IngredientCount, ...] = ()
    removed: tuple[IngredientCount, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "previous_parse_status": (
                self.previous_parse_status.value if self.previous_parse_status else None
            ),
            "current_parse_status": (
                self.current_parse_status.value if self.current_parse_status else None
            ),
            "added": [row.as_payload() for row in self.added],
            "removed": [row.as_payload() for row in self.removed],
        }


@dataclass(frozen=True)
class LabelChangeProjection:
    status: LabelChangeStatus
    current_version: int
    previous_version: int | None
    changed_fields: tuple[str, ...]
    formula: FormulaChange

    def as_payload(self) -> dict[str, Any]:
        return {
            "scope": "confirmed_label_history",
            "status": self.status.value,
            "current_version": self.current_version,
            "previous_version": self.previous_version,
            "changed_fields": list(self.changed_fields),
            "formula": self.formula.as_payload(),
        }


def _assert_snapshot_integrity(snapshot: LabelSnapshot, *, role: str) -> None:
    if label_content_fingerprint(snapshot.facts) != snapshot.content_fingerprint:
        raise LabelHistoryInvariantError(f"{role}_label_fingerprint_mismatch")
    if snapshot.version_number < 1:
        raise LabelHistoryInvariantError(f"{role}_label_version_invalid")


def _validate_chain(current: LabelSnapshot, previous: LabelSnapshot | None) -> None:
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
    if previous.content_fingerprint == current.content_fingerprint:
        raise LabelHistoryInvariantError("adjacent_label_versions_have_same_content")

    expected_fields = label_changed_fields(previous.facts, current.facts)
    if list(current.changed_fields or []) != expected_fields:
        raise LabelHistoryInvariantError("label_changed_fields_mismatch")


def _formula_parse(snapshot: LabelSnapshot) -> FormulaParse:
    ingredients_text: object = (
        snapshot.facts.get("ingredients_text")
        if isinstance(snapshot.facts, Mapping)
        else None
    )
    return parse_formula(ingredients_text)


def _entry_counts(parse: FormulaParse) -> tuple[list[str], Counter[str], dict[str, str]] | None:
    sequence: list[str] = []
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for row in parse.tokens:
        normalized = normalize_name(row.raw_name)
        if normalized is None:
            return None
        sequence.append(normalized)
        counts[normalized] += 1
        display.setdefault(normalized, row.raw_name)
    return sequence, counts, display


def _count_payload(
    delta: Counter[str],
    *,
    sequence: list[str],
    display: dict[str, str],
) -> tuple[IngredientCount, ...]:
    emitted: set[str] = set()
    out: list[IngredientCount] = []
    for key in sequence:
        if key in emitted or delta.get(key, 0) <= 0:
            continue
        emitted.add(key)
        out.append(IngredientCount(name=display[key], occurrences=delta[key]))
    return tuple(out)


def _formula_change(
    current: LabelSnapshot,
    previous: LabelSnapshot,
) -> FormulaChange:
    if "ingredients" not in (current.changed_fields or []):
        return FormulaChange(status=FormulaChangeStatus.UNCHANGED)

    old_formula = _formula_parse(previous)
    new_formula = _formula_parse(current)

    if old_formula.status is not ParseStatus.PARSED or new_formula.status is not ParseStatus.PARSED:
        return FormulaChange(
            status=FormulaChangeStatus.NOT_COMPARABLE,
            previous_parse_status=old_formula.status,
            current_parse_status=new_formula.status,
        )

    old_counts = _entry_counts(old_formula)
    new_counts = _entry_counts(new_formula)
    if old_counts is None or new_counts is None:
        return FormulaChange(
            status=FormulaChangeStatus.NOT_COMPARABLE,
            previous_parse_status=old_formula.status,
            current_parse_status=new_formula.status,
        )

    old_sequence, old_counter, old_display = old_counts
    new_sequence, new_counter, new_display = new_counts

    if old_sequence == new_sequence:
        return FormulaChange(
            status=FormulaChangeStatus.UNCHANGED,
            previous_parse_status=old_formula.status,
            current_parse_status=new_formula.status,
        )
    if old_counter == new_counter:
        return FormulaChange(
            status=FormulaChangeStatus.REORDERED_ONLY,
            previous_parse_status=old_formula.status,
            current_parse_status=new_formula.status,
        )

    added_counter = new_counter - old_counter
    removed_counter = old_counter - new_counter
    return FormulaChange(
        status=FormulaChangeStatus.INGREDIENT_SET_CHANGED,
        previous_parse_status=old_formula.status,
        current_parse_status=new_formula.status,
        added=_count_payload(
            added_counter, sequence=new_sequence, display=new_display,
        ),
        removed=_count_payload(
            removed_counter, sequence=old_sequence, display=old_display,
        ),
    )

def project_label_change(
    *,
    current: LabelSnapshot,
    previous: LabelSnapshot | None,
) -> LabelChangeProjection:
    """Compare exactly the two supplied immutable label observations.

    No session parameter is accepted: Step 12A cannot query, write, consult a
    clock, or ask a model after the caller has selected the two observations.
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
    "FormulaChange",
    "FormulaChangeStatus",
    "IngredientCount",
    "LabelChangeProjection",
    "LabelChangeStatus",
    "LabelHistoryInvariantError",
    "project_label_change",
]
