"""Step 7B — printed ingredient lists, read into canonical identities.

The deterministic bridge between a transcribed ingredient string and Step 7A's
canonical identity registry:

    printed ``ingredients_text``
      → conservative top-level parsing
      → ordered exact candidate names
      → one batch identity resolution
      → ordered formula identity result

What it deliberately does not do: decide what any substance does, whether it is
safe, how much is present, whether it is permitted, or whether the product is
any good. It produces no score, grade, verdict or recommendation. Identity
presence is not efficacy, and printed order is not concentration.

Step 7A remains the only identity authority; this layer adds parsing and nothing
else. See ``docs/architecture/FORMULA_RESOLUTION.md``.
"""
from app.domains.formulas.parser import (
    MAX_FORMULA_TOKENS,
    MAX_INGREDIENTS_TEXT_LENGTH,
    FormulaParse,
    FormulaToken,
    ParseStatus,
    parse_formula,
)
from app.domains.formulas.service import (
    FormulaIngredientResolution,
    FormulaResolution,
    resolve_formula,
)

__all__ = [
    "MAX_FORMULA_TOKENS",
    "MAX_INGREDIENTS_TEXT_LENGTH",
    "FormulaIngredientResolution",
    "FormulaParse",
    "FormulaResolution",
    "FormulaToken",
    "ParseStatus",
    "parse_formula",
    "resolve_formula",
]
