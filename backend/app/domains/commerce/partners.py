"""The closed partner registry and the one outbound address it may build.

A partner is a literal row in :data:`PARTNERS`, reviewed like code, because it
is code. There is no merchant table, no remote configuration and no address a
client can supply. An operator chooses **at most one** registered partner with
``COMMERCE_PARTNER`` and may give that partner's own affiliate tag with
``COMMERCE_AFFILIATE_TAG``. Nothing else chooses it: not the product, its grade
or decision, its category, the alternative, a price, a payout, a commission or
how often people follow a link. None of those is an input here.

Unset — the default — means Commerce is off and every handoff answers
``unavailable`` without reading anything.

The address
-----------
V1 links to the partner's **search page for the exact barcode**, never to a
product page it would have to claim is the same pack. The query is the GS1
barcode itself: digits only, check digit verified. No AI-written search
phrase, no merchant SKU, no product name.

The address is assembled from registry constants and those digits, then
parsed back and checked against the registry before it may leave the server:
``https`` only, the exact registered host (no user-info, no port, no case or
suffix games), the exact search path, exactly the expected query parameters in
order, no fragment, nothing outside printable ASCII, and a bounded length. Any
difference is :class:`UnsafeDestination`, and the caller shows no link. The
server never fetches the partner page; it only names it.

What the affiliate tag is
-------------------------
The partner's identifier for GlamGenius as the referring source, set once by
an operator. It never carries anything about a person: no account, device,
household, subject, scan, label snapshot, inventory or AI run id, no email or
phone, no trait. There is no per-person sub-id in V1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from app import config


@dataclass(frozen=True)
class Partner:
    """One registered outbound destination. Only what the handoff needs."""

    key: str
    display_name: str
    #: The exact hostname. Compared as a whole string, never as a suffix.
    host: str
    #: The exact path of the partner's search page.
    search_path: str
    #: The query parameter that carries the barcode.
    query_parameter: str
    #: The query parameter that carries the partner's affiliate tag, if any.
    affiliate_parameter: str | None


#: Every partner V1 knows. Enabling one is an operator's decision, taken after
#: confirming the partner's current programme terms (disclosure wording, use
#: in an app) and that its search page answers an exact barcode. See
#: ``docs/architecture/COMMERCE_HANDOFF.md``.
PARTNERS: MappingProxyType[str, Partner] = MappingProxyType({
    "amazon_in": Partner(
        key="amazon_in",
        display_name="Amazon.in",
        host="www.amazon.in",
        search_path="/s",
        query_parameter="k",
        affiliate_parameter="tag",
    ),
})
PARTNER_KEYS: tuple[str, ...] = tuple(PARTNERS)

#: A partner's affiliate tag: letters, digits and inner hyphens, 1-64 long.
AFFILIATE_TAG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?")
#: GTIN-8, GTIN-12 (UPC-A), GTIN-13 (EAN-13) or GTIN-14, ASCII digits only.
#: ``[0-9]`` rather than ``\\d``: ``\\d`` also matches other scripts' digits.
_GTIN = re.compile(r"(?:[0-9]{8}|[0-9]{12,14})")
#: Anything that is not printable ASCII, plus whitespace and the backslash.
_UNSAFE_CHARACTER = re.compile(r"[^\x21-\x7e]|\\")
#: A registry address is short. A long one is somebody else's.
MAX_URL_LENGTH = 256


class UnsafeDestination(ValueError):
    """The address is not exactly one the registry would build. Carries a code only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ActivePartner:
    """The one partner an operator enabled, with its optional affiliate tag."""

    partner: Partner
    affiliate_tag: str | None


def is_exact_gtin(value: object) -> bool:
    """A GS1 barcode a partner's search can be asked about: digits only, check digit valid."""
    if not isinstance(value, str) or not _GTIN.fullmatch(value) or not value.strip("0"):
        return False
    digits = [ord(character) - 48 for character in value]
    body, check = digits[:-1], digits[-1]
    total = sum(digit * (3 if position % 2 == 0 else 1) for position, digit in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def configuration_errors(partner_key: str, affiliate_tag: str) -> list[str]:
    """What is wrong with this operator configuration, in words an operator can act on."""
    errors: list[str] = []
    if partner_key and partner_key not in PARTNERS:
        errors.append("COMMERCE_PARTNER must be empty or one of: " + ", ".join(PARTNER_KEYS) + ".")
    if affiliate_tag:
        if not partner_key:
            errors.append("COMMERCE_AFFILIATE_TAG is set but COMMERCE_PARTNER is not.")
        elif not AFFILIATE_TAG.fullmatch(affiliate_tag):
            errors.append("COMMERCE_AFFILIATE_TAG must be 1-64 letters, digits or inner hyphens.")
        elif partner_key in PARTNERS and PARTNERS[partner_key].affiliate_parameter is None:
            errors.append("COMMERCE_AFFILIATE_TAG is set for a partner that takes no affiliate tag.")
    return errors


def _configured() -> tuple[str, str]:
    partner_key = (getattr(config, "COMMERCE_PARTNER", "") or "").strip().lower()
    affiliate_tag = (getattr(config, "COMMERCE_AFFILIATE_TAG", "") or "").strip()
    return partner_key, affiliate_tag


def active_partner() -> ActivePartner | None:
    """The enabled partner, or ``None``. A malformed configuration enables nothing."""
    partner_key, affiliate_tag = _configured()
    if not partner_key or configuration_errors(partner_key, affiliate_tag):
        return None
    return ActivePartner(partner=PARTNERS[partner_key], affiliate_tag=affiliate_tag or None)


def _expected_query(active: ActivePartner, barcode: str) -> list[tuple[str, str]]:
    pairs = [(active.partner.query_parameter, barcode)]
    if active.affiliate_tag and active.partner.affiliate_parameter:
        pairs.append((active.partner.affiliate_parameter, active.affiliate_tag))
    return pairs


def _encode(pairs: list[tuple[str, str]]) -> str:
    return urlencode(pairs, quote_via=quote, safe="")


def verify_destination(url: object, active: ActivePartner, barcode: str) -> str:
    """Return ``url`` only if it is exactly the address the registry builds for ``barcode``."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH or _UNSAFE_CHARACTER.search(url):
        raise UnsafeDestination("destination_malformed")
    if not is_exact_gtin(barcode):
        raise UnsafeDestination("barcode_unusable")
    try:
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise UnsafeDestination("destination_malformed") from exc
    partner = active.partner
    # ``urlsplit`` lower-cases the scheme, so the raw prefix is checked too.
    if parts.scheme != "https" or not url.startswith("https://"):
        raise UnsafeDestination("scheme_not_https")
    # The whole network location, compared as a string: this refuses user-info
    # (``user@host``), any port (``host:443``), a longer host that ends in the
    # registered one, and a different letter case.
    if parts.netloc != partner.host:
        raise UnsafeDestination("host_not_registered")
    if parts.path != partner.search_path:
        raise UnsafeDestination("path_not_registered")
    if parts.fragment:
        raise UnsafeDestination("fragment_not_allowed")
    expected = _expected_query(active, barcode)
    # Exactly the expected parameters, in order, and encoded exactly as the
    # registry encodes them: no extra parameter, no repeat, no alternative or
    # double encoding of the same value.
    if pairs != expected or parts.query != _encode(expected):
        raise UnsafeDestination("query_not_registered")
    # And finally the whole string, byte for byte, against the one address the
    # registry builds. Every check above only explains which part differed.
    if url != _canonical(active, barcode):
        raise UnsafeDestination("destination_not_registered")
    return url


def _canonical(active: ActivePartner, barcode: str) -> str:
    partner = active.partner
    return urlunsplit(("https", partner.host, partner.search_path, _encode(_expected_query(active, barcode)), ""))


def search_url(active: ActivePartner, barcode: str) -> str:
    """The partner's search page for one exact barcode, or :class:`UnsafeDestination`."""
    if not is_exact_gtin(barcode):
        raise UnsafeDestination("barcode_unusable")
    return verify_destination(_canonical(active, barcode), active, barcode)


__all__ = [
    "AFFILIATE_TAG",
    "ActivePartner",
    "MAX_URL_LENGTH",
    "PARTNERS",
    "PARTNER_KEYS",
    "Partner",
    "UnsafeDestination",
    "active_partner",
    "configuration_errors",
    "is_exact_gtin",
    "search_url",
    "verify_destination",
]
