from app.domains.official_records.matching import match_recall
from app.domains.official_records.source import (
    SOURCE_ADAPTER_VERSION,
    SOURCE_URL,
    normalise_batch,
    normalise_licence,
    parse_recall_rows,
)


def test_adapter_uses_authoritative_foscos_columns_and_is_deterministic():
    rows = parse_recall_rows({"rows": [{
        "recall_id": "FR-42", "brand_name": "Acme", "batch_lot_no": "B-123",
        "product": "Milk", "license_no": "10001234567890", "recall_status": "Active",
    }]})
    assert SOURCE_URL == "https://foscos.fssai.gov.in/food-recall"
    assert SOURCE_ADAPTER_VERSION.endswith(".v1")
    assert rows[0]["external_record_id"] == "FR-42"
    assert rows[0]["batch_lot"] == "B-123"


def test_pack_match_requires_exact_licence_and_batch_and_never_fuzzy_matches():
    pack = {"fssai_licence": "10001-234567890", "batch_number": " B-123 ", "brand": "Acme", "product_name": "Milk"}
    record = {"licence": "10001234567890", "batch_lot": "b-123", "brand_name": "Acme", "product_name": "Milk"}
    assert normalise_licence(pack["fssai_licence"]) == normalise_licence(record["licence"])
    assert normalise_batch(pack["batch_number"]) == normalise_batch(record["batch_lot"])
    assert match_recall(pack, record) == "matched"
    assert match_recall({**pack, "batch_number": "B 123"}, record) == "identity_mismatch"
    assert match_recall({**pack, "fssai_licence": None}, record) == "not_matched"


def test_brand_or_product_conflict_is_not_a_recall_match():
    pack = {"fssai_licence": "123", "batch_number": "A1", "brand": "Other"}
    record = {"licence": "123", "batch_lot": "A1", "brand_name": "Acme"}
    assert match_recall(pack, record) == "identity_mismatch"
