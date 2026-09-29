"""Lane E — the privacy export is complete, SQL-scoped, uncapped, and honest.

``INCLUDED`` in the privacy registry is a promise that the account holder can
read the table back. Before this lane, 33 of the 111 ``INCLUDED`` tables were
read by no export handler at all; every collection was cut at 20 000 rows with
no sign in the file; a failed domain was replaced by a marker and the route
still answered 200 and recorded a successful export; and a label-error
report's internal storage key was exported verbatim.

This file holds the repaired contract against a real PostgreSQL database:

* the coverage contract has exactly one entry per ``INCLUDED`` table, is
  internally consistent, and is load-bearing (drift refuses the export);
* every contract-exported table's real row reaches its owner's file — and
  another account's row, reached through that account's own parents, never
  does; a reference to another account's row is stripped, not echoed;
* the historical audit case (a beauty item's details, usage and condition
  notes) is in the file;
* more than 20 000 rows of one collection all arrive;
* a failed domain answers 503 and records no successful export, and a retry
  succeeds and records exactly one;
* no storage path, credential or token appears anywhere in the file, and the
  whole file serialises with the standard JSON encoder;
* building the export changes nothing.

Step 15 adds one ``INCLUDED`` table, ``consumer_referral_invites`` (exported as
``growth.referral.issued_codes``, scoped through the inviting account), and one
withheld column, ``app_events.client_event_id``; the proofs for both are at the
end of this file.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from app.bootstrap import seed_inventory_categories
from app.domains.analytics.models import AppEvent
from app.domains.audit.models import ACTION_PRIVACY_EXPORTED, AuditEvent
from app.domains.identity import service as identity
from app.domains.inventory.models import (
    AccessoryItemDetail,
    BeautyProductDetail,
    DuplicateCandidate,
    HairProductDetail,
    InventoryItem,
    InventoryItemImage,
    InventoryValueEvent,
    ItemConditionEvent,
    ItemExpiryEvent,
    ItemRelationship,
    ItemUsageEvent,
    PerfumeDetail,
    ShoeItemDetail,
    WardrobeItemDetail,
)
from app.domains.media.models import MEDIA_PURPOSE_ANALYSIS, MEDIA_PURPOSE_INVENTORY, MediaAsset
from app.domains.planning.models import (
    AirQualitySnapshot,
    DailyPlan,
    DailyPlanAction,
    DailyPlanInput,
    ExternalIntegration,
    LaundryStateEvent,
    NotificationDelivery,
    NotificationDevice,
    OutfitSchedule,
    PlanRecalculationEvent,
    WeeklyPlan,
    WeeklyPlanDay,
)
from app.domains.privacy import REGISTRY, Classification, included_tables
from app.domains.privacy import export as export_mod
from app.domains.privacy.coverage import (
    EXPORT_COVERAGE,
    ExportCoverage,
    Handler,
    Scope,
    coverage_drift,
)
from app.domains.product.models import FssaiComplaintHandoff, LabelErrorReport, ScanDevice
from app.domains.progress.models import (
    ComparisonSession,
    GoalUpdate,
    MemoryCategoryPreference,
    MetricEvent,
    ProgressGoal,
    ProgressSnapshot,
    ScoreExplanation,
    Streak,
)
from app.domains.recommendation.models import (
    CompatibilityEdge,
    Look,
    LookItem,
    PurchaseEvaluation,
    PurchaseEvaluationFactor,
    RecommendationEntitlement,
    RecommendationInput,
    RecommendationRun,
    ShoppingCandidate,
)
from app.shared.database.registry import Base
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, text

pytestmark = pytest.mark.asyncio

#: The exact gap this lane closed, computed mechanically on base 2651f3f0:
#: tables the registry classified INCLUDED that no export handler selected.
PRE_LANE_E_UNCOVERED = frozenset({
    "accessory_item_details", "air_quality_snapshots", "app_events",
    "beauty_product_details", "comparison_sessions", "compatibility_edges",
    "daily_plan_actions", "daily_plan_inputs", "duplicate_candidates",
    "fssai_complaint_handoffs", "goal_updates", "hair_product_details",
    "inventory_item_images", "inventory_value_events", "item_condition_events",
    "item_expiry_events", "item_relationships", "item_usage_events",
    "laundry_state_events", "look_items", "memory_category_preferences",
    "outfit_schedule", "perfume_details", "plan_recalculation_events",
    "progress_snapshots", "purchase_evaluation_factors",
    "recommendation_entitlements", "recommendation_inputs", "score_explanations",
    "shoe_item_details", "streaks", "wardrobe_item_details", "weekly_plan_days",
})

CONTRACT_TABLES = sorted(
    table for table, entry in EXPORT_COVERAGE.items() if entry.handler == Handler.CONTRACT
)

TODAY = date(2026, 9, 1)


def _tables() -> dict[str, Any]:
    return dict(Base.metadata.tables)


def _json(value: Any) -> Any:
    """How the export writes a Python value."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _keys(payload: Any) -> set[str]:
    """Every dict key anywhere in a payload."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= _keys(value)
    return found


# ---------------------------------------------------------------------------
# The contract itself
# ---------------------------------------------------------------------------

async def test_every_included_table_has_exactly_one_export_contract():
    """``set(EXPORT_COVERAGE) == included_tables()``, both ways."""
    assert set(EXPORT_COVERAGE) == included_tables()
    assert coverage_drift() == (set(), set())
    # Lane E's 111, plus Step 15's ``consumer_referral_invites``. Nothing was
    # reclassified to keep the old count.
    assert len(EXPORT_COVERAGE) == 112


async def test_the_lane_e_gap_is_closed_by_real_contracts():
    """The 33 previously unexported tables are exported by the generic exporter."""
    assert included_tables() >= PRE_LANE_E_UNCOVERED
    assert set(CONTRACT_TABLES) == PRE_LANE_E_UNCOVERED


async def test_no_included_table_was_reclassified_to_close_the_gap():
    for table in PRE_LANE_E_UNCOVERED:
        assert REGISTRY[table] == Classification.INCLUDED, table


async def test_every_contract_names_a_real_domain_and_an_sql_scope():
    tables = _tables()
    for table, entry in EXPORT_COVERAGE.items():
        assert entry.domain in export_mod.DOMAIN_HANDLERS, table
        assert entry.paths, table
        columns = tables[table].columns
        if table == "accounts":
            assert entry.scope == Scope.ACCOUNT_ROW
            continue
        if "account_id" in columns:
            # A row that carries its owner is scoped by it directly.
            assert entry.scope == Scope.ACCOUNT, table
            assert entry.parent is None, table
        else:
            assert entry.scope == Scope.PARENT, table
            fk, parent = entry.parent
            assert parent in EXPORT_COVERAGE, table
            targets = {f.column.table.name for f in columns[fk].foreign_keys}
            assert targets == {parent}, (table, fk, targets)
        for column in entry.withheld:
            assert column in columns, (table, column)


async def test_contract_rows_declare_every_reference_to_account_owned_data():
    """A foreign key from a contract row to another account-owned table is
    declared, so the exporter checks the referenced row is this account's
    before writing its id into the file."""
    tables = _tables()
    included = included_tables()
    for table in CONTRACT_TABLES:
        entry = EXPORT_COVERAGE[table]
        declared = dict(entry.references)
        parent_fk = entry.parent[0] if entry.parent else None
        for column in tables[table].columns:
            targets = {f.column.table.name for f in column.foreign_keys} & included
            targets.discard("accounts")
            if not targets or column.name == parent_fk:
                continue
            assert declared.get(column.name) in targets, (table, column.name, targets)
        for column, target in entry.references:
            assert {f.column.table.name for f in tables[table].columns[column].foreign_keys} == {target}
        for column, target in entry.reference_lists:
            assert column in tables[table].columns and target in EXPORT_COVERAGE


async def test_an_included_table_without_a_contract_refuses_the_export(monkeypatch):
    """The future developer's mistake: classified INCLUDED, exported by nothing."""
    monkeypatch.setitem(REGISTRY, "some_new_table", Classification.INCLUDED)
    assert coverage_drift() == ({"some_new_table"}, set())
    with pytest.raises(export_mod.PrivacyExportIncomplete) as refused:
        await export_mod.build_export(None, uuid.uuid4())  # refused before any query
    assert refused.value.unproven == ("some_new_table:no_export_contract",)


async def test_a_contract_for_a_table_that_is_not_included_refuses_the_export(monkeypatch):
    monkeypatch.setitem(
        EXPORT_COVERAGE, "invites",
        ExportCoverage(domain="identity", paths=("x",), scope=Scope.ACCOUNT, handler=Handler.DOMAIN),
    )
    assert coverage_drift() == (set(), {"invites"})
    with pytest.raises(export_mod.PrivacyExportIncomplete) as refused:
        await export_mod.build_export(None, uuid.uuid4())
    assert refused.value.unproven == ("invites:not_included",)


# ---------------------------------------------------------------------------
# One owned graph per account
# ---------------------------------------------------------------------------

@dataclass
class Graph:
    """One account's rows: the parents, and one row per contract table."""

    account_id: uuid.UUID
    tag: str
    item: uuid.UUID = None
    other_item: uuid.UUID = None
    media: uuid.UUID = None
    look: uuid.UUID = None
    rows: dict[str, tuple[uuid.UUID, dict[str, Any]]] = field(default_factory=dict)

    def sentinel(self, label: str) -> str:
        return f"lane-e-{self.tag}-{label}"


async def _register(session, account_id: uuid.UUID) -> None:
    await identity.register_account(session, account_id)


async def _seed_graph(session, account_id: uuid.UUID, tag: str) -> Graph:
    """Every contract table, with a real row under this account's own parents."""
    graph = Graph(account_id=account_id, tag=tag)
    s = graph.sentinel
    item = InventoryItem(account_id=account_id, category="beauty", display_name=s("item"))
    other_item = InventoryItem(account_id=account_id, category="hair", display_name=s("item-2"))
    media = MediaAsset(
        account_id=account_id, storage_backend="local",
        storage_key=f"media/{account_id}/{s('media-key')}.jpg", content_type="image/jpeg",
        byte_size=10, sha256=hashlib.sha256(s("a").encode()).hexdigest(), purpose=MEDIA_PURPOSE_INVENTORY,
    )
    media_2 = MediaAsset(
        account_id=account_id, storage_backend="local",
        storage_key=f"media/{account_id}/{s('media-key-2')}.jpg", content_type="image/jpeg",
        byte_size=10, sha256=hashlib.sha256(s("b").encode()).hexdigest(), purpose=MEDIA_PURPOSE_ANALYSIS,
    )
    run = RecommendationRun(account_id=account_id, kind="occasion")
    session.add_all([item, other_item, media, media_2, run])
    await session.flush()
    look = Look(account_id=account_id, run_id=run.id, variant="safe", title=s("look"), why_it_works=s("why"))
    candidate = ShoppingCandidate(account_id=account_id, source="manual", category="beauty", display_name=s("candidate"))
    daily = DailyPlan(account_id=account_id, plan_date=TODAY, cache_key=uuid.uuid4().hex)
    weekly = WeeklyPlan(account_id=account_id, week_start=TODAY)
    goal = ProgressGoal(account_id=account_id, kind="consistency", title=s("goal"), starts_on=TODAY)
    metric = MetricEvent(
        account_id=account_id, metric_key="routine_consistency", formula_version="v1",
        period_start=TODAY, unit="percent", dedup_hash=uuid.uuid4().hex,
    )
    session.add_all([look, candidate, daily, weekly, goal, metric])
    await session.flush()
    evaluation = PurchaseEvaluation(
        account_id=account_id, candidate_id=candidate.id, run_id=run.id, verdict="BUY",
        roi_score=0.5, summary=s("summary"),
    )
    session.add(evaluation)
    await session.flush()
    graph.item, graph.other_item, graph.media, graph.look = item.id, other_item.id, media.id, look.id

    specs: dict[str, tuple[type, dict[str, Any]]] = {
        "wardrobe_item_details": (WardrobeItemDetail, dict(
            item_id=item.id, colour=s("colour"), fabric=s("fabric"), season=["winter"],
            occasion=[s("occasion")], care_instructions=s("care"),
        )),
        "shoe_item_details": (ShoeItemDetail, dict(
            item_id=item.id, shoe_type=s("shoe"), size="8", heel_height=Decimal("2.5"),
            occasion=[s("shoe-occasion")], weather_suitability=["dry"],
        )),
        "accessory_item_details": (AccessoryItemDetail, dict(
            item_id=item.id, accessory_type=s("accessory"), metal="silver", occasion=[s("acc-occasion")],
        )),
        "beauty_product_details": (BeautyProductDetail, dict(
            item_id=item.id, product_type="serum", purpose=s("purpose"), ingredients_text=s("ingredients"),
            active_ingredients=["niacinamide"], opened_date=TODAY, remaining_percent=40,
        )),
        "hair_product_details": (HairProductDetail, dict(
            item_id=other_item.id, product_type="oil", purpose=s("hair-purpose"),
            active_ingredients=["argan"], use_frequency=s("weekly"),
        )),
        "perfume_details": (PerfumeDetail, dict(
            item_id=item.id, fragrance_family=s("family"), season=["summer"], occasion=["evening"],
            longevity_user_reported=s("longevity"),
        )),
        "inventory_item_images": (InventoryItemImage, dict(item_id=item.id, media_asset_id=media.id, position=1)),
        "inventory_value_events": (InventoryValueEvent, dict(
            item_id=item.id, metric_version="v1", estimated_value=Decimal("499.50"), currency="INR",
            inputs={"mrp": 499.5, "note": s("value-input")}, explanation=s("value"),
        )),
        "item_condition_events": (ItemConditionEvent, dict(item_id=item.id, condition="good", note=s("condition-note"))),
        "item_expiry_events": (ItemExpiryEvent, dict(
            item_id=item.id, expiry_date=date(2027, 1, 1), source="label", note=s("expiry-note"),
        )),
        "item_usage_events": (ItemUsageEvent, dict(item_id=item.id, used_on=TODAY, quantity=2, note=s("usage-note"))),
        "item_relationships": (ItemRelationship, dict(
            account_id=account_id, from_item_id=item.id, to_item_id=other_item.id, relationship_type=s("pairs"),
        )),
        "duplicate_candidates": (DuplicateCandidate, dict(
            account_id=account_id, item_a_id=item.id, item_b_id=other_item.id, confidence=0.8, reason=s("duplicate"),
        )),
        "laundry_state_events": (LaundryStateEvent, dict(
            account_id=account_id, item_id=item.id, state="clean", note=s("laundry"),
        )),
        "recommendation_inputs": (RecommendationInput, dict(
            run_id=run.id, input_type="occasion", input_key="occasion", value={"note": s("input")}, source="user",
        )),
        "recommendation_entitlements": (RecommendationEntitlement, dict(
            account_id=account_id, feature=s("feature"), period_key="2026-09", included=3, used=1,
        )),
        "look_items": (LookItem, dict(
            look_id=look.id, slot="top", ownership="owned", inventory_item_id=item.id, display_name=s("look-item"),
        )),
        "outfit_schedule": (OutfitSchedule, dict(
            account_id=account_id, plan_date=TODAY, look_id=look.id, item_ids=[str(item.id)], note=s("outfit"),
        )),
        "compatibility_edges": (CompatibilityEdge, dict(
            account_id=account_id, item_a_id=item.id, item_b_id=other_item.id, score=0.7, basis={"why": s("compat")},
        )),
        "purchase_evaluation_factors": (PurchaseEvaluationFactor, dict(
            evaluation_id=evaluation.id, factor_key="price", label=s("factor"), raw_value=1.0, weight=0.5,
            contribution=0.5, explanation=s("factor-why"),
        )),
        "daily_plan_actions": (DailyPlanAction, dict(
            plan_id=daily.id, module="care", action_type="step", title=s("action"), inventory_item_id=item.id,
        )),
        "daily_plan_inputs": (DailyPlanInput, dict(
            plan_id=daily.id, input_type="weather", input_key="uv", value={"v": s("plan-input")}, source="provider",
        )),
        "weekly_plan_days": (WeeklyPlanDay, dict(
            weekly_plan_id=weekly.id, plan_date=TODAY, daily_plan_id=daily.id, note=s("week-day"),
        )),
        "air_quality_snapshots": (AirQualitySnapshot, dict(
            account_id=account_id, for_date=TODAY, aqi=123, index_system="IN_NAQI", location=s("city"),
            raw={"k": s("aq-raw")},
        )),
        "plan_recalculation_events": (PlanRecalculationEvent, dict(
            account_id=account_id, plan_date=TODAY, trigger="weather", detail=s("recalc"), changed_keys=["uv"],
        )),
        "goal_updates": (GoalUpdate, dict(
            goal_id=goal.id, account_id=account_id, value=3.0, note=s("goal-update"), recorded_on=TODAY,
        )),
        "progress_snapshots": (ProgressSnapshot, dict(
            account_id=account_id, period="week", period_start=TODAY, period_end=date(2026, 9, 7),
            metrics={"m": s("snapshot")},
        )),
        "comparison_sessions": (ComparisonSession, dict(
            account_id=account_id, baseline_media_id=media.id, current_media_id=media_2.id, body_area="face",
            checks={"note": s("comparison")},
        )),
        "score_explanations": (ScoreExplanation, dict(
            account_id=account_id, metric_event_id=metric.id, metric_key="routine_consistency",
            formula_version="v1", formula="a/b", plain_english=s("explained"),
        )),
        "streaks": (Streak, dict(
            account_id=account_id, behaviour=s("streak"), current_length=4, longest_length=9, started_on=TODAY,
        )),
        "memory_category_preferences": (MemoryCategoryPreference, dict(
            account_id=account_id, category=s("cat"), enabled=False,
        )),
        "app_events": (AppEvent, dict(account_id=account_id, name=s("event"), properties={"screen": s("props")})),
        "fssai_complaint_handoffs": (FssaiComplaintHandoff, dict(
            account_id=account_id, barcode="8901234567890", reason="label_information", product_name=s("product"),
            brand=s("brand"), batch_number=s("batch"), fssai_licence="10012345678901", photo_asset_id=media.id,
        )),
    }
    assert set(specs) == set(CONTRACT_TABLES), "every contract table needs a real row here"
    for table, (model, values) in specs.items():
        row = model(**values)
        session.add(row)
        await session.flush()
        graph.rows[table] = (row.id, values)
    return graph


@pytest_asyncio.fixture
async def two_graphs(db_clean):
    """Account A and account B, each with a complete owned graph."""
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        await seed_inventory_categories(session)
        await _register(session, account_a)
        await _register(session, account_b)
        graph_a = await _seed_graph(session, account_a, "A")
        graph_b = await _seed_graph(session, account_b, "B")
        await session.commit()
    async with get_sessionmaker()() as session:
        export_a = await export_mod.build_export(session, account_a)
    return graph_a, graph_b, export_a


# ---------------------------------------------------------------------------
# Real rows reach their owner, and only their owner
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", CONTRACT_TABLES)
async def test_a_repaired_table_is_in_its_owners_export_and_nobody_elses(table, two_graphs):
    graph_a, graph_b, export_a = two_graphs
    entry = EXPORT_COVERAGE[table]
    rows = export_a["domains"][entry.domain][entry.key]
    by_id = {row["id"]: row for row in rows}

    a_id, values = graph_a.rows[table]
    b_id, _ = graph_b.rows[table]
    assert str(a_id) in by_id, f"{table}: A's own row is missing from A's export"
    assert str(b_id) not in by_id, f"{table}: B's row is in A's export"

    row = by_id[str(a_id)]
    expected_columns = {c.name for c in _tables()[table].columns} - set(entry.withheld)
    assert set(row) == expected_columns, table
    for column, value in values.items():
        assert row[column] == _json(value), (table, column)


async def test_nothing_of_account_b_appears_anywhere_in_account_as_file(two_graphs):
    graph_a, graph_b, export_a = two_graphs
    dumped = json.dumps(export_a)
    assert "lane-e-B-" not in dumped
    for b_id, _ in graph_b.rows.values():
        assert str(b_id) not in dumped
    for b_parent in (graph_b.account_id, graph_b.item, graph_b.other_item, graph_b.media, graph_b.look):
        assert str(b_parent) not in dumped
    # And A's own sentinels are all there: one per contract row at least.
    for table in CONTRACT_TABLES:
        a_id, _ = graph_a.rows[table]
        assert str(a_id) in dumped, table


async def test_a_parent_owned_child_is_scoped_by_its_parent_in_sql(two_graphs):
    """B's children hang off B's own items, looks, runs and plans. Every one of
    them is excluded from A's file because the SQL only reaches A's parents —
    and A's children are present, under A's parents."""
    graph_a, graph_b, export_a = two_graphs
    for table in CONTRACT_TABLES:
        entry = EXPORT_COVERAGE[table]
        if entry.scope != Scope.PARENT:
            continue
        exported = {row["id"] for row in export_a["domains"][entry.domain][entry.key]}
        assert str(graph_a.rows[table][0]) in exported, table
        assert str(graph_b.rows[table][0]) not in exported, table


async def test_a_withheld_column_never_leaves_through_the_contract_exporter(db_clean, monkeypatch):
    """Withholding a column holds for any contract table, not only the one
    that does it today (``app_events.client_event_id``, proven below).

    The contract is widened here, for this test only, to withhold one
    column, and the column must be absent from every row while the rest of
    the row is still there.
    """
    from dataclasses import replace

    account_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await seed_inventory_categories(session)
        await _register(session, account_id)
        graph = await _seed_graph(session, account_id, "A")
        await session.commit()
    entry = EXPORT_COVERAGE["item_usage_events"]
    monkeypatch.setitem(EXPORT_COVERAGE, "item_usage_events", replace(entry, withheld=("note",)))

    async with get_sessionmaker()() as session:
        payload = await export_mod.build_export(session, account_id)

    (row,) = payload["domains"]["inventory"]["item_usage_events"]
    assert row["id"] == str(graph.rows["item_usage_events"][0])
    assert "note" not in row and row["quantity"] == 2
    assert "lane-e-A-usage-note" not in json.dumps(payload)


async def test_a_reference_to_another_accounts_row_is_stripped_not_echoed(db_clean):
    """Broken invariants: A's own rows pointing at B's item, photo and look.

    The rows are A's and stay in A's file. The ids are B's, so they are
    replaced with nothing and the row says why.
    """
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        await seed_inventory_categories(session)
        await _register(session, account_a)
        await _register(session, account_b)
        graph_a = await _seed_graph(session, account_a, "A")
        graph_b = await _seed_graph(session, account_b, "B")
        relation = ItemRelationship(
            account_id=account_a, from_item_id=graph_a.item, to_item_id=graph_b.item, relationship_type="cross",
        )
        image = InventoryItemImage(item_id=graph_a.other_item, media_asset_id=graph_b.media, position=2)
        look_item = LookItem(
            look_id=graph_a.look, slot="shoes", ownership="owned", inventory_item_id=graph_b.item,
            display_name="cross",
        )
        schedule = OutfitSchedule(
            account_id=account_a, plan_date=date(2026, 9, 2), look_id=graph_b.look,
            item_ids=[str(graph_a.item), str(graph_b.item), "not-an-id"],
        )
        session.add_all([relation, image, look_item, schedule])
        await session.commit()
        ids = relation.id, image.id, look_item.id, schedule.id

    async with get_sessionmaker()() as session:
        export_a = await export_mod.build_export(session, account_a)

    inventory = export_a["domains"]["inventory"]
    styling = export_a["domains"]["quiz_and_styling"]
    relation_row = next(r for r in inventory["item_relationships"] if r["id"] == str(ids[0]))
    assert relation_row["from_item_id"] == str(graph_a.item)
    assert relation_row["to_item_id"] is None
    assert relation_row["invalid_references"] == ["to_item_id"]
    image_row = next(r for r in inventory["inventory_item_images"] if r["id"] == str(ids[1]))
    assert image_row["media_asset_id"] is None
    assert image_row["invariant"] == "reference_ownership_invalid"
    look_row = next(r for r in styling["look_items"] if r["id"] == str(ids[2]))
    assert look_row["inventory_item_id"] is None
    schedule_row = next(r for r in styling["outfit_schedule"] if r["id"] == str(ids[3]))
    assert schedule_row["look_id"] is None
    assert schedule_row["item_ids"] == [str(graph_a.item)]
    assert sorted(schedule_row["invalid_references"]) == ["item_ids", "look_id"]

    dumped = json.dumps(export_a)
    for foreign in (graph_b.item, graph_b.media, graph_b.look, graph_b.account_id):
        assert str(foreign) not in dumped


# ---------------------------------------------------------------------------
# The historical audit case, through the real route
# ---------------------------------------------------------------------------

async def test_a_beauty_items_details_usage_and_condition_notes_are_in_the_export(
    app_client, db_clean, registered_supabase_user,
):
    """The original finding: none of these reached the person's file."""
    token, account_id = await registered_supabase_user()
    async with get_sessionmaker()() as session:
        await seed_inventory_categories(session)
        item = InventoryItem(account_id=account_id, category="beauty", display_name="Barrier cream")
        session.add(item)
        await session.flush()
        session.add_all([
            BeautyProductDetail(
                item_id=item.id, product_type="moisturiser", size="50 ml",
                purpose="Night-time hydration for dry cheeks",
                ingredients_text="Aqua, Glycerin, Niacinamide, Ceramide NP",
                active_ingredients=["niacinamide", "ceramide np"], use_frequency="Every evening",
                routine_position="After serum", opened_date=date(2026, 8, 1),
                period_after_opening_months=12, remaining_percent=60,
            ),
            ItemUsageEvent(item_id=item.id, used_on=date(2026, 9, 3), quantity=2,
                           note="Two pumps after cleansing"),
            ItemConditionEvent(item_id=item.id, condition="worn", note="Pump sticks slightly"),
        ])
        await session.commit()
        item_id = str(item.id)

    response = await app_client.get(
        "/api/v2/privacy/export", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    inventory = response.json()["domains"]["inventory"]
    (details,) = (r for r in inventory["beauty_product_details"] if r["item_id"] == item_id)
    assert details["purpose"] == "Night-time hydration for dry cheeks"
    assert details["ingredients_text"] == "Aqua, Glycerin, Niacinamide, Ceramide NP"
    assert details["active_ingredients"] == ["niacinamide", "ceramide np"]
    assert details["size"] == "50 ml"
    assert details["use_frequency"] == "Every evening"
    assert details["routine_position"] == "After serum"
    assert details["opened_date"] == "2026-08-01"
    assert details["period_after_opening_months"] == 12
    assert details["remaining_percent"] == 60
    (usage,) = (r for r in inventory["item_usage_events"] if r["item_id"] == item_id)
    assert usage["note"] == "Two pumps after cleansing" and usage["quantity"] == 2
    assert usage["used_on"] == "2026-09-03"
    (condition,) = (r for r in inventory["item_condition_events"] if r["item_id"] == item_id)
    assert condition["condition"] == "worn" and condition["note"] == "Pump sticks slightly"


# ---------------------------------------------------------------------------
# No row ceiling
# ---------------------------------------------------------------------------

ABOVE_THE_OLD_CAP = 20_050


async def test_more_than_twenty_thousand_rows_of_one_collection_are_all_exported(db_clean):
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        await _register(session, account_a)
        await _register(session, account_b)
        await session.commit()
        for account, count, tag in ((account_a, ABOVE_THE_OLD_CAP, "A"), (account_b, 7, "B")):
            await session.execute(text(
                "INSERT INTO app_events (id, account_id, name, properties, created_at, updated_at) "
                "SELECT gen_random_uuid(), :account, :name, jsonb_build_object('n', g), now(), now() "
                "FROM generate_series(1, :count) AS g"
            ), {"account": account, "name": f"lane-e-{tag}-bulk", "count": count})
        await session.commit()
        owned = (await session.execute(
            select(func.count()).select_from(AppEvent).where(AppEvent.account_id == account_a)
        )).scalar_one()
    assert owned == ABOVE_THE_OLD_CAP

    async with get_sessionmaker()() as session:
        export_a = await export_mod.build_export(session, account_a)

    rows = export_a["domains"]["ai_and_ops"]["app_events"]
    assert len(rows) == owned == ABOVE_THE_OLD_CAP
    ordinals = {row["properties"]["n"] for row in rows}
    assert ordinals == set(range(1, ABOVE_THE_OLD_CAP + 1))
    assert {n for n in ordinals if n > 20_000} == set(range(20_001, ABOVE_THE_OLD_CAP + 1))
    assert {row["name"] for row in rows} == {"lane-e-A-bulk"}
    assert len({row["id"] for row in rows}) == ABOVE_THE_OLD_CAP


async def test_the_exporter_has_no_row_limit_left_in_it():
    import inspect

    source = inspect.getsource(export_mod)
    assert "_MAX_ROWS" not in source
    assert ".limit(" not in source


# ---------------------------------------------------------------------------
# Failure is not success
# ---------------------------------------------------------------------------

async def _state_digest(exclude: frozenset[str] = frozenset({"audit_events"})) -> dict[str, str]:
    """A digest of every table's full contents, to prove nothing changed."""
    digests: dict[str, str] = {}
    async with get_sessionmaker()() as session:
        for table in Base.metadata.sorted_tables:
            if table.name in exclude:
                continue
            digests[table.name] = (await session.execute(text(
                f'SELECT md5(coalesce(string_agg(t::text, \'|\' ORDER BY t::text), \'\')) FROM "{table.name}" t'
            ))).scalar_one()
    return digests


async def _successful_export_audits(account_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as session:
        return (await session.execute(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.account_id == account_id, AuditEvent.action == ACTION_PRIVACY_EXPORTED,
            )
        )).scalar_one()


async def test_a_failed_domain_is_refused_with_no_success_audit_and_a_retry_succeeds_once(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    async with get_sessionmaker()() as session:
        await seed_inventory_categories(session)
        await _seed_graph(session, account_id, "A")
        await session.commit()
    before = await _state_digest()

    original = export_mod.DOMAIN_HANDLERS["planning"]

    async def _broken(session, account_id):
        await session.execute(text("SELECT 1 / 0"))

    export_mod.DOMAIN_HANDLERS["planning"] = _broken
    try:
        refused = await app_client.get(
            "/api/v2/privacy/export", headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        export_mod.DOMAIN_HANDLERS["planning"] = original

    assert refused.status_code == 503, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "PRIVACY_EXPORT_INCOMPLETE"
    assert detail["message"] == export_mod.PRIVACY_EXPORT_INCOMPLETE_MESSAGE
    assert detail["retryable"] is True
    assert "domains" not in refused.json()
    for leaked in ("planning", "lane-e-A-", "division by zero", "SELECT"):
        assert leaked not in refused.text
    assert await _successful_export_audits(account_id) == 0
    assert await _state_digest() == before, "a failed export changed product state"

    retried = await app_client.get(
        "/api/v2/privacy/export", headers={"Authorization": f"Bearer {token}"},
    )
    assert retried.status_code == 200, retried.text
    assert "lane-e-A-action" in retried.text
    assert await _successful_export_audits(account_id) == 1
    assert await _state_digest() == before, "a successful export changed product state"


async def test_a_covered_path_missing_from_the_file_refuses_the_export(db_clean, monkeypatch):
    account_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await _register(session, account_id)
        await session.commit()
    original = export_mod.DOMAIN_HANDLERS["inventory"]

    async def _drops_a_collection(session, account_id):
        payload = await original(session, account_id)
        payload.pop("wardrobe_item_details")
        return payload

    monkeypatch.setitem(export_mod.DOMAIN_HANDLERS, "inventory", _drops_a_collection)
    async with get_sessionmaker()() as session:
        with pytest.raises(export_mod.PrivacyExportIncomplete) as refused:
            await export_mod.build_export(session, account_id)
    assert refused.value.unproven == ("wardrobe_item_details:path_missing",)


async def test_a_covered_table_its_domain_never_read_refuses_the_export(db_clean, monkeypatch):
    account_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await _register(session, account_id)
        await session.commit()

    async def _claims_without_reading(session, account_id):
        return {key: [] for key in ("ai_runs", "ai_run_outputs", "audit_events", "beta_usage_events", "app_events")}

    monkeypatch.setitem(export_mod.DOMAIN_HANDLERS, "ai_and_ops", _claims_without_reading)
    async with get_sessionmaker()() as session:
        with pytest.raises(export_mod.PrivacyExportIncomplete) as refused:
            await export_mod.build_export(session, account_id)
    assert set(refused.value.unproven) == {
        f"{table}:not_read" for table in ("ai_runs", "ai_run_outputs", "audit_events", "beta_usage_events", "app_events")
    }


# ---------------------------------------------------------------------------
# What never leaves
# ---------------------------------------------------------------------------

#: Internal paths, credentials and tokens. None of these names may appear as a
#: key anywhere in an export.
FORBIDDEN_KEYS = {
    "storage_key", "storage_backend", "photo_key", "credential_ref", "sync_cursor",
    "claim_token", "provider_ticket_id", "provider_error_code", "notice_cursor",
    "token_hash", "expo_push_token",
}


async def test_storage_paths_credentials_and_tokens_never_leave_and_the_truth_stays(db_clean):
    account_id = uuid.uuid4()
    secrets = {
        "media": f"media/{account_id}/lane-e-secret-media-key.jpg",
        "report": f"label-reports/{account_id}/lane-e-secret-report-key.jpg",
        "credential": "lane-e-secret-credential-ref",
        "cursor": "lane-e-secret-sync-cursor",
        "claim": "lane-e-secret-claim-token",
        "ticket": "lane-e-secret-provider-ticket",
        "device_hash": "lane-e-secret-device-token-hash",
        "expo": "ExponentPushToken[lane-e-secret-expo]",
    }
    async with get_sessionmaker()() as session:
        await _register(session, account_id)
        media = MediaAsset(
            account_id=account_id, storage_backend="supabase", storage_key=secrets["media"],
            content_type="image/jpeg", byte_size=10, sha256="0" * 64, purpose=MEDIA_PURPOSE_INVENTORY,
        )
        session.add_all([
            media,
            LabelErrorReport(
                account_id=account_id, client_report_id="with-photo", barcode="8901234567890",
                subject="MRP", reason="wrong_value", photo_key=secrets["report"],
            ),
            LabelErrorReport(
                account_id=account_id, client_report_id="without-photo", barcode="8901234567890",
                subject="Net weight", reason="wrong_value", photo_key=None,
            ),
            ExternalIntegration(
                account_id=account_id, kind="calendar", provider="google", status="connected",
                credential_ref=secrets["credential"], sync_cursor=secrets["cursor"],
                external_account_label="Work calendar",
            ),
            NotificationDelivery(
                account_id=account_id, plan_date=TODAY, notification_key="care", dedup_hash=uuid.uuid4().hex,
                title="Evening routine", status="provider_accepted", claim_token=secrets["claim"],
                provider_ticket_id=secrets["ticket"],
            ),
            ScanDevice(device_key=uuid.uuid4().hex, token_hash=secrets["device_hash"], claimed_by_account_id=account_id),
            NotificationDevice(
                account_id=account_id, device_key=uuid.uuid4().hex, platform="android",
                expo_push_token=secrets["expo"],
            ),
        ])
        await session.commit()
        media_id = str(media.id)

    async with get_sessionmaker()() as session:
        payload = await export_mod.build_export(session, account_id)
    dumped = json.dumps(payload)

    for name, secret in secrets.items():
        assert secret not in dumped, name
    assert "lane-e-secret" not in dumped
    assert f"label-reports/{account_id}" not in dumped and f"media/{account_id}" not in dumped
    assert not (_keys(payload["domains"]) & FORBIDDEN_KEYS), _keys(payload["domains"]) & FORBIDDEN_KEYS

    # The truthful public metadata is still there.
    reports = {r["client_report_id"]: r for r in payload["domains"]["product_scans"]["label_error_reports"]}
    assert reports["with-photo"]["photo_attached"] is True
    assert reports["without-photo"]["photo_attached"] is False
    (asset,) = payload["domains"]["media"]["assets"]
    assert asset["id"] == media_id and asset["content_url"] == f"/api/v2/media/{media_id}/content"
    (integration,) = payload["domains"]["planning"]["calendar_integrations"]
    assert integration["external_account_label"] == "Work calendar"
    (delivery,) = payload["domains"]["planning"]["notification_deliveries"]
    assert delivery["status"] == "provider_accepted"


# ---------------------------------------------------------------------------
# A document, deterministic, and a pure read
# ---------------------------------------------------------------------------

async def test_the_whole_export_serialises_with_the_standard_json_encoder(two_graphs):
    _, _, export_a = two_graphs
    text_form = json.dumps(export_a, allow_nan=False)  # no ``default=``: nothing may need one
    assert json.loads(text_form) == export_a


async def test_the_registry_summary_states_exactly_what_was_exported(two_graphs):
    _, _, export_a = two_graphs
    summary = export_a["registry_summary"]
    assert summary["exported_tables"] == summary["included_tables"] == sorted(included_tables())
    locations = summary["export_locations"]
    assert set(locations) == included_tables()
    assert locations["beauty_product_details"] == ["inventory.beauty_product_details"]
    assert locations["purchase_decisions"] == [
        "shopping.subjects[*].decisions", "shopping.unattributed_decisions",
    ]
    # Step 15: the referral history has its own place; telemetry keeps Lane E's.
    assert locations["consumer_referral_invites"] == ["growth.referral.issued_codes"]
    assert locations["app_events"] == ["ai_and_ops.app_events"]


async def test_two_exports_of_the_same_data_are_identical_and_change_nothing(two_graphs):
    graph_a, _, first = two_graphs
    before = await _state_digest(exclude=frozenset())
    async with get_sessionmaker()() as session:
        second = await export_mod.build_export(session, graph_a.account_id)
    assert await _state_digest(exclude=frozenset()) == before
    first = {**first, "generated_at": None}
    second = {**second, "generated_at": None}
    assert first == second


# ---------------------------------------------------------------------------
# Step 15: consumer referral history and the telemetry retry id
# ---------------------------------------------------------------------------

async def _referral(session, account_id: uuid.UUID, *, uses: int, active: bool, days: int) -> tuple:
    """One referral capability issued to ``account_id``, as Step 15 issues it:
    an ordinary invite plus the binding that says whose it was to share."""
    from datetime import UTC, datetime, timedelta

    from app.domains.beta_access import service as beta
    from app.domains.growth.models import PROGRAM_VERSION, ConsumerReferralInvite

    invite = await beta.create_invite(
        session, label=PROGRAM_VERSION, max_uses=3,
        expires_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=days),
    )
    invite.uses_count = uses
    invite.active = active
    binding = ConsumerReferralInvite(
        inviter_account_id=account_id, invite_id=invite.id, program_version=PROGRAM_VERSION,
    )
    session.add(binding)
    await session.flush()
    return invite, binding


async def test_step15_referral_history_is_in_its_inviters_export_and_nobody_elses(db_clean):
    """A's referral history is in A's file; B's binding, B's invite and every
    identifier of either never are. No code, invite id or binding id leaves."""
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        await _register(session, account_a)
        await _register(session, account_b)
        a_old, a_old_binding = await _referral(session, account_a, uses=2, active=False, days=-40)
        a_new, a_new_binding = await _referral(session, account_a, uses=1, active=True, days=20)
        b_invite, b_binding = await _referral(session, account_b, uses=3, active=True, days=10)
        await session.commit()
        expected = [
            (a_old.expires_at.isoformat(), 3, 2, False),
            (a_new.expires_at.isoformat(), 3, 1, True),
        ]
        a_codes = (a_old.code, a_new.code)
        a_ids = (a_old.id, a_new.id, a_old_binding.id, a_new_binding.id)
        b_ids = (b_invite.id, b_binding.id, account_b)
        b_code, b_expiry = b_invite.code, b_invite.expires_at.isoformat()

    async with get_sessionmaker()() as session:
        export_a = await export_mod.build_export(session, account_a)

    history = export_a["domains"]["growth"]["referral"]
    assert history["program_version"] == "consumer-referral-v1"
    assert history["lifetime_limit"] == 3
    assert history["lifetime_successful_admissions"] == 3
    issued = history["issued_codes"]
    # Both bindings were written in one transaction, so they share a
    # ``created_at`` and the order falls to the id tie-break: compare as a set.
    assert sorted(
        (row["expires_at"], row["max_admissions"], row["successful_admissions"], row["active"])
        for row in issued
    ) == sorted(expected)
    for row in issued:
        assert set(row) == {"issued_at", "expires_at", "max_admissions", "successful_admissions", "active"}

    dumped = json.dumps(export_a)
    for code in (*a_codes, b_code):
        assert code not in dumped, "a referral code is a live capability and never leaves"
    for identifier in (*a_ids, *b_ids):
        assert str(identifier) not in dumped
    assert b_expiry not in json.dumps(history)
    assert not (_keys(export_a["domains"]["growth"]) & {"code", "invite_id", "inviter_account_id", "id"})


async def test_step15_the_referral_read_is_what_proves_the_table_was_exported(db_clean, monkeypatch):
    """The coverage proof is the real, SQL-scoped read: a growth handler that
    returns the right shape without reading the binding is refused."""
    account_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await _register(session, account_id)
        await session.commit()

    async def _shape_without_reading(session, account_id):
        return {"referral": {"issued_codes": []}}

    monkeypatch.setitem(export_mod.DOMAIN_HANDLERS, "growth", _shape_without_reading)
    async with get_sessionmaker()() as session:
        with pytest.raises(export_mod.PrivacyExportIncomplete) as refused:
            await export_mod.build_export(session, account_id)
    assert refused.value.unproven == ("consumer_referral_invites:not_read",)


async def test_step15_app_events_keep_lane_e_authority_and_withhold_the_retry_id(db_clean):
    """``app_events`` is exported once, by Lane E's generic contract at
    ``ai_and_ops.app_events``, and ``client_event_id`` never leaves."""
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    retry_a, retry_b = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        await _register(session, account_a)
        await _register(session, account_b)
        mine = AppEvent(
            account_id=account_a, name="growth.scan_again",
            properties={"surface": "product_result"}, client_event_id=retry_a,
        )
        theirs = AppEvent(
            account_id=account_b, name="growth.scan_again",
            properties={"surface": "product_result"}, client_event_id=retry_b,
        )
        session.add_all([mine, theirs])
        await session.commit()
        mine_id, theirs_id = mine.id, theirs.id

    async with get_sessionmaker()() as session:
        export_a = await export_mod.build_export(session, account_a)

    (row,) = export_a["domains"]["ai_and_ops"]["app_events"]
    assert row["id"] == str(mine_id) and row["name"] == "growth.scan_again"
    assert "client_event_id" not in row
    assert set(row) == {c.name for c in AppEvent.__table__.columns} - {"client_event_id"}
    assert set(export_a["domains"]["growth"]) == {"referral"}, "telemetry is not exported a second time"
    dumped = json.dumps(export_a)
    for leaked in (retry_a, retry_b, theirs_id, account_b):
        assert str(leaked) not in dumped


async def test_step15_no_privacy_export_path_is_capped():
    """The frozen Step 15 telemetry exporter cut the export at 10 000 rows.
    It is gone; the growth domain reads through the same uncapped reads as
    every other domain, and ``app_events`` stays on Lane E's uncapped path."""
    import inspect

    from app.domains.growth import analytics, referral

    assert not hasattr(analytics, "analytics_export")
    assert not hasattr(referral, "referral_export")
    for source in (inspect.getsource(export_mod._growth), inspect.getsource(referral.referral_history)):
        assert ".limit(" not in source and "10_000" not in source and "10000" not in source
