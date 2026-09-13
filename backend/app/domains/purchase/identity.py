"""Conservative, versioned identity for longitudinal purchase memory.

This is deliberately exact matching, not similarity.  A missing stable fact is
an insufficient identity, never permission to imply that two products match.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domains.recommendation.models import ShoppingCandidate

PURCHASE_IDENTITY_VERSION = "step-9a-v1"
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}


def _text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value.strip()).casefold()
    return value or None


def _value(value: Any) -> Any:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return {key: _value(value[key]) for key in sorted(value) if _value(value[key]) is not None}
    if isinstance(value, list):
        return [_value(item) for item in value]
    return value


def _canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path in {"", "/"}:
        return None
    query = urlencode(sorted((key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                             if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), query, ""))


def identity_for_candidate(candidate: ShoppingCandidate) -> dict[str, str | None]:
    """Return an exact account-independent fingerprint or an insufficient state."""
    name, brand = _text(candidate.display_name), _text(candidate.brand)
    url = _canonical_url(candidate.product_url)
    details = _value(candidate.details or {})
    # A merchant's stable product path is a strong identifier.  Without one,
    # require both the labelled maker/name and reviewed category facts; simple
    # name matching is intentionally never enough.
    has_specific_details = isinstance(details, dict) and len(details) >= 2
    if not url and not (name and brand and has_specific_details):
        return {"version": PURCHASE_IDENTITY_VERSION, "state": "insufficient", "fingerprint": None}
    material = {
        "category": candidate.category,
        "brand": brand,
        "display_name": name,
        "subcategory": _text(candidate.subcategory),
        "url": url,
        "details": details,
        "style": {
            key: _text(getattr(candidate, key))
            for key in ("colour", "size", "fabric", "fit", "formality")
            if _text(getattr(candidate, key)) is not None
        },
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "version": PURCHASE_IDENTITY_VERSION,
        "state": "exact",
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }

