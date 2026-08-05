"""The critical journey, driven entirely through the real V2 HTTP API.

The service-level journeys (``test_critical_journey.py`` and
``test_critical_journey_full.py``) prove the domain services and the deletion
state machine behave. This one proves the same flow works through the routes a
phone actually calls — request schemas, auth, feature flags, serialisation and
all — from an empty database and a fresh reference-data seed.

Order of the walk::

    migrate + seed → admin invite → reserve → simulated Supabase sign-up →
    register → /me → profile → onboarding → consent → seven-category inventory →
    media + usage → scan → quiz → occasion → styling → look feedback →
    shopping evaluation + decision → Today (weather, event, completion,
    regenerate) → weekly planner → routines + adherence → progress + goals →
    controlled memory (learn, correct, delete) → privacy export →
    account deletion → deleted identity is refused everywhere.

External boundaries mocked
--------------------------
* Supabase Auth token verification — the conftest ``fake_supabase_user``.
* Supabase Auth admin ``delete_user`` — a spy, so deletion ordering is checkable.
* Supabase Storage — an in-memory adapter.
* AI provider — the deterministic conftest ``FakeProvider``.
* Weather — supplied through ``POST /today/weather``; no forecast is fetched.

Assertions are about data relationships, not status codes: the look references
the occasion, the evaluation compares against owned inventory, a completed
action survives a rebuild, a corrected memory fact supersedes the original, the
export carries every domain, and Supabase Auth is deleted only after storage and
the database.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import List

import pytest
from sqlalchemy import func, select

from app.bootstrap import run as run_seed
from app.domains.media.storage import factory as storage_factory
from app.domains.privacy import deletion_service
from app.domains.privacy.models import STATE_COMPLETE
from app.shared.database.sql import get_sessionmaker
from tests.conftest import auth
from tests.journey import (
    JOURNEY_DATE,
    SEVEN_CATEGORIES,
    ok,
    populate_every_domain,
    register_through_invite,
)


pytestmark = pytest.mark.asyncio


class _FakeStorage:
    backend_name = "fake"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key, data, content_type):
        self.objects[key] = data

    async def get(self, key):
        from app.domains.media.storage.base import StorageObjectMissing

        if key not in self.objects:
            raise StorageObjectMissing(key)
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)

    async def exists(self, key):
        return key in self.objects

    async def presigned_get_url(self, key, ttl):
        return f"https://signed.example/{key}"

    async def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix):
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)


@pytest.fixture
def fake_storage(monkeypatch):
    storage = _FakeStorage()
    storage_factory.set_storage(storage)
    yield storage
    storage_factory.set_storage(None)


@pytest.fixture
def deletion_spy(monkeypatch, fake_storage):
    """Records the order of the deletion stages against the storage state.

    The point of the ordering rule is that Supabase Auth — the last thing that
    can identify the account — is deleted only once the objects and the rows
    are already gone. So the spy snapshots how many objects remain at the
    moment auth deletion is called.
    """

    calls: List[dict] = []

    class _AuthAdmin:
        @staticmethod
        def delete_user(uid):
            calls.append({"uid": str(uid), "objects_remaining": len(fake_storage.objects)})

    class _Auth:
        admin = _AuthAdmin()

    class _Admin:
        auth = _Auth()

    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin", lambda: _Admin
    )
    return calls


async def test_critical_journey_through_the_api(
    app_client, db_clean, fake_supabase_user, fake_provider, fake_storage, deletion_spy
):
    factory = get_sessionmaker()

    # ------------------------------------------------------------------
    # 1. Reference-data seed on an empty, migrated database.
    # ------------------------------------------------------------------
    async with factory() as session:
        seed = await run_seed(session)
        await session.commit()
    assert seed["counts"]["inventory_categories"] == 7

    # ------------------------------------------------------------------
    # 2-6. Admin invite → reserve → Supabase sign-up → register → /me.
    # ------------------------------------------------------------------
    admin_token, _ = fake_supabase_user(email="admin@example.com", admin=True)
    token, account_id, invite_code = await register_through_invite(
        app_client, fake_supabase_user, email="journey@example.com", admin_token=admin_token
    )

    me = ok(await app_client.get("/api/v2/me", headers=auth(token)))
    assert me["account"]["id"] == str(account_id)
    assert me["account"]["is_admin"] is False

    # The invite is spent: a second account cannot reuse the same code.
    second = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": invite_code, "email": "gatecrasher@example.com"},
    )
    assert second.status_code in (400, 404, 409), second.text

    # ------------------------------------------------------------------
    # 7-20. Everything from profile through controlled memory.
    # ------------------------------------------------------------------
    created = await populate_every_domain(app_client, token)

    # --- Profile and onboarding actually persisted --------------------
    profile = ok(await app_client.get("/api/v2/profile", headers=auth(token)))
    attributes = {row["key"]: row["value"] for row in profile["attributes"]}
    assert attributes["skin_tone"] == "medium"
    assert attributes["climate"] == "hot_humid"

    onboarding = ok(await app_client.get("/api/v2/onboarding/status", headers=auth(token)))
    assert "goal" in onboarding["completed_steps"]
    assert "lifestyle" in onboarding["completed_steps"]
    assert onboarding["current_step"] not in ("goal", "lifestyle"), (
        "resuming must continue from the next unanswered step"
    )

    completed = ok(await app_client.post("/api/v2/onboarding/complete", headers=auth(token)))
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None

    # Resuming after completion returns the same session, not a fresh one.
    resumed = ok(await app_client.get("/api/v2/onboarding/status", headers=auth(token)))
    assert resumed["id"] == completed["id"]

    # --- Consent ------------------------------------------------------
    consent = ok(await app_client.get("/api/v2/consent", headers=auth(token)))
    assert consent["photo_analysis"]["granted"] is True

    # --- All seven inventory categories -------------------------------
    summary = ok(await app_client.get("/api/v2/inventory/summary", headers=auth(token)))
    counted = summary["categories"]
    assert set(counted) == set(SEVEN_CATEGORIES), (
        "the summary must account for exactly the seven canonical categories"
    )
    for category in SEVEN_CATEGORIES:
        assert counted[category] >= 1, f"{category} has nothing in it"

    wardrobe_item_id = created["inventory"]["wardrobe"][0]
    item = ok(await app_client.get(
        f"/api/v2/inventory/items/{wardrobe_item_id}", headers=auth(token)
    ))
    assert item["usage_count"] == 1, "the recorded usage event must be on the item"
    assert created["media"] in item["image_ids"], "the uploaded photo must stay attached to the item"

    # --- Scan ---------------------------------------------------------
    scan = created["scan"]
    assert scan["status"] == "ok"
    assert scan["analysis"], "a successful scan must carry its observations"
    history = ok(await app_client.get("/api/v2/scan/history", headers=auth(token)))
    assert [row["id"] for row in history["scans"]] == [scan["id"]]
    # The image itself is request data, never a stored or returned artefact.
    assert "image_base64" not in str(history)

    # --- Quiz ---------------------------------------------------------
    latest = ok(await app_client.get("/api/v2/quiz/latest", headers=auth(token)))
    assert latest["submission"]["id"] == created["quiz"]["id"]
    assert latest["submission"]["derived_style_vibe"] == created["quiz"]["derived_style_vibe"]
    assert latest["submission"]["schema_version"] == created["quiz"]["schema_version"]

    # --- Occasion styling ---------------------------------------------
    occasion = created["styling"]["occasion"]
    styling = created["styling"]["styling"]
    assert styling["occasion"]["id"] == occasion["id"], (
        "the generated looks must reference the occasion they were asked for"
    )
    assert styling["looks"], "styling must produce at least one look"
    look = styling["looks"][0]

    owned_ids = {value for ids in created["inventory"].values() for value in ids}
    look_item_ids = {row["inventory_item_id"] for row in look["owned_items"]}
    assert look_item_ids, "a look with no items is not a look"
    assert look_item_ids <= owned_ids, "styling must only use items the user owns"
    # Anything the user does not own is labelled as an optional addition rather
    # than presented as if it were in their wardrobe.
    for addition in look["optional_additions"]:
        assert addition["owned"] is False
        assert addition["inventory_item_id"] is None

    feedback = ok(await app_client.post(
        f"/api/v2/looks/{look['id']}/feedback",
        headers=auth(token),
        json={"rating": "loved", "note": "Good for Mondays."},
    ))
    assert feedback["feedback"]["rating"] == "loved"
    assert feedback["saved"] is True, "a loved look must be kept"

    # --- Shopping -----------------------------------------------------
    evaluation = created["shopping"]
    assert evaluation["verdict"] in {"buy", "wait", "skip"}
    compared = {
        row["inventory_item_id"]
        for row in evaluation["similar_owned_products"] + evaluation["existing_alternatives"]
    }
    assert compared & owned_ids, (
        "the evaluation must compare the candidate against what is already owned"
    )

    decision = ok(await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
        headers=auth(token),
        json={"decision": "skipped", "note": "Already covered."},
    ))
    assert decision["decision"]["decision"] == "skipped"

    # --- Today --------------------------------------------------------
    today = created["planning"]["today"]
    assert today["plan_date"] == JOURNEY_DATE.isoformat()
    assert today["weather"]["condition"] == "humid"
    assert today["event_note"] == "Team review", "the calendar event must reach the day"

    # Re-read before completing: later steps in the journey (new routines, new
    # inventory) legitimately move the day's context, and the plan is rebuilt
    # with fresh action ids when it does.
    current_today = ok(await app_client.get(
        f"/api/v2/today?plan_date={JOURNEY_DATE.isoformat()}", headers=auth(token)
    ))
    actions = current_today["primary"] + current_today["optional_modules"]
    assert actions, "a plan with no actions gives the user nothing to do"
    action_id = actions[0]["id"]
    ok(await app_client.post(
        f"/api/v2/today/actions/{action_id}/complete",
        headers=auth(token),
        json={"completed": True},
    ))

    regenerated = ok(await app_client.post(
        "/api/v2/today/regenerate",
        headers=auth(token),
        json={"plan_date": JOURNEY_DATE.isoformat(), "reason": "manual"},
    ))
    still_done = [
        row for row in regenerated["primary"] + regenerated["optional_modules"]
        if row["completed"]
    ]
    assert still_done, "regenerating must not un-tick what the user already did"

    reread = ok(await app_client.get(
        f"/api/v2/today?plan_date={JOURNEY_DATE.isoformat()}", headers=auth(token)
    ))
    assert sum(
        1 for row in reread["primary"] + reread["optional_modules"] if row["completed"]
    ) == len(still_done), "a re-read must not duplicate or drop completions"

    # --- Weekly planner -----------------------------------------------
    week = created["planning"]["week"]
    assert len(week["days"]) == 7
    assert week["days"][0]["plan_date"] == JOURNEY_DATE.isoformat()

    read_week = ok(await app_client.get(
        f"/api/v2/planner/week?week_start={JOURNEY_DATE.isoformat()}", headers=auth(token)
    ))
    assert read_week["week_start"] == week["week_start"]
    assert len(read_week["days"]) == 7

    # --- Routines and ingredient safety -------------------------------
    routines = created["routines"]
    assert routines["routines"], "the shelf items must produce at least one routine"
    kinds = {row["kind"] for row in routines["routines"]}
    assert {"morning", "wash_day"} <= kinds, (
        "the skincare and hair-care routines both have to be built"
    )
    morning = next(row for row in routines["routines"] if row["kind"] == "morning")
    assert morning["steps"], "a routine with no steps is not a routine"
    assert morning["explanation_source"] == "deterministic"

    # A step backed by an owned shelf product names it; a gap is labelled as a
    # gap rather than quietly recommending something the user does not have.
    owned_steps = [step for step in morning["steps"] if step["owned"]]
    assert owned_steps, "the shelf items must be used by the routine"
    assert owned_steps[0]["inventory_item_id"] in created["inventory"]["beauty"]
    for step in morning["steps"]:
        if not step["owned"]:
            assert step["is_gap"] is True
            assert step["product_name"] is None

    step_id = owned_steps[0]["id"]
    adherence = ok(await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete",
        headers=auth(token),
        json={"done_on": JOURNEY_DATE.isoformat(), "completed": True},
    ))
    assert adherence["completed"] is True

    # Aliases: "l-ascorbic acid" is vitamin C and "aha" is glycolic acid as far
    # as a label is concerned, and both must resolve to the canonical keys the
    # seeded catalogue holds.
    check = ok(await app_client.post(
        "/api/v2/ingredients/check",
        headers=auth(token),
        json={
            "ingredients": ["l-ascorbic acid", "glycolic acid", "retinol"],
            "against_owned": True,
        },
    ))
    assert check["identified"], "a checked ingredient must resolve against the catalogue"
    resolved = {row["ingredient_key"] for row in check["identified"]}
    assert {"ascorbic_acid", "glycolic_acid", "retinol"} <= resolved, (
        f"aliases did not resolve to canonical ingredients: {sorted(resolved)}"
    )
    detail = ok(await app_client.get(
        "/api/v2/ingredients/ascorbic_acid", headers=auth(token)
    ))
    assert detail["display_name"]
    assert check["knowledge_version"]
    assert check["checked_against_owned"] is True
    # Anything not recognised is listed rather than silently dropped, so a
    # clean result can be trusted to mean "we read it all".
    assert "unidentified" in check
    # Nothing in a safety answer may read as a diagnosis.
    text = str(check).lower()
    for banned in ("you have", "diagnos", "prescri", "dosage", "treat your"):
        assert banned not in text, f"'{banned}' must not appear in ingredient guidance"

    # --- Progress and goals -------------------------------------------
    progress = ok(await app_client.get(
        f"/api/v2/progress?as_of={JOURNEY_DATE.isoformat()}", headers=auth(token)
    ))
    assert progress["metrics"], "the progress screen must have metrics to show"
    for metric in progress["metrics"]:
        assert metric["formula_version"], f"{metric['key']} has no formula version"
        assert metric["explanation"]
    assert not any(
        "overall" in metric["key"] or "attractive" in metric["key"]
        for metric in progress["metrics"]
    ), "there must be no overall appearance score"

    goal = created["goal"]
    updated_goal = ok(await app_client.patch(
        f"/api/v2/goals/{goal['id']}",
        headers=auth(token),
        json={"status": "achieved", "progress_note": "Kept it up."},
    ))
    assert updated_goal["status"] == "achieved"
    assert updated_goal["metric_key"] == "routine_consistency"
    assert updated_goal["starting_value"] is not None, (
        "a goal must record where it started, or progress is unmeasurable"
    )

    # --- Controlled memory: learn → correct → delete -------------------
    learned = created["memory"]
    assert learned["learned"], "feedback must say plainly what it learned"
    fact_id = learned["learned"]["id"]
    assert learned["learned"]["influences_recommendations"] is True

    memory = ok(await app_client.get("/api/v2/memory", headers=auth(token)))
    live = {row["id"]: row for row in memory["facts"]}
    assert fact_id in live
    original_wording = live[fact_id]["fact"]

    corrected = ok(await app_client.patch(
        f"/api/v2/memory/{fact_id}",
        headers=auth(token),
        json={"fact": "Prefers deep teal for evenings only."},
    ))
    assert corrected["fact"] == "Prefers deep teal for evenings only."
    assert corrected["fact"] != original_wording
    # A correction from the user outranks anything inferred.
    assert corrected["confidence"] == 1.0
    assert corrected["verification_state"] == "corrected"

    after_correction = ok(await app_client.get("/api/v2/memory", headers=auth(token)))
    wordings = [row["fact"] for row in after_correction["facts"]]
    assert original_wording not in wordings, "the corrected value must supersede the old one"

    ok(await app_client.delete(f"/api/v2/memory/{fact_id}", headers=auth(token)))
    after_delete = ok(await app_client.get("/api/v2/memory", headers=auth(token)))
    assert fact_id not in {row["id"] for row in after_delete["facts"]}, (
        "a deleted fact must stop influencing anything immediately"
    )

    # The deletion is still visible in the honest memory export, as a tombstone.
    memory_export = ok(await app_client.get("/api/v2/memory/export", headers=auth(token)))
    exported = str(memory_export)
    assert fact_id in exported, "an export that hides deletions is not an honest export"

    # ------------------------------------------------------------------
    # 21. Privacy export.
    # ------------------------------------------------------------------
    export = ok(await app_client.get("/api/v2/privacy/export", headers=auth(token)))
    assert export["schema_version"]
    assert export["account"]["id"] == str(account_id)

    expected_domains = {
        "identity", "profile", "consent", "inventory", "media", "scans",
        "quiz_and_styling", "shopping", "planning", "routines",
        "progress_and_memory", "ai_and_ops",
    }
    assert expected_domains <= set(export["domains"])
    for name in expected_domains:
        assert export["domains"][name] != {"error": "domain_export_failed"}, name

    exported_text = str(export)
    exported_categories = {
        row["category"] for row in export["domains"]["inventory"]["items"]
    }
    assert set(SEVEN_CATEGORIES) <= exported_categories

    # Nothing internal or secret rides along.
    for secret in ("storage_key", "storage_backend", "image_base64", "service_role", "SUPABASE"):
        assert secret not in exported_text, f"'{secret}' must not appear in an export"

    # ------------------------------------------------------------------
    # 22. Account deletion, stage by stage.
    # ------------------------------------------------------------------
    assert fake_storage.objects, "there must be something in storage to delete"

    requested = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert requested.status_code == 202, requested.text
    assert requested.json()["state"] == "requested"

    async with factory() as session:
        processed = await deletion_service.drain_all(session)
        await session.commit()
    assert processed >= 1

    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job is not None
        assert job.state == STATE_COMPLETE
        assert job.completed_at is not None

    # Storage prefix empty, application rows gone, Auth deleted last.
    assert fake_storage.objects == {}
    assert len(deletion_spy) == 1
    assert deletion_spy[0]["uid"] == str(account_id)
    assert deletion_spy[0]["objects_remaining"] == 0, (
        "Supabase Auth must not be deleted while objects are still stored"
    )

    async with factory() as session:
        from app.domains.identity.models import Account
        from app.domains.inventory.models import InventoryItem
        from app.domains.media.models import MediaAsset
        from app.domains.privacy.models import AccountDeletionJob

        for model in (Account, InventoryItem, MediaAsset):
            remaining = (await session.execute(
                select(func.count()).select_from(model).where(
                    model.account_id == account_id
                    if hasattr(model, "account_id") else model.id == account_id
                )
            )).scalar_one()
            assert remaining == 0, f"{model.__tablename__} still holds the account's rows"

        # The only permitted survivor is the minimal deletion record.
        jobs = (await session.execute(
            select(AccountDeletionJob).where(AccountDeletionJob.account_id == account_id)
        )).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].state == STATE_COMPLETE

    # ------------------------------------------------------------------
    # 23. The deleted identity is refused everywhere.
    # ------------------------------------------------------------------
    me_after = await app_client.get("/api/v2/me", headers=auth(token))
    assert me_after.status_code == 403
    assert me_after.json()["detail"]["code"] == "REGISTRATION_REQUIRED"

    for path in (
        "/api/v2/profile",
        "/api/v2/inventory/items",
        "/api/v2/consent",
        "/api/v2/scan/history",
        "/api/v2/quiz/latest",
        "/api/v2/today",
        "/api/v2/planner/week",
        "/api/v2/progress",
        "/api/v2/memory",
        "/api/v2/goals",
        "/api/v2/privacy/export",
    ):
        resp = await app_client.get(path, headers=auth(token))
        assert resp.status_code in (401, 403), f"{path} answered {resp.status_code} after deletion"
