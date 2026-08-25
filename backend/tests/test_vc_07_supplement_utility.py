"""VC-07 pure utility and safety-boundary regressions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.domains.supplements.engine import (
    SUPPLEMENT_COMPONENT_NORMALIZATION_VERSION,
    SUPPLEMENT_UTILITY_VERSION,
    build_utility,
    normalize_component,
)


@dataclass
class Fact:
    raw_name: str
    normalized_name: str
    id: object
    verification_state: str = "confirmed"
    amount: Decimal | None = None
    unit: str | None = None
    serving_text: str | None = None
    source: str = "user_declared"
    confidence: float | None = 1.0
    canonical_component_key: str | None = None


def item(name: str, facts: list[Fact], *, expiry: date | None = None, purpose: str | None = None, confirmed: str = "confirmed"):
    return {"id": str(uuid4()), "display_name": name, "brand": "Brand", "verification_state": confirmed, "expiry_date": expiry, "user_entered_purpose": purpose, "facts": facts}


def test_normalization_is_conservative_and_unicode_stable():
    assert normalize_component("  Vitamin\u00a0 C!! ") == "vitamin c"
    assert normalize_component("Magnesium citrate") != normalize_component("Magnesium glycinate")


def test_exact_overlap_and_reviewed_alias_are_deterministic():
    a = Fact("Vitamin C", "vitamin c", uuid4(), amount=Decimal("500"), unit="mg", serving_text="Per tablet")
    b = Fact("ascorbic acid", "ascorbic acid", uuid4(), amount=Decimal("250"), unit="mg")
    items = [item("A", [a]), item("B", [b])]
    result = build_utility(items, today=date(2026, 8, 1))
    assert result["overlaps"][0]["display_name"] == "Vitamin C"
    assert result["overlaps"][0]["product_count"] == 2
    assert result["overlaps"][0]["items"][0]["fact"]["amount"] in {"250", "500"}
    assert result["utility_version"] == SUPPLEMENT_UTILITY_VERSION
    assert result["normalization_version"] == SUPPLEMENT_COMPONENT_NORMALIZATION_VERSION
    assert result["fingerprint"] == build_utility(items, today=date(2026, 8, 1))["fingerprint"]


def test_unconfirmed_facts_and_drafts_do_not_drive_overlap():
    a = Fact("Vitamin D", "vitamin d", uuid4(), verification_state="draft")
    b = Fact("Vitamin D", "vitamin d", uuid4())
    result = build_utility([item("A", [a]), item("B", [b])])
    assert result["overlaps"] == []
    assert result["confirmation_needed"]
    draft_item = build_utility([item("Draft", [b], confirmed="draft")])
    assert draft_item["overlaps"] == []


def test_amounts_units_and_missing_fields_are_preserved_without_totals():
    first = Fact("Vitamin C", "vitamin c", uuid4(), amount=Decimal("500"), unit="mg", serving_text="Per tablet")
    second = Fact("Vitamin C", "vitamin c", uuid4(), amount=Decimal("250"), unit="mcg")
    result = build_utility([item("A", [first]), item("B", [second])])
    amounts = {row["fact"]["amount"] for row in result["overlaps"][0]["items"]}
    assert amounts == {"500", "250"}
    assert all("total" not in str(result[key]).lower() for key in ("supplements", "overlaps"))
    assert "serving_text" in result["supplements"][1]["missing_information"]


def test_expiry_states_and_professional_boundary_are_factual():
    today = date(2026, 8, 1)
    past = build_utility([item("Past", [], expiry=today - timedelta(days=1), purpose="Can I take this with my medicine?")], today=today)
    coming = build_utility([item("Coming", [], expiry=today + timedelta(days=10))], today=today)
    unknown = build_utility([item("Unknown", [])], today=today)
    assert past["supplements"][0]["expiry_state"] == "past"
    assert past["supplements"][0]["professional_boundary"] is True
    assert coming["supplements"][0]["expiry_state"] == "coming_up"
    assert unknown["supplements"][0]["expiry_state"] == "unknown"
    prohibited = " ".join(str(past["supplements"]).lower())
    for phrase in ("take 500 mg", "you are deficient", "recommended dose", "you should take", "too much"):
        assert phrase not in prohibited
