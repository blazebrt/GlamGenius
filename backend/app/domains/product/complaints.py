"""Structured FSSAI review-and-handoff contract.

There is deliberately no user free text and no claim that this service files a
complaint.  FSSAI's official portal remains the sole filing authority.
"""
from __future__ import annotations

from typing import Any

FSSAI_CONSUMER_GRIEVANCE_URL = "https://foscos.fssai.gov.in/consumergrievance/faqs"
COMPLAINT_REASONS = ("food_safety", "label_information", "misleading_claim", "packaging")

REQUEST_TEMPLATES = {
    "food_safety": "I request that the food-safety information on this pack be reviewed.",
    "label_information": "I request that the label information on this pack be reviewed.",
    "misleading_claim": "I request that the claim shown on this pack be reviewed.",
    "packaging": "I request that the packaging information on this pack be reviewed.",
}


def prepared_fields(facts: dict[str, Any], photo_asset_id: str | None) -> dict[str, str | None]:
    return {
        "product_name": facts.get("product_name") or facts.get("name"),
        "brand": facts.get("brand"),
        "batch_number": facts.get("batch_number"),
        "fssai_licence": facts.get("fssai_licence"),
        "photo_asset_id": photo_asset_id,
    }


def missing_preparation_fields(fields: dict[str, str | None]) -> list[str]:
    return [field for field, value in fields.items() if not value]
