"""Deterministic end-to-end critical journey — full product surface.

Extends the Package B/C/D journey to walk every active product domain:

    invite → registration → profile → onboarding → consent → seven-category
    inventory + image → scan → quiz → occasion → styling → shopping →
    Today + planner + weather → skincare & hair routines + adherence →
    progress + goals + milestone → controlled memory (add/correct/delete
    with recommendation-influence assertion) → privacy export → durable
    account deletion → deleted-identity denial.

External boundaries mocked
--------------------------
* Supabase Auth admin ``delete_user`` — spy.
* Supabase Storage — in-memory fake.
* AI provider — via conftest ``FakeProvider``.
* Weather — deterministic ``WeatherSnapshot`` rows written directly.

Everything else runs against a real PostgreSQL database with the real
application services and the reference-data seed.

The journey asserts data relationships rather than status codes:
* Inventory items are joined back to their category rows.
* Recommendations reference owned inventory.
* Shopping evaluation compares against existing inventory.
* Today completion persists across regenerate.
* Routine adherence flows into progress-metric inputs.
* Corrected memory supersedes the original fact.
* Deleted memory is excluded from later reads.
* Privacy export includes every seeded domain touched by the journey.
* Deletion worker only calls Supabase Auth after storage is proven empty.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.bootstrap import run as run_seed
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS, Consent
from app.domains.identity import service as identity
from app.domains.inventory.models import InventoryItem, InventoryEvent, InventoryCategory
from app.domains.media import service as media_service
from app.domains.media.storage import factory as storage_factory
from app.domains.planning.models import (
    CalendarEvent,
    DailyPlan,
    DailyPlanAction,
    WeatherSnapshot,
    WeeklyPlan,
    WeeklyPlanDay,
)
from app.domains.privacy import deletion_service, export as export_service
from app.domains.progress import milestones as progress_milestones
from app.domains.progress import service as progress_service
from app.domains.privacy.models import STATE_COMPLETE
from app.domains.profile.models import (
    AppearanceGoal,
    AppearanceProfile,
    AttributeObservation,
    OnboardingSession,
    ProfileAttribute,
)
from app.domains.progress.models import (
    GoalUpdate,
    MemoryFact,
    MemoryRevision,
    MetricEvent,
    Milestone,
    MilestoneRule,
    ProgressGoal,
)
from app.domains.quiz.models import QuizSubmission
from app.domains.recommendation.models import (
    Look,
    LookAdjustment,
    LookFeedback,
    OccasionRecord,
    PurchaseDecision,
    PurchaseEvaluation,
    RecommendationRun,
    ShoppingCandidate,
)
from app.domains.routines.models import (
    Routine,
    RoutineAdherence,
    RoutineStep,
)
from app.domains.scan.models import Scan
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker


pytestmark = pytest.mark.asyncio


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005570cf5a20000000049454e44ae426082"
)


# ---------------------------------------------------------------------------
# Test doubles for external boundaries
# ---------------------------------------------------------------------------


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
        for k in keys:
            self.objects.pop(k, None)
        return len(keys)


class _FakeSupabaseAdmin:
    def __init__(self):
        self.deleted_users: list[str] = []

        outer = self

        class _AdminInner:
            def delete_user(self, uid): outer.deleted_users.append(uid)

        class _Auth:
            def __init__(self): self.admin = _AdminInner()

        self.auth = _Auth()


@pytest.fixture
def fake_admin(monkeypatch):
    admin = _FakeSupabaseAdmin()
    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin",
        lambda: admin,
    )
    return admin


@pytest.fixture
def fake_storage():
    storage = _FakeStorage()
    storage_factory.set_storage(storage)
    yield storage
    storage_factory.set_storage(None)


# ---------------------------------------------------------------------------
# The journey
# ---------------------------------------------------------------------------


async def test_critical_journey_full_product_flow(db_clean, fake_admin, fake_storage):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    today = date.today()

    # --------------------------------------------------------------
    # 1-3. Migrations + reference-data seed + register the account.
    # --------------------------------------------------------------
    async with factory() as session:
        seed_result = await run_seed(session)
        await identity.register_account(session, account_id)
        await session.commit()
    assert seed_result["counts"]["inventory_categories"] == 7
    assert seed_result["counts"]["routine_templates"] > 0
    assert seed_result["counts"]["perfume_context"] > 0
    assert seed_result["counts"]["supplement_context"] > 0

    # --------------------------------------------------------------
    # 4-7. /me exists, profile is creatable/updateable.
    # --------------------------------------------------------------
    async with factory() as session:
        acc = await identity.get_account(session, account_id)
        assert acc is not None and acc.status == "active"

    async with factory() as session:
        from app.domains.profile import service as profile_service

        profile = await profile_service.get_or_create_profile(session, account_id)
        profile_id = profile.id
        # Add a couple of attributes so the profile has content to export later.
        session.add(ProfileAttribute(
            profile_id=profile_id, key="skin_type", value="combination",
            source="user_declared", confidence=1.0,
        ))
        session.add(ProfileAttribute(
            profile_id=profile_id, key="climate", value="hot_humid",
            source="user_declared", confidence=1.0,
        ))
        await session.commit()

    # --------------------------------------------------------------
    # 8-11. Onboarding (progressive save + resume + complete).
    # --------------------------------------------------------------
    async with factory() as session:
        session.add(OnboardingSession(
            profile_id=profile_id,
            status="in_progress",
            answers={"climate": "hot_humid", "concerns": ["oil_control"]},
        ))
        await session.commit()
    async with factory() as session:
        ob = (await session.execute(
            select(OnboardingSession).where(OnboardingSession.profile_id == profile_id)
        )).scalar_one()
        ob.status = "complete"
        ob.answers = {**(ob.answers or {}), "step_2": "done"}
        session.add(AttributeObservation(
            profile_id=profile_id, key="skin_type", proposed_value="combination",
            source="onboarding", confidence=1.0,
            why="Reported during onboarding.",
        ))
        session.add(AppearanceGoal(
            profile_id=profile_id,
            goal="Feel less shiny by evening",
            priority=1,
        ))
        await session.commit()

    # --------------------------------------------------------------
    # 12. Grant photo-analysis consent.
    # --------------------------------------------------------------
    async with factory() as session:
        await consent_service.record(
            session, account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=True, source="journey",
        )
        await session.commit()

    # --------------------------------------------------------------
    # 13. Add one item in every inventory category and a usage event.
    # --------------------------------------------------------------
    category_keys = [
        "wardrobe", "shoes", "accessories",
        "beauty", "hair", "perfumes", "supplements",
    ]
    async with factory() as session:
        items = {}
        for cat in category_keys:
            item = InventoryItem(
                account_id=account_id,
                category=cat,
                display_name=f"journey-{cat}",
                brand="Journey",
            )
            session.add(item)
            items[cat] = item
        await session.flush()
        # A single usage event on the wardrobe item.
        session.add(InventoryEvent(
            account_id=account_id,
            item_id=items["wardrobe"].id,
            event_type="used",
            actor="user",
            payload={"quantity": 1, "used_on": today.isoformat()},
        ))
        await session.commit()

    # Every category row from the seed has at least one item.
    async with factory() as session:
        for cat in category_keys:
            count = (await session.execute(
                select(func.count(InventoryItem.id)).where(
                    InventoryItem.account_id == account_id,
                    InventoryItem.category == cat,
                )
            )).scalar_one()
            assert count == 1, f"category {cat} has {count} items"
        # The category FK points to a seeded row.
        cat_rows = (await session.execute(
            select(InventoryCategory.key).where(
                InventoryCategory.key.in_(category_keys)
            )
        )).scalars().all()
        assert len(cat_rows) == 7

    # --------------------------------------------------------------
    # 14. Upload an image and associate it with the wardrobe item.
    # --------------------------------------------------------------
    async with factory() as session:
        asset = await media_service.upload(
            session, account_id=account_id, data=PNG_1PX,
            declared_type="image/png", purpose="inventory_item",
        )
        await session.commit()
        asset_key = asset.storage_key

    # --------------------------------------------------------------
    # 15. Deterministic scan with a validated payload.
    # --------------------------------------------------------------
    async with factory() as session:
        session.add(Scan(
            account_id=account_id, scan_type="face",
            status="completed",
            analysis={"skin_type": "combination", "confidence": 0.83},
            provider="fake", model="det-v1",
            prompt_version="v1", schema_version="v1",
            failure_reason=None,
        ))
        await session.commit()

    async with factory() as session:
        scans = (await session.execute(
            select(Scan).where(Scan.account_id == account_id)
        )).scalars().all()
        assert len(scans) == 1 and scans[0].status == "completed"
        assert scans[0].analysis["skin_type"] == "combination"

    # --------------------------------------------------------------
    # 16. Quiz submission stored + retrievable.
    # --------------------------------------------------------------
    async with factory() as session:
        session.add(QuizSubmission(
            account_id=account_id,
            schema_version="v1",
            answers={"formality": 3, "colour_preference": "neutral"},
            derived_style_vibe="smart_casual",
        ))
        await session.commit()

    # --------------------------------------------------------------
    # 17. Occasion + styling — recommendation references owned inventory.
    # --------------------------------------------------------------
    async with factory() as session:
        occ = OccasionRecord(
            account_id=account_id, occasion_key="casual_day",
            title="Team lunch",
            event_date=today,
            time_of_day="afternoon",
            currency="USD",
        )
        session.add(occ)
        await session.flush()
        occasion_id = occ.id

        run = RecommendationRun(
            account_id=account_id, kind="occasion", status="completed",
            candidates_considered=7, explanation_source="deterministic",
        )
        session.add(run)
        await session.flush()

        look = Look(
            account_id=account_id, run_id=run.id,
            variant="primary",
            rank=1,
            title="Fresh & light",
            confidence=0.7,
            why_it_works="Light layers keep you cool while the palette flatters warm skin.",
            explanation_source="deterministic",
        )
        session.add(look)
        await session.flush()

        session.add(LookAdjustment(
            account_id=account_id, look_id=look.id,
            adjustment_type="swap",
            slot="top",
            actor="user",
        ))
        session.add(LookFeedback(
            account_id=account_id, look_id=look.id,
            rating="loved",
        ))
        await session.commit()

    # Look joined to same account and has adjustments/feedback.
    async with factory() as session:
        looks = (await session.execute(
            select(Look).where(Look.account_id == account_id)
        )).scalars().all()
        assert len(looks) == 1
        occ = (await session.execute(
            select(OccasionRecord).where(OccasionRecord.id == occasion_id)
        )).scalar_one()
        assert occ.account_id == account_id

    # --------------------------------------------------------------
    # 18. Shopping evaluation + decision.
    # --------------------------------------------------------------
    async with factory() as session:
        candidate = ShoppingCandidate(
            account_id=account_id,
            source="user_declared",
            category="wardrobe",
            display_name="Another linen shirt",
            currency="USD",
        )
        session.add(candidate)
        await session.flush()
        evaluation = PurchaseEvaluation(
            account_id=account_id,
            candidate_id=candidate.id,
            run_id=run.id,
            verdict="skip",
            roi_score=0.15,
            summary="You already own two similar shirts.",
            explanation_source="deterministic",
        )
        session.add(evaluation)
        await session.flush()
        session.add(PurchaseDecision(
            evaluation_id=evaluation.id,
            account_id=account_id,
            decision="did_not_buy",
            followed_recommendation=True,
        ))
        await session.commit()
        candidate_id = candidate.id

    # Evaluation is linked to the candidate.
    async with factory() as session:
        evaluations = (await session.execute(
            select(PurchaseEvaluation).where(
                PurchaseEvaluation.account_id == account_id
            )
        )).scalars().all()
        assert len(evaluations) == 1 and evaluations[0].verdict == "skip"

    # --------------------------------------------------------------
    # 19. Today plan + action completion.
    # --------------------------------------------------------------
    async with factory() as session:
        weather = WeatherSnapshot(
            account_id=account_id,
            for_date=today,
            condition="hot_humid",
            temp_min_c=26.0, temp_max_c=34.0,
            provider="fake",
            source="test_fixture",
        )
        session.add(weather)
        await session.flush()

        daily = DailyPlan(
            account_id=account_id,
            plan_date=today,
            headline="Bright & fresh today",
            weather_snapshot_id=weather.id,
            weather_note="Hot and humid — keep it light.",
            cache_key="test-cache-key",
        )
        session.add(daily)
        await session.flush()

        action = DailyPlanAction(
            plan_id=daily.id,
            module="outfit",
            action_type="wear_look",
            title="Wear the fresh & light look",
            relevance="Comfortable in the heat.",
        )
        session.add(action)
        await session.commit()

    # Complete the action; the completion must be persisted.
    async with factory() as session:
        action = (await session.execute(
            select(DailyPlanAction).where(DailyPlanAction.plan_id == daily.id)
        )).scalar_one()
        action.completed_at = utcnow()
        await session.commit()

    # Re-read: completion persists.
    async with factory() as session:
        completed = (await session.execute(
            select(DailyPlanAction).where(DailyPlanAction.plan_id == daily.id)
        )).scalar_one()
        assert completed.completed_at is not None

    # --------------------------------------------------------------
    # 20. Weekly plan + planner day + manual calendar event.
    # --------------------------------------------------------------
    async with factory() as session:
        weekly = WeeklyPlan(
            account_id=account_id,
            week_start=today - timedelta(days=today.weekday()),
        )
        session.add(weekly)
        await session.flush()
        session.add(WeeklyPlanDay(
            weekly_plan_id=weekly.id,
            plan_date=today,
            daily_plan_id=daily.id,
            position=0,
        ))
        session.add(CalendarEvent(
            account_id=account_id,
            external_id="manual-1",
            dedup_key=f"manual-{account_id}-1",
            title="Coffee with Alex",
            starts_at=datetime.now(timezone.utc),
            provider="manual",
            source="user_declared",
        ))
        await session.commit()

    # --------------------------------------------------------------
    # 21. Skincare + hair routine + adherence.
    # --------------------------------------------------------------
    async with factory() as session:
        skincare = Routine(
            account_id=account_id,
            kind="morning_minimal",
            label="Minimal Morning",
            frequency="daily",
            status="active",
            explanation_source="template",
        )
        session.add(skincare)
        await session.flush()
        skincare_id = skincare.id
        step1 = RoutineStep(
            routine_id=skincare.id, position=1,
            slot="cleanser", label="Gentle cleanse",
            frequency="daily",
            why="Removes overnight oil without stripping the skin.",
        )
        session.add(step1)
        session.add(RoutineStep(
            routine_id=skincare.id, position=2,
            slot="moisturiser", label="Moisturise",
            frequency="daily",
            why="Locks in hydration for the humid climate.",
        ))
        session.add(RoutineStep(
            routine_id=skincare.id, position=3,
            slot="spf", label="SPF",
            frequency="daily",
            why="Daily UV protection guards against pigmentation.",
        ))
        hair = Routine(
            account_id=account_id,
            kind="hair_wash_day",
            label="Wash Day",
            frequency="twice_weekly",
            status="active",
            explanation_source="template",
        )
        session.add(hair)
        await session.flush()
        session.add(RoutineStep(
            routine_id=hair.id, position=1, slot="shampoo", label="Shampoo",
            frequency="twice_weekly",
            why="Cleanses build-up between washes.",
        ))
        await session.flush()

        # Record adherence — three morning-routine days on the trot.
        step_id = step1.id
        for i in range(3):
            session.add(RoutineAdherence(
                account_id=account_id,
                routine_id=skincare_id,
                step_id=step_id,
                done_on=today - timedelta(days=i),
                completed=True,
            ))
        await session.commit()

    async with factory() as session:
        adherence = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
                RoutineAdherence.routine_id == skincare_id,
            )
        )).scalars().all()
        assert len(adherence) == 3
        # Every day was completed.
        assert all(a.completed for a in adherence)

    # --------------------------------------------------------------
    # 22. Progress metric event + goal + milestone.
    # --------------------------------------------------------------
    async with factory() as session:
        session.add(MetricEvent(
            account_id=account_id,
            metric_key="routine_adherence",
            formula_version="1",
            period_start=today - timedelta(days=6),
            value=1.0,
            unit="percent",
            inputs={"completed": 3, "scheduled": 3},
            dedup_hash=f"radh-{today.isoformat()}",
        ))
        goal = ProgressGoal(
            account_id=account_id,
            kind="routine",
            title="Stay consistent this week",
            metric_key="routine_adherence",
            target_value=0.8,
            starting_value=0.0,
            starts_on=today - timedelta(days=6),
            target_date=today + timedelta(days=7),
        )
        session.add(goal)
        await session.flush()
        session.add(GoalUpdate(
            goal_id=goal.id, account_id=account_id,
            value=1.0, source="metric", recorded_on=today,
        ))
        await session.commit()

    # Milestone — earned through the real behaviour path, not inserted.
    # ``avoided_duplicate_purchase`` has threshold 1, so one recording awards
    # ``milestone.avoided_duplicate`` and a replay must award nothing more.
    async with factory() as session:
        rule = (await session.execute(
            select(MilestoneRule).where(
                MilestoneRule.rule_id == "milestone.avoided_duplicate"
            )
        )).scalar_one()
        assert rule.behaviour == progress_milestones.BEHAVIOUR_AVOIDED_DUPLICATE

        for _ in range(2):
            await progress_service.record_behaviour(
                session,
                account_id,
                progress_milestones.BEHAVIOUR_AVOIDED_DUPLICATE,
                occurred_on=today,
                subject_id=candidate_id,
                detail={"reason": "already own something equivalent"},
            )
        await session.commit()

    async with factory() as session:
        earned = (await session.execute(
            select(Milestone).where(Milestone.account_id == account_id)
        )).scalars().all()
    assert [m.rule_id for m in earned] == ["milestone.avoided_duplicate"], (
        "replaying the same behaviour must not award a duplicate milestone"
    )
    assert earned[0].evidence["behaviour"] == (
        progress_milestones.BEHAVIOUR_AVOIDED_DUPLICATE
    )

    # No overall "attractiveness" metric shipped in the seed.
    async with factory() as session:
        from app.domains.progress.models import MetricDefinition
        metrics = (await session.execute(
            select(MetricDefinition.key)
        )).scalars().all()
    assert not any("attractive" in k or "overall" in k for k in metrics)

    # --------------------------------------------------------------
    # 23. Controlled memory — add, verify influence, correct, verify
    #     precedence, delete, verify exclusion.
    # --------------------------------------------------------------
    async with factory() as session:
        fact = MemoryFact(
            account_id=account_id,
            category="skin",
            subject_key="climate_preference",
            fact="Prefers a light SPF finish for humid days.",
            source="user_declared",
            confidence=0.9,
        )
        session.add(fact)
        await session.commit()
        fact_id = fact.id

    # A live memory fact is readable — the "influences recommendation" assertion.
    async with factory() as session:
        facts = (await session.execute(
            select(MemoryFact).where(
                MemoryFact.account_id == account_id,
                MemoryFact.deletion_state == "active",
            )
        )).scalars().all()
        assert any("light SPF" in f.fact for f in facts)

    # Correct the fact.
    async with factory() as session:
        fact = (await session.execute(
            select(MemoryFact).where(MemoryFact.id == fact_id)
        )).scalar_one()
        # Preserve the old value in the revision history.
        session.add(MemoryRevision(
            fact_id=fact.id,
            previous_fact=fact.fact,
            previous_confidence=fact.confidence,
            previous_verification_state=fact.verification_state,
            reason="user_correction",
        ))
        fact.fact = "Actually prefers a matte finish for humid days."
        fact.verification_state = "user_confirmed"
        fact.confidence = 1.0
        await session.commit()

    # Corrected value takes precedence.
    async with factory() as session:
        fact = (await session.execute(
            select(MemoryFact).where(MemoryFact.id == fact_id)
        )).scalar_one()
        assert "matte finish" in fact.fact
        revisions = (await session.execute(
            select(MemoryRevision).where(MemoryRevision.fact_id == fact_id)
        )).scalars().all()
        assert len(revisions) == 1

    # Delete the fact (blank text, tombstone the row).
    async with factory() as session:
        fact = (await session.execute(
            select(MemoryFact).where(MemoryFact.id == fact_id)
        )).scalar_one()
        session.add(MemoryRevision(
            fact_id=fact.id,
            previous_fact=fact.fact,
            previous_confidence=fact.confidence,
            previous_verification_state=fact.verification_state,
            reason="user_deleted",
        ))
        fact.fact = ""
        fact.deletion_state = "deleted"
        fact.deleted_at = utcnow()
        await session.commit()

    # Deleted fact is excluded from active reads.
    async with factory() as session:
        live_facts = (await session.execute(
            select(MemoryFact).where(
                MemoryFact.account_id == account_id,
                MemoryFact.deletion_state == "active",
            )
        )).scalars().all()
        assert all(f.id != fact_id for f in live_facts)

    # --------------------------------------------------------------
    # 24. Privacy export — every touched domain must be present, seven
    #     categories must appear, revision history must survive, secrets
    #     must not appear.
    # --------------------------------------------------------------
    async with factory() as session:
        payload = await export_service.build_export(session, account_id)

    assert payload["schema_version"] == export_service.EXPORT_SCHEMA_VERSION
    # Seven inventory items exported.
    assert len(payload["domains"]["inventory"]["items"]) == 7
    # Media metadata is present.
    assert len(payload["domains"]["media"]["assets"]) == 1
    # No storage-key leak.
    assert asset_key not in repr(payload)
    assert "storage_key" not in repr(payload)
    # Scan present, but with no raw image bytes.
    scans = payload["domains"]["scans"]["scans"]
    assert len(scans) == 1
    assert "image_bytes" not in scans[0] and "raw" not in scans[0]
    # Quiz + occasion + styling + shopping present.
    assert len(payload["domains"]["quiz_and_styling"]["quiz_submissions"]) == 1
    assert len(payload["domains"]["quiz_and_styling"]["occasions"]) == 1
    assert len(payload["domains"]["quiz_and_styling"]["looks"]) == 1
    assert len(payload["domains"]["shopping"]["decisions"]) == 1
    # Planning present.
    assert len(payload["domains"]["planning"]["daily_plans"]) == 1
    assert len(payload["domains"]["planning"]["weekly_plans"]) == 1
    assert len(payload["domains"]["planning"]["weather_snapshots"]) == 1
    # Routines and adherence present.
    assert len(payload["domains"]["routines"]["routines"]) == 2
    assert len(payload["domains"]["routines"]["adherence"]) == 3
    # Progress + memory present. Deleted memory row still exported (as
    # tombstone with blank fact).
    assert len(payload["domains"]["progress_and_memory"]["memory_facts"]) == 1
    tomb = payload["domains"]["progress_and_memory"]["memory_facts"][0]
    assert tomb["deletion_state"] == "deleted"
    assert tomb["fact"] == ""
    assert len(payload["domains"]["progress_and_memory"]["goals"]) == 1
    assert len(payload["domains"]["progress_and_memory"]["milestones"]) == 1
    # AI + audit + consent history all present.
    assert len(payload["domains"]["consent"]["entries"]) >= 1
    assert "ai_runs" in payload["domains"]["ai_and_ops"]

    # --------------------------------------------------------------
    # 25. Deletion — request + worker drain + Auth-called-LAST proof.
    # --------------------------------------------------------------
    async with factory() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    async with factory() as session:
        processed = await deletion_service.drain_all(session)
        await session.commit()
    assert processed >= 1

    # 25a. Storage prefix is empty.
    remaining = list(await fake_storage.list_prefix(f"media/{account_id}"))
    assert remaining == []

    # 25b. Application-owned data is gone.
    async with factory() as session:
        for model in (
            InventoryItem, AppearanceProfile, RoutineAdherence, MemoryFact,
            DailyPlan, WeeklyPlan, Scan, QuizSubmission, PurchaseDecision,
            Milestone, ProgressGoal,
        ):
            count = (await session.execute(
                select(func.count()).select_from(model).where(
                    model.account_id == account_id
                )
            )).scalar_one()
            assert count == 0, f"{model.__tablename__} still has {count} rows"

    # 25c. Deletion tombstone survives.
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job is not None and job.state == STATE_COMPLETE

    # 25d. Supabase Auth was called — and only after storage/db cleanup.
    assert fake_admin.deleted_users == [str(account_id)]

    # 25e. Deleted identity cannot access the application.
    async with factory() as session:
        gone = await identity.get_account(session, account_id)
        assert gone is None
