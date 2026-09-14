"""Conservative strategy-specific identity for longitudinal purchase memory."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.domains.purchase.contract import resolve_purchase_strategy
from app.domains.recommendation.models import ShoppingCandidate
from app.domains.routines.parser import canonical_declared_keys

PURCHASE_IDENTITY_VERSION = "step-9a-v2"
_TRUSTED_STATES = {"user_declared", "confirmed"}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value.strip()).casefold()
    return value or None


def _set(values: object) -> list[str] | None:
    if not isinstance(values, list):
        return None
    normal = sorted({value for item in values if (value := _text(item)) is not None})
    return normal or None


def _exact(material: dict[str, Any]) -> dict[str, str | None]:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {"version": PURCHASE_IDENTITY_VERSION, "state": "exact",
            "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def _insufficient() -> dict[str, str | None]:
    return {"version": PURCHASE_IDENTITY_VERSION, "state": "insufficient", "fingerprint": None}


def identity_for_candidate(candidate: ShoppingCandidate) -> dict[str, str | None]:
    """Return exact identity only from trusted, strategy-specific product facts.

    URLs are deliberately excluded: a product page is not proof of a SKU or
    variant. Price, temporal/source metadata, intended use, and all verdict
    facts are also excluded.
    """
    strategy = resolve_purchase_strategy(candidate.category)
    if (strategy is None or strategy.state != "active" or candidate.verification_state not in _TRUSTED_STATES
            or candidate.uncertain_fields):
        return _insufficient()
    brand, name = _text(candidate.brand), _text(candidate.display_name)
    if not brand or not name:
        return _insufficient()
    details = candidate.details if isinstance(candidate.details, dict) else {}
    if strategy.key == "care_purchase":
        product_type = _text(details.get("product_type"))
        ingredients = canonical_declared_keys(details.get("active_ingredients"))
        # Purpose is a role, not product identity. Do not use it alone.
        if not product_type or not ingredients:
            return _insufficient()
        return _exact({"strategy": strategy.key, "category": candidate.category,
                       "brand": brand, "name": name, "product_type": product_type,
                       "active_ingredients": ingredients})
    if strategy.key == "fragrance_purchase":
        concentration = _text(details.get("concentration"))
        family = _text(details.get("fragrance_family"))
        # Season, occasion and reported longevity describe desired use, not a
        # bottle. They must never influence exact matching.
        if not concentration or not family:
            return _insufficient()
        return _exact({"strategy": strategy.key, "category": candidate.category,
                       "brand": brand, "name": name, "concentration": concentration,
                       "fragrance_family": family})
    if strategy.key == "style_purchase":
        subcategory = _text(candidate.subcategory)
        size, fabric, colour = _text(candidate.size), _text(candidate.fabric), _text(candidate.colour)
        if not subcategory or not size or not fabric or not colour:
            return _insufficient()
        return _exact({"strategy": strategy.key, "category": candidate.category,
                       "brand": brand, "name": name, "subcategory": subcategory,
                       "size": size, "fabric": fabric, "colour": colour})
    return _insufficient()
