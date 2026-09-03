"""Talking to the Open Food Facts API.

Open Food Facts asks every caller to identify itself with a descriptive
User-Agent and will rate-limit or block anonymous traffic. That is a stated
condition of using the API, so the header is built here, in the one place all
outbound calls go through, rather than left to each caller to remember.
"""
from __future__ import annotations

import logging
from typing import Any

from app import config

logger = logging.getLogger(__name__)

API_BASE = "https://world.openfoodfacts.org"
PRODUCT_PATH = "/api/v2/product/{barcode}.json"

# The fields Store A holds. Asking for these and no more keeps the response
# aligned with what may legally be stored.
#
# ``categories_tags`` and ``countries_tags`` are the taxonomy arrays. They are
# requested rather than the ``categories``/``countries`` text alone because
# only the arrays carry Open Food Facts' own classification: their schema
# describes the text fields as untaxonomised, written in whichever language the
# last editor was using, and "mostly used for debugging and testing purposes".
# See ``app/domains/off/taxonomy.py`` for the quotations and the reasoning.
REQUESTED_FIELDS = (
    "code,product_name,brands,ingredients_text,nutriments,categories,"
    "categories_tags,countries_tags,"
    "image_url,quantity,countries,last_modified_t"
)


def user_agent() -> str:
    """The identifying header Open Food Facts requires.

    Name, version and a contact address, which is the shape they ask for.
    """
    contact = config.OFF_CONTACT_EMAIL or config.SUPPORT_URL
    return f"{config.OFF_APP_NAME}/{config.OFF_APP_VERSION} ({contact})"


def request_headers() -> dict[str, str]:
    return {"User-Agent": user_agent(), "Accept": "application/json"}


async def fetch_product(barcode: str) -> dict[str, Any] | None:
    """Fetch one product. Returns the raw payload, or None when not found.

    Never raises for a network problem: a missing product is a normal answer,
    and the caller decides what to do without it.
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError:  # pragma: no cover - httpx is a dependency
        logger.warning("httpx not installed; Open Food Facts lookups disabled")
        return None

    url = API_BASE + PRODUCT_PATH.format(barcode=barcode)
    try:
        async with httpx.AsyncClient(timeout=config.OFF_TIMEOUT_SECONDS) as client:
            response = await client.get(
                url, headers=request_headers(), params={"fields": REQUESTED_FIELDS},
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json() or {}
    except Exception as exc:  # noqa: BLE001 - a lookup failure is not a request failure
        logger.info("off_lookup_failed barcode=%s error=%s", barcode, type(exc).__name__)
        return None

    if payload.get("status") != 1:
        return None
    return payload.get("product") or None
