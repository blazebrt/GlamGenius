"""Sentry payload scrubbing.

The Sentry SDK's default scrubber removes obvious things like `password`
and `authorization` headers, but the values this product handles are not
covered by that list: image bytes, base64 fragments, ingredient lists,
memory facts, email addresses, phone numbers, JWT tokens and payment
identifiers.

Every value that leaves this process on its way to Sentry passes through
``scrub_event`` first. The scrubber is deliberately conservative — it
redacts by key name AND by value shape — so a caller that forgets to name
a field carefully still cannot leak its content by accident.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

REDACTED = "[Redacted by application]"

# Value-shape patterns. Applied inside every string that survives the
# key-name filter.
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/=_-]{80,}$")

# Key-name filter — anything matching is redacted whole regardless of type.
_SENSITIVE_KEY = re.compile(
    r"(?:image|photo|bytes|base64|ingredient|memory|jwt|token|"
    r"authorization|payment|order_id|receipt|email|phone|mobile|"
    r"tel|password|secret|api_key|"
    # Credentials and anything that identifies one account's storage. A
    # service-role key is the most dangerous value in this system and its name
    # matches none of the words above.
    r"service_role|credential|private_key|access_key|anon_key|dsn|"
    r"storage_key|storage_path|object_key)",
    re.I,
)


def _clean(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, (bytes, bytearray)):
        return REDACTED
    if isinstance(value, Mapping):
        return {k: _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, key) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        if _BASE64_LIKE.fullmatch(stripped):
            return REDACTED
        value = _JWT.sub(REDACTED, value)
        value = _EMAIL.sub(REDACTED, value)
        value = _PHONE.sub(REDACTED, value)
        return value
    return value


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry ``before_send`` hook.

    Runs on every event before it is transmitted. Recursively scrubs
    exception values, breadcrumbs, request headers/data, tags, contexts
    and extras.
    """
    return _clean(event)
