"""VC-07 API ownership, confirmation, overlap and export regressions."""
from __future__ import annotations

import importlib

import pytest

from tests.conftest import auth
from tests.journey import ok
from app.shared.security.deps import get_current_account

pytestmark = pytest.mark.asyncio


async def test_summary_route_has_one_registered_get_operation():
    supplements = importlib.import_module("app.api.v2.supplements")
    server = importlib.import_module("server")

    routes = [route for route in supplements.router.routes if getattr(route, "path", None) == "/supplements/summary"]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}
    assert list(server.app.openapi()["paths"]["/api/v2/supplements/summary"]) == ["get"]


async def test_every_customer_supplement_route_requires_a_registered_account():
    supplements = importlib.import_module("app.api.v2.supplements")

    routes = [
        route
        for route in supplements.router.routes
        if getattr(route, "path", "").startswith("/supplements")
    ]
    assert routes
    for route in routes:
        dependencies = route.dependant.dependencies
        assert any(dependency.call is get_current_account for dependency in dependencies), route.path


async def test_professional_boundary_requires_registered_account(
    app_client, db_clean, fake_supabase_user, registered_supabase_user,
):
    path = "/api/v2/supplements/professional-boundary"

    anonymous = await app_client.post(path, json={"question": "Can I take this with my medicine?"})
    assert anonymous.status_code == 401

    unregistered_token, _ = fake_supabase_user()
    unregistered = await app_client.post(
        path, headers=auth(unregistered_token), json={"question": "Can I take this with my medicine?"},
    )
    assert unregistered.status_code == 403
    assert unregistered.json()["detail"]["code"] == "REGISTRATION_REQUIRED"

    registered_token, _ = await registered_supabase_user()
    medical = ok(await app_client.post(
        path, headers=auth(registered_token), json={"question": "Can I take this with my medicine?"},
    ))
    assert medical["boundary"] is True
    medical_text = str(medical).lower()
    for prohibited in ("diagnosis", "dosage", "interaction"):
        assert prohibited not in medical_text

    ordinary = ok(await app_client.post(
        path, headers=auth(registered_token), json={"question": "I take this after breakfast."},
    ))
    assert ordinary == {
        "boundary": False,
        "message": "We track supplements as inventory — name, brand, dates and how often you take them.",
    }


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
    fact_b = ok(await app_client.post(
        f"/api/v2/supplements/items/{second['id']}/label-facts", headers=headers_a,
        json={"raw_name": "ascorbic acid"},
    ))
    assert fact_b["verification_state"] == "confirmed"
    assert fact_b["canonical_component_key"] == "vitamin c"
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
    exported_details = exported["domains"]["inventory"]["supplement_details"]
    assert any(row["item_id"] == first["id"] for row in exported_details)

    assert (await app_client.delete(
        f"/api/v2/supplements/items/{first['id']}/label-facts/{fact_a['id']}", headers=headers_a,
    )).status_code == 200


async def test_public_label_fact_contract_rejects_forged_provenance_and_replays_safely(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = ok(await app_client.post(
        "/api/v2/inventory/items", headers=headers,
        json={"category": "supplements", "display_name": "C", "details": {"supplement_name": "C"}},
    ))
    url = f"/api/v2/supplements/items/{item['id']}/label-facts"
    for field, value in {
        "source": "photo_extracted", "verification_state": "draft", "confidence": 0.2,
        "source_ai_run_id": "00000000-0000-0000-0000-000000000000", "model_version": "x", "prompt_version": "x",
    }.items():
        response = await app_client.post(url, headers=headers, json={"raw_name": "Vitamin C", field: value})
        assert response.status_code == 422, field

    payload = {"raw_name": "Vitamin C", "amount": "500", "unit": "mg", "client_mutation_id": "fact-replay"}
    first = ok(await app_client.post(url, headers=headers, json=payload))
    replay = ok(await app_client.post(url, headers=headers, json=payload))
    assert replay["id"] == first["id"]
    mismatch = await app_client.post(url, headers=headers, json={**payload, "amount": "250"})
    assert mismatch.status_code == 422
    patch = await app_client.patch(f"{url}/{first['id']}", headers=headers, json={"verification_state": "draft"})
    assert patch.status_code == 422
