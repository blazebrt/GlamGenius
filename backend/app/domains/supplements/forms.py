"""Exact compound-form identity for a printed supplement label name.

Step 13. Two identities are kept apart on purpose:

* **Nutrient identity** — ``canonical_component_key`` on a label fact, set at
  write time by ``engine.component_identity``. "Magnesium oxide", "Magnesium
  citrate" and plain "Magnesium" all share ``magnesium``. It drives overlap and
  nothing else.
* **Form identity** — which exact compound the pack prints. Resolved here, from
  the printed name alone, by exact lookup in a small explicit table. It drives
  form-specific knowledge and package chemistry.

A shared nutrient key is never evidence of a shared form. Nothing here guesses:
there is no fuzzy matching, no substring search, no "most common form", no
model. A printed "Magnesium" states no form, and gets none. A printed name this
table does not list is "not enough information", whatever it looks like.

Why a separate table and not the knowledge base's aliases
---------------------------------------------------------
``knowledge.Compound.aliases`` were written for nutrient-level overlap, and the
knowledge file says itself that nothing in it has been checked. Several of
those aliases name a *different* molecular form from the entry they sit under —
"magnesium chloride hexahydrate" and "epsom salt" sit under anhydrous formulas,
"calcium citrate malate" under calcium citrate, and bare "vitamin c", "b12" and
"curcumin" under one specific form each. Reading them as form identities would
be exactly the silent substitution Step 13 forbids, so they are not read here.

Package chemistry
-----------------
The share of a compound's weight that is the named element is arithmetic on
standard atomic weights (``chemistry.py``). It is offered only when the printed
name fixes one exact molecular formula, hydration included. Where the salt, the
hydration state or a chelate's make-up is not fixed by the printed name, the
calculation is withheld and the reason is stated. It is never multiplied by a
printed amount: a label may already print that amount as the element, and a
product of the two would be an intake figure, which this product does not make.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domains.supplements.chemistry import elemental_percent
from app.domains.supplements.engine import normalize_component
from app.domains.supplements.knowledge import COMPOUNDS

FORM_IDENTITY_VERSION = "step-13-forms-v1"


class FormStatus(StrEnum):
    #: The printed name is listed here and denotes one compound form.
    EXACT = "exact"
    #: The printed name is a bare nutrient name; the label as recorded states no form.
    NOT_STATED = "not_stated"
    #: Anything else. A form may well be printed; we do not recognise it exactly.
    NOT_ENOUGH_INFORMATION = "not_enough_information"


class ChemistryStatus(StrEnum):
    CALCULATED = "calculated"
    WITHHELD = "withheld"
    #: The form is not a mineral compound with an element share to calculate.
    NOT_APPLICABLE = "not_applicable"
    NOT_ENOUGH_INFORMATION = "not_enough_information"


class WithheldReason(StrEnum):
    HYDRATION_NOT_STATED = "hydration_not_stated"
    SALT_FORM_NOT_STATED = "salt_form_not_stated"
    COMPOSITION_VARIES = "composition_varies"
    EXACT_FORMULA_NOT_ESTABLISHED = "exact_formula_not_established"
    FORM_NOT_STATED = "form_not_stated"


#: Display names of the elements package chemistry may name.
ELEMENT_NAMES: dict[str, str] = {
    "Mg": "magnesium", "Fe": "iron", "Zn": "zinc", "Ca": "calcium",
}

#: Nutrient keys whose compounds carry an element share to calculate.
MINERAL_KEYS: frozenset[str] = frozenset({"magnesium", "iron", "zinc", "calcium"})


@dataclass(frozen=True)
class ExactForm:
    """One printed name that denotes one compound form."""

    canonical_component_key: str
    #: The knowledge base's ``compound_form`` this name denotes. Knowledge is
    #: looked up by this *and* the key, never by the key alone.
    compound_form: str
    #: A flat formula with any water of hydration written out, set only when
    #: the printed name fixes the exact molecular form.
    formula: str | None = None
    element: str | None = None
    element_atoms: int = 1
    hydration: str | None = None
    #: Why the formula is not set, for a mineral form where it could have been.
    withheld: WithheldReason | None = None


def _mineral(key: str, form: str, *, formula: str | None = None, element: str,
             atoms: int = 1, hydration: str | None = None,
             withheld: WithheldReason | None = None) -> ExactForm:
    if (formula is None) == (withheld is None):
        raise ValueError(f"{form}: a mineral form needs a formula or a reason it has none")
    return ExactForm(key, form, formula, element if formula else None, atoms, hydration, withheld)


_H = WithheldReason.HYDRATION_NOT_STATED
_S = WithheldReason.SALT_FORM_NOT_STATED
_C = WithheldReason.COMPOSITION_VARIES
_X = WithheldReason.EXACT_FORMULA_NOT_ESTABLISHED

# Printed spelling (normalised) -> exact form. Written out by hand, one line per
# spelling, so a reviewer reads every identity this system will ever assert.
# British and American spellings of the same salt are the only synonyms here.
_MINERAL_FORMS: dict[str, ExactForm] = {
    # --- Magnesium ---------------------------------------------------------
    "magnesium oxide": _mineral("magnesium", "magnesium oxide", formula="MgO", element="Mg"),
    # Mono- and trimagnesium citrate are both sold as "magnesium citrate".
    "magnesium citrate": _mineral("magnesium", "magnesium citrate", element="Mg", withheld=_S),
    "trimagnesium dicitrate": _mineral("magnesium", "magnesium citrate", element="Mg", withheld=_H),
    "trimagnesium dicitrate anhydrous": _mineral(
        "magnesium", "magnesium citrate", formula="Mg3C12H10O14", element="Mg", atoms=3, hydration="anhydrous"),
    # A chelate's make-up differs by manufacturer; the name does not fix it.
    "magnesium bisglycinate": _mineral("magnesium", "magnesium bisglycinate", element="Mg", withheld=_C),
    "magnesium bis glycinate": _mineral("magnesium", "magnesium bisglycinate", element="Mg", withheld=_C),
    "magnesium malate": _mineral("magnesium", "magnesium malate", element="Mg", withheld=_S),
    "magnesium chloride": _mineral("magnesium", "magnesium chloride", element="Mg", withheld=_H),
    "magnesium chloride anhydrous": _mineral(
        "magnesium", "magnesium chloride", formula="MgCl2", element="Mg", hydration="anhydrous"),
    "magnesium chloride hexahydrate": _mineral(
        "magnesium", "magnesium chloride", formula="MgCl2H12O6", element="Mg", hydration="hexahydrate"),
    "magnesium sulfate": _mineral("magnesium", "magnesium sulfate", element="Mg", withheld=_H),
    "magnesium sulphate": _mineral("magnesium", "magnesium sulfate", element="Mg", withheld=_H),
    "magnesium sulfate anhydrous": _mineral(
        "magnesium", "magnesium sulfate", formula="MgSO4", element="Mg", hydration="anhydrous"),
    "magnesium sulphate anhydrous": _mineral(
        "magnesium", "magnesium sulfate", formula="MgSO4", element="Mg", hydration="anhydrous"),
    "magnesium sulfate heptahydrate": _mineral(
        "magnesium", "magnesium sulfate", formula="MgSO4H14O7", element="Mg", hydration="heptahydrate"),
    "magnesium sulphate heptahydrate": _mineral(
        "magnesium", "magnesium sulfate", formula="MgSO4H14O7", element="Mg", hydration="heptahydrate"),
    "magnesium l threonate": _mineral("magnesium", "magnesium L-threonate", element="Mg", withheld=_X),
    # --- Iron --------------------------------------------------------------
    "ferrous sulfate": _mineral("iron", "ferrous sulfate", element="Fe", withheld=_H),
    "ferrous sulphate": _mineral("iron", "ferrous sulfate", element="Fe", withheld=_H),
    "ferrous sulfate anhydrous": _mineral(
        "iron", "ferrous sulfate", formula="FeSO4", element="Fe", hydration="anhydrous"),
    "ferrous sulphate anhydrous": _mineral(
        "iron", "ferrous sulfate", formula="FeSO4", element="Fe", hydration="anhydrous"),
    "ferrous sulfate monohydrate": _mineral(
        "iron", "ferrous sulfate", formula="FeSO4H2O", element="Fe", hydration="monohydrate"),
    "ferrous sulphate monohydrate": _mineral(
        "iron", "ferrous sulfate", formula="FeSO4H2O", element="Fe", hydration="monohydrate"),
    "ferrous sulfate heptahydrate": _mineral(
        "iron", "ferrous sulfate", formula="FeSO4H14O7", element="Fe", hydration="heptahydrate"),
    "ferrous sulphate heptahydrate": _mineral(
        "iron", "ferrous sulfate", formula="FeSO4H14O7", element="Fe", hydration="heptahydrate"),
    # "Dried" ferrous sulfate is partly dehydrated to a range, not one formula.
    "dried ferrous sulfate": _mineral("iron", "ferrous sulfate", element="Fe", withheld=_C),
    "dried ferrous sulphate": _mineral("iron", "ferrous sulfate", element="Fe", withheld=_C),
    "ferrous sulfate dried": _mineral("iron", "ferrous sulfate", element="Fe", withheld=_C),
    "ferrous sulphate dried": _mineral("iron", "ferrous sulfate", element="Fe", withheld=_C),
    "ferrous fumarate": _mineral("iron", "ferrous fumarate", formula="FeC4H2O4", element="Fe"),
    "ferrous gluconate": _mineral("iron", "ferrous gluconate", element="Fe", withheld=_H),
    "ferrous gluconate dihydrate": _mineral(
        "iron", "ferrous gluconate", formula="FeC12H26O16", element="Fe", hydration="dihydrate"),
    "ferrous bisglycinate": _mineral("iron", "ferrous bisglycinate", element="Fe", withheld=_C),
    "ferrous bis glycinate": _mineral("iron", "ferrous bisglycinate", element="Fe", withheld=_C),
    # A purity, not a compound: the name fixes no formula.
    "carbonyl iron": _mineral("iron", "carbonyl iron", element="Fe", withheld=_C),
    # --- Zinc --------------------------------------------------------------
    "zinc oxide": _mineral("zinc", "zinc oxide", formula="ZnO", element="Zn"),
    "zinc sulfate": _mineral("zinc", "zinc sulfate", element="Zn", withheld=_H),
    "zinc sulphate": _mineral("zinc", "zinc sulfate", element="Zn", withheld=_H),
    "zinc sulfate anhydrous": _mineral(
        "zinc", "zinc sulfate", formula="ZnSO4", element="Zn", hydration="anhydrous"),
    "zinc sulphate anhydrous": _mineral(
        "zinc", "zinc sulfate", formula="ZnSO4", element="Zn", hydration="anhydrous"),
    "zinc sulfate monohydrate": _mineral(
        "zinc", "zinc sulfate", formula="ZnSO4H2O", element="Zn", hydration="monohydrate"),
    "zinc sulphate monohydrate": _mineral(
        "zinc", "zinc sulfate", formula="ZnSO4H2O", element="Zn", hydration="monohydrate"),
    "zinc sulfate heptahydrate": _mineral(
        "zinc", "zinc sulfate", formula="ZnSO4H14O7", element="Zn", hydration="heptahydrate"),
    "zinc sulphate heptahydrate": _mineral(
        "zinc", "zinc sulfate", formula="ZnSO4H14O7", element="Zn", hydration="heptahydrate"),
    "zinc gluconate": _mineral("zinc", "zinc gluconate", element="Zn", withheld=_H),
    "zinc picolinate": _mineral("zinc", "zinc picolinate", element="Zn", withheld=_X),
    "zinc bisglycinate": _mineral("zinc", "zinc bisglycinate", element="Zn", withheld=_C),
    "zinc bis glycinate": _mineral("zinc", "zinc bisglycinate", element="Zn", withheld=_C),
    # --- Calcium -----------------------------------------------------------
    "calcium carbonate": _mineral("calcium", "calcium carbonate", formula="CaCO3", element="Ca"),
    "calcium citrate": _mineral("calcium", "calcium citrate", element="Ca", withheld=_H),
    "calcium citrate anhydrous": _mineral(
        "calcium", "calcium citrate", formula="Ca3C12H10O14", element="Ca", atoms=3, hydration="anhydrous"),
    "calcium citrate tetrahydrate": _mineral(
        "calcium", "calcium citrate", formula="Ca3C12H18O18", element="Ca", atoms=3, hydration="tetrahydrate"),
    "calcium lactate": _mineral("calcium", "calcium lactate", element="Ca", withheld=_H),
    "calcium lactate anhydrous": _mineral(
        "calcium", "calcium lactate", formula="CaC6H10O6", element="Ca", hydration="anhydrous"),
    "calcium lactate pentahydrate": _mineral(
        "calcium", "calcium lactate", formula="CaC6H20O11", element="Ca", hydration="pentahydrate"),
}

# Named forms of the non-mineral nutrients. No element share applies to these.
_OTHER_FORMS: dict[str, tuple[str, str]] = {
    "cholecalciferol": ("vitamin d", "vitamin D3 (cholecalciferol)"),
    "vitamin d3": ("vitamin d", "vitamin D3 (cholecalciferol)"),
    "ergocalciferol": ("vitamin d", "vitamin D2 (ergocalciferol)"),
    "vitamin d2": ("vitamin d", "vitamin D2 (ergocalciferol)"),
    "cyanocobalamin": ("vitamin b12", "cyanocobalamin"),
    "methylcobalamin": ("vitamin b12", "methylcobalamin"),
    "mecobalamin": ("vitamin b12", "methylcobalamin"),
    "adenosylcobalamin": ("vitamin b12", "adenosylcobalamin"),
    "cobamamide": ("vitamin b12", "adenosylcobalamin"),
    "folic acid": ("folate", "folic acid"),
    "pteroylglutamic acid": ("folate", "folic acid"),
    "levomefolic acid": ("folate", "L-5-methyltetrahydrofolate"),
    "l methylfolate": ("folate", "L-5-methyltetrahydrofolate"),
    "ascorbic acid": ("vitamin c", "ascorbic acid"),
    "l ascorbic acid": ("vitamin c", "ascorbic acid"),
    "sodium ascorbate": ("vitamin c", "sodium ascorbate"),
    "ascorbyl palmitate": ("vitamin c", "ascorbyl palmitate"),
    "ubiquinone": ("coenzyme q10", "ubiquinone"),
    "ubiquinol": ("coenzyme q10", "ubiquinol"),
}

#: Bare nutrient names. The label as recorded names the nutrient and no form.
NUTRIENT_LEVEL_NAMES: frozenset[str] = frozenset({
    "magnesium", "elemental magnesium", "iron", "elemental iron", "zinc", "elemental zinc",
    "calcium", "elemental calcium", "vitamin d", "vit d", "vitamin b12", "vit b12", "b12",
    "cobalamin", "folate", "vitamin b9", "vitamin c", "vit c", "coenzyme q10", "coq10",
    "co q10", "co q 10", "curcumin", "curcuminoids", "omega 3", "omega 3 fatty acids",
})


def _build_table() -> dict[str, ExactForm]:
    table: dict[str, ExactForm] = dict(_MINERAL_FORMS)
    for name, (key, form) in _OTHER_FORMS.items():
        table[name] = ExactForm(key, form)
    # Every knowledge entry's own form name denotes that entry, exactly.
    for compound in COMPOUNDS:
        spelling = normalize_component(compound.form)
        if spelling in table:
            continue
        if compound.key in MINERAL_KEYS:
            # A mineral form reached only by its own name still states no
            # formula here unless listed above; its arithmetic stays withheld.
            table[spelling] = ExactForm(compound.key, compound.form, element=None,
                                        withheld=WithheldReason.EXACT_FORMULA_NOT_ESTABLISHED)
        else:
            table[spelling] = ExactForm(compound.key, compound.form)
    overlap = set(table) & NUTRIENT_LEVEL_NAMES
    if overlap:  # pragma: no cover - guarded by tests
        raise ValueError(f"a nutrient-level name is also listed as a form: {sorted(overlap)}")
    known_forms = {(c.key, c.form) for c in COMPOUNDS}
    for spelling, form in table.items():
        if (form.canonical_component_key, form.compound_form) not in known_forms:
            raise ValueError(f"{spelling!r} names a form the knowledge base does not define")
    return table


EXACT_FORMS: dict[str, ExactForm] = _build_table()


@dataclass(frozen=True)
class PackageChemistry:
    status: ChemistryStatus
    element: str | None = None
    percent_by_weight: Decimal | None = None
    formula: str | None = None
    hydration: str | None = None
    withheld_reason: WithheldReason | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "element": ELEMENT_NAMES.get(self.element) if self.element else None,
            "element_symbol": self.element,
            "percent_by_weight": format(self.percent_by_weight, "f") if self.percent_by_weight is not None else None,
            "formula": self.formula,
            "hydration": self.hydration,
            "withheld_reason": self.withheld_reason.value if self.withheld_reason else None,
        }


@dataclass(frozen=True)
class FormResolution:
    status: FormStatus
    form: ExactForm | None = None
    #: The nutrient key the fact carries, kept so a bare nutrient name can be
    #: told apart as a mineral (form missing) or not (nothing to calculate).
    canonical_component_key: str | None = None

    @property
    def knowledge_key(self) -> tuple[str, str] | None:
        """The exact (nutrient, form) pair knowledge may be read for, or None."""
        if self.status is not FormStatus.EXACT or self.form is None:
            return None
        return self.form.canonical_component_key, self.form.compound_form


def resolve_form(printed_name: str, *, canonical_component_key: str | None) -> FormResolution:
    """Which exact form this printed name denotes, if any.

    ``canonical_component_key`` is the nutrient key stored on the fact. A form
    whose nutrient disagrees with it is refused rather than trusted: the row and
    this table no longer describe the same thing, and choosing either would be
    a guess.
    """
    spelling = normalize_component(printed_name or "")
    if not spelling:
        return FormResolution(FormStatus.NOT_ENOUGH_INFORMATION)
    if spelling in NUTRIENT_LEVEL_NAMES:
        return FormResolution(FormStatus.NOT_STATED, canonical_component_key=canonical_component_key)
    form = EXACT_FORMS.get(spelling)
    if form is None or form.canonical_component_key != canonical_component_key:
        return FormResolution(FormStatus.NOT_ENOUGH_INFORMATION)
    return FormResolution(FormStatus.EXACT, form, canonical_component_key=canonical_component_key)


def package_chemistry(resolution: FormResolution) -> PackageChemistry:
    """The element's share of the compound's weight, or why it is not given.

    A property of the compound, computed from atomic weights. Never an amount
    in a serving, never an amount absorbed, and never combined with a printed
    amount.
    """
    if resolution.status is FormStatus.NOT_STATED:
        if resolution.canonical_component_key in MINERAL_KEYS:
            return PackageChemistry(ChemistryStatus.WITHHELD, withheld_reason=WithheldReason.FORM_NOT_STATED)
        return PackageChemistry(ChemistryStatus.NOT_APPLICABLE)
    if resolution.status is not FormStatus.EXACT or resolution.form is None:
        return PackageChemistry(ChemistryStatus.NOT_ENOUGH_INFORMATION)
    form = resolution.form
    if form.formula and form.element:
        return PackageChemistry(
            ChemistryStatus.CALCULATED,
            element=form.element,
            percent_by_weight=elemental_percent(form.formula, form.element, form.element_atoms),
            formula=form.formula,
            hydration=form.hydration,
        )
    if form.withheld is not None:
        return PackageChemistry(ChemistryStatus.WITHHELD, withheld_reason=form.withheld)
    return PackageChemistry(ChemistryStatus.NOT_APPLICABLE)


__all__ = [
    "ELEMENT_NAMES",
    "EXACT_FORMS",
    "FORM_IDENTITY_VERSION",
    "MINERAL_KEYS",
    "NUTRIENT_LEVEL_NAMES",
    "ChemistryStatus",
    "ExactForm",
    "FormResolution",
    "FormStatus",
    "PackageChemistry",
    "WithheldReason",
    "package_chemistry",
    "resolve_form",
]
