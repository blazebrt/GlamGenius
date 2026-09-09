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

import ipaddress
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[Redacted by application]"

# Value-shape patterns. Applied inside every string that survives the
# key-name filter.
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/=_-]{80,}$")
_APIKEY_QUERY = re.compile(r"([?&]apikey=)[^&#\s]+", re.I)
_OAUTH_QUERY = re.compile(r"([?&](?:code|state|access_token|refresh_token|client_secret)=)[^&#\s]+", re.I)
_OAUTH_KV = re.compile(r"((?:[\"']?\b(?:code|state|access_token|refresh_token|client_secret)\b[\"']?)\s*[:=]\s*[\"']?)[^\s,}&\"']+", re.I)
_AUTH_HEADER = re.compile(r"((?:authorization\s*[:=]\s*)?Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)
# A URL carrying credentials -- a database URL, a broker URL, anything with
# `user:password@`. Redacted whole rather than just the credentials: the host
# it names is infrastructure detail that nothing in a crash report needs, and
# leaving it invites somebody to reconstruct the rest.
_URL_WITH_CREDENTIALS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@\S*", re.I)

# An IP address anywhere inside free text -- an upstream error, a connection
# message, a proxy header echoed into an exception. Two stages on purpose:
# a loose candidate match, then `ipaddress` decides. A regex tight enough to
# recognise every IPv6 form and loose enough to miss none is not a regex worth
# trusting, and a looser one would redact software versions. `2.12.5` is not an
# address and the standard library says so; `203.0.113.42` and `2001:db8::42`
# are, and it says that too.
#
# The second branch admits dots as well as colons, and that is the whole of a
# fix independent review asked for. An IPv6 address may legally end in a dotted
# IPv4 part -- `::ffff:192.0.2.128`, `2001:db8::192.0.2.33` -- and a
# colons-only candidate class cannot represent one. What happened instead was
# that the two halves were matched separately: `2001:db8::192.0.2.33` became
# `2001:db8::[Redacted]`, leaving the network prefix in the clear, and
# `::ffff:192.0.2.128` became two redactions where one address had been. One
# address should produce one redaction, and no part of it should survive.
#
# Requiring a colon in this branch is what keeps version strings out of it:
# `2.12.5` and `v2.12.5` reach neither branch, so nothing about them depends on
# `ipaddress` being lenient.
_IP_CANDIDATE = re.compile(
    r"(?<![\w.:-])("
    r"(?:\d{1,3}\.){3}\d{1,3}"
    r"|(?=[0-9A-Fa-f.:]*:)[0-9A-Fa-f.:]{2,45}"
    r")(?![\w.-])"
)


def _redact_ip_addresses(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        candidate = match.group(1)
        # Sentence punctuation gets swept into a candidate: an address that
        # ends a message picks up the full stop after it. A dot is never valid
        # at either end of an address, so trimming one can only turn a
        # non-address into an address -- never the reverse -- and the trimmed
        # characters are put back untouched. Colons are deliberately left
        # alone: `::1` and `2001:db8::` are real addresses.
        core = candidate.strip(".")
        if not core:
            return candidate
        try:
            ipaddress.ip_address(core)
        except ValueError:
            return candidate
        head, _, tail = candidate.partition(core)
        return f"{head}{REDACTED}{tail}"

    return _IP_CANDIDATE.sub(_replace, value)

# Key-name filter — anything matching is redacted whole regardless of type.
_SENSITIVE_KEY = re.compile(
    r"(?:image|photo|bytes|base64|ingredient|memory|jwt|token|"
    r"authorization|payment|order_id|receipt|email|phone|mobile|"
    r"tel|password|secret|api_key|"
    # Credentials and anything that identifies one account's storage. A
    # service-role key is the most dangerous value in this system and its name
    # matches none of the words above.
    r"service_role|credential|private_key|access_key|anon_key|dsn|"
    r"storage_key|storage_path|object_key|"

    # --- Health and safety state -----------------------------------------
    # Step 8K's contract is that ephemeral safety state is never stored,
    # logged, echoed or counted. A serialized request reaching Sentry inside a
    # future exception would break that contract without anybody writing a
    # line of logging code, so the whole `safety` subtree and every field it
    # holds is redacted by name, wherever it appears and however deeply nested.
    #
    # Matched narrowly on purpose. Generic operational words -- `status`,
    # `reason`, `message`, `code` -- are deliberately absent: redacting those
    # would blind every crash report in the product to protect nothing.
    r"safety|pregnan|breastfeed|breast_feed|lactat|"
    r"medication|medicine|medical|diagnos|symptom|"
    r"\bcondition|handoff|hand_off|"
    # `stated_age` and a bare `age`; anchored so `page`, `usage`, `language`,
    # `storage` and `message` are untouched.
    r"stated_age|\bage\b|subject_is_child|subject_child|is_child|"

    # --- Who and what this request was about ------------------------------
    # None of these is a secret in the credential sense, and each one links a
    # crash to one person or one product they were holding.
    r"account_id|user_id|device_id|device_key|client_scan|scan_id|scan_event|"
    r"ai_run|media_asset|snapshot_id|label_snapshot|content_fingerprint|"
    r"fingerprint|barcode|product_name|brand|product_type|"

    # --- The sentence a customer would have read --------------------------
    # The keys stay readable: `verdict_key` and `reason_key` are global
    # governance identifiers and are useful in a report. The rendered text is
    # what was shown to one person about their own body.
    r"verdict_text|reason_text|source_url|canonical_url|"

    # --- Profile facts, by container ---------------------------------------
    # What somebody said about their own skin is personal context, and the
    # milestone's contract keeps all of it out of observability. The container
    # is redacted whole rather than walked into, so a fact nobody has invented
    # yet is covered on the day it is added -- enumerating individual fact
    # names would protect only the ones that already exist. `care_skin_` covers
    # the currently relevant facts when one appears on its own rather than
    # inside its container.
    r"profile|personal_context|personal_lens|personal_fact|care_skin_|"

    # --- Network identity ---------------------------------------------------
    # `send_default_pii=False` already asks the SDK not to attach these. That
    # is one layer and not a proof: a custom context, a proxy header echoed
    # into an exception, or a future integration can each carry an address the
    # SDK never chose to send. Anchored so that `description`, `recipe`,
    # `equipment` and `zip` are untouched.
    r"\bip[_-]?address\b|client[_-]?ip\b|remote[_-]?(?:ip|addr)\b|"
    r"forwarded[_-]?for\b|real[_-]?ip\b|\bip[_-]?v[46]\b)",
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
        value = _URL_WITH_CREDENTIALS.sub(REDACTED, value)
        # Before the phone pattern: a dotted quad also looks like a long run of
        # digits and separators, and "redacted as an address" is the truthful
        # label for it.
        value = _redact_ip_addresses(value)
        value = _JWT.sub(REDACTED, value)
        value = _EMAIL.sub(REDACTED, value)
        value = _PHONE.sub(REDACTED, value)
        value = _APIKEY_QUERY.sub(r"\1[REDACTED]", value)
        value = _OAUTH_QUERY.sub(r"\1[REDACTED]", value)
        value = _AUTH_HEADER.sub(r"\1[REDACTED]", value)
        value = _OAUTH_KV.sub(r"\1[REDACTED]", value)
        return value
    return value


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry ``before_send`` hook.

    Runs on every event before it is transmitted. Recursively scrubs
    exception values, breadcrumbs, request headers/data, tags, contexts
    and extras.
    """
    return _clean(event)
