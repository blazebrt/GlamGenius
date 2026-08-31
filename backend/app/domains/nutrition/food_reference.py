"""Published thresholds the food score will use.

Three sets, all release-owned reference data:

1. **Nutrient thresholds** — what counts as high or low, per 100 g.
2. **Additive risk tiers** — INS numbers, what each does, and how it is treated.
3. **Culinary ingredients** — the things that must never receive a letter grade,
   because they are ingredients rather than foods.

**Nothing here has been opened and confirmed by the system that wrote it.** The
environment has no outbound network access: FSSAI, the National Institute of
Nutrition, the UK Food Standards Agency and EFSA are all unreachable from it.
So a citation here is a pointer for a person to check, never a claim that the
number has been read off the source.

That constraint decides the shape of set 1. The UK FSA front-of-pack values are
long-published, stable and widely reproduced, so they are carried in full. The
specific per-100 g figures in ICMR-NIN Table 15.1 are **not** carried: writing
plausible numbers under that citation would produce exactly the kind of
precise-looking, authoritative-looking, unverifiable entry the evidence rule
exists to prevent. Those rows are loaded as NOT_ENOUGH_INFORMATION naming
precisely what has to be transcribed from the document, which is more useful
than an invented figure and is the only defensible option.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domains.evidence.grading import Confidence

TIER_CLINICAL = "clinically_studied"
TIER_NOT_ENOUGH = "not_enough_information"
TIER_AVOID = "avoid"


@dataclass(frozen=True)
class Source:
    """One publication a rule can rest on.

    ``url`` is a condition of use rather than a convenience: a rule that lowers
    somebody's grade has to show them what it is based on, so a source with
    nothing to open cannot support a negative claim. ``publisher`` is stated
    rather than parsed out of the citation, because splitting a citation string
    on its first full stop is a guess that breaks on the first author list.
    """

    name: str
    url: str
    identifier: str
    publisher: str = ""


# --- Sources ---------------------------------------------------------------
FSA_FOP = Source(
    "UK Food Standards Agency / Department of Health. Guide to creating a front of pack "
    "(FoP) nutrition label for pre-packed products sold through retail outlets. 2016.",
    "https://www.gov.uk/government/publications/front-of-pack-nutrition-labelling-guidance",
    "UK-FSA-FOP-2016",
    "UK Food Standards Agency",
)
ICMR_NIN_2024 = Source(
    "ICMR-NIN. Dietary Guidelines for Indians, 2024. National Institute of Nutrition, "
    "Hyderabad. Table 15.1 (label reading thresholds).",
    "https://www.nin.res.in/dietaryguidelines/pdfjs/locale/DGI07052024P.pdf",
    "ICMR-NIN-DGI-2024",
    "ICMR-National Institute of Nutrition",
)
FSSAI_LABELLING = Source(
    "FSSAI. Food Safety and Standards (Labelling and Display) Regulations, 2020, and the "
    "subsequent front-of-pack nutrition labelling amendments.",
    "https://www.fssai.gov.in/cms/food-safety-and-standards-regulations.php",
    "FSSAI-LABELLING-2020",
    "FSSAI",
)
FSSAI_ADDITIVES = Source(
    "FSSAI. Food Safety and Standards (Food Products Standards and Food Additives) "
    "Regulations, 2011, as amended.",
    "https://www.fssai.gov.in/cms/food-safety-and-standards-regulations.php",
    "FSSAI-ADDITIVES-2011",
    "FSSAI",
)
FSSAI_TRANSFAT = Source(
    "FSSAI. Food Safety and Standards (Prohibition and Restrictions on Sales) Amendment "
    "Regulations, 2021 — industrial trans fatty acids limit.",
    "https://www.fssai.gov.in/cms/food-safety-and-standards-regulations.php",
    "FSSAI-TFA-2021",
    "FSSAI",
)
FSSAI_BROMATE = Source(
    "FSSAI. Removal of potassium bromate from the list of permitted food additives, "
    "notification dated 20 June 2016.",
    "https://www.fssai.gov.in/cms/food-safety-and-standards-regulations.php",
    "FSSAI-BROMATE-2016",
    "FSSAI",
)
MONTEIRO_NOVA_2019 = Source(
    "Monteiro CA, Cannon G, Levy RB, et al. Ultra-processed foods: what they are and how "
    "to identify them. Public Health Nutrition 22(5):936-941, 2019.",
    # The DOI resolver rather than a publisher landing page: it is the citation's
    # canonical address and does not move when the publisher reorganises.
    "https://doi.org/10.1017/S1368980018003762",
    "MONTEIRO-NOVA-2019",
    "Public Health Nutrition (Cambridge University Press)",
)
WHO_SALT = Source(
    "World Health Organization. Guideline: Sodium intake for adults and children. 2012.",
    "https://www.who.int/publications/i/item/9789241504836", "WHO-SODIUM-2012",
    "World Health Organization",
)


# ===========================================================================
# 1. Nutrient thresholds
# ===========================================================================
@dataclass(frozen=True)
class Threshold:
    """One nutrient's banding, per 100 g, from one authority."""

    nutrient: str
    basis: str                     # "solid" or "drink"
    low_max: Decimal | None        # <= this is low
    high_min: Decimal | None       # > this is high
    unit: str
    source: Source
    confidence: Confidence
    note: str | None = None
    disagreement: str | None = None

    @property
    def tier(self) -> str:
        return TIER_NOT_ENOUGH if self.high_min is None and self.low_max is None else TIER_CLINICAL


def _d(value: str) -> Decimal:
    return Decimal(value)


THRESHOLDS: tuple[Threshold, ...] = (
    # --- UK FSA front-of-pack, solids, per 100 g -------------------------
    Threshold("total fat", "solid", _d("3.0"), _d("17.5"), "g per 100 g", FSA_FOP, Confidence.HIGH,
              note="Low is 3.0 g or less; high is above 17.5 g; between the two is medium."),
    Threshold("saturated fat", "solid", _d("1.5"), _d("5.0"), "g per 100 g", FSA_FOP, Confidence.HIGH),
    Threshold("total sugars", "solid", _d("5.0"), _d("22.5"), "g per 100 g", FSA_FOP, Confidence.HIGH,
              note="Total sugars, not added sugars. The FSA scheme does not band added sugar separately.",
              disagreement="ICMR-NIN and FSSAI both frame their sugar guidance around ADDED sugar. "
                           "A score built on the FSA bands is therefore measuring a different quantity "
                           "from one built on the Indian guidance, and the two are not interchangeable."),
    Threshold("salt", "solid", _d("0.3"), _d("1.5"), "g per 100 g", FSA_FOP, Confidence.HIGH,
              note="Sodium multiplied by 2.5 gives salt. Indian labels usually declare sodium, "
                   "so a conversion is needed before these bands apply."),
    Threshold("sodium", "solid", _d("0.12"), _d("0.6"), "g per 100 g", FSA_FOP, Confidence.HIGH,
              note="Derived arithmetically from the FSA salt bands by dividing by 2.5. "
                   "Not separately published as sodium."),
    # --- UK FSA, drinks, per 100 ml --------------------------------------
    Threshold("total fat", "drink", _d("1.5"), _d("8.75"), "g per 100 ml", FSA_FOP, Confidence.HIGH),
    Threshold("saturated fat", "drink", _d("0.75"), _d("2.5"), "g per 100 ml", FSA_FOP, Confidence.HIGH),
    Threshold("total sugars", "drink", _d("2.5"), _d("11.25"), "g per 100 ml", FSA_FOP, Confidence.HIGH),
    Threshold("salt", "drink", _d("0.3"), _d("0.75"), "g per 100 ml", FSA_FOP, Confidence.HIGH),

    # --- ICMR-NIN Table 15.1 — deliberately not filled in ----------------
    # The task names this table and it exists. The system that wrote this file
    # cannot open it, and inventing its numbers would be worse than leaving
    # them out, so each row states exactly what must be transcribed.
    Threshold("sodium", "solid", None, None, "g per 100 g", ICMR_NIN_2024, Confidence.LOW,
              note="ICMR-NIN Dietary Guidelines for Indians 2024, Table 15.1: transcribe the "
                   "per-100 g sodium threshold. Not carried here because it could not be read."),
    Threshold("added sugar", "solid", None, None, "g per 100 g", ICMR_NIN_2024, Confidence.LOW,
              note="ICMR-NIN 2024 Table 15.1: transcribe the per-100 g added sugar threshold."),
    Threshold("total sugar", "solid", None, None, "g per 100 g", ICMR_NIN_2024, Confidence.LOW,
              note="ICMR-NIN 2024 Table 15.1: transcribe the per-100 g total sugar threshold."),
    Threshold("added fat", "solid", None, None, "g per 100 g", ICMR_NIN_2024, Confidence.LOW,
              note="ICMR-NIN 2024 Table 15.1: transcribe the per-100 g added fat threshold."),
    Threshold("total fat", "solid", None, None, "g per 100 g", ICMR_NIN_2024, Confidence.LOW,
              note="ICMR-NIN 2024 Table 15.1: transcribe the per-100 g total fat threshold. "
                   "Compare with the FSA figure of 17.5 g and record both if they differ."),

    # --- FSSAI added-sugar energy rule -----------------------------------
    Threshold("added sugar (share of energy)", "solid", None, None, "% of total energy",
              FSSAI_LABELLING, Confidence.LOW,
              note="The rule to check: added sugar above 10% of total energy per 100 g or 100 ml "
                   "attracts red coding. Confirm the current wording and whether it is in force.",
              disagreement="FSSAI's front-of-pack proposals have been revised repeatedly and "
                           "contested publicly. Whether a 10%-of-energy red-coding rule is "
                           "currently notified, still in draft, or superseded could not be "
                           "established here. Treat as unconfirmed until read."),
)


# ===========================================================================
# 2. Additive risk tiers
# ===========================================================================
@dataclass(frozen=True)
class Additive:
    """One additive, what it does, and how the product treats it."""

    ins: str | None
    name: str
    function: str                  # plain language, per LEGAL_RULES.md
    tier: str                      # green | amber | red | black
    source: Source
    confidence: Confidence
    note: str | None = None
    disagreement: str | None = None


TIER_GREEN = "green"
TIER_AMBER = "amber"
TIER_RED = "red"
TIER_BLACK = "black"

ADDITIVES: tuple[Additive, ...] = (
    Additive("924", "Potassium bromate", "Made bread dough rise higher and look whiter.",
             TIER_BLACK, FSSAI_BROMATE, Confidence.MEDIUM,
             note="Removed from India's permitted list in 2016. Should not appear on any "
                  "Indian label; if it does, that is the finding."),
    Additive(None, "Industrial trans fat (partially hydrogenated oils)",
             "A hardened oil that gives long shelf life and a firm texture.",
             TIER_BLACK, FSSAI_TRANSFAT, Confidence.MEDIUM,
             note="The limit to confirm: not more than 2% by mass of the total oils and fats, "
                  "in force from January 2022.",
             disagreement="The limit stepped down over several years — 5%, then 3%, then 2%. "
                          "Confirm which figure applies to the product's date of manufacture."),
    Additive("102", "Tartrazine", "A yellow colour.", TIER_AMBER, FSSAI_ADDITIVES, Confidence.LOW,
             note="Permitted in India within limits. Carries a warning about effects on "
                  "attention in children in the EU.",
             disagreement="EFSA has re-evaluated tartrazine and did not conclude it unsafe at "
                          "permitted levels, while the EU still requires the children's warning. "
                          "Both positions need recording once read."),
    Additive("110", "Sunset Yellow FCF", "An orange-yellow colour.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW, note="Same warning category as tartrazine in the EU."),
    Additive("122", "Carmoisine", "A red colour.", TIER_AMBER, FSSAI_ADDITIVES, Confidence.LOW),
    Additive("129", "Allura Red AC", "A red colour.", TIER_AMBER, FSSAI_ADDITIVES, Confidence.LOW),
    Additive("621", "Monosodium glutamate", "Adds a savoury taste.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW,
             note="Permitted; labels must declare it. Indian rules restrict its use in some "
                  "foods and for young children — confirm which."),
    Additive("211", "Sodium benzoate", "Stops mould and yeast growing.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW,
             note="Can form benzene in the presence of vitamin C in drinks. Worth checking "
                  "whether that combination is what the label shows."),
    Additive("223", "Sodium metabisulphite", "Keeps colour and stops spoilage.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW,
             note="Must be declared because some people react to sulphites."),
    Additive("330", "Citric acid", "Adds sourness and helps keep food fresh.", TIER_GREEN,
             FSSAI_ADDITIVES, Confidence.LOW),
    Additive("500", "Sodium bicarbonate", "Raising agent — baking soda.", TIER_GREEN,
             FSSAI_ADDITIVES, Confidence.LOW),
    Additive("322", "Lecithin", "Keeps oil and water mixed.", TIER_GREEN,
             FSSAI_ADDITIVES, Confidence.LOW),
    Additive("415", "Xanthan gum", "Thickens.", TIER_GREEN, FSSAI_ADDITIVES, Confidence.LOW),
    Additive("471", "Mono- and diglycerides of fatty acids", "Keeps oil and water mixed.",
             TIER_GREEN, FSSAI_ADDITIVES, Confidence.LOW,
             note="Can be made from partially hydrogenated oil, which would carry trans fat. "
                  "Whether that matters in practice needs checking."),
    Additive("951", "Aspartame", "A sweetener used instead of sugar.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW,
             disagreement="IARC classified aspartame as possibly carcinogenic to humans in 2023 "
                          "while JECFA kept the acceptable daily intake unchanged. Both "
                          "positions must be recorded; they are not the same claim."),
    Additive("955", "Sucralose", "A sweetener used instead of sugar.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW),
    Additive("950", "Acesulfame potassium", "A sweetener used instead of sugar.", TIER_AMBER,
             FSSAI_ADDITIVES, Confidence.LOW),
    Additive("320", "Butylated hydroxyanisole (BHA)", "Stops fat going rancid.", TIER_RED,
             FSSAI_ADDITIVES, Confidence.LOW,
             disagreement="Listed as reasonably anticipated to be a human carcinogen by the US "
                          "National Toxicology Program, yet permitted at low levels in India and "
                          "the EU. Record both."),
    Additive("319", "Tertiary butylhydroquinone (TBHQ)", "Stops fat going rancid.", TIER_RED,
             FSSAI_ADDITIVES, Confidence.LOW),
)


# ===========================================================================
# 3. Culinary ingredients — never graded
# ===========================================================================
@dataclass(frozen=True)
class CulinaryIngredient:
    """Something used to cook with, which a letter grade would misrepresent."""

    key: str
    name: str
    aliases: tuple[str, ...]
    why_never_graded: str
    daily_guidance: str | None = None
    guidance_source: Source | None = None
    guidance_confidence: Confidence | None = None
    disagreement: str | None = None


_WHY_FAT = ("A cooking fat, not a food. Graded per 100 g it would always score badly, "
            "which says nothing useful about a spoonful used to cook a meal.")
_WHY_SWEET = ("A sweetener, not a food. Per 100 g it is close to pure sugar by definition, "
              "so a grade would only restate what it is.")
_WHY_SALT = "Salt is salt. A grade would only restate what it is."

CULINARY_INGREDIENTS: tuple[CulinaryIngredient, ...] = (
    CulinaryIngredient("ghee", "Ghee", ("ghee", "clarified butter", "desi ghee", "tup"), _WHY_FAT,
                       daily_guidance="Counted within total visible fat. Transcribe the ICMR-NIN "
                                      "2024 figure for visible fat or oil per day.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW),
    CulinaryIngredient("butter", "Butter", ("butter", "makhan", "white butter"), _WHY_FAT,
                       daily_guidance="Counted within total visible fat; see ghee.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW),
    CulinaryIngredient("cooking oil", "Cooking oil",
                       ("cooking oil", "refined oil", "sunflower oil", "mustard oil", "sarson ka tel",
                        "groundnut oil", "coconut oil", "rice bran oil", "sesame oil", "til oil",
                        "soybean oil", "olive oil", "palm oil", "vegetable oil"), _WHY_FAT,
                       daily_guidance="Counted within total visible fat; see ghee.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW,
                       disagreement="ICMR-NIN's visible-fat figure varies with age and activity, "
                                    "so a single number would misrepresent it. Transcribe the range."),
    CulinaryIngredient("vanaspati", "Vanaspati", ("vanaspati", "dalda", "hydrogenated vegetable oil"),
                       _WHY_FAT + " Its trans fat content is the thing worth reporting, not a grade.",
                       daily_guidance=None),
    CulinaryIngredient("salt", "Salt", ("salt", "namak", "table salt", "iodised salt", "sendha namak",
                                        "kala namak", "rock salt", "sea salt"), _WHY_SALT,
                       daily_guidance="Less than 5 g of salt a day for adults.",
                       guidance_source=WHO_SALT, guidance_confidence=Confidence.MEDIUM,
                       disagreement="WHO's figure is under 5 g of salt, equivalent to under 2 g of "
                                    "sodium. Confirm whether ICMR-NIN 2024 states the same number "
                                    "and record it if it differs."),
    CulinaryIngredient("sugar", "Sugar", ("sugar", "chini", "cane sugar", "white sugar",
                                          "brown sugar", "caster sugar"), _WHY_SWEET,
                       daily_guidance="Transcribe the ICMR-NIN 2024 limit for added sugar as a "
                                      "share of total energy.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW,
                       disagreement="WHO recommends under 10% of energy from free sugars with a "
                                    "conditional further recommendation of under 5%. Whether "
                                    "ICMR-NIN 2024 matches needs reading."),
    CulinaryIngredient("jaggery", "Jaggery", ("jaggery", "gur", "gud", "bella", "vellam"), _WHY_SWEET,
                       daily_guidance="Counted as added sugar; see sugar.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW,
                       disagreement="Widely believed healthier than white sugar because it retains "
                                    "traces of minerals. The quantities involved are very small. "
                                    "Any claim either way needs a source before it is made."),
    CulinaryIngredient("honey", "Honey", ("honey", "shahad", "madhu"), _WHY_SWEET,
                       daily_guidance="Counted as added sugar; see sugar.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW),
    CulinaryIngredient("shakkar", "Shakkar", ("shakkar", "shakkar powder", "unrefined sugar"),
                       _WHY_SWEET, daily_guidance="Counted as added sugar; see sugar.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW),
    CulinaryIngredient("misri", "Misri", ("misri", "mishri", "rock sugar", "crystal sugar"),
                       _WHY_SWEET, daily_guidance="Counted as added sugar; see sugar.",
                       guidance_source=ICMR_NIN_2024, guidance_confidence=Confidence.LOW),
    CulinaryIngredient("vinegar", "Vinegar", ("vinegar", "sirka", "apple cider vinegar",
                                              "white vinegar", "synthetic vinegar"),
                       "A condiment used in small amounts. Per 100 g it is mostly water and acid, "
                       "so a grade would be meaningless either way.",
                       daily_guidance=None),
)


def culinary_keys() -> frozenset[str]:
    """Every alias that must return NOT_GRADED rather than a letter."""
    return frozenset(
        alias.lower()
        for ingredient in CULINARY_INGREDIENTS
        for alias in (*ingredient.aliases, ingredient.name)
    )


def is_culinary_ingredient(name: str) -> bool:
    """True when this must never receive a letter grade."""
    return (name or "").strip().lower() in culinary_keys()
