"""Step 12A — deterministic confirmed-label change authority."""
from __future__ import annotations

import uuid

import pytest
from app.domains.product import change_projection
from app.domains.product.change_projection import (
    FormulaChangeStatus,
    LabelChangeStatus,
    LabelHistoryInvariantError,
    project_label_change,
)
from app.domains.product.confidence import ProductConfidence
from app.domains.product.models import LabelSnapshot
from app.domains.product.service import (
    label_changed_fields,
    label_content_fingerprint,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_product_scan import _seed_label_run, device, off_clean  # noqa: F401

pytestmark = pytest.mark.asyncio


def _snapshot(
    facts: dict,
    *,
    barcode: str = "8900000000012",
    version: int = 1,
    previous: LabelSnapshot | None = None,
) -> LabelSnapshot:
    return LabelSnapshot(
        id=uuid.uuid4(),
        barcode=barcode,
        scan_event_id=uuid.uuid4(),
        facts=facts,
        confidence=ProductConfidence.UNVERIFIED.value,
        content_fingerprint=label_content_fingerprint(facts),
        version_number=version,
        previous_snapshot_id=previous.id if previous else None,
        changed_fields=label_changed_fields(previous.facts, facts) if previous else [],
        completeness="complete_for_grading",
    )


async def test_first_observation_has_no_invented_change():
    current = _snapshot({"ingredients_text": "Water,Glycerin"})
    result = await project_label_change(object(), current=current, previous=None)
    assert result.status is LabelChangeStatus.FIRST_OBSERVED_VERSION
    assert result.changed_fields == ()
    assert result.formula.status is FormulaChangeStatus.NOT_APPLICABLE


async def test_case_only_label_change_does_not_become_an_ingredient_change(db_clean):
    old = _snapshot({"ingredients_text": "Water,Glycerin"})
    new = _snapshot(
        {"ingredients_text": "water,glycerin"}, version=2, previous=old,
    )
    async with get_sessionmaker()() as session:
        result = await project_label_change(session, current=new, previous=old)
    assert result.changed_fields == ("ingredients",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED
    assert result.formula.added == ()
    assert result.formula.removed == ()


async def test_reorder_is_reported_without_calling_it_add_or_remove(db_clean):
    old = _snapshot({"ingredients_text": "Water,Glycerin,Niacinamide"})
    new = _snapshot(
        {"ingredients_text": "Niacinamide,Water,Glycerin"},
        version=2,
        previous=old,
    )
    async with get_sessionmaker()() as session:
        result = await project_label_change(session, current=new, previous=old)
    assert result.formula.status is FormulaChangeStatus.REORDERED_ONLY
    assert result.formula.added == ()
    assert result.formula.removed == ()


async def test_occurrence_delta_preserves_duplicates_and_printed_names(db_clean):
    old = _snapshot({"ingredients_text": "Water,Glycerin,Glycerin"})
    new = _snapshot(
        {"ingredients_text": "Water,Glycerin,Niacinamide,Niacinamide"},
        version=2,
        previous=old,
    )
    async with get_sessionmaker()() as session:
        result = await project_label_change(session, current=new, previous=old)
    assert result.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert [row.as_payload() for row in result.formula.added] == [
        {"name": "Niacinamide", "occurrences": 2},
    ]
    assert [row.as_payload() for row in result.formula.removed] == [
        {"name": "Glycerin", "occurrences": 1},
    ]


async def test_unreadable_formula_is_not_partially_compared(db_clean):
    old = _snapshot({"ingredients_text": "Water,Glycerin"})
    new = _snapshot(
        {"ingredients_text": "Water\nGlycerin"}, version=2, previous=old,
    )
    async with get_sessionmaker()() as session:
        result = await project_label_change(session, current=new, previous=old)
    assert result.formula.status is FormulaChangeStatus.NOT_COMPARABLE
    assert result.formula.added == ()
    assert result.formula.removed == ()
    assert result.formula.current_parse_status.value == "ambiguous_boundary"


async def test_noningredient_pack_change_does_not_query_formula(monkeypatch):
    old = _snapshot({"ingredients_text": "Water", "net_quantity": "100 g"})
    new = _snapshot(
        {"ingredients_text": "Water", "net_quantity": "120 g"},
        version=2,
        previous=old,
    )

    def _must_not_run(*args, **kwargs):
        pytest.fail("formula parser ran even though ingredients did not change")

    monkeypatch.setattr(change_projection, "_formula_parse", _must_not_run)
    result = await project_label_change(object(), current=new, previous=old)
    assert result.changed_fields == ("net_quantity",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_barcode",
        "wrong_previous_id",
        "noncontiguous",
        "same_fingerprint",
        "changed_fields_drift",
        "facts_drift",
    ],
)
async def test_corrupt_history_fails_closed(mutation):
    old = _snapshot({"ingredients_text": "Water"})
    new = _snapshot(
        {"ingredients_text": "Water,Glycerin"}, version=2, previous=old,
    )
    if mutation == "wrong_barcode":
        old.barcode = "8900000000099"
    elif mutation == "wrong_previous_id":
        new.previous_snapshot_id = uuid.uuid4()
    elif mutation == "noncontiguous":
        old.version_number = 0
    elif mutation == "same_fingerprint":
        old.content_fingerprint = new.content_fingerprint
        old.facts = dict(new.facts)
    elif mutation == "changed_fields_drift":
        new.changed_fields = []
    elif mutation == "facts_drift":
        new.facts = {"ingredients_text": "Different"}

    with pytest.raises(LabelHistoryInvariantError):
        await project_label_change(object(), current=new, previous=old)


async def test_projection_never_selects_latest_or_writes(monkeypatch):
    from app.domains.product import service as product_service

    async def _latest_must_not_run(*args, **kwargs):
        pytest.fail("projection selected a latest snapshot")

    monkeypatch.setattr(product_service, "latest_label_snapshot", _latest_must_not_run)

    class Session:
        def add(self, *args, **kwargs):
            pytest.fail("projection attempted a write")

        async def flush(self, *args, **kwargs):
            pytest.fail("projection attempted a write")

        async def commit(self, *args, **kwargs):
            pytest.fail("projection attempted a write")

    old = _snapshot({"ingredients_text": "Water", "net_quantity": "100 g"})
    new = _snapshot(
        {"ingredients_text": "Water", "net_quantity": "120 g"},
        version=2,
        previous=old,
    )
    result = await project_label_change(Session(), current=new, previous=old)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED


async def _confirm(client, device, token, account_id, barcode: str, facts: dict):
    run_id = await _seed_label_run(facts, account_id)
    response = await client.post(
        "/api/v2/scan/label/confirm",
        headers={**device, **auth(token)},
        json={
            "barcode": barcode,
            "ai_run_id": str(run_id),
            "client_scan_id": uuid.uuid4().hex,
        },
    )
    assert response.status_code == 201, response.text


async def test_product_result_exposes_additive_immediate_version_change(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8900000000128"
    token, account_id = await registered_supabase_user()
    first = {
        "product_name": "Observed serum",
        "ingredients_text": "Water,Glycerin",
        "nutrition_per_100g": {"energy_kcal": "10"},
        "nutrition_basis": "per_100g",
    }
    await _confirm(app_client, device, token, account_id, barcode, first)

    first_result = await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )
    assert first_result.status_code == 200, first_result.text
    assert first_result.json()["label_change"]["status"] == "first_observed_version"

    second = {**first, "ingredients_text": "Water,Niacinamide"}
    await _confirm(app_client, device, token, account_id, barcode, second)
    second_result = await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )
    assert second_result.status_code == 200, second_result.text
    change = second_result.json()["label_change"]
    assert change["status"] == "changed"
    assert change["previous_version"] == 1
    assert change["current_version"] == 2
    assert change["changed_fields"] == ["ingredients"]
    assert change["formula"]["status"] == "ingredient_set_changed"
    assert change["formula"]["added"] == [{"name": "Niacinamide", "occurrences": 1}]
    assert change["formula"]["removed"] == [{"name": "Glycerin", "occurrences": 1}]


async def test_a_to_b_to_a_compares_only_the_immediate_predecessor(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8900000000135"
    token, account_id = await registered_supabase_user()
    base = {
        "product_name": "Observed cleanser",
        "nutrition_per_100g": {"energy_kcal": "10"},
        "nutrition_basis": "per_100g",
    }
    for ingredients in ("Water", "Water,Glycerin", "Water"):
        await _confirm(
            app_client, device, token, account_id, barcode,
            {**base, "ingredients_text": ingredients},
        )

    result = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    change = result.json()["label_change"]
    assert change["current_version"] == 3
    assert change["previous_version"] == 2
    assert change["formula"]["removed"] == [{"name": "Glycerin", "occurrences": 1}]

    async with get_sessionmaker()() as session:
        versions = (await session.execute(
            select(LabelSnapshot)
            .where(LabelSnapshot.barcode == barcode)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
    assert [row.version_number for row in versions] == [1, 2, 3]
    assert versions[2].previous_snapshot_id == versions[1].id



async def test_open_food_facts_refresh_never_manufactures_confirmed_label_history(
    db_clean, off_clean, app_client, device, monkeypatch,
):
    from datetime import UTC, datetime, timedelta

    from app.domains.off import client as off_client
    from app.domains.off.models import OffProduct
    from app.domains.off.store import get_off_sessionmaker
    from app.domains.product import service as product_service

    barcode = "8900000000142"
    async with get_off_sessionmaker()() as off_session:
        off_session.add(OffProduct(
            barcode=barcode,
            product_name="Catalogue version A",
            ingredients_text="water",
            fetched_at=datetime.now(UTC) - product_service.OFF_CACHE_TTL - timedelta(days=1),
        ))
        await off_session.commit()

    async def _refresh(_barcode: str):
        return {
            "product_name": "Catalogue version B",
            "brands": "Example",
            "ingredients_text": "water,glycerin",
        }

    monkeypatch.setattr(off_client, "fetch_product", _refresh)
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["facts_provenance"] == "open_food_facts"
    assert payload["label_version"] is None
    assert payload["label_change"] is None


async def test_label_change_is_additive_and_does_not_change_scientific_verdict(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8900000000159"
    token, account_id = await registered_supabase_user()
    base = {
        "product_name": "Observed food",
        "ingredients_text": "Water,Glycerin",
        "nutrition_per_100g": {
            "energy_kcal": "100",
            "sugars_g": "2",
            "salt_g": "0.1",
        },
        "nutrition_basis": "per_100g",
    }
    await _confirm(app_client, device, token, account_id, barcode, base)
    before = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()

    await _confirm(
        app_client, device, token, account_id, barcode,
        {**base, "ingredients_text": "Water,Niacinamide"},
    )
    after = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()

    for key in (
        "grade",
        "band",
        "outcome",
        "decision",
        "negatives",
        "positives",
        "components",
        "evidence",
        "trace",
        "result_contract_version",
    ):
        assert after[key] == before[key], key

    assert before["label_change"]["status"] == "first_observed_version"
    assert after["label_change"]["status"] == "changed"


async def test_reference_mode_keeps_history_explicitly_product_scoped(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8900000000166"
    token, account_id = await registered_supabase_user()
    first = {
        "product_name": "Observed product",
        "ingredients_text": "Water",
        "nutrition_per_100g": {"energy_kcal": "10"},
        "nutrition_basis": "per_100g",
    }
    await _confirm(app_client, device, token, account_id, barcode, first)
    await _confirm(
        app_client, device, token, account_id, barcode,
        {**first, "ingredients_text": "Water,Glycerin"},
    )

    response = await app_client.get(
        f"/api/v2/scan/verdict/{barcode}?physical_pack_context=false",
        headers=device,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["physical_pack_context"] is False
    assert payload["label_change"]["scope"] == "confirmed_label_history"
    assert payload["label_change"]["status"] == "changed"


async def test_corrupt_chain_is_a_governed_public_failure_without_internal_reason(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8900000000173"
    token, account_id = await registered_supabase_user()
    base = {
        "product_name": "Observed product",
        "ingredients_text": "Water",
        "nutrition_per_100g": {"energy_kcal": "10"},
        "nutrition_basis": "per_100g",
    }
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(
        app_client, device, token, account_id, barcode,
        {**base, "ingredients_text": "Water,Glycerin"},
    )

    async with get_sessionmaker()() as session:
        latest = (await session.execute(
            select(LabelSnapshot)
            .where(LabelSnapshot.barcode == barcode)
            .order_by(LabelSnapshot.version_number.desc())
            .limit(1)
        )).scalar_one()
        latest.previous_snapshot_id = uuid.uuid4()
        await session.commit()

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["message"] == "This product history is not available right now."
    assert "label_predecessor" not in response.text
