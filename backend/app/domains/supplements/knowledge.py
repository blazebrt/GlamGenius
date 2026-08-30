"""The supplement absorption knowledge base.

Extends VC-07. Keyed on the same ``canonical_component_key`` that
``engine.component_identity()`` produces, so a label the customer photographed
and an entry in here meet on one key.

Two kinds of number live here and they are kept strictly apart:

* **Elemental percentage** — arithmetic on atomic weights, in ``chemistry.py``.
  Computed, not remembered, and not citable to a study because it is not a
  finding. Confidence is HIGH for all of them, always.
* **Absorption** — how much of that element or compound a person actually takes
  up. This comes from studies, varies between people and with dose, food and
  iron status, and is frequently disputed. Confidence is stated per entry and
  disagreements are recorded with both figures.

Hydration state is modelled because it changes the answer enormously: ferrous
sulfate is 36.8% iron dry and 20.1% as the heptahydrate an Indian label
normally means.

**Nothing in this file has been opened and checked by this system.** The
environment that generated it has no outbound access to any journal, regulator
or library, so every ``source_url`` is a citation to be verified by a person,
not a link that has been followed. That is why the loader writes drafts and why
``verification`` starts at UNVERIFIED for every entry: the constitution's
knowledge-verification rule puts a human between a citation and a published
number, and this is that gap made explicit rather than papered over.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domains.supplements.chemistry import elemental_percent


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Verification(StrEnum):
    """Whether a person has opened the source and confirmed the number."""

    UNVERIFIED = "unverified"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"


# The tier vocabulary the authoring tool uses.
TIER_CLINICAL = "clinically_studied"
TIER_TRADITIONAL = "traditional_use"
TIER_NOT_ENOUGH = "not_enough_information"


@dataclass(frozen=True)
class Absorption:
    """An absorption or relative-bioavailability figure, with its citation."""

    summary: str
    value_text: str
    unit: str
    source_name: str
    source_url: str
    source_identifier: str
    confidence: Confidence
    # Both figures, when the literature does not agree.
    disagreement: str | None = None


@dataclass(frozen=True)
class Compound:
    """One compound form of one nutrient."""

    key: str                     # canonical_component_key, e.g. "magnesium"
    nutrient: str                # display name, e.g. "Magnesium"
    form: str                    # e.g. "magnesium oxide"
    aliases: tuple[str, ...] = ()
    formula: str | None = None
    element: str | None = None
    element_atoms: int = 1
    hydration: str | None = None
    equivalent_percent: Decimal | None = None   # for non-elemental equivalents
    equivalent_note: str | None = None
    absorption: Absorption | None = None        # None => not enough information
    note: str | None = None

    @property
    def elemental_percent(self) -> Decimal | None:
        """Computed, never stored. Arithmetic cannot drift from its inputs."""
        if self.formula and self.element:
            return elemental_percent(self.formula, self.element, self.element_atoms)
        return self.equivalent_percent

    @property
    def percent_kind(self) -> str | None:
        if self.formula and self.element:
            return "elemental_by_weight"
        if self.equivalent_percent is not None:
            return "equivalent_by_weight"
        return None

    @property
    def tier(self) -> str:
        if self.absorption is None:
            return TIER_NOT_ENOUGH
        return TIER_CLINICAL

    @property
    def absorption_confidence(self) -> Confidence | None:
        return self.absorption.confidence if self.absorption else None


# --- Citations -------------------------------------------------------------
# Named once so the same work is cited identically everywhere, and so a
# reviewer checking one entry has checked it for every entry that shares it.

FIROZ_2001 = ("Firoz M, Graber M. Bioavailability of US commercial magnesium preparations. "
              "Magnesium Research 2001;14(4):257-62.", "https://pubmed.ncbi.nlm.nih.gov/11794633/", "PMID:11794633")
WALKER_2003 = ("Walker AF et al. Mg citrate found more bioavailable than other Mg preparations. "
               "Magnesium Research 2003;16(3):183-91.", "https://pubmed.ncbi.nlm.nih.gov/14596323/", "PMID:14596323")
HALLBERG_1989 = ("Hallberg L, Brune M, Rossander L. Iron absorption in man: ascorbic acid and "
                 "dose-dependent inhibition by phytate. Am J Clin Nutr 1989;49(1):140-4.",
                 "https://pubmed.ncbi.nlm.nih.gov/2911999/", "PMID:2911999")
LAYRISSE_2000 = ("Layrisse M et al. Iron bioavailability in humans from breakfasts enriched with iron "
                 "bis-glycine chelate. J Nutr 2000;130(9):2195-9.",
                 "https://pubmed.ncbi.nlm.nih.gov/10958811/", "PMID:10958811")
WEGMULLER_2014 = ("Wegmüller R et al. Zinc absorption by young adults from supplemental zinc citrate is "
                  "comparable with that from zinc gluconate and higher than from zinc oxide. "
                  "J Nutr 2014;144(2):132-6.", "https://pubmed.ncbi.nlm.nih.gov/24259556/", "PMID:24259556")
HEANEY_1999 = ("Heaney RP, Dowell MS, Barger-Lux MJ. Absorption of calcium as the carbonate and citrate "
               "salts, with some observations on method. Osteoporos Int 1999;9(1):19-23.",
               "https://pubmed.ncbi.nlm.nih.gov/10367024/", "PMID:10367024")
TRIPKOVIC_2012 = ("Tripkovic L et al. Comparison of vitamin D2 and vitamin D3 supplementation in raising "
                  "serum 25-hydroxyvitamin D status: a systematic review and meta-analysis. "
                  "Am J Clin Nutr 2012;95(6):1357-64.", "https://pubmed.ncbi.nlm.nih.gov/22552031/", "PMID:22552031")
IOM_FOLATE = ("Institute of Medicine. Dietary Reference Intakes for Thiamin, Riboflavin, Niacin, "
              "Vitamin B6, Folate, Vitamin B12, Pantothenic Acid, Biotin, and Choline. "
              "National Academies Press, 1998.", "https://www.ncbi.nlm.nih.gov/books/NBK114310/",
              "NBK114310")
LEVINE_1996 = ("Levine M et al. Vitamin C pharmacokinetics in healthy volunteers: evidence for a "
               "recommended dietary allowance. Proc Natl Acad Sci USA 1996;93(8):3704-9.",
               "https://pubmed.ncbi.nlm.nih.gov/8623000/", "PMID:8623000")
SHOBA_1998 = ("Shoba G et al. Influence of piperine on the pharmacokinetics of curcumin in animals and "
              "human volunteers. Planta Medica 1998;64(4):353-6.",
              "https://pubmed.ncbi.nlm.nih.gov/9619120/", "PMID:9619120")
CUOMO_2011 = ("Cuomo J et al. Comparative absorption of a standardized curcuminoid mixture and its "
              "lecithin formulation. J Nat Prod 2011;74(4):664-9.",
              "https://pubmed.ncbi.nlm.nih.gov/21413691/", "PMID:21413691")
DYERBERG_2010 = ("Dyerberg J et al. Bioavailability of marine n-3 fatty acid formulations. "
                 "Prostaglandins Leukot Essent Fatty Acids 2010;83(3):137-41.",
                 "https://pubmed.ncbi.nlm.nih.gov/20638827/", "PMID:20638827")
EFSA_B12 = ("EFSA Panel on Dietetic Products, Nutrition and Allergies. Scientific Opinion on Dietary "
            "Reference Values for cobalamin (vitamin B12). EFSA Journal 2015;13(7):4150.",
            "https://www.efsa.europa.eu/en/efsajournal/pub/4150", "EFSA-Q-2011-01230")


def _absorption(citation: tuple[str, str, str], *, summary: str, value: str, unit: str,
                confidence: Confidence, disagreement: str | None = None) -> Absorption:
    name, url, identifier = citation
    return Absorption(
        summary=summary, value_text=value, unit=unit,
        source_name=name, source_url=url, source_identifier=identifier,
        confidence=confidence, disagreement=disagreement,
    )


# --- The compounds ----------------------------------------------------------
# absorption=None is a deliberate answer, not an omission: it means no source
# was found that this system could name with confidence, so the entry says
# "not enough information" rather than estimating.

COMPOUNDS: tuple[Compound, ...] = (
    # --- Magnesium ---------------------------------------------------------
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium oxide",
        aliases=("mg oxide", "magnesia", "magnesium oxide heavy", "light magnesium oxide"),
        formula="MgO", element="Mg",
        absorption=_absorption(
            FIROZ_2001,
            summary="Poorly absorbed compared with soluble magnesium salts.",
            value="about 4", unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
            disagreement=(
                "Firoz & Graber report about 4% absorption for oxide. Other work finds oxide "
                "much closer to soluble salts once dose and gut transit are accounted for, so "
                "the gap between forms may be far smaller than 4% suggests."
            ),
        ),
    ),
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium citrate",
        aliases=("mg citrate", "trimagnesium dicitrate", "magnesium citrate anhydrous"),
        formula="Mg3C12H10O14", element="Mg", element_atoms=3,
        absorption=_absorption(
            WALKER_2003,
            summary="Better absorbed than oxide in a direct comparison.",
            value="higher than oxide; about 25-30% of the dose", unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
            disagreement=(
                "Walker 2003 found citrate more bioavailable than oxide and amino-acid chelate. "
                "Study sizes across this literature are small and figures are not consistent."
            ),
        ),
    ),
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium bisglycinate",
        aliases=("magnesium glycinate", "mg glycinate", "magnesium diglycinate", "magnesium bis glycinate"),
        formula="MgC4H8N2O4", element="Mg",
        absorption=None,
        note="Widely sold on a gentleness claim. No human comparative absorption figure this system could cite.",
    ),
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium malate",
        aliases=("mg malate",), formula="MgC4H4O5", element="Mg",
        absorption=None,
        note="No human comparative absorption figure this system could cite.",
    ),
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium chloride",
        aliases=("mg chloride", "magnesium chloride hexahydrate"),
        formula="MgCl2", element="Mg",
        hydration="Anhydrous. The hexahydrate sold on most labels is 12.0% magnesium.",
        absorption=_absorption(
            FIROZ_2001,
            summary="Among the better-absorbed soluble salts in this comparison.",
            value="higher than oxide", unit="relative to oxide",
            confidence=Confidence.LOW,
            disagreement="Small study; figures across the magnesium literature are inconsistent.",
        ),
    ),
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium sulfate",
        aliases=("epsom salt", "epsom salts", "mg sulfate", "magnesium sulphate"),
        formula="MgSO4", element="Mg",
        hydration="Anhydrous. Epsom salt is the heptahydrate at 9.9% magnesium.",
        absorption=None,
        note="Mostly used externally or as a laxative rather than as an oral magnesium supplement.",
    ),
    Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium L-threonate",
        aliases=("magnesium threonate", "magtein", "mg l threonate"),
        formula="MgC8H14O10", element="Mg",
        absorption=None,
        note="Brain-magnesium claims rest on rodent work. No human comparative absorption figure this system could cite.",
    ),

    # --- Iron --------------------------------------------------------------
    Compound(
        key="iron", nutrient="Iron", form="ferrous sulfate",
        aliases=("ferrous sulphate", "iron sulfate", "fesO4", "ferrous sulphate dried"),
        formula="FeSO4", element="Fe",
        hydration="Anhydrous. The heptahydrate usual on labels is 20.1% iron; dried ferrous sulfate is about 30%.",
        absorption=_absorption(
            HALLBERG_1989,
            summary="The reference form against which other iron salts are compared.",
            value="about 10-15 on an empty stomach, much lower with food, phytate or tea",
            unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
            disagreement=(
                "Absorption depends far more on the person's iron status and the meal than on "
                "the salt. A depleted person may absorb several times what a replete person does."
            ),
        ),
    ),
    Compound(
        key="iron", nutrient="Iron", form="ferrous fumarate",
        aliases=("iron fumarate",), formula="FeC4H2O4", element="Fe",
        absorption=_absorption(
            HALLBERG_1989,
            summary="Treated as equivalent to ferrous sulfate in absorption.",
            value="about 100", unit="% relative to ferrous sulfate",
            confidence=Confidence.MEDIUM,
        ),
    ),
    Compound(
        key="iron", nutrient="Iron", form="ferrous gluconate",
        aliases=("iron gluconate",), formula="FeC12H22O14", element="Fe",
        hydration="Anhydrous. The dihydrate on many labels is 11.6% iron.",
        absorption=_absorption(
            HALLBERG_1989,
            summary="Comparable to ferrous sulfate, with a lower iron payload per gram.",
            value="about 89-100", unit="% relative to ferrous sulfate",
            confidence=Confidence.LOW,
            disagreement="Relative figures for gluconate vary between reviews and are rarely measured directly.",
        ),
    ),
    Compound(
        key="iron", nutrient="Iron", form="ferrous bisglycinate",
        aliases=("iron bisglycinate", "ferrous bis glycinate", "iron amino acid chelate", "ferrochel"),
        formula="FeC4H8N2O4", element="Fe",
        absorption=_absorption(
            LAYRISSE_2000,
            summary="Better absorbed than ferrous sulfate when taken with food, and less affected by phytate.",
            value="about 2-4 times ferrous sulfate with a phytate-rich meal", unit="ratio to ferrous sulfate",
            confidence=Confidence.MEDIUM,
            disagreement=(
                "The advantage is large with inhibitory meals and small or absent on an empty "
                "stomach, so a single ratio misrepresents it. Reported ratios range from roughly "
                "1.3 to 4 depending on the meal."
            ),
        ),
    ),
    Compound(
        key="iron", nutrient="Iron", form="carbonyl iron",
        aliases=("carbonyl iron", "elemental iron powder"),
        equivalent_percent=Decimal("98.0"),
        equivalent_note="Essentially elemental iron powder; purity is typically stated as 98% or higher.",
        absorption=None,
        note="Absorbed slowly and dependent on stomach acid. No comparative human figure this system could cite.",
    ),

    # --- Zinc --------------------------------------------------------------
    Compound(
        key="zinc", nutrient="Zinc", form="zinc oxide",
        aliases=("zn oxide",), formula="ZnO", element="Zn",
        absorption=_absorption(
            WEGMULLER_2014,
            summary="Absorbed less well than zinc citrate or gluconate in this comparison.",
            value="about 50 (citrate and gluconate about 61)", unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
            disagreement=(
                "Wegmüller found oxide lower than citrate and gluconate. Other work finds oxide "
                "comparable when taken with food, and the difference may matter little at "
                "supplement doses."
            ),
        ),
    ),
    Compound(
        key="zinc", nutrient="Zinc", form="zinc sulfate",
        aliases=("zinc sulphate", "zn sulfate"), formula="ZnSO4", element="Zn",
        hydration="Anhydrous. The monohydrate is 36.4% and the heptahydrate 22.7% zinc.",
        absorption=None,
        note="Long used in trials of zinc deficiency, but this system found no comparative absorption figure it could cite.",
    ),
    Compound(
        key="zinc", nutrient="Zinc", form="zinc gluconate",
        aliases=("zn gluconate",), formula="ZnC12H22O14", element="Zn",
        absorption=_absorption(
            WEGMULLER_2014,
            summary="Comparable to zinc citrate and better absorbed than oxide.",
            value="about 61", unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
        ),
    ),
    Compound(
        key="zinc", nutrient="Zinc", form="zinc picolinate",
        aliases=("zn picolinate",), formula="ZnC12H8N2O4", element="Zn",
        absorption=None,
        note=(
            "The often-quoted advantage traces to one small 1987 study. This system found no "
            "modern comparison it could cite, so the figure is not carried."
        ),
    ),
    Compound(
        key="zinc", nutrient="Zinc", form="zinc bisglycinate",
        aliases=("zinc glycinate", "zn bisglycinate", "zinc amino acid chelate"),
        formula="ZnC4H8N2O4", element="Zn",
        absorption=None,
        note="No human comparative absorption figure this system could cite.",
    ),

    # --- Calcium -----------------------------------------------------------
    Compound(
        key="calcium", nutrient="Calcium", form="calcium carbonate",
        aliases=("ca carbonate", "limestone", "oyster shell calcium", "coral calcium"),
        formula="CaCO3", element="Ca",
        absorption=_absorption(
            HEANEY_1999,
            summary="Absorbed well with a meal; needs stomach acid.",
            value="about 22-27 when taken with food", unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
            disagreement=(
                "Heaney found carbonate and citrate close when both are taken with a meal. "
                "Other work reports citrate roughly 22-27% better absorbed, particularly on an "
                "empty stomach or with low stomach acid. Both figures are in the literature."
            ),
        ),
    ),
    Compound(
        key="calcium", nutrient="Calcium", form="calcium citrate",
        aliases=("ca citrate", "calcium citrate malate"),
        formula="Ca3C12H18O18", element="Ca", element_atoms=3,
        hydration="Tetrahydrate, the usual supplement form. Anhydrous calcium citrate is 24.1% calcium.",
        absorption=_absorption(
            HEANEY_1999,
            summary="Absorbed without stomach acid, so it does not depend on being taken with food.",
            value="about 24; comparable to carbonate with a meal", unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
            disagreement="See calcium carbonate: whether citrate is meaningfully better is disputed.",
        ),
    ),
    Compound(
        key="calcium", nutrient="Calcium", form="calcium lactate",
        aliases=("ca lactate",), formula="CaC6H10O6", element="Ca",
        hydration="Anhydrous. The pentahydrate common on labels is 13.0% calcium.",
        absorption=None,
        note="No comparative human absorption figure this system could cite.",
    ),

    # --- Vitamin D ---------------------------------------------------------
    Compound(
        key="vitamin d", nutrient="Vitamin D", form="vitamin D3 (cholecalciferol)",
        aliases=("cholecalciferol", "vitamin d3", "vit d3", "d3"),
        absorption=_absorption(
            TRIPKOVIC_2012,
            summary="Raises blood 25-hydroxyvitamin D more effectively than D2.",
            value="more effective than D2, particularly as a bolus dose", unit="comparison",
            confidence=Confidence.HIGH,
        ),
    ),
    Compound(
        key="vitamin d", nutrient="Vitamin D", form="vitamin D2 (ergocalciferol)",
        aliases=("ergocalciferol", "vitamin d2", "vit d2", "d2"),
        absorption=_absorption(
            TRIPKOVIC_2012,
            summary="Raises blood 25-hydroxyvitamin D less effectively than D3.",
            value="less effective than D3", unit="comparison",
            confidence=Confidence.HIGH,
            disagreement="Daily dosing narrows the gap; the difference is clearest with intermittent large doses.",
        ),
    ),

    # --- Vitamin B12 -------------------------------------------------------
    Compound(
        key="vitamin b12", nutrient="Vitamin B12", form="cyanocobalamin",
        aliases=("cyanocobalamin", "vitamin b12", "b12", "cobalamin"),
        absorption=_absorption(
            EFSA_B12,
            summary="Uptake is capped by intrinsic factor, so only a small amount of any single dose is taken up.",
            value="roughly 1.5-2 micrograms absorbed per dose regardless of size",
            unit="micrograms per dose",
            confidence=Confidence.MEDIUM,
            disagreement="A small further fraction, often cited near 1%, is absorbed passively at high doses.",
        ),
    ),
    Compound(
        key="vitamin b12", nutrient="Vitamin B12", form="methylcobalamin",
        aliases=("methylcobalamin", "methyl b12", "mecobalamin"),
        absorption=None,
        note=(
            "Marketed as better retained than cyanocobalamin. This system found no human "
            "comparative absorption figure it could cite."
        ),
    ),
    Compound(
        key="vitamin b12", nutrient="Vitamin B12", form="adenosylcobalamin",
        aliases=("adenosylcobalamin", "dibencozide", "cobamamide"),
        absorption=None,
        note="No human comparative absorption figure this system could cite.",
    ),

    # --- Folate ------------------------------------------------------------
    Compound(
        key="folate", nutrient="Folate", form="folic acid",
        aliases=("folic acid", "pteroylglutamic acid", "vitamin b9"),
        absorption=_absorption(
            IOM_FOLATE,
            summary="Absorbed far better than the folate naturally present in food.",
            value="about 85 on an empty stomach; taken as 100 when defining folate equivalents",
            unit="% of the dose absorbed",
            confidence=Confidence.HIGH,
        ),
    ),
    Compound(
        key="folate", nutrient="Folate", form="L-5-methyltetrahydrofolate",
        aliases=("methylfolate", "5 mthf", "l methylfolate", "levomefolic acid", "metafolin"),
        absorption=_absorption(
            IOM_FOLATE,
            summary="Comparable to folic acid at raising blood folate.",
            value="comparable to folic acid", unit="comparison",
            confidence=Confidence.LOW,
            disagreement=(
                "Reported as equivalent in some trials and modestly better in others. The "
                "practical difference for most people is unclear."
            ),
        ),
    ),

    # --- Vitamin C ---------------------------------------------------------
    Compound(
        key="vitamin c", nutrient="Vitamin C", form="ascorbic acid",
        aliases=("ascorbic acid", "l ascorbic acid", "vitamin c", "vit c"),
        absorption=_absorption(
            LEVINE_1996,
            summary="Absorption falls sharply as the dose rises.",
            value="about 70-90 at 30-180 mg, below 50 above 1 g", unit="% of the dose absorbed",
            confidence=Confidence.HIGH,
        ),
    ),
    Compound(
        key="vitamin c", nutrient="Vitamin C", form="sodium ascorbate",
        aliases=("sodium ascorbate",),
        equivalent_percent=Decimal("88.9"),
        equivalent_note="Ascorbic acid equivalent by weight: 176.12 / 198.11.",
        absorption=_absorption(
            LEVINE_1996,
            summary="Behaves as ascorbic acid once dissolved; gentler on the stomach.",
            value="as ascorbic acid", unit="comparison",
            confidence=Confidence.MEDIUM,
            disagreement="Carries sodium, which matters for anyone limiting it.",
        ),
    ),
    Compound(
        key="vitamin c", nutrient="Vitamin C", form="ascorbyl palmitate",
        aliases=("ascorbyl palmitate", "fat soluble vitamin c"),
        absorption=None,
        note=(
            "Used mainly as an antioxidant in formulations. This system found no human evidence "
            "it could cite that it works as a vitamin C source."
        ),
    ),
    Compound(
        key="vitamin c", nutrient="Vitamin C", form="liposomal vitamin C",
        aliases=("liposomal vitamin c", "liposomal ascorbic acid"),
        absorption=None,
        note=(
            "Claims of far higher absorption are common in marketing. This system found no "
            "independent human comparison it could cite."
        ),
    ),

    # --- CoQ10 -------------------------------------------------------------
    Compound(
        key="coenzyme q10", nutrient="Coenzyme Q10", form="ubiquinone",
        aliases=("coenzyme q10", "coq10", "ubiquinone", "co q 10"),
        absorption=None,
        note=(
            "Poorly absorbed and highly dependent on the oil formulation it is carried in. "
            "This system found no figure it could cite that is independent of the product tested."
        ),
    ),
    Compound(
        key="coenzyme q10", nutrient="Coenzyme Q10", form="ubiquinol",
        aliases=("ubiquinol", "reduced coq10", "kaneka ubiquinol"),
        absorption=None,
        note=(
            "Marketed as substantially better absorbed than ubiquinone. The comparisons this "
            "system is aware of are manufacturer-linked, so no figure is carried."
        ),
    ),

    # --- Curcumin ----------------------------------------------------------
    Compound(
        key="curcumin", nutrient="Curcumin", form="curcumin (plain extract)",
        aliases=("curcumin", "turmeric extract", "curcuminoids", "haldi extract"),
        absorption=_absorption(
            SHOBA_1998,
            summary="Very poorly absorbed on its own.",
            value="very low; serum levels near or below detection at ordinary doses",
            unit="% of the dose absorbed",
            confidence=Confidence.MEDIUM,
        ),
    ),
    Compound(
        key="curcumin", nutrient="Curcumin", form="curcumin with piperine",
        aliases=("curcumin with piperine", "curcumin bioperine", "turmeric with black pepper"),
        absorption=_absorption(
            SHOBA_1998,
            summary="Piperine raises curcumin blood levels substantially.",
            value="about 2000 percent increase reported", unit="% increase versus plain curcumin",
            confidence=Confidence.LOW,
            disagreement=(
                "The 2000% figure comes from one small 1998 study in ten volunteers at a 2 g dose "
                "and is quoted far beyond what it can support. The direction is well accepted; "
                "the size is not."
            ),
        ),
    ),
    Compound(
        key="curcumin", nutrient="Curcumin", form="curcumin phospholipid complex",
        aliases=("meriva", "curcumin phytosome", "curcumin phospholipid complex"),
        absorption=_absorption(
            CUOMO_2011,
            summary="A lecithin formulation raised total curcuminoid absorption markedly.",
            value="about 29 times plain extract", unit="ratio to plain extract",
            confidence=Confidence.LOW,
            disagreement=(
                "Measured by the formulation's manufacturer. The ratio also depends heavily on "
                "which curcuminoid is measured."
            ),
        ),
    ),

    # --- Omega-3 -----------------------------------------------------------
    Compound(
        key="omega 3", nutrient="Omega-3", form="ethyl ester (EE)",
        aliases=("ethyl ester", "omega 3 ethyl ester", "epa ethyl ester", "fish oil concentrate"),
        absorption=_absorption(
            DYERBERG_2010,
            summary="Absorbed less well than the triglyceride form.",
            value="about 73 relative to natural fish oil triglyceride", unit="% relative to triglyceride",
            confidence=Confidence.MEDIUM,
            disagreement="Taking it with a fatty meal narrows the gap considerably.",
        ),
    ),
    Compound(
        key="omega 3", nutrient="Omega-3", form="triglyceride (rTG or natural TG)",
        aliases=("triglyceride", "rtg", "re esterified triglyceride", "natural triglyceride", "fish oil tg"),
        absorption=_absorption(
            DYERBERG_2010,
            summary="Better absorbed than the ethyl ester form.",
            value="about 124 relative to natural fish oil", unit="% relative to natural fish oil",
            confidence=Confidence.MEDIUM,
        ),
    ),
)


def compounds_for(key: str) -> tuple[Compound, ...]:
    return tuple(c for c in COMPOUNDS if c.key == key)


def raw_aliases() -> tuple[tuple[str, str, str], ...]:
    """Every label spelling in this file as ``(alias, key, nutrient)``.

    Returned unnormalised so this module never has to import the engine, which
    would be a cycle: the engine folds these into REVIEWED_ALIASES at import and
    normalises them with its own function, keeping one normaliser in the system.
    """
    return tuple(
        (alias, compound.key, compound.nutrient)
        for compound in COMPOUNDS
        for alias in (*compound.aliases, compound.form)
    )
