"""Elemental percentage by weight, computed rather than remembered.

Every number this module produces is arithmetic on IUPAC standard atomic
weights. That matters: it is the one part of the absorption knowledge base that
needs no citation and carries no uncertainty, so it is kept strictly apart from
absorption figures, which come from studies and vary between people.

Hydration state is modelled explicitly because it is not a detail. Ferrous
sulfate is 36.8% iron anhydrous and 20.1% as the heptahydrate that Indian
labels usually mean — reading one for the other misstates the dose by most of
its value.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

# IUPAC 2021 standard atomic weights, the conventional single values.
ATOMIC_WEIGHTS: dict[str, Decimal] = {
    "H": Decimal("1.008"),
    "C": Decimal("12.011"),
    "N": Decimal("14.007"),
    "O": Decimal("15.999"),
    "Na": Decimal("22.990"),
    "Mg": Decimal("24.305"),
    "P": Decimal("30.974"),
    "S": Decimal("32.06"),
    "Cl": Decimal("35.45"),
    "Ca": Decimal("40.078"),
    "Fe": Decimal("55.845"),
    "Co": Decimal("58.933"),
    "Zn": Decimal("65.38"),
}

_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")


def molar_mass(formula: str) -> Decimal:
    """Molar mass of a flat formula such as ``MgO`` or ``FeSO4``.

    Deliberately simple: the formulas here are expanded by hand into flat
    element counts, so no bracket or hydrate parsing is needed and there is
    nothing clever to get wrong.
    """
    total = Decimal(0)
    matched = 0
    for element, count in _TOKEN.findall(formula):
        if element not in ATOMIC_WEIGHTS:
            raise KeyError(f"No atomic weight for {element!r} in {formula!r}")
        total += ATOMIC_WEIGHTS[element] * Decimal(count or 1)
        matched += len(element) + len(count)
    if matched != len(formula):
        raise ValueError(f"Could not read all of formula {formula!r}")
    return total


def elemental_percent(formula: str, element: str, atoms: int = 1) -> Decimal:
    """Percentage of ``formula``'s mass contributed by ``atoms`` of ``element``."""
    share = ATOMIC_WEIGHTS[element] * Decimal(atoms) / molar_mass(formula) * Decimal(100)
    return share.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
