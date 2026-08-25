"""VC-07 API ownership, confirmation, overlap and export regressions."""
from __future__ import annotations

import pytest

from tests.conftest import auth
from tests.journey import ok

pytestmark = pytest.mark.asyncio


async def test_owned_label_facts_overlap_and_cross_account_isolation(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    headers_a = auth(token_a)
    headers_b = auth(token_b)

    first = ok(await app_client.post(
        "/api/v2/inventory/items", headers=headers_a,
        json={"category": "supplements", "display_name": "Daily C", "details": {"supplement_name": "Daily C"}},
    ))
    second = ok(await app_client.post(
        "/api/v2/inventory/items", headers=headers_a,
        json={"category": "supplements", "display_name": "Travel C", "details": {"supplement_name": "Travel C"}},
    ))

    fact_a = ok(await app_client.post(
        f"/api/v2/supplements/items/{first['id']}/label-facts", headers=headers_a,
        json={"raw_name": "Vitamin C", "amount": "500", "unit": "mg", "serving_text": "Per tablet"},
    ))
    candidate = ok(await app_client.post(
        f"/api/v2/supplements/items/{second['id']}/label-facts", headers=headers_a,
        json={"raw_name": "ascorbic acid", "verification_state": "draft"},
    ))
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers_a))
    assert summary["overlaps"] == []
    confirmed = ok(await app_client.post(
        f"/api/v2/supplements/items/{second['id']}/label-facts/{candidate['id']}/confirm", headers=headers_a,
    ))
    assert confirmed["verification_state"] == "confirmed"
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers_a))
    assert summary["overlaps"][0]["product_count"] == 2
    assert {row["fact"]["amount"] for row in summary["overlaps"][0]["items"]} == {"500", None}
    assert "fingerprint" in summary and summary["utility_version"] == "vc-07-v1"

    assert (await app_client.get(
        f"/api/v2/supplements/items/{first['id']}/label-facts", headers=headers_b,
    )).status_code == 404
    assert str(account_b) not in repr(summary)
    assert str(account_a) not in repr(summary), "account identifiers stay out of customer utility output"

    exported = ok(await app_client.get("/api/v2/privacy/export", headers=headers_a))
    exported_facts = exported["domains"]["routines"]["supplement_label_components"]
    assert any(row["id"] == fact_a["id"] for row in exported_facts)

    assert (await app_client.delete(
        f"/api/v2/supplements/items/{first['id']}/label-facts/{fact_a['id']}", headers=headers_a,
    )).status_code == 200
