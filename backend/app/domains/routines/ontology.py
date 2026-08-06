"""The reviewed ingredient ontology and rule set.

This file is **reference data, not logic**. It is the single written-down source
for what the engine knows about cosmetic ingredients, and it is seeded into the
``ingredients``, ``ingredient_aliases`` and ``*_rules`` tables so every warning
the product shows can point at a row a human reviewed.

Three rules govern what may be added here.

1. **Cosmetic, never clinical.** Everything below is about how products behave
   next to each other — texture, irritation, buildup, oxidation. Nothing here
   describes, names or treats a condition. A user question that needs a
   clinician gets the boundary in ``safety.py``, not a rule.

2. **Honest severity.** ``avoid`` is reserved for things the user themselves
   declared (an allergy). Widely repeated claims that the evidence does not
   actually support are recorded as ``info`` with an ``evidence_note`` saying
   so, rather than being quietly dropped or promoted to a warning. Telling
   someone a myth is a real problem is its own kind of harm.

3. **No model may add to this file.** The AI explains what the rules produced.
   It is never asked what is safe, and its output cannot introduce a rule —
   warnings are built from rows, keyed by ``rule_id``.
"""
from __future__ import annotations

from dataclasses import dataclass

ONTOLOGY_VERSION = "phase6-v1"

# --- Severity ---------------------------------------------------------------
SEVERITY_INFO = "info"        # worth knowing; not a problem
SEVERITY_CAUTION = "caution"  # commonly irritating together; separate or patch test
SEVERITY_AVOID = "avoid"      # the user told us to avoid this
SEVERITIES = (SEVERITY_INFO, SEVERITY_CAUTION, SEVERITY_AVOID)


@dataclass(frozen=True)
class Ingredient:
    key: str
    display_name: str
    inci_name: str | None
    family: str
    summary: str
    aliases: tuple[str, ...] = ()
    # What it is commonly used for, in plain non-clinical words.
    common_use: str = ""


# --- Families ---------------------------------------------------------------
FAMILY_RETINOID = "retinoid"
FAMILY_AHA = "aha"
FAMILY_BHA = "bha"
FAMILY_VITAMIN_C = "vitamin_c"
FAMILY_NIACINAMIDE = "niacinamide"
FAMILY_BENZOYL_PEROXIDE = "benzoyl_peroxide"
FAMILY_AZELAIC = "azelaic"
FAMILY_HUMECTANT = "humectant"
FAMILY_CERAMIDE = "ceramide"
FAMILY_OCCLUSIVE = "occlusive"
FAMILY_EMOLLIENT = "emollient"
FAMILY_SUNSCREEN = "sunscreen_filter"
FAMILY_ANTIOXIDANT = "antioxidant"
FAMILY_SURFACTANT = "surfactant"
FAMILY_PROTEIN = "protein"
FAMILY_SILICONE = "silicone"
FAMILY_CONDITIONING = "cationic_conditioner"
FAMILY_OIL = "oil"
FAMILY_SCALP_SOOTHER = "scalp_soother"
FAMILY_FRAGRANCE = "fragrance"
FAMILY_ESSENTIAL_OIL = "essential_oil"
FAMILY_ALCOHOL = "drying_alcohol"
FAMILY_CLAY = "clay"


def _ing(key: str, display: str, inci: str | None, family: str, summary: str, aliases: tuple[str, ...] = (), common_use: str = "") -> Ingredient:
    return Ingredient(key=key, display_name=display, inci_name=inci, family=family, summary=summary, aliases=aliases, common_use=common_use)


# --- The ontology -----------------------------------------------------------
# Curated, widely documented cosmetic ingredients. Aliases exist because labels
# print INCI names, marketing names and abbreviations interchangeably, and a
# parser that only knows one of them silently sees an empty shelf.
INGREDIENTS: tuple[Ingredient, ...] = (
    # Retinoids
    _ing("retinol", "Retinol", "Retinol", FAMILY_RETINOID,
         "A vitamin A derivative used in evening products.",
         ("vitamin a", "retinyl alcohol"), "Commonly used for skin texture and tone over time."),
    _ing("retinaldehyde", "Retinaldehyde", "Retinal", FAMILY_RETINOID,
         "A vitamin A derivative, a step along from retinol.",
         ("retinal", "retinaldehyde"), "Used in evening products."),
    _ing("retinyl_palmitate", "Retinyl palmitate", "Retinyl Palmitate", FAMILY_RETINOID,
         "A gentler vitamin A ester.", ("retinyl palmitate",), "Used in evening products."),
    _ing("adapalene", "Adapalene", "Adapalene", FAMILY_RETINOID,
         "A retinoid sold over the counter in some markets.",
         ("differin",), "Follow the product label and a qualified professional's guidance."),

    # Exfoliating acids
    _ing("glycolic_acid", "Glycolic acid", "Glycolic Acid", FAMILY_AHA,
         "A small alpha hydroxy acid.", ("aha", "glycolic"), "Used for surface exfoliation."),
    _ing("lactic_acid", "Lactic acid", "Lactic Acid", FAMILY_AHA,
         "A larger, gentler alpha hydroxy acid.", ("lactic",), "Used for gentle exfoliation."),
    _ing("mandelic_acid", "Mandelic acid", "Mandelic Acid", FAMILY_AHA,
         "A large-molecule alpha hydroxy acid.", ("mandelic",), "Used for gentle exfoliation."),
    _ing("salicylic_acid", "Salicylic acid", "Salicylic Acid", FAMILY_BHA,
         "An oil-soluble beta hydroxy acid.", ("bha", "salicylic", "beta hydroxy acid"),
         "Common in products for oily-looking or congested-looking skin."),

    # Brightening / antioxidant
    _ing("ascorbic_acid", "Vitamin C (L-ascorbic acid)", "Ascorbic Acid", FAMILY_VITAMIN_C,
         "The most studied form of topical vitamin C.",
         ("l-ascorbic acid", "vitamin c", "l ascorbic acid"), "Used in morning products for tone."),
    _ing("sodium_ascorbyl_phosphate", "Vitamin C (sodium ascorbyl phosphate)", "Sodium Ascorbyl Phosphate", FAMILY_VITAMIN_C,
         "A more stable vitamin C derivative.", ("sap", "sodium ascorbyl phosphate"), "Used for tone."),
    _ing("niacinamide", "Niacinamide", "Niacinamide", FAMILY_NIACINAMIDE,
         "A form of vitamin B3.", ("vitamin b3", "nicotinamide"),
         "Widely used and generally well tolerated."),
    _ing("vitamin_e", "Vitamin E", "Tocopherol", FAMILY_ANTIOXIDANT,
         "A fat-soluble antioxidant, often paired with vitamin C.",
         ("tocopherol", "tocopheryl acetate"), "Used to support formula stability and skin feel."),
    _ing("ferulic_acid", "Ferulic acid", "Ferulic Acid", FAMILY_ANTIOXIDANT,
         "An antioxidant used to stabilise vitamin C formulas.", ("ferulic",), ""),

    # Blemish-oriented
    _ing("benzoyl_peroxide", "Benzoyl peroxide", "Benzoyl Peroxide", FAMILY_BENZOYL_PEROXIDE,
         "A widely available ingredient for blemish-prone skin.", ("bpo", "benzoyl peroxide"),
         "Follow the product label."),
    _ing("azelaic_acid", "Azelaic acid", "Azelaic Acid", FAMILY_AZELAIC,
         "A gentle acid used for tone and texture.", ("azelaic",), ""),

    # Hydration and barrier
    _ing("hyaluronic_acid", "Hyaluronic acid", "Sodium Hyaluronate", FAMILY_HUMECTANT,
         "A humectant that holds water.", ("ha", "sodium hyaluronate", "hyaluronan"), "Used for hydration."),
    _ing("glycerin", "Glycerin", "Glycerin", FAMILY_HUMECTANT,
         "One of the most reliable humectants there is.", ("glycerol", "glycerine"), "Used for hydration."),
    _ing("panthenol", "Panthenol", "Panthenol", FAMILY_HUMECTANT,
         "Provitamin B5, a humectant and soother.", ("pro-vitamin b5", "provitamin b5", "vitamin b5"), ""),
    _ing("urea", "Urea", "Urea", FAMILY_HUMECTANT, "A humectant, and at higher levels a softener.", ("carbamide",), ""),
    _ing("ceramides", "Ceramides", "Ceramide NP", FAMILY_CERAMIDE,
         "Lipids that are part of the skin's own surface.",
         ("ceramide", "ceramide np", "ceramide ap", "ceramide eop"), "Used in barrier-supporting products."),
    _ing("squalane", "Squalane", "Squalane", FAMILY_EMOLLIENT,
         "A light, stable emollient.", ("squalane",), ""),
    _ing("shea_butter", "Shea butter", "Butyrospermum Parkii Butter", FAMILY_OCCLUSIVE,
         "A rich butter used to seal moisture in.", ("shea", "butyrospermum parkii"), ""),
    _ing("petrolatum", "Petrolatum", "Petrolatum", FAMILY_OCCLUSIVE,
         "A very effective occlusive.", ("petroleum jelly", "white petrolatum"), ""),
    _ing("dimethicone", "Dimethicone", "Dimethicone", FAMILY_SILICONE,
         "A silicone that smooths and seals.", ("silicone",), ""),

    # Sunscreen filters
    _ing("zinc_oxide", "Zinc oxide", "Zinc Oxide", FAMILY_SUNSCREEN,
         "A mineral sunscreen filter.", ("zno",), "Used in sunscreens."),
    _ing("titanium_dioxide", "Titanium dioxide", "Titanium Dioxide", FAMILY_SUNSCREEN,
         "A mineral sunscreen filter.", ("tio2",), "Used in sunscreens."),
    _ing("avobenzone", "Avobenzone", "Butyl Methoxydibenzoylmethane", FAMILY_SUNSCREEN,
         "An organic sunscreen filter.", ("butyl methoxydibenzoylmethane",), "Used in sunscreens."),

    # Cleansing
    _ing("sodium_laureth_sulfate", "Sodium laureth sulfate", "Sodium Laureth Sulfate", FAMILY_SURFACTANT,
         "A common cleansing surfactant.", ("sles",), ""),
    _ing("sodium_lauryl_sulfate", "Sodium lauryl sulfate", "Sodium Lauryl Sulfate", FAMILY_SURFACTANT,
         "A stronger cleansing surfactant.", ("sls",), ""),
    _ing("cocamidopropyl_betaine", "Cocamidopropyl betaine", "Cocamidopropyl Betaine", FAMILY_SURFACTANT,
         "A milder co-surfactant.", ("capb",), ""),
    _ing("kaolin", "Kaolin clay", "Kaolin", FAMILY_CLAY, "An absorbent clay.", ("china clay",), ""),

    # Hair
    _ing("hydrolysed_protein", "Hydrolysed protein", "Hydrolyzed Wheat Protein", FAMILY_PROTEIN,
         "Small protein fragments used in hair products.",
         ("hydrolyzed wheat protein", "hydrolysed wheat protein", "hydrolyzed keratin",
          "hydrolysed keratin", "hydrolyzed silk", "keratin"),
         "Used in strengthening hair products."),
    _ing("amino_acids", "Amino acids", "Arginine", FAMILY_PROTEIN,
         "Protein building blocks used in hair products.", ("arginine", "cysteine"), ""),
    _ing("behentrimonium_chloride", "Behentrimonium chloride", "Behentrimonium Chloride", FAMILY_CONDITIONING,
         "A conditioning agent that helps detangling.", ("btmc", "behentrimonium"), ""),
    _ing("cetrimonium_chloride", "Cetrimonium chloride", "Cetrimonium Chloride", FAMILY_CONDITIONING,
         "A conditioning and detangling agent.", ("cetrimonium",), ""),
    _ing("coconut_oil", "Coconut oil", "Cocos Nucifera Oil", FAMILY_OIL,
         "A traditional pre-wash hair oil in India.", ("cocos nucifera", "nariyal tel"), ""),
    _ing("argan_oil", "Argan oil", "Argania Spinosa Kernel Oil", FAMILY_OIL,
         "A light finishing oil.", ("argania spinosa",), ""),
    _ing("amla", "Amla", "Phyllanthus Emblica Fruit Extract", FAMILY_ANTIOXIDANT,
         "Indian gooseberry, long used in hair oils.", ("indian gooseberry", "phyllanthus emblica"), ""),
    _ing("zinc_pyrithione", "Zinc pyrithione", "Zinc Pyrithione", FAMILY_SCALP_SOOTHER,
         "A scalp-care ingredient found in many shampoos.", ("zpt", "pyrithione zinc"),
         "Follow the product label."),
    _ing("ketoconazole", "Ketoconazole", "Ketoconazole", FAMILY_SCALP_SOOTHER,
         "A scalp-care ingredient available in some shampoos.", (),
         "Follow the product label and a qualified professional's guidance."),
    _ing("tea_tree_oil", "Tea tree oil", "Melaleuca Alternifolia Leaf Oil", FAMILY_ESSENTIAL_OIL,
         "An essential oil used in scalp products.", ("melaleuca", "tea tree"), ""),

    # Things worth flagging for sensitivity
    _ing("fragrance", "Fragrance", "Parfum", FAMILY_FRAGRANCE,
         "Added scent. A common trigger for people who react to products.",
         ("parfum", "perfume", "aroma"), ""),
    _ing("denatured_alcohol", "Denatured alcohol", "Alcohol Denat.", FAMILY_ALCOHOL,
         "A fast-evaporating alcohol used for a light feel.",
         ("alcohol denat", "sd alcohol", "ethanol"), ""),
    _ing("menthol", "Menthol", "Menthol", FAMILY_ESSENTIAL_OIL,
         "Gives a cooling feel; can sting on compromised skin.", ("peppermint oil",), ""),
)

INGREDIENT_BY_KEY: dict[str, Ingredient] = {row.key: row for row in INGREDIENTS}


def alias_index() -> dict[str, str]:
    """Every searchable spelling mapped to its ingredient key.

    Longest-first matching happens in the parser; this is just the lookup.
    """
    index: dict[str, str] = {}
    for row in INGREDIENTS:
        index[row.display_name.lower()] = row.key
        index[row.key.replace("_", " ")] = row.key
        if row.inci_name:
            index[row.inci_name.lower()] = row.key
        for alias in row.aliases:
            index[alias.lower()] = row.key
    return index


# --- Pairwise rules ---------------------------------------------------------


@dataclass(frozen=True)
class CompatibilityRule:
    rule_id: str
    family_a: str
    family_b: str
    severity: str
    headline: str
    guidance: str
    evidence_note: str


COMPATIBILITY_RULES: tuple[CompatibilityRule, ...] = (
    CompatibilityRule(
        "rule.retinoid_aha", FAMILY_RETINOID, FAMILY_AHA, SEVERITY_CAUTION,
        "Retinoid and glycolic-type acid in the same routine",
        "Using both on the same night is a common cause of stinging and flaking. Many people alternate nights instead.",
        "Widely documented cumulative-irritation caution. This is about comfort, not safety.",
    ),
    CompatibilityRule(
        "rule.retinoid_bha", FAMILY_RETINOID, FAMILY_BHA, SEVERITY_CAUTION,
        "Retinoid and salicylic acid in the same routine",
        "Both can be drying. Alternating nights is the usual way people manage it.",
        "Widely documented cumulative-irritation caution.",
    ),
    CompatibilityRule(
        "rule.aha_bha", FAMILY_AHA, FAMILY_BHA, SEVERITY_CAUTION,
        "Two exfoliating acids together",
        "Stacking acids multiplies the exfoliation rather than improving it. Pick one for a given day.",
        "Cumulative irritation. Both products remain fine used separately.",
    ),
    CompatibilityRule(
        "rule.retinoid_benzoyl_peroxide", FAMILY_RETINOID, FAMILY_BENZOYL_PEROXIDE, SEVERITY_CAUTION,
        "Retinoid and benzoyl peroxide at the same time",
        "These are usually kept to different times of day. Use one in the morning and one at night if you want both.",
        "Long-standing formulation caution about some retinoids losing effect alongside benzoyl peroxide.",
    ),
    CompatibilityRule(
        "rule.vitamin_c_benzoyl_peroxide", FAMILY_VITAMIN_C, FAMILY_BENZOYL_PEROXIDE, SEVERITY_INFO,
        "Vitamin C and benzoyl peroxide in the same step",
        "Applying them together can oxidise the vitamin C. Separating them by time of day avoids it.",
        "A stability point about the product, not a safety one.",
    ),
    CompatibilityRule(
        # Deliberately `info`, not `caution`. This pairing is one of the most
        # repeated cautions online and the evidence does not support it. Saying
        # so plainly is more useful than either repeating it or staying silent.
        "rule.vitamin_c_niacinamide", FAMILY_VITAMIN_C, FAMILY_NIACINAMIDE, SEVERITY_INFO,
        "Vitamin C and niacinamide together",
        "You will read that these cannot be combined. Current evidence does not support that, and plenty of formulas contain both. Use them together unless your own skin tells you otherwise.",
        "Commonly repeated claim traced to a 1960s study using unformulated heat-stressed ingredients. Recorded here so the app can correct it rather than echo it.",
    ),
    CompatibilityRule(
        "rule.protein_protein", FAMILY_PROTEIN, FAMILY_PROTEIN, SEVERITY_CAUTION,
        "Several protein-containing hair products at once",
        "Layering protein products can leave hair feeling stiff or straw-like. Balance them with a moisture-focused conditioner.",
        "Well-documented protein/moisture balance point in hair care. Not a health matter.",
    ),
    CompatibilityRule(
        "rule.silicone_no_clarifier", FAMILY_SILICONE, FAMILY_SILICONE, SEVERITY_INFO,
        "Silicone products with no clarifying wash",
        "Silicones can build up over time. A clarifying shampoo every few washes is the usual answer.",
        "Buildup is a product-feel matter, not a scalp condition.",
    ),
    CompatibilityRule(
        "rule.essential_oil_sensitivity", FAMILY_ESSENTIAL_OIL, FAMILY_RETINOID, SEVERITY_CAUTION,
        "Essential oils alongside a retinoid",
        "Essential oils sting far more on skin that is already being exfoliated. Keep them apart if you notice discomfort.",
        "Comfort caution. Applies to the product's feel, not to any condition.",
    ),
)


# --- Routine ordering -------------------------------------------------------
# Slot numbers are the order things go on. Lower goes on first. These are the
# ordinary "thin to thick, sunscreen last" conventions, nothing clinical.


@dataclass(frozen=True)
class StepSlot:
    key: str
    label: str
    order: int
    category: str            # beauty | hair
    required: bool = False
    times: tuple[str, ...] = ("morning", "evening")
    why: str = ""


SKIN_SLOTS: tuple[StepSlot, ...] = (
    StepSlot("cleanser", "Cleanser", 10, "beauty", required=True, why="Everything after this works better on a clean face."),
    StepSlot("exfoliant", "Exfoliant", 20, "beauty", times=("evening",), why="Used occasionally rather than daily."),
    StepSlot("toner", "Toner or essence", 30, "beauty", why="A light hydrating layer before the heavier ones."),
    StepSlot("treatment", "Treatment or serum", 40, "beauty", why="The active step, applied where it can reach skin directly."),
    StepSlot("eye", "Eye product", 50, "beauty", why="Applied before heavier creams so it is not diluted."),
    StepSlot("moisturiser", "Moisturiser", 60, "beauty", required=True, why="Seals in the hydration from the steps above."),
    StepSlot("face_oil", "Face oil", 70, "beauty", times=("evening",), why="Goes last of the leave-on steps because it seals."),
    StepSlot("sunscreen", "Sunscreen", 80, "beauty", required=True, times=("morning",), why="The last morning step, and the one with the most visible long-term payoff."),
)

HAIR_SLOTS: tuple[StepSlot, ...] = (
    StepSlot("pre_wash_oil", "Pre-wash oil", 5, "hair", times=("wash_day",), why="Applied before washing so the wash does not strip as much."),
    StepSlot("shampoo", "Shampoo", 10, "hair", required=True, times=("wash_day",), why="The cleansing step."),
    StepSlot("scalp_care", "Scalp product", 15, "hair", times=("wash_day",), why="Applied to the scalp while it is clean."),
    StepSlot("conditioner", "Conditioner", 20, "hair", required=True, times=("wash_day",), why="Restores slip and softness after cleansing."),
    StepSlot("hair_mask", "Mask or deep conditioner", 25, "hair", times=("weekly",), why="A longer conditioning step, used occasionally."),
    StepSlot("leave_in", "Leave-in", 30, "hair", times=("wash_day",), why="Stays in to keep hair soft as it dries."),
    StepSlot("heat_protectant", "Heat protectant", 40, "hair", times=("wash_day", "event"), why="Goes on before any heat tool, every time."),
    StepSlot("styling", "Styling product", 50, "hair", times=("wash_day", "event"), why="The last step, once everything else is in."),
)

ALL_SLOTS: tuple[StepSlot, ...] = SKIN_SLOTS + HAIR_SLOTS
SLOT_BY_KEY: dict[str, StepSlot] = {row.key: row for row in ALL_SLOTS}

# Product type wording people actually type, mapped to a slot.
PRODUCT_TYPE_SLOTS: dict[str, str] = {
    # skin
    "cleanser": "cleanser", "face wash": "cleanser", "facewash": "cleanser", "cleansing oil": "cleanser",
    "micellar water": "cleanser", "face cleanser": "cleanser",
    "exfoliant": "exfoliant", "scrub": "exfoliant", "peel": "exfoliant", "exfoliating toner": "exfoliant",
    "toner": "toner", "essence": "toner", "mist": "toner",
    "serum": "treatment", "treatment": "treatment", "ampoule": "treatment", "spot treatment": "treatment",
    "eye cream": "eye", "eye serum": "eye", "under eye": "eye",
    "moisturiser": "moisturiser", "moisturizer": "moisturiser", "cream": "moisturiser",
    "lotion": "moisturiser", "gel cream": "moisturiser", "night cream": "moisturiser",
    "face oil": "face_oil", "facial oil": "face_oil",
    "sunscreen": "sunscreen", "spf": "sunscreen", "sunblock": "sunscreen",
    # hair
    "hair oil": "pre_wash_oil", "pre-wash oil": "pre_wash_oil", "prewash oil": "pre_wash_oil", "champi oil": "pre_wash_oil",
    "shampoo": "shampoo", "clarifying shampoo": "shampoo", "dry shampoo": "styling",
    "scalp serum": "scalp_care", "scalp tonic": "scalp_care", "scalp treatment": "scalp_care",
    "conditioner": "conditioner", "rinse-out conditioner": "conditioner",
    "hair mask": "hair_mask", "deep conditioner": "hair_mask", "hair pack": "hair_mask",
    "leave-in": "leave_in", "leave in conditioner": "leave_in", "hair serum": "leave_in",
    "heat protectant": "heat_protectant", "heat protection spray": "heat_protectant",
    "styling cream": "styling", "hair gel": "styling", "mousse": "styling", "hair spray": "styling", "wax": "styling",
}


def slot_for_product_type(product_type: str | None, category: str) -> str | None:
    """Map a user's product-type wording onto a routine slot."""
    if not product_type:
        return None
    text = " ".join(str(product_type).lower().split())
    if text in PRODUCT_TYPE_SLOTS:
        slot = PRODUCT_TYPE_SLOTS[text]
        return slot if SLOT_BY_KEY[slot].category == category else None
    # Longest phrase first, so "cleansing oil" beats "oil".
    for phrase in sorted(PRODUCT_TYPE_SLOTS, key=len, reverse=True):
        if phrase in text:
            slot = PRODUCT_TYPE_SLOTS[phrase]
            if SLOT_BY_KEY[slot].category == category:
                return slot
    return None


# --- Climate ----------------------------------------------------------------


@dataclass(frozen=True)
class ClimateRule:
    rule_id: str
    condition: str
    slot: str
    note: str


CLIMATE_RULES: tuple[ClimateRule, ...] = (
    ClimateRule("rule.climate_humid_moisturiser", "humid", "moisturiser",
                "In humid weather a lighter gel or lotion usually sits better than a rich cream."),
    ClimateRule("rule.climate_hot_sunscreen", "hot", "sunscreen",
                "Hot, bright days are when reapplying sunscreen actually matters."),
    ClimateRule("rule.climate_monsoon_cleanser", "humid", "cleanser",
                "Humid, sticky days often call for a slightly more thorough evening cleanse."),
    ClimateRule("rule.climate_cold_moisturiser", "cold", "moisturiser",
                "In cold, dry air a richer cream or an occlusive on top holds moisture better."),
    ClimateRule("rule.climate_cold_face_oil", "cold", "face_oil",
                "Dry cold weather is when a face oil earns its place as a final step."),
    ClimateRule("rule.climate_hot_hair_oil", "hot", "pre_wash_oil",
                "A pre-wash oil is useful in summer when washes are more frequent."),
)


# --- Perfume ----------------------------------------------------------------


@dataclass(frozen=True)
class PerfumeRule:
    rule_id: str
    factor: str
    match: str
    families: tuple[str, ...]
    note: str


PERFUME_RULES: tuple[PerfumeRule, ...] = (
    PerfumeRule("perfume.hot_light", "weather", "hot", ("citrus", "aquatic", "fresh", "green", "floral"),
                "Lighter, fresher families tend to sit better in heat; heavier ones can feel cloying."),
    PerfumeRule("perfume.humid_light", "weather", "humid", ("citrus", "aquatic", "fresh", "green"),
                "Humidity carries scent further, so less is usually more."),
    PerfumeRule("perfume.cold_warm", "weather", "cold", ("woody", "oriental", "amber", "spicy", "gourmand"),
                "Cooler air holds warmer, heavier families well."),
    PerfumeRule("perfume.evening_deeper", "time_of_day", "evening", ("woody", "oriental", "amber", "gourmand", "chypre"),
                "Evenings are the usual home for deeper families."),
    PerfumeRule("perfume.morning_fresh", "time_of_day", "morning", ("citrus", "fresh", "green", "aquatic"),
                "Mornings suit brighter openings."),
    PerfumeRule("perfume.formal_refined", "occasion", "formal", ("woody", "chypre", "floral", "oriental"),
                "More formal rooms suit something composed rather than loud."),
    PerfumeRule("perfume.office_quiet", "occasion", "office", ("citrus", "fresh", "green", "woody"),
                "Shared spaces are the one place where a quieter scent is simple courtesy."),
    PerfumeRule("perfume.festive_rich", "occasion", "festive", ("oriental", "amber", "gourmand", "floral", "spicy"),
                "Festive occasions carry richer families comfortably."),
)

# Recognised fragrance families, so a user's free-text entry can be matched.
FRAGRANCE_FAMILY_ALIASES: dict[str, str] = {
    "citrus": "citrus", "cologne": "citrus", "hesperidic": "citrus",
    "aquatic": "aquatic", "marine": "aquatic", "oceanic": "aquatic",
    "fresh": "fresh", "clean": "fresh", "soapy": "fresh",
    "green": "green", "herbal": "green", "aromatic": "green",
    "floral": "floral", "flowery": "floral", "white floral": "floral", "rose": "floral", "jasmine": "floral",
    "woody": "woody", "wood": "woody", "sandalwood": "woody", "cedar": "woody", "oud": "woody",
    "oriental": "oriental", "amber": "amber", "ambery": "amber",
    "spicy": "spicy", "spice": "spicy",
    "gourmand": "gourmand", "sweet": "gourmand", "vanilla": "gourmand",
    "chypre": "chypre", "mossy": "chypre",
    "musk": "musk", "musky": "musk",
    "powdery": "powdery",
    "attar": "attar", "itr": "attar",
}


def normalise_fragrance_family(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).lower().split())
    if text in FRAGRANCE_FAMILY_ALIASES:
        return FRAGRANCE_FAMILY_ALIASES[text]
    for alias in sorted(FRAGRANCE_FAMILY_ALIASES, key=len, reverse=True):
        if alias in text:
            return FRAGRANCE_FAMILY_ALIASES[alias]
    return None
