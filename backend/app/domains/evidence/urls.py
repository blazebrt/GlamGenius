"""One answer to "is this a URL somebody can actually open?".

There is exactly one of these on purpose. A source URL is checked when a claim
is authored and again when a reader decides whether that claim may be published
as public knowledge, and two validators drifting apart would mean a source that
passed authoring quietly failing resolution — or, far worse, the reverse.

**Why a prefix check is not enough.** ``value.startswith("https://")`` accepts
``https://``, ``https://?x=1`` and ``https:///path``. None of those has a host,
so none is openable; each would sail through authoring and sit in the database
looking like provenance. The string has to be parsed, not sniffed.

Standard library only, and no network: this asks whether a string is a
well-formed absolute http(s) URL, never whether the host exists or answers. A
DNS lookup here would make authoring depend on the network and make resolution
non-deterministic, and neither is a trade worth making to catch a dead link.
"""
from __future__ import annotations

import unicodedata
from urllib.parse import urlsplit

#: The only two schemes a citation may use. ``ftp:`` is not openable in the
#: sense a reader needs, and ``javascript:`` is not a citation at all.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def openable_url(value: object) -> str | None:
    """The stripped URL if it is a usable absolute http(s) address, else ``None``.

    Returns the value rather than a boolean so callers can store exactly what
    was validated instead of re-deriving it and risking a different string.

    Fails closed on: a non-string, blank, any embedded whitespace or control
    character, a scheme other than http/https, a missing authority, a missing
    host, and a malformed authority or port.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Interior whitespace and control characters. A URL containing either was
    # mis-transcribed or deliberately smuggled; splitting on it and keeping the
    # first half would be guessing at what a reviewer meant.
    if any(character.isspace() or unicodedata.category(character)[0] == "C"
           for character in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ALLOWED_URL_SCHEMES:
        return None
    if not parts.netloc:
        return None
    try:
        # ``hostname`` and ``port`` each raise on a malformed authority — an
        # unclosed IPv6 bracket, a non-numeric or out-of-range port. Both are
        # read purely so those raise *here*, where the answer is a clean
        # rejection, rather than somewhere downstream that assumed a parsed URL.
        # ``port`` is otherwise unused, which is the point.
        host = parts.hostname
        port = parts.port          # noqa: F841 — read for its side effect below
    except ValueError:
        return None
    if not host:
        return None
    return text


__all__ = ["ALLOWED_URL_SCHEMES", "openable_url"]
