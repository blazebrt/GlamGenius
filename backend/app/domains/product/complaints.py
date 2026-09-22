"""Structured FSSAI review-and-handoff contract.

There is deliberately no user free text and no claim that this service files a
complaint.  FSSAI's official portal remains the sole filing authority.
"""
from __future__ import annotations

from collections.abc import Mapping

FSSAI_CONSUMER_GRIEVANCE_URL = "https://foscos.fssai.gov.in/consumergrievance/faqs"
COMPLAINT_REASONS = ("food_safety", "label_information", "misleading_claim", "packaging")

REQUEST_TEMPLATES = {
    "food_safety": "I request that the food-safety information on this pack be reviewed.",
    "label_information": "I request that the label information on this pack be reviewed.",
    "misleading_claim": "I request that the claim shown on this pack be reviewed.",
    "packaging": "I request that the packaging information on this pack be reviewed.",
}


def _pack_text(value: object) -> str | None:
    """One printed pack field, or ``None`` when stored JSON cannot prove text.

    Every field below stands for words a person read off a physical pack. A
    JSONB column cannot promise that: a legacy or corrupt row may hold a list,
    an object, a number or a bool where a line of printed text belongs.

    The refusal is deliberate, and it is not the same as a fallback. Rendering
    ``["10000000000000"]`` as text would put a string nobody printed into a
    document a person is about to take to a regulator, and it would look
    exactly like a licence number that had been read. So arbitrary JSON is
    never stringified; it is reported as absent, which is what it is.

    Whitespace is trimmed because leading and trailing space is transcription
    noise rather than content, and a value that is only whitespace states
    nothing at all.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def prepared_fields(
    facts: Mapping[str, object], photo_asset_id: str | None
) -> dict[str, str | None]:
    """The pack fields a person may review before going to the FSSAI portal.

    ``facts`` has already crossed the top-level readability boundary — its
    caller obtained it from
    :func:`app.domains.product.service.readable_label_facts`, which is the one
    authority on whether a stored ``LabelSnapshot.facts`` value is a fact
    object at all, and which answers ``{}`` when it is not. This function does
    not ask that question a second time: a duplicate ``isinstance`` check here
    would be a competing authority that could drift from the real one, and the
    drift would only ever be visible as a defect in production.

    What is left is the narrower question this layer owns — whether each
    individual value is usable printed text. A readable mapping can still hold
    ``{"batch_number": 12345}``, and a number is not something a pack printed.
    Each field is therefore resolved through :func:`_pack_text` and is absent
    unless it is nonblank text.

    Nothing is invented. No value is filled from Open Food Facts, which is a
    catalogue and cannot state what one particular pack said.
    """
    return {
        # The printed name, with the legacy ``name`` key as the documented
        # alias. The alias is reached whenever the preferred key yields no
        # usable text, so a malformed ``product_name`` does not mask a perfectly
        # good ``name`` beside it.
        "product_name": _pack_text(facts.get("product_name")) or _pack_text(facts.get("name")),
        "brand": _pack_text(facts.get("brand")),
        "batch_number": _pack_text(facts.get("batch_number")),
        "fssai_licence": _pack_text(facts.get("fssai_licence")),
        # Not a pack field: an identifier this server issued and already
        # checked for ownership. It is passed through unchanged.
        "photo_asset_id": photo_asset_id,
    }


def missing_preparation_fields(fields: dict[str, str | None]) -> list[str]:
    return [field for field, value in fields.items() if not value]
