"""The closed vocabulary a shopper may report, and how a lot number is compared.

Zero free text is a constitutional rule, so this module is the whole language of
Step 5. Every code names something a person can *see* on a pack. None of them
names a conclusion: a swollen pack is an observation, "unsafe" is a verdict, and
the difference is what keeps this system reportable rather than defamatory.
"""
from __future__ import annotations

import unicodedata
from typing import Any

# What the app said does not match what the pack says. These are about our data,
# they are true of the product as catalogued, and Step 3 label capture — not
# this — is how the data actually gets corrected.
OBSERVATION_BARCODE_RESULT_DIFFERS = "barcode_result_differs_from_pack"
OBSERVATION_INGREDIENTS_DIFFER = "ingredients_list_differs_from_app"
OBSERVATION_NUTRITION_DIFFERS = "nutrition_panel_differs_from_app"
OBSERVATION_PACK_SIZE_DIFFERS = "pack_size_differs_from_app"

# The condition of one physical pack. True of that lot at most, never of the
# product, because the next lot was made on another day in another line.
OBSERVATION_DATE_MARKING_UNREADABLE = "date_marking_unreadable"
OBSERVATION_SEAL_BROKEN = "seal_broken"
OBSERVATION_PACK_LEAKING = "pack_leaking"
OBSERVATION_PACK_SWOLLEN = "pack_swollen"
OBSERVATION_VISIBLE_FOREIGN_MATERIAL = "visible_foreign_material"
OBSERVATION_INSECT_OBSERVED = "insect_observed"

PRODUCT_DATA_OBSERVATIONS = frozenset({
    OBSERVATION_BARCODE_RESULT_DIFFERS,
    OBSERVATION_INGREDIENTS_DIFFER,
    OBSERVATION_NUTRITION_DIFFERS,
    OBSERVATION_PACK_SIZE_DIFFERS,
})
PACK_CONDITION_OBSERVATIONS = frozenset({
    OBSERVATION_DATE_MARKING_UNREADABLE,
    OBSERVATION_SEAL_BROKEN,
    OBSERVATION_PACK_LEAKING,
    OBSERVATION_PACK_SWOLLEN,
    OBSERVATION_VISIBLE_FOREIGN_MATERIAL,
    OBSERVATION_INSECT_OBSERVED,
})
OBSERVATION_CODES = PRODUCT_DATA_OBSERVATIONS | PACK_CONDITION_OBSERVATIONS

SCOPE_PRODUCT = "product"
SCOPE_BATCH = "batch"


def observation_scope(code: str) -> str:
    """Pack-condition observations are always about one lot. Never widened."""
    return SCOPE_BATCH if code in PACK_CONDITION_OBSERVATIONS else SCOPE_PRODUCT


def is_batch_scoped(code: str) -> bool:
    return code in PACK_CONDITION_OBSERVATIONS


#: Values printed where a lot number belongs that identify nothing. Matching two
#: packs on one of these would merge strangers' packs into a single signal.
BATCH_PLACEHOLDERS = frozenset({
    "na", "n/a", "nil", "none", "not applicable", "not available", "other", "others",
    "-", ".", "no", "loose", "loose sample", "sold as loose",
})


def normalise_batch(value: Any) -> str | None:
    """A lot number for exact comparison, or None when it identifies nothing.

    Deliberately this domain's own rule rather than an import from the official
    source adapter: community aggregation must not acquire a dependency on the
    FSSAI ingestion layer, and the two are free to diverge. Case and stray
    whitespace are forgiving; separators are not, so ``B-123`` and ``B 123``
    stay different lots. Short real codes such as ``C`` or ``L0`` survive.
    """
    if value is None or not isinstance(value, str):
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    if not text:
        return None
    normalized = text.casefold()
    if normalized in BATCH_PLACEHOLDERS:
        return None
    return None if set(normalized) == {"0"} else normalized
