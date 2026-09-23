"""Exact compound-form identity for a printed supplement label name.

Step 13. Two identities are kept apart on purpose:

* **Nutrient identity** — resolved by ``identity.component_identity`` from the
  reviewed vocabulary. "Magnesium oxide", "Magnesium citrate" and plain
  "Magnesium" all share ``magnesium``. It drives overlap and nothing else.
* **Form identity** — which exact compound the pack prints. Resolved here, from
  the printed name alone, by exact lookup in a small explicit table.

A shared nutrient key is never evidence of a shared form. Nothing here guesses:
there is no fuzzy matching, no substring search, no "most common form", no
model. A printed "Magnesium" states no form, and gets none. A printed name this
table does not list is "not enough information", whatever it looks like.

Explicit authority only
-----------------------
Every entry below is written out by hand, one line per printed spelling. None is
derived from ``knowledge.py``. That file says itself that nothing in it has been
checked, and a value does not become reviewed chemistry identity by being stored
in ``Compound.form`` rather than ``Compound.aliases``: several of its aliases
name a *different* molecular form from their entry (an anhydrous formula under
"magnesium chloride hexahydrate"), and several of its form names are marketing
formulations rather than compounds ("liposomal vitamin C", "curcumin with
piperine", "ethyl ester (EE)"). ``COMPOUNDS`` is used here only as a validation
target: an entry marked knowledge-eligible must name a subject the knowledge
base defines. It never adds a spelling.

Recognised label form versus knowledge-eligible form
----------------------------------------------------
A printed name can be specific enough to show back to the customer as a form,
and still not specific enough to join empirical research about one compound.
"Magnesium citrate" names a magnesium citrate, but not whether it is the mono- or
the trimagnesium salt; a chelate name does not fix what a maker actually
chelated. Such names are *recognised* (shown, and chemistry withheld with the
reason) but not *knowledge-eligible*. Only a knowledge-eligible form is ever
used as a key into ``supplement_component_knowledge``.

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

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.domains.supplements.chemistry import elemental_percent
from app.domains.supplements.knowledge import COMPOUNDS
from app.domains.supplements.names import NUTRIENT_SPELLINGS, normalize_component

FORM_IDENTITY_VERSION = "step-13-forms-v2"


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

#: Bare nutrient names. The label as recorded names the nutrient and no form.
NUTRIENT_LEVEL_NAMES: frozenset[str] = frozenset(NUTRIENT_SPELLINGS)


@dataclass(frozen=True)
class ExactForm:
    """One printed name that denotes one recognised compound form."""

    canonical_component_key: str
    #: The form's name as shown to the customer. When ``knowledge_eligible``, it
    #: is also the knowledge base's ``compound_form`` for exactly this compound.
    compound_form: str
    #: May this form be joined to empirical knowledge about ``compound_form``?
    #: True only when the printed name fixes the compound that knowledge
    #: describes. Recognition alone never makes a knowledge join key.
    knowledge_eligible: bool = False
    #: A flat formula with any water of hydration written out, set only when
    #: the printed name fixes the exact molecular form.
    formula: str | None = None
    element: str | None = None
    element_atoms: int = 1
    hydration: str | None = None
    #: Why the formula is not set, for a mineral form where it could have been.
    withheld: WithheldReason | None = None


def _mineral(key: str, form: str, *, eligible: bool, formula: str | None = None, element: str,
             atoms: int = 1, hydration: str | None = None,
             withheld: WithheldReason | None = None) -> ExactForm:
    if (formula is None) == (withheld is None):
        raise ValueError(f"{form}: a mineral form needs a formula or a reason it has none")
    return ExactForm(key, form, eligible, formula, element if formula else None, atoms, hydration, withheld)


def _named(key: str, form: str) -> ExactForm:
    """A non-mineral form: one named molecule, no element share to calculate."""
    return ExactForm(key, form, knowledge_eligible=True)


_H = WithheldReason.HYDRATION_NOT_STATED
_S = WithheldReason.SALT_FORM_NOT_STATED
_C = WithheldReason.COMPOSITION_VARIES
_X = WithheldReason.EXACT_FORMULA_NOT_ESTABLISHED

# Printed spelling (normalised) -> exact form. Written out by hand, one line per
# spelling, so a reviewer reads every identity this system will ever assert.
# British and American spellings of the same salt are the only spelling
# synonyms here; a hydrate is only ever named by its own printed hydrate name.
#
# ``eligible=True``: the printed name fixes the salt the knowledge entry is
# about. A hydrate name, or an unstated hydrate, is still that salt; hydration
# changes the arithmetic, not which compound it is. ``eligible=False``: the
# name does not fix the compound (salt stoichiometry unstated, or a chelate or
# preparation whose make-up differs between makers).
_MINERAL_FORMS: dict[str, ExactForm] = {
    # --- Magnesium ---------------------------------------------------------
    "magnesium oxide": _mineral("magnesium", "magnesium oxide", eligible=True, formula="MgO", element="Mg"),
    # Mono- and trimagnesium citrate are both sold as "magnesium citrate".
    "magnesium citrate": _mineral("magnesium", "magnesium citrate", eligible=False, element="Mg", withheld=_S),
    "trimagnesium dicitrate": _mineral("magnesium", "magnesium citrate", eligible=True, element="Mg", withheld=_H),
    "trimagnesium dicitrate anhydrous": _mineral(
        "magnesium", "magnesium citrate", eligible=True, formula="Mg3C12H10O14", element="Mg", atoms=3,
        hydration="anhydrous"),
    # A chelate's make-up differs by manufacturer; the name does not fix it.
    "magnesium bisglycinate": _mineral("magnesium", "magnesium bisglycinate", eligible=False, element="Mg", withheld=_C),
    "magnesium bis glycinate": _mineral("magnesium", "magnesium bisglycinate", eligible=False, element="Mg", withheld=_C),
    "magnesium malate": _mineral("magnesium", "magnesium malate", eligible=False, element="Mg", withheld=_S),
    "magnesium chloride": _mineral("magnesium", "magnesium chloride", eligible=True, element="Mg", withheld=_H),
    "magnesium chloride anhydrous": _mineral(
        "magnesium", "magnesium chloride", eligible=True, formula="MgCl2", element="Mg", hydration="anhydrous"),
    "magnesium chloride hexahydrate": _mineral(
        "magnesium", "magnesium chloride", eligible=True, formula="MgCl2H12O6", element="Mg", hydration="hexahydrate"),
    "magnesium sulfate": _mineral("magnesium", "magnesium sulfate", eligible=True, element="Mg", withheld=_H),
    "magnesium sulphate": _mineral("magnesium", "magnesium sulfate", eligible=True, element="Mg", withheld=_H),
    "magnesium sulfate anhydrous": _mineral(
        "magnesium", "magnesium sulfate", eligible=True, formula="MgSO4", element="Mg", hydration="anhydrous"),
    "magnesium sulphate anhydrous": _mineral(
        "magnesium", "magnesium sulfate", eligible=True, formula="MgSO4", element="Mg", hydration="anhydrous"),
    "magnesium sulfate heptahydrate": _mineral(
        "magnesium", "magnesium sulfate", eligible=True, formula="MgSO4H14O7", element="Mg", hydration="heptahydrate"),
    "magnesium sulphate heptahydrate": _mineral(
        "magnesium", "magnesium sulfate", eligible=True, formula="MgSO4H14O7", element="Mg", hydration="heptahydrate"),
    "magnesium l threonate": _mineral("magnesium", "magnesium L-threonate", eligible=True, element="Mg", withheld=_X),
    # --- Iron --------------------------------------------------------------
    "ferrous sulfate": _mineral("iron", "ferrous sulfate", eligible=True, element="Fe", withheld=_H),
    "ferrous sulphate": _mineral("iron", "ferrous sulfate", eligible=True, element="Fe", withheld=_H),
    "ferrous sulfate anhydrous": _mineral(
        "iron", "ferrous sulfate", eligible=True, formula="FeSO4", element="Fe", hydration="anhydrous"),
    "ferrous sulphate anhydrous": _mineral(
        "iron", "ferrous sulfate", eligible=True, formula="FeSO4", element="Fe", hydration="anhydrous"),
    "ferrous sulfate monohydrate": _mineral(
        "iron", "ferrous sulfate", eligible=True, formula="FeSO4H2O", element="Fe", hydration="monohydrate"),
    "ferrous sulphate monohydrate": _mineral(
        "iron", "ferrous sulfate", eligible=True, formula="FeSO4H2O", element="Fe", hydration="monohydrate"),
    "ferrous sulfate heptahydrate": _mineral(
        "iron", "ferrous sulfate", eligible=True, formula="FeSO4H14O7", element="Fe", hydration="heptahydrate"),
    "ferrous sulphate heptahydrate": _mineral(
        "iron", "ferrous sulfate", eligible=True, formula="FeSO4H14O7", element="Fe", hydration="heptahydrate"),
    # "Dried" ferrous sulfate is partly dehydrated to a range, not one formula.
    "dried ferrous sulfate": _mineral("iron", "dried ferrous sulfate", eligible=False, element="Fe", withheld=_C),
    "dried ferrous sulphate": _mineral("iron", "dried ferrous sulfate", eligible=False, element="Fe", withheld=_C),
    "ferrous sulfate dried": _mineral("iron", "dried ferrous sulfate", eligible=False, element="Fe", withheld=_C),
    "ferrous sulphate dried": _mineral("iron", "dried ferrous sulfate", eligible=False, element="Fe", withheld=_C),
    "ferrous fumarate": _mineral("iron", "ferrous fumarate", eligible=True, formula="FeC4H2O4", element="Fe"),
    "ferrous gluconate": _mineral("iron", "ferrous gluconate", eligible=True, element="Fe", withheld=_H),
    "ferrous gluconate dihydrate": _mineral(
        "iron", "ferrous gluconate", eligible=True, formula="FeC12H26O16", element="Fe", hydration="dihydrate"),
    "ferrous bisglycinate": _mineral("iron", "ferrous bisglycinate", eligible=False, element="Fe", withheld=_C),
    "ferrous bis glycinate": _mineral("iron", "ferrous bisglycinate", eligible=False, element="Fe", withheld=_C),
    # A purity, not a compound: the name fixes no formula.
    "carbonyl iron": _mineral("iron", "carbonyl iron", eligible=False, element="Fe", withheld=_C),
    # --- Zinc --------------------------------------------------------------
    "zinc oxide": _mineral("zinc", "zinc oxide", eligible=True, formula="ZnO", element="Zn"),
    "zinc sulfate": _mineral("zinc", "zinc sulfate", eligible=True, element="Zn", withheld=_H),
    "zinc sulphate": _mineral("zinc", "zinc sulfate", eligible=True, element="Zn", withheld=_H),
    "zinc sulfate anhydrous": _mineral(
        "zinc", "zinc sulfate", eligible=True, formula="ZnSO4", element="Zn", hydration="anhydrous"),
    "zinc sulphate anhydrous": _mineral(
        "zinc", "zinc sulfate", eligible=True, formula="ZnSO4", element="Zn", hydration="anhydrous"),
    "zinc sulfate monohydrate": _mineral(
        "zinc", "zinc sulfate", eligible=True, formula="ZnSO4H2O", element="Zn", hydration="monohydrate"),
    "zinc sulphate monohydrate": _mineral(
        "zinc", "zinc sulfate", eligible=True, formula="ZnSO4H2O", element="Zn", hydration="monohydrate"),
    "zinc sulfate heptahydrate": _mineral(
        "zinc", "zinc sulfate", eligible=True, formula="ZnSO4H14O7", element="Zn", hydration="heptahydrate"),
    "zinc sulphate heptahydrate": _mineral(
        "zinc", "zinc sulfate", eligible=True, formula="ZnSO4H14O7", element="Zn", hydration="heptahydrate"),
    "zinc gluconate": _mineral("zinc", "zinc gluconate", eligible=True, element="Zn", withheld=_H),
    "zinc picolinate": _mineral("zinc", "zinc picolinate", eligible=True, element="Zn", withheld=_X),
    "zinc bisglycinate": _mineral("zinc", "zinc bisglycinate", eligible=False, element="Zn", withheld=_C),
    "zinc bis glycinate": _mineral("zinc", "zinc bisglycinate", eligible=False, element="Zn", withheld=_C),
    # --- Calcium -----------------------------------------------------------
    "calcium carbonate": _mineral("calcium", "calcium carbonate", eligible=True, formula="CaCO3", element="Ca"),
    "calcium citrate": _mineral("calcium", "calcium citrate", eligible=True, element="Ca", withheld=_H),
    "calcium citrate anhydrous": _mineral(
        "calcium", "calcium citrate", eligible=True, formula="Ca3C12H10O14", element="Ca", atoms=3,
        hydration="anhydrous"),
    "calcium citrate tetrahydrate": _mineral(
        "calcium", "calcium citrate", eligible=True, formula="Ca3C12H18O18", element="Ca", atoms=3,
        hydration="tetrahydrate"),
    "calcium lactate": _mineral("calcium", "calcium lactate", eligible=True, element="Ca", withheld=_H),
    "calcium lactate anhydrous": _mineral(
        "calcium", "calcium lactate", eligible=True, formula="CaC6H10O6", element="Ca", hydration="anhydrous"),
    "calcium lactate pentahydrate": _mineral(
        "calcium", "calcium lactate", eligible=True, formula="CaC6H20O11", element="Ca", hydration="pentahydrate"),
}

# Named molecules of the non-mineral nutrients. Each printed spelling names one
# molecule exactly (D3 *is* cholecalciferol; mecobalamin *is* methylcobalamin),
# so each is knowledge-eligible for that molecule. Marketing formulations —
# liposomal, "with piperine", phospholipid complexes, ethyl-ester or
# triglyceride fish oils, plain "extract" — are deliberately absent: the words
# describe a preparation, not the compound a study measured.
_OTHER_FORMS: dict[str, ExactForm] = {
    "cholecalciferol": _named("vitamin d", "vitamin D3 (cholecalciferol)"),
    "vitamin d3": _named("vitamin d", "vitamin D3 (cholecalciferol)"),
    "vitamin d3 cholecalciferol": _named("vitamin d", "vitamin D3 (cholecalciferol)"),
    "ergocalciferol": _named("vitamin d", "vitamin D2 (ergocalciferol)"),
    "vitamin d2": _named("vitamin d", "vitamin D2 (ergocalciferol)"),
    "vitamin d2 ergocalciferol": _named("vitamin d", "vitamin D2 (ergocalciferol)"),
    "cyanocobalamin": _named("vitamin b12", "cyanocobalamin"),
    "methylcobalamin": _named("vitamin b12", "methylcobalamin"),
    "mecobalamin": _named("vitamin b12", "methylcobalamin"),
    "adenosylcobalamin": _named("vitamin b12", "adenosylcobalamin"),
    "cobamamide": _named("vitamin b12", "adenosylcobalamin"),
    "folic acid": _named("folate", "folic acid"),
    "pteroylglutamic acid": _named("folate", "folic acid"),
    "levomefolic acid": _named("folate", "L-5-methyltetrahydrofolate"),
    "l methylfolate": _named("folate", "L-5-methyltetrahydrofolate"),
    "l 5 methyltetrahydrofolate": _named("folate", "L-5-methyltetrahydrofolate"),
    "ascorbic acid": _named("vitamin c", "ascorbic acid"),
    "l ascorbic acid": _named("vitamin c", "ascorbic acid"),
    "sodium ascorbate": _named("vitamin c", "sodium ascorbate"),
    "ascorbyl palmitate": _named("vitamin c", "ascorbyl palmitate"),
    "ubiquinone": _named("coenzyme q10", "ubiquinone"),
    "ubiquinol": _named("coenzyme q10", "ubiquinol"),
}


def build_exact_forms(knowledge_subjects: Iterable[Any]) -> dict[str, ExactForm]:
    """The explicit table, validated against the knowledge base's subjects.

    ``knowledge_subjects`` is read for validation only — to prove that every
    knowledge-eligible entry names a subject the knowledge base defines. It is
    never a source of spellings: a compound added there adds nothing here.
    """
    table: dict[str, ExactForm] = {**_MINERAL_FORMS, **_OTHER_FORMS}
    clash = set(table) & NUTRIENT_LEVEL_NAMES
    if clash:
        raise ValueError(f"a nutrient-level name is also listed as a form: {sorted(clash)}")
    for spelling in table:
        if normalize_component(spelling) != spelling:
            raise ValueError(f"{spelling!r} is not a normalised spelling")
    known = {(subject.key, subject.form) for subject in knowledge_subjects}
    for spelling, form in table.items():
        if form.knowledge_eligible and (form.canonical_component_key, form.compound_form) not in known:
            raise ValueError(f"{spelling!r} is knowledge-eligible for a subject the knowledge base does not define")
    return table


EXACT_FORMS: dict[str, ExactForm] = build_exact_forms(COMPOUNDS)


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
        """The exact (nutrient, form) pair knowledge may be read for, or None.

        None unless the form is exact *and* knowledge-eligible. A recognised
        form that does not fix its compound never becomes a knowledge join.
        """
        if self.status is not FormStatus.EXACT or self.form is None or not self.form.knowledge_eligible:
            return None
        return self.form.canonical_component_key, self.form.compound_form


def resolve_form(printed_name: str, *, canonical_component_key: str | None) -> FormResolution:
    """Which exact form this printed name denotes, if any.

    ``canonical_component_key`` is the fact's *revalidated* nutrient key (see
    ``identity.effective_key``); None means its identity could not be
    trusted. A form whose nutrient disagrees with it is refused rather than
    trusted: choosing either would be a guess.
    """
    spelling = normalize_component(printed_name or "")
    if not spelling or canonical_component_key is None:
        return FormResolution(FormStatus.NOT_ENOUGH_INFORMATION)
    if spelling in NUTRIENT_LEVEL_NAMES:
        if NUTRIENT_SPELLINGS[spelling] != canonical_component_key:
            return FormResolution(FormStatus.NOT_ENOUGH_INFORMATION)
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
    "build_exact_forms",
    "normalize_component",
    "package_chemistry",
    "resolve_form",
]
