"""Routines-owned projection of an authoritative Care routine plan.

The compiler consumes this module only.  The Care service adapts its immutable
plan into these small directives at the domain boundary, keeping the pure
compiler independent from Care dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoutineSlotDirective:
    slot: str
    category: str
    required: bool
    active: bool
    selected_item_id: str | None
    is_gap: bool = False


@dataclass(frozen=True, slots=True)
class RoutineSelectionPlan:
    plan_version: str
    plan_fingerprint: str
    effort: str
    effort_source: str
    directives: tuple[RoutineSlotDirective, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for directive in self.directives:
            if directive.slot in seen:
                raise ValueError(f"Duplicate routine selection directive for slot {directive.slot!r}")
            seen.add(directive.slot)
            if not directive.active and directive.required:
                raise ValueError(f"Required routine slot {directive.slot!r} cannot be inactive")
            if not directive.active and directive.selected_item_id is not None:
                raise ValueError(f"Inactive routine slot {directive.slot!r} cannot select an item")
            if directive.active and not directive.required and directive.selected_item_id is None:
                raise ValueError(f"Active optional routine slot {directive.slot!r} requires a selected item")
            if directive.is_gap and directive.selected_item_id is not None:
                raise ValueError(f"Routine slot {directive.slot!r} cannot be both a gap and selected")

    @property
    def by_slot(self) -> dict[str, RoutineSlotDirective]:
        return {directive.slot: directive for directive in self.directives}


__all__ = ["RoutineSelectionPlan", "RoutineSlotDirective"]
