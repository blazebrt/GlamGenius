"""Step 9A append-only decision history and exact guard coverage."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from app.domains.privacy import deletion_service
from app.domains.privacy import export as privacy_export
from app.domains.purchase.identity import PURCHASE_IDENTITY_VERSION, identity_for_candidate
from app.domains.recommendation.models import PurchaseDecision, PurchaseDecisionEvent, ShoppingCandidate
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate


def _candidate(**changes):
    values = dict(
        id=uuid.uuid4(), account_id=uuid.uuid4(), source="manual", category="beauty",
        display_name="Gentle Cleanser", brand="Example Labs", subcategory="cleanser",
        details={"product_type": "cleanser", "purpose": "cleanse", "active_ingredients": ["Glycerin"]},
        verification_state="confirmed", price=Decimal("499.00"), currency="INR",
        product_url="https://shop.example.test/p/gentle-cleanser?utm_source=campaign",
    )
    values.update(changes)
    return ShoppingCandidate(**values)


def test_identity_is_exact_conservative_and_price_or_tracking_does_not_change_it():
    first = identity_for_candidate(_candidate())
    second = identity_for_candidate(_candidate(price=Decimal("599.00"), product_url="https://shop.example.test/p/gentle-cleanser?gclid=x"))
    changed = identity_for_candidate(_candidate(details={"product_type": "serum", "purpose": "treat", "active_ingredients": ["Niacinamide"]}))
    insufficient = identity_for_candidate(_candidate(brand=None, product_url=None, details={"product_type": "cleanser"}))
    assert first["version"] == PURCHASE_IDENTITY_VERSION
    assert first["state"] == second["state"] == "exact"
    assert first["fingerprint"] == second["fingerprint"]
    assert changed["fingerprint"] != first["fingerprint"]
    assert insufficient == {"version": PURCHASE_IDENTITY_VERSION, "state": "insufficient", "fingerprint": None}


def test_care_identity_uses_canonical_active_ingredient_keys_fail_closed():
    canonical = identity_for_candidate(_candidate(details={
        "product_type": "cleanser", "active_ingredients": ["Glycerin", "Niacinamide"],
    }))
    aliases = identity_for_candidate(_candidate(details={
        "product_type": "cleanser", "active_ingredients": [" niacinamide ", "GLYCERINE", "glycerol"],
    }))
    different = identity_for_candidate(_candidate(details={
        "product_type": "cleanser", "active_ingredients": ["Glycerin", "Hyaluronic acid"],
    }))
    unresolved = identity_for_candidate(_candidate(details={
        "product_type": "cleanser", "active_ingredients": ["Glycerin", "Unknown active"],
    }))
    hair = identity_for_candidate(_candidate(category="hair", details={
        "product_type": "cleanser", "active_ingredients": ["Glycerin", "Niacinamide"],
    }))
    assert canonical["state"] == aliases["state"] == "exact"
    assert canonical["fingerprint"] == aliases["fingerprint"]
    assert canonical["fingerprint"] != different["fingerprint"]
    assert unresolved == {"version": PURCHASE_IDENTITY_VERSION, "state": "insufficient", "fingerprint": None}
    assert canonical["fingerprint"] != hair["fingerprint"]


def test_care_identity_fails_closed_for_ambiguous_active_declaration():
    ambiguous = identity_for_candidate(_candidate(details={
        "product_type": "cleanser", "active_ingredients": ["Glycerin and Niacinamide"],
    }))
    assert ambiguous == {"version": PURCHASE_IDENTITY_VERSION, "state": "insufficient", "fingerprint": None}


def test_identity_is_strategy_scoped_and_ignores_fragrance_use_context():
    first = _candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edp", "season": "winter", "occasion": "evening"},
    )
    changed_use = _candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edp", "season": "summer", "occasion": "office", "longevity_user_reported": "long"},
    )
    changed_product = _candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edt"},
    )
    draft_style = _candidate(
        category="beauty", verification_state="draft", subcategory="shirt", size="m", fabric="cotton", colour="blue", details={},
    )
    assert identity_for_candidate(first)["fingerprint"] == identity_for_candidate(changed_use)["fingerprint"]
    assert identity_for_candidate(first)["fingerprint"] != identity_for_candidate(changed_product)["fingerprint"]
    assert identity_for_candidate(draft_style)["state"] == "insufficient"


def test_event_lookup_index_covers_current_decision_idempotency_queries():
    indexes = {index.name: tuple(column.name for column in index.columns) for index in PurchaseDecisionEvent.__table__.indexes}
    assert indexes["ix_purchase_decision_events_decision_created"] == ("decision_id", "created_at")


async def _make_candidate_exact(account_id: uuid.UUID) -> uuid.UUID:
    candidate_id = await _seed_db_candidate(account_id)
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        candidate = await session.get(ShoppingCandidate, candidate_id)
        candidate.brand = "Example Labs"
        candidate.display_name = "Gentle Cleanser"
        candidate.details = {
            "product_type": "cleanser", "purpose": "cleanse",
            "active_ingredients": ["Niacinamide"],
        }
        await session.commit()
    return candidate_id


async def _decide(app_client, token: str, candidate_id: uuid.UUID, decision: str) -> None:
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": decision},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_history_paginates_account_scoped_events(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    first, second = await _make_candidate_exact(account_id), await _make_candidate_exact(account_id)
    await _decide(app_client, token, first, "waiting")
    await _decide(app_client, token, first, "bought")
    await _decide(app_client, token, second, "waiting")
    pages, before = [], None
    while True:
        suffix = f"&before={before}" if before else ""
        response = await app_client.get(f"/api/v2/shopping/decision-history?limit=1{suffix}", headers=auth(token))
        assert response.status_code == 200, response.text
        page = response.json()
        pages.extend(page["items"])
        before = page["next_before"]
        if before is None:
            break
    assert len(pages) == 3
    assert len({row["id"] for row in pages}) == 3
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        expected = (await session.execute(
            select(PurchaseDecisionEvent.id).where(
                PurchaseDecisionEvent.account_id == account_id,
            ).order_by(PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc())
        )).scalars().all()
    assert [row["id"] for row in pages] == [str(event_id) for event_id in expected]
    other_token, _ = await registered_supabase_user()
    assert (await app_client.get(f"/api/v2/shopping/decision-history?before={pages[0]['id']}", headers=auth(other_token))).status_code == 404
    assert (await app_client.get(f"/api/v2/shopping/decision-history?before={uuid.uuid4()}", headers=auth(token))).status_code == 404


@pytest.mark.asyncio
async def test_guard_counts_distinct_candidates_and_excludes_wrong_version_or_strategy(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    first, second = await _make_candidate_exact(account_id), await _make_candidate_exact(account_id)
    await _decide(app_client, token, first, "waiting")
    await _decide(app_client, token, first, "bought")
    initial = (await app_client.get(f"/api/v2/shopping/candidates/{first}/purchase-guard", headers=auth(token))).json()
    assert initial["prior_consideration_count"] == 1
    await _decide(app_client, token, second, "waiting")
    guard = (await app_client.get(f"/api/v2/shopping/candidates/{first}/purchase-guard", headers=auth(token))).json()
    assert guard["prior_consideration_count"] == 2
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        event = await session.scalar(select(PurchaseDecisionEvent).where(PurchaseDecisionEvent.candidate_id == second))
        event.identity_version = "untrusted-version"
        event.strategy_key = "fragrance_purchase"
        await session.commit()
    isolated = (await app_client.get(f"/api/v2/shopping/candidates/{first}/purchase-guard", headers=auth(token))).json()
    assert isolated["prior_consideration_count"] == 1


@pytest.mark.asyncio
async def test_guard_reports_truthful_legacy_and_new_coverage(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    legacy_id, new_id = await _make_candidate_exact(account_id), await _make_candidate_exact(account_id)
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        session.add(PurchaseDecision(
            account_id=account_id, candidate_id=legacy_id, evaluation_id=None,
            strategy_key="care_purchase", recommendation_verdict="wait",
            recommendation_version="legacy", recommendation_snapshot={}, decision="waiting",
            followed_recommendation=True,
        ))
        await session.commit()
    legacy = await app_client.get(f"/api/v2/shopping/candidates/{legacy_id}/purchase-guard", headers=auth(token))
    fresh = await app_client.get(f"/api/v2/shopping/candidates/{new_id}/purchase-guard", headers=auth(token))
    assert legacy.json()["guard_state"] == "historical_context_incomplete"
    assert fresh.json()["guard_state"] == "no_step9a_prior_event"
    assert fresh.json()["history_coverage"]["state"] == "step_9a_events_only"


@pytest.mark.asyncio
async def test_purchase_events_export_and_delete_through_real_state_machine(app_client, db_clean, registered_supabase_user, monkeypatch):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    await _decide(app_client, token, candidate_id, "waiting")
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        bundle = await privacy_export.build_export(session, account_id)
        events = bundle["domains"]["shopping"]["decision_events"]
        assert len(events) == 1 and events[0]["account_id"] == str(account_id)

    async def no_external(*_args, **_kwargs):
        return None
    monkeypatch.setattr(deletion_service, "_delete_supabase_identity", no_external)
    monkeypatch.setattr(deletion_service, "_remove_external_integrations", no_external)
    async with get_sessionmaker()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
        await deletion_service.drain_all(session)
        await session.commit()
    async with get_sessionmaker()() as session:
        remaining = await session.scalar(select(func.count()).select_from(PurchaseDecisionEvent).where(PurchaseDecisionEvent.account_id == account_id))
    assert remaining == 0


@pytest.mark.asyncio
async def test_concurrent_candidate_retries_append_one_event(
    app_client, db_clean, registered_supabase_user,
):
    """The real route sessions prove database locks, not a process-local mutex."""
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    responses = await asyncio.gather(
        _decide(app_client, token, candidate_id, "waiting"),
        _decide(app_client, token, candidate_id, "waiting"),
    )
    assert responses == [None, None]
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        care_rows = await session.scalar(select(func.count()).select_from(PurchaseDecision).where(
            PurchaseDecision.account_id == account_id,
            PurchaseDecision.candidate_id == candidate_id,
        ))
        care_events = await session.scalar(select(func.count()).select_from(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.candidate_id == candidate_id,
        ))
    assert care_rows == care_events == 1


def test_identity_canonicalization_is_deterministic_but_not_fuzzy():
    canonical = identity_for_candidate(_candidate(
        brand=" Example   Labs ",
        display_name=" Gentle   Cleanser ",
        details={"product_type": " Cleanser ", "active_ingredients": ["Niacinamide", " Glycerin "]},
    ))
    equivalent = identity_for_candidate(_candidate(
        brand="example labs",
        display_name="gentle cleanser",
        details={"product_type": "cleanser", "active_ingredients": ["glycerin", "NIACINAMIDE", "glycerin"]},
    ))
    materially_different = identity_for_candidate(_candidate(
        brand="example labs",
        display_name="gentle cleanser plus",
        details={"product_type": "cleanser", "active_ingredients": ["glycerin", "niacinamide"]},
    ))
    assert canonical == equivalent
    assert materially_different["state"] == "exact"
    assert materially_different["fingerprint"] != canonical["fingerprint"]


def test_identity_insufficiency_matrix_and_category_isolation():
    insufficient = [
        _candidate(brand=None),
        _candidate(display_name="   "),
        _candidate(details={"active_ingredients": ["Glycerin"]}),
        _candidate(details={"product_type": "cleanser", "active_ingredients": []}),
        _candidate(category="perfumes", details={"fragrance_family": "woody"}),
        _candidate(category="perfumes", details={"concentration": "edp"}),
        _candidate(category="beauty", subcategory=None, size="m", fabric="cotton", colour="blue", details={}),
        _candidate(category="beauty", subcategory="shirt", size=None, fabric="cotton", colour="blue", details={}),
        _candidate(category="beauty", subcategory="shirt", size="m", fabric=None, colour="blue", details={}),
        _candidate(category="beauty", subcategory="shirt", size="m", fabric="cotton", colour=None, details={}),
        _candidate(category="beauty", verification_state="draft", subcategory="shirt", size="m", fabric="cotton", colour="blue", details={}),
        _candidate(uncertain_fields=["brand"]),
    ]
    assert all(identity_for_candidate(candidate)["state"] == "insufficient" for candidate in insufficient)

    beauty = identity_for_candidate(_candidate(category="beauty"))
    hair = identity_for_candidate(_candidate(category="hair"))
    assert beauty["state"] == hair["state"] == "exact"
    assert beauty["fingerprint"] != hair["fingerprint"]

    style_m = identity_for_candidate(_candidate(
        category="beauty", subcategory="shirt", size="m", fabric="cotton", colour="blue", details={},
    ))
    style_l = identity_for_candidate(_candidate(
        category="beauty", subcategory="shirt", size="l", fabric="cotton", colour="blue", details={},
    ))
    fragrance_edp = identity_for_candidate(_candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edp"},
    ))
    fragrance_edt = identity_for_candidate(_candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edt"},
    ))
    assert style_m["fingerprint"] != style_l["fingerprint"]
    assert fragrance_edp["fingerprint"] != fragrance_edt["fingerprint"]


@pytest.mark.asyncio
async def test_meaningful_transitions_append_without_rewriting_prior_event(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    await _decide(app_client, token, candidate_id, "waiting")
    await _decide(app_client, token, candidate_id, "bought")
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        events = (await session.execute(
            select(PurchaseDecisionEvent).where(
                PurchaseDecisionEvent.account_id == account_id,
                PurchaseDecisionEvent.candidate_id == candidate_id,
            ).order_by(PurchaseDecisionEvent.created_at.asc(), PurchaseDecisionEvent.id.asc())
        )).scalars().all()
        current = await session.scalar(select(PurchaseDecision).where(
            PurchaseDecision.account_id == account_id,
            PurchaseDecision.candidate_id == candidate_id,
        ))
    assert [event.decision for event in events] == ["waiting", "bought"]
    assert current is not None and current.decision == "bought"


@pytest.mark.asyncio
async def test_event_write_failure_rolls_back_mutable_decision(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    from app.domains.purchase import decision_memory
    from app.shared.database.sql import get_sessionmaker

    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)

    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("forced Step 9A event failure")

    monkeypatch.setattr(decision_memory, "record_decision_event", fail_event)
    with pytest.raises(RuntimeError, match="forced Step 9A event failure"):
        await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
            headers=auth(token), json={"decision": "waiting"},
        )

    async with get_sessionmaker()() as session:
        decisions = await session.scalar(select(func.count()).select_from(PurchaseDecision).where(
            PurchaseDecision.account_id == account_id,
            PurchaseDecision.candidate_id == candidate_id,
        ))
        events = await session.scalar(select(func.count()).select_from(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.account_id == account_id,
            PurchaseDecisionEvent.candidate_id == candidate_id,
        ))
    assert decisions == events == 0


@pytest.mark.asyncio
async def test_history_same_timestamp_cursor_and_limits_are_safe(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    first, second = await _make_candidate_exact(account_id), await _make_candidate_exact(account_id)
    await _decide(app_client, token, first, "waiting")
    await _decide(app_client, token, first, "bought")
    await _decide(app_client, token, second, "waiting")

    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.account_id == account_id,
        ))).scalars().all()
        anchor = rows[0].created_at
        for row in rows:
            row.created_at = anchor
        await session.commit()
        expected = [str(event_id) for event_id in (await session.execute(
            select(PurchaseDecisionEvent.id).where(
                PurchaseDecisionEvent.account_id == account_id,
            ).order_by(PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc())
        )).scalars().all()]

    seen, before = [], None
    while True:
        suffix = f"&before={before}" if before else ""
        response = await app_client.get(
            f"/api/v2/shopping/decision-history?limit=1{suffix}", headers=auth(token),
        )
        assert response.status_code == 200, response.text
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        before = page["next_before"]
        if before is None:
            break
    assert seen == expected
    assert len(seen) == len(set(seen)) == 3
    assert (await app_client.get("/api/v2/shopping/decision-history?limit=50", headers=auth(token))).status_code == 200
    assert (await app_client.get("/api/v2/shopping/decision-history?limit=0", headers=auth(token))).status_code == 422
    assert (await app_client.get("/api/v2/shopping/decision-history?limit=51", headers=auth(token))).status_code == 422


@pytest.mark.asyncio
async def test_purchase_guard_refuses_cross_account_candidate(
    app_client, db_clean, registered_supabase_user,
):
    _, owner_account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(owner_account_id)
    other_token, _ = await registered_supabase_user()
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard",
        headers=auth(other_token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_account_deletion_preserves_other_accounts_purchase_events(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    other_token, other_account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    other_candidate_id = await _make_candidate_exact(other_account_id)
    await _decide(app_client, token, candidate_id, "waiting")
    await _decide(app_client, other_token, other_candidate_id, "waiting")

    async def no_external(*_args, **_kwargs):
        return None

    monkeypatch.setattr(deletion_service, "_delete_supabase_identity", no_external)
    monkeypatch.setattr(deletion_service, "_remove_external_integrations", no_external)
    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
        await deletion_service.drain_all(session)
        await session.commit()
    async with get_sessionmaker()() as session:
        deleted_count = await session.scalar(select(func.count()).select_from(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.account_id == account_id,
        ))
        survivor_count = await session.scalar(select(func.count()).select_from(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.account_id == other_account_id,
        ))
    assert deleted_count == 0
    assert survivor_count == 1
