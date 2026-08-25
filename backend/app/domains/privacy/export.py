"""Privacy export service.

Assembles a JSON snapshot of everything the account owns from every domain
listed in :mod:`app.domains.privacy` as ``INCLUDED``. Each domain has a
handler that turns a set of ORM rows into a JSON-safe dict.

Boundaries this service enforces
--------------------------------
* Rows are always filtered by ``account_id`` at the SQL level, either directly
  (when the row carries ``account_id``) or through the parent row (e.g.
  ``look_items`` are joined via ``looks``).
* Secrets never appear. There are no columns in the ORM that store secrets
  today, but the registry marks tables ``SECRET_EXCLUDED`` for any future
  columns; ``included_tables()`` excludes them.
* Raw storage keys and provider paths are omitted. ``media_assets`` is exported
  via :func:`app.domains.media.service.to_public_dict`, which already strips
  ``storage_key`` and ``storage_backend``.
* Face/hair/hand image bytes are transient request data and never enter the
  export.
* Large collections are capped (``_MAX_ROWS``) so a runaway export cannot OOM
  the server; the cap is generous enough to cover a full beta account.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AIRun, AIRunOutput
from app.domains.audit.models import AuditEvent
from app.domains.beta_access.models import (
    BetaUsageEvent,
    Invite,
    InviteRedemption,
)
from app.domains.consent.models import Consent
from app.domains.identity.models import Account
from app.domains.inventory.models import (
    InventoryAttribute,
    InventoryEvent,
    InventoryItem,
    SupplementDetail,
)
from app.domains.media import service as media_service
from app.domains.media.models import MediaAsset
from app.domains.planning.models import (
    CalendarEvent,
    DailyPlan,
    EventReadyAction,
    EventReadyPlan,
    ExternalIntegration,
    WeatherSnapshot,
    WeeklyPlan,
)
from app.domains.privacy import EXPORT_SCHEMA_VERSION, REGISTRY, Classification
from app.domains.profile.models import (
    AppearanceGoal,
    AppearanceProfile,
    AttributeObservation,
    OnboardingSession,
    ProfileAttribute,
)
from app.domains.progress.models import (
    MetricEvent,
    Milestone,
    ProgressGoal,
    ProgressPhoto,
)
from app.domains.quiz.models import QuizSubmission
from app.domains.recommendation.models import (
    Look,
    LookAdjustment,
    LookFeedback,
    PurchaseDecision,
    PurchaseEvaluation,
    RecommendationRun,
    ShoppingCandidate,
    StyleRequest,
)
from app.domains.recommendation.models import (
    OccasionRecord as Occasion,
)
from app.domains.routines.models import (
    CareExperienceFeedback,
    HydrationPreference,
    MaintenanceEvent,
    MaintenancePreference,
    NutritionPreference,
    ProductExpiryEvent,
    ProductIngredient,
    Routine,
    RoutineAdherence,
    RoutineRecommendationRun,
    RoutineStep,
    SupplementSafetyFlag,
    UserReportedObservation,
)
from app.domains.scan.models import Scan
from app.domains.supplements.models import SupplementLabelComponent
from app.shared.database.base import utcnow

logger = logging.getLogger(__name__)

# Generous per-collection cap. A beta account with heavy use has been observed
# at ~3.5k inventory events and ~1.6k routine adherence rows; 20 000 covers
# well above that and keeps the export bounded.
_MAX_ROWS = 20_000


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _row_dict(row: Any, fields: list[str]) -> dict[str, Any]:
    """Turn a subset of a row's columns into a JSON-safe dict.

    ``uuid.UUID`` and ``datetime`` are serialised to strings; everything else
    is returned as-is (SQLAlchemy already gives us primitives for JSON, int
    and bool).
    """
    out: dict[str, Any] = {}
    for name in fields:
        value = getattr(row, name, None)
        if isinstance(value, uuid.UUID):
            out[name] = str(value)
        elif isinstance(value, datetime):
            out[name] = value.isoformat()
        else:
            out[name] = value
    return out


async def _fetch(session: AsyncSession, stmt) -> list[Any]:
    stmt = stmt.limit(_MAX_ROWS)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Domain handlers
# ---------------------------------------------------------------------------

async def _identity(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    account = (await session.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        return {}
    redemptions = await _fetch(
        session,
        select(InviteRedemption).where(InviteRedemption.account_id == account_id),
    )
    # Join through invite table to expose the code (safe to show to the owner)
    invites = await _fetch(
        session,
        select(Invite).where(
            Invite.id.in_(select(InviteRedemption.invite_id).where(
                InviteRedemption.account_id == account_id
            ))
        ),
    )
    invite_lookup = {inv.id: inv for inv in invites}
    return {
        "id": str(account.id),
        "status": account.status,
        "created_at": _iso(account.created_at),
        "updated_at": _iso(account.updated_at),
        "deletion_requested_at": _iso(account.deletion_requested_at),
        "invite_redemptions": [
            {
                "invite_code": getattr(invite_lookup.get(r.invite_id), "code", None),
                "redeemed_at": _iso(r.created_at),
            }
            for r in redemptions
        ],
    }


async def _profile(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    profiles = await _fetch(
        session,
        select(AppearanceProfile).where(AppearanceProfile.account_id == account_id),
    )
    profile_ids = [p.id for p in profiles]
    attributes = await _fetch(
        session,
        select(ProfileAttribute).where(ProfileAttribute.profile_id.in_(profile_ids)),
    ) if profile_ids else []
    goals = await _fetch(
        session,
        select(AppearanceGoal).where(AppearanceGoal.profile_id.in_(profile_ids)),
    ) if profile_ids else []
    observations = await _fetch(
        session,
        select(AttributeObservation).where(AttributeObservation.profile_id.in_(profile_ids)),
    ) if profile_ids else []
    onboarding = await _fetch(
        session,
        select(OnboardingSession).where(OnboardingSession.profile_id.in_(profile_ids)),
    ) if profile_ids else []
    return {
        "profiles": [_row_dict(p, [c.name for c in AppearanceProfile.__table__.columns]) for p in profiles],
        "attributes": [_row_dict(a, [c.name for c in ProfileAttribute.__table__.columns]) for a in attributes],
        "goals": [_row_dict(g, [c.name for c in AppearanceGoal.__table__.columns]) for g in goals],
        "observations": [_row_dict(o, [c.name for c in AttributeObservation.__table__.columns]) for o in observations],
        "onboarding_sessions": [_row_dict(o, [c.name for c in OnboardingSession.__table__.columns]) for o in onboarding],
    }


async def _consent(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    rows = await _fetch(
        session,
        select(Consent).where(Consent.account_id == account_id).order_by(Consent.recorded_at.desc()),
    )
    return {"entries": [_row_dict(r, [c.name for c in Consent.__table__.columns]) for r in rows]}


async def _inventory(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    items = await _fetch(
        session,
        select(InventoryItem).where(InventoryItem.account_id == account_id),
    )
    item_ids = [i.id for i in items]
    attrs = await _fetch(
        session,
        select(InventoryAttribute).where(InventoryAttribute.item_id.in_(item_ids)),
    ) if item_ids else []
    events = await _fetch(
        session,
        select(InventoryEvent).where(InventoryEvent.account_id == account_id),
    )
    supplement_details = await _fetch(
        session,
        select(SupplementDetail).where(SupplementDetail.item_id.in_(item_ids)),
    ) if item_ids else []
    return {
        "items": [_row_dict(i, [c.name for c in InventoryItem.__table__.columns]) for i in items],
        "attributes": [_row_dict(a, [c.name for c in InventoryAttribute.__table__.columns]) for a in attrs],
        "events": [_row_dict(e, [c.name for c in InventoryEvent.__table__.columns]) for e in events],
        "supplement_details": [
            _row_dict(row, [c.name for c in SupplementDetail.__table__.columns])
            for row in supplement_details
        ],
    }


async def _media(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    assets = await _fetch(
        session,
        select(MediaAsset).where(MediaAsset.account_id == account_id),
    )
    # ``to_public_dict`` already strips storage_key / storage_backend and
    # returns only safe fields.
    return {"assets": [media_service.to_public_dict(a) for a in assets]}


async def _scans(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    rows = await _fetch(
        session,
        select(Scan).where(Scan.account_id == account_id).order_by(Scan.created_at.desc()),
    )
    # Scan rows never contain raw image bytes — face/hair/hand photos are
    # transient request data. We keep the analysis result reference.
    return {"scans": [_row_dict(r, [c.name for c in Scan.__table__.columns]) for r in rows]}


async def _quiz_and_styling(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    submissions = await _fetch(
        session,
        select(QuizSubmission).where(QuizSubmission.account_id == account_id),
    )
    occasions = await _fetch(
        session,
        select(Occasion).where(Occasion.account_id == account_id),
    )
    style_requests = await _fetch(
        session,
        select(StyleRequest).where(StyleRequest.account_id == account_id),
    )
    runs = await _fetch(
        session,
        select(RecommendationRun).where(RecommendationRun.account_id == account_id),
    )
    looks = await _fetch(
        session,
        select(Look).where(Look.account_id == account_id),
    )
    adjustments = await _fetch(
        session,
        select(LookAdjustment).where(LookAdjustment.account_id == account_id),
    )
    feedback = await _fetch(
        session,
        select(LookFeedback).where(LookFeedback.account_id == account_id),
    )
    return {
        "quiz_submissions": [_row_dict(r, [c.name for c in QuizSubmission.__table__.columns]) for r in submissions],
        "occasions": [_row_dict(r, [c.name for c in Occasion.__table__.columns]) for r in occasions],
        "style_requests": [_row_dict(r, [c.name for c in StyleRequest.__table__.columns]) for r in style_requests],
        "recommendation_runs": [_row_dict(r, [c.name for c in RecommendationRun.__table__.columns]) for r in runs],
        "looks": [_row_dict(r, [c.name for c in Look.__table__.columns]) for r in looks],
        "look_adjustments": [_row_dict(r, [c.name for c in LookAdjustment.__table__.columns]) for r in adjustments],
        "look_feedback": [_row_dict(r, [c.name for c in LookFeedback.__table__.columns]) for r in feedback],
    }


async def _shopping(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    candidates = await _fetch(session, select(ShoppingCandidate).where(ShoppingCandidate.account_id == account_id))
    evaluations = await _fetch(session, select(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id))
    decisions = await _fetch(session, select(PurchaseDecision).where(PurchaseDecision.account_id == account_id))
    return {
        "candidates": [_row_dict(r, [c.name for c in ShoppingCandidate.__table__.columns]) for r in candidates],
        "evaluations": [_row_dict(r, [c.name for c in PurchaseEvaluation.__table__.columns]) for r in evaluations],
        "decisions": [_row_dict(r, [c.name for c in PurchaseDecision.__table__.columns]) for r in decisions],
    }


async def _planning(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    daily = await _fetch(session, select(DailyPlan).where(DailyPlan.account_id == account_id))
    weekly = await _fetch(session, select(WeeklyPlan).where(WeeklyPlan.account_id == account_id))
    calendar = await _fetch(session, select(CalendarEvent).where(CalendarEvent.account_id == account_id))
    integrations = await _fetch(session, select(ExternalIntegration).where(ExternalIntegration.account_id == account_id))
    weather = await _fetch(session, select(WeatherSnapshot).where(WeatherSnapshot.account_id == account_id))
    event_ready_plans = await _fetch(session, select(EventReadyPlan).where(EventReadyPlan.account_id == account_id))
    event_ready_actions = await _fetch(
        session,
        select(EventReadyAction).where(EventReadyAction.event_ready_plan_id.in_([row.id for row in event_ready_plans])),
    ) if event_ready_plans else []
    return {
        "daily_plans": [_row_dict(r, [c.name for c in DailyPlan.__table__.columns]) for r in daily],
        "weekly_plans": [_row_dict(r, [c.name for c in WeeklyPlan.__table__.columns]) for r in weekly],
        "calendar_events": [_row_dict(r, [c.name for c in CalendarEvent.__table__.columns]) for r in calendar],
        # Connection health is user-relevant; opaque credential and provider
        # cursor machinery are intentionally excluded from privacy export.
        "calendar_integrations": [_row_dict(r, ["id", "kind", "provider", "status", "scopes", "external_account_label", "last_synced_at", "last_error", "revoked_at"]) for r in integrations],
        "weather_snapshots": [_row_dict(r, [c.name for c in WeatherSnapshot.__table__.columns]) for r in weather],
        "event_ready_plans": [_row_dict(r, [c.name for c in EventReadyPlan.__table__.columns]) for r in event_ready_plans],
        "event_ready_actions": [_row_dict(r, [c.name for c in EventReadyAction.__table__.columns]) for r in event_ready_actions],
    }


async def _routines(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    routines = await _fetch(session, select(Routine).where(Routine.account_id == account_id))
    steps = await _fetch(
        session,
        select(RoutineStep).where(RoutineStep.routine_id.in_([r.id for r in routines])),
    ) if routines else []
    adherence = await _fetch(session, select(RoutineAdherence).where(RoutineAdherence.account_id == account_id))
    recommendation_runs = await _fetch(
        session,
        select(RoutineRecommendationRun).where(
            RoutineRecommendationRun.account_id == account_id,
        ),
    )
    product_ingredients = await _fetch(
        session,
        select(ProductIngredient).where(ProductIngredient.account_id == account_id),
    )
    observations = await _fetch(
        session,
        select(UserReportedObservation).where(UserReportedObservation.account_id == account_id),
    )
    product_expiry_events = await _fetch(
        session,
        select(ProductExpiryEvent).where(ProductExpiryEvent.account_id == account_id),
    )
    supplement_safety_flags = await _fetch(
        session,
        select(SupplementSafetyFlag).where(SupplementSafetyFlag.account_id == account_id),
    )
    label_components = await _fetch(
        session, select(SupplementLabelComponent).where(SupplementLabelComponent.account_id == account_id),
    )
    nutrition_preferences = await _fetch(
        session,
        select(NutritionPreference).where(NutritionPreference.account_id == account_id),
    )
    hydration_preferences = await _fetch(
        session,
        select(HydrationPreference).where(HydrationPreference.account_id == account_id),
    )
    experience_feedback = await _fetch(
        session,
        select(CareExperienceFeedback).where(CareExperienceFeedback.account_id == account_id),
    )
    maintenance_preferences = await _fetch(
        session,
        select(MaintenancePreference).where(MaintenancePreference.account_id == account_id),
    )
    maintenance_events = await _fetch(
        session,
        select(MaintenanceEvent).where(MaintenanceEvent.account_id == account_id),
    )
    return {
        "maintenance_preferences": [
            _row_dict(r, [c.name for c in MaintenancePreference.__table__.columns])
            for r in maintenance_preferences
        ],
        "maintenance_events": [
            _row_dict(r, [c.name for c in MaintenanceEvent.__table__.columns])
            for r in maintenance_events
        ],
        "routines": [_row_dict(r, [c.name for c in Routine.__table__.columns]) for r in routines],
        "steps": [_row_dict(r, [c.name for c in RoutineStep.__table__.columns]) for r in steps],
        "adherence": [_row_dict(r, [c.name for c in RoutineAdherence.__table__.columns]) for r in adherence],
        "recommendation_runs": [
            _row_dict(r, [c.name for c in RoutineRecommendationRun.__table__.columns])
            for r in recommendation_runs
        ],
        "product_ingredients": [
            _row_dict(r, [c.name for c in ProductIngredient.__table__.columns])
            for r in product_ingredients
        ],
        "observations": [
            _row_dict(r, [c.name for c in UserReportedObservation.__table__.columns])
            for r in observations
        ],
        "product_expiry_events": [
            _row_dict(r, [c.name for c in ProductExpiryEvent.__table__.columns])
            for r in product_expiry_events
        ],
        "supplement_safety_flags": [
            _row_dict(r, [c.name for c in SupplementSafetyFlag.__table__.columns])
            for r in supplement_safety_flags
        ],
        "supplement_label_components": [
            _row_dict(r, [c.name for c in SupplementLabelComponent.__table__.columns])
            for r in label_components
        ],
        "nutrition_preferences": [
            _row_dict(r, [c.name for c in NutritionPreference.__table__.columns])
            for r in nutrition_preferences
        ],
        "hydration_preferences": [
            _row_dict(r, [c.name for c in HydrationPreference.__table__.columns])
            for r in hydration_preferences
        ],
        "experience_feedback": [
            _row_dict(r, [c.name for c in CareExperienceFeedback.__table__.columns])
            for r in experience_feedback
        ],
    }


async def _progress_and_memory(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    # Local imports to keep the top of the module tidy.
    from app.domains.progress.models import (
        FeedbackEvent,
        GamificationEvent,
        MemoryFact,
        MemoryRevision,
        MemorySource,
    )

    events = await _fetch(session, select(MetricEvent).where(MetricEvent.account_id == account_id))
    goals = await _fetch(session, select(ProgressGoal).where(ProgressGoal.account_id == account_id))
    milestones = await _fetch(session, select(Milestone).where(Milestone.account_id == account_id))
    photos = await _fetch(session, select(ProgressPhoto).where(ProgressPhoto.account_id == account_id))
    facts = await _fetch(session, select(MemoryFact).where(MemoryFact.account_id == account_id))

    # Revisions and sources hang off the fact, not the account, so they are
    # fetched through the account's facts. Corrections and tombstones are the
    # part of memory a user most needs to see: an export that lists only the
    # current wording hides what was remembered before, and what was deleted.
    fact_ids = select(MemoryFact.id).where(MemoryFact.account_id == account_id)
    revisions = await _fetch(
        session, select(MemoryRevision).where(MemoryRevision.fact_id.in_(fact_ids))
    )
    sources = await _fetch(
        session, select(MemorySource).where(MemorySource.fact_id.in_(fact_ids))
    )
    feedback = await _fetch(
        session, select(FeedbackEvent).where(FeedbackEvent.account_id == account_id)
    )
    behaviours = await _fetch(
        session, select(GamificationEvent).where(GamificationEvent.account_id == account_id)
    )

    return {
        "metric_events": [_row_dict(r, [c.name for c in MetricEvent.__table__.columns]) for r in events],
        "goals": [_row_dict(r, [c.name for c in ProgressGoal.__table__.columns]) for r in goals],
        "milestones": [_row_dict(r, [c.name for c in Milestone.__table__.columns]) for r in milestones],
        "photos": [_row_dict(r, [c.name for c in ProgressPhoto.__table__.columns]) for r in photos],
        "memory_facts": [_row_dict(r, [c.name for c in MemoryFact.__table__.columns]) for r in facts],
        "memory_revisions": [_row_dict(r, [c.name for c in MemoryRevision.__table__.columns]) for r in revisions],
        "memory_sources": [_row_dict(r, [c.name for c in MemorySource.__table__.columns]) for r in sources],
        "feedback_events": [_row_dict(r, [c.name for c in FeedbackEvent.__table__.columns]) for r in feedback],
        "behaviour_events": [_row_dict(r, [c.name for c in GamificationEvent.__table__.columns]) for r in behaviours],
    }


async def _ai_and_ops(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    runs = await _fetch(
        session,
        select(AIRun).where(AIRun.account_id == account_id).order_by(AIRun.created_at.desc()),
    )
    outputs = await _fetch(
        session,
        select(AIRunOutput).where(AIRunOutput.ai_run_id.in_([r.id for r in runs])),
    ) if runs else []
    audit = await _fetch(
        session,
        select(AuditEvent).where(AuditEvent.account_id == account_id).order_by(AuditEvent.created_at.desc()),
    )
    beta = await _fetch(
        session,
        select(BetaUsageEvent).where(BetaUsageEvent.account_id == account_id),
    )
    return {
        "ai_runs": [_row_dict(r, [c.name for c in AIRun.__table__.columns]) for r in runs],
        "ai_run_outputs": [_row_dict(r, [c.name for c in AIRunOutput.__table__.columns]) for r in outputs],
        "audit_events": [_row_dict(r, [c.name for c in AuditEvent.__table__.columns]) for r in audit],
        "beta_usage_events": [_row_dict(r, [c.name for c in BetaUsageEvent.__table__.columns]) for r in beta],
    }


DomainHandler = Callable[[AsyncSession, uuid.UUID], Any]

DOMAIN_HANDLERS: dict[str, DomainHandler] = {
    "identity": _identity,
    "profile": _profile,
    "consent": _consent,
    "inventory": _inventory,
    "media": _media,
    "scans": _scans,
    "quiz_and_styling": _quiz_and_styling,
    "shopping": _shopping,
    "planning": _planning,
    "routines": _routines,
    "progress_and_memory": _progress_and_memory,
    "ai_and_ops": _ai_and_ops,
}


async def build_export(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """Build the complete privacy-export payload for ``account_id``."""
    domains: dict[str, Any] = {}
    for name, handler in DOMAIN_HANDLERS.items():
        try:
            domains[name] = await handler(session, account_id)
        except Exception:  # noqa: BLE001 — one domain must not sink the export
            logger.exception("privacy_export_domain_failed domain=%s", name)
            # Emit an explicit failure marker so the user can see something
            # went wrong for that domain rather than silently missing it.
            domains[name] = {"error": "domain_export_failed"}

    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "account": {"id": str(account_id)},
        "domains": domains,
        "registry_summary": {
            "included_domains": sorted(DOMAIN_HANDLERS.keys()),
            "included_tables": sorted(
                name for name, kind in REGISTRY.items()
                if kind == Classification.INCLUDED
            ),
            "not_user_owned": sorted(
                name for name, kind in REGISTRY.items()
                if kind == Classification.NOT_USER_OWNED
            ),
            "operational_only": sorted(
                name for name, kind in REGISTRY.items()
                if kind == Classification.OPERATIONAL
            ),
            "legally_retained": sorted(
                name for name, kind in REGISTRY.items()
                if kind == Classification.LEGALLY_RETAINED
            ),
        },
    }
