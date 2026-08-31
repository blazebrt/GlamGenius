from app.domains.product.complaints import (
    FSSAI_CONSUMER_GRIEVANCE_URL,
    REQUEST_TEMPLATES,
    missing_preparation_fields,
    prepared_fields,
)


def test_fssai_handoff_is_a_structured_request_not_an_accusation():
    assert "request" in REQUEST_TEMPLATES["food_safety"].lower()
    assert "unsafe" not in " ".join(REQUEST_TEMPLATES.values()).lower()
    assert FSSAI_CONSUMER_GRIEVANCE_URL.startswith("https://foscos.fssai.gov.in/")


def test_fssai_handoff_never_invents_missing_pack_facts():
    fields = prepared_fields({"product_name": "Pack", "brand": "Brand"}, None)

    assert missing_preparation_fields(fields) == [
        "batch_number",
        "fssai_licence",
        "photo_asset_id",
    ]
