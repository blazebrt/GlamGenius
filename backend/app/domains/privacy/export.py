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
from collections.abc import Callable, Iterator
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
from app.domains.care.models import CareProductPreference
from app.domains.care.product_preferences import CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY, CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY
from app.domains.community.models import CommunityObservationReport
from app.domains.consent.models import Consent
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import RELATION_SELF
from app.domains.identity.models import Account
from app.domains.inventory.models import (
    InventoryAttribute,
    InventoryEvent,
    InventoryImportCandidate,
    InventoryImportJob,
    InventoryItem,
    InventoryProductLink,
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
    NotificationDelivery,
    NotificationPreference,
    WeatherSnapshot,
    WeeklyPlan,
)
from app.domains.privacy import EXPORT_SCHEMA_VERSION, REGISTRY, Classification
from app.domains.product.models import LabelErrorReport, ScanDecisionEvent, ScanEvent
from app.domains.profile.models import (
    AppearanceGoal,
    AppearanceProfile,
    AttributeObservation,
    FitPreference,
    LifestyleContext,
    OnboardingSession,
    ProfileAttribute,
    ProfileChangeEvent,
    StylePreference,
    UserConstraint,
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
    PurchaseDecisionEvent,
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
    ShelfManagerDecisionEvent,
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


#: Every child table that hangs off ``appearance_profiles.id``.
#:
#: Five of these — change events, style, fit, lifestyle and constraints — were
#: classified INCLUDED in the registry and exported by nothing. The registry is
#: a promise that the account holder can read their own data back, and for
#: those five it had been making that promise to nobody. Step 11B keeps it.
_PROFILE_CHILD_TABLES: tuple[tuple[str, Any], ...] = (
    ("attributes", ProfileAttribute),
    ("change_events", ProfileChangeEvent),
    ("observations", AttributeObservation),
    ("style_preferences", StylePreference),
    ("fit_preferences", FitPreference),
    ("lifestyle_context", LifestyleContext),
    ("user_constraints", UserConstraint),
    ("goals", AppearanceGoal),
    ("onboarding_sessions", OnboardingSession),
)


async def _profile_payload(session: AsyncSession, profile: AppearanceProfile) -> dict[str, Any]:
    """One profile and everything attached to it, by ``profile_id``."""
    payload: dict[str, Any] = {
        "profile": _row_dict(profile, [c.name for c in AppearanceProfile.__table__.columns]),
    }
    for label, model in _PROFILE_CHILD_TABLES:
        rows = await _fetch(session, select(model).where(model.profile_id == profile.id))
        payload[label] = [_row_dict(r, [c.name for c in model.__table__.columns]) for r in rows]
    return payload


def _empty_profile_payload() -> dict[str, Any]:
    return {"profile": None, **{label: [] for label, _ in _PROFILE_CHILD_TABLES}}


async def _household_members(
    session: AsyncSession, account_id: uuid.UUID,
) -> tuple[uuid.UUID | None, list[FamilyProfile]]:
    """This account's circle and its members, in household order."""
    circle_id = await session.scalar(
        select(FamilyCircle.id).where(FamilyCircle.account_id == account_id)
    )
    if circle_id is None:
        return None, []
    members = await _fetch(
        session,
        select(FamilyProfile)
        .where(FamilyProfile.circle_id == circle_id)
        .order_by(FamilyProfile.position),
    )
    return circle_id, list(members)


def _group_decision_rows(
    rows: list[Any], members: list[FamilyProfile],
) -> tuple[dict[uuid.UUID, list[Any]], list[Any], list[Any]]:
    """Split one account's decision rows into per-member, legacy and orphaned.

    Three outcomes, and the middle one is the point of this whole slice.

    A row naming a member of this household goes to that member. A row naming a
    subject this account does not own is **orphaned** — the foreign key proves
    the member exists somewhere, never that it is this account's, and putting a
    stranger's id in a customer's file would leak it. And a subject-less row is
    legacy: only safely attributable to the account holder if it predates the
    household, because after that it could have been about anybody in it.

    Nothing is decided by guessing. Where the answer is unknown the row still
    goes in the file — it is the customer's data — just without a name on it.
    """
    known = {member.id: member for member in members}
    by_member: dict[uuid.UUID, list[Any]] = {member.id: [] for member in members}
    legacy: list[Any] = []
    orphaned: list[Any] = []
    for row in rows:
        subject_id = row.household_subject_id
        if subject_id is None:
            legacy.append(row)
        elif subject_id in known:
            by_member[subject_id].append(row)
        else:
            orphaned.append(row)
    return by_member, legacy, orphaned


def _safe_legacy_split(
    rows: list[Any], *, circle_created_at: datetime | None, timestamp: str,
) -> tuple[list[Any], list[Any]]:
    """Legacy rows the account holder can honestly claim, and the rest.

    Strictly ``<``: a row written in the same instant the household was created
    is ambiguous, and ambiguity is never resolved in favour of the account
    holder. One row, one account — getting it wrong shows one person another
    person's purchase history.
    """
    if circle_created_at is None:
        return list(rows), []
    safe, ambiguous = [], []
    for row in rows:
        when = getattr(row, timestamp, None)
        (safe if when is not None and when < circle_created_at else ambiguous).append(row)
    return safe, ambiguous


def _unattributed_row(row: Any, fields: list[str]) -> dict[str, Any]:
    """An owned row with the identity it claims stripped out.

    Used for a row pointing at another account's member. The data is this
    customer's and belongs in their file; the foreign id is somebody else's and
    does not.
    """
    payload = _row_dict(row, fields)
    payload["household_subject_id"] = None
    return payload


async def _profile(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """The appearance domain, grouped by the human each profile describes.

    A flat list would hand somebody one undifferentiated pile of several
    people's bodies, which is exactly the confusion a household introduces and
    the export has to answer.

    This is a **pure read**. A household whose account holder has not yet been
    adopted is *labelled* as the account holder without the row being touched:
    an export that adopted as a side effect would mean downloading your data
    changed it. That holds for malformed identity too — nothing here repairs,
    adopts, merges or deletes, whatever it finds.

    Where the rest of the product refuses to answer on malformed identity, this
    records it and carries on. An export is the one surface where stopping is
    the wrong answer: somebody exercising a data right must still receive their
    data, and a household whose ``self`` row is missing has not stopped owning
    the rows underneath it. So identity is left unstated, the problem goes in
    ``invariant_errors``, and every row this account owns is still in the file.
    """
    profiles = await _fetch(
        session,
        select(AppearanceProfile).where(AppearanceProfile.account_id == account_id),
    )
    # Whether a household exists is a fact about the circle, not about how many
    # members happen to be in it. Inferring it from ``members`` would read a
    # corrupt circle with no rows at all as "this account never opened a
    # household" — the most alarming state there is, silently reported as the
    # most ordinary one.
    circle_id = await session.scalar(
        select(FamilyCircle.id).where(FamilyCircle.account_id == account_id)
    )
    members = (await _fetch(
        session,
        select(FamilyProfile)
        .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
        .where(FamilyCircle.account_id == account_id)
        .order_by(FamilyProfile.position),
    )) if circle_id is not None else []

    # Exactly one active account holder, or none named at all. Taking the first
    # of two would label one human as the account holder by insertion order,
    # and the profile underneath a ``self`` label is the one the legacy row is
    # attributed to — so getting it wrong would attach the signed-in person's
    # history to somebody else in their household, inside the file they asked
    # for precisely to see who has what.
    self_rows = [m for m in members if m.relation == RELATION_SELF and m.active]
    invariant_errors: list[dict[str, str]] = []
    if len(self_rows) == 1:
        self_row = self_rows[0]
    else:
        self_row = None
        if circle_id is not None:
            invariant_errors.append({
                "error": (
                    "household_self_profile_missing" if not self_rows
                    else "household_has_multiple_self_profiles"
                ),
            })

    by_subject = {p.household_subject_id: p for p in profiles if p.household_subject_id}
    legacy = next((p for p in profiles if p.household_subject_id is None), None)

    subjects: list[dict[str, Any]] = []
    claimed: set[uuid.UUID] = set()

    for member in members:
        profile = by_subject.get(member.id)
        is_self = self_row is not None and member.id == self_row.id
        # The unadopted case: no profile is bound to the self row yet, but the
        # legacy row is that person's. Named here, not rewritten.
        if profile is None and is_self and legacy is not None:
            profile = legacy
        if profile is not None:
            claimed.add(profile.id)
        entry: dict[str, Any] = {
            "household_subject_id": str(member.id),
            "relation": member.relation,
            "age_band": member.age_band,
            "active": member.active,
            "is_account_holder": is_self,
            "adopted": profile is not None and profile.household_subject_id is not None,
        }
        entry.update(
            await _profile_payload(session, profile) if profile is not None
            else _empty_profile_payload()
        )
        subjects.append(entry)

    # An account with no household still has an account holder, and their
    # profile is the legacy row. It appears under a null subject because there
    # is no household row to name — not because it belongs to nobody.
    #
    # When the household exists but its account holder could not be identified,
    # the same row is still exported — it is the customer's data and they asked
    # for it — but it is not labelled as anybody's, because the one thing worse
    # than an unlabelled profile is a confidently mislabelled one.
    if legacy is not None and legacy.id not in claimed:
        unattributed = circle_id is not None and self_row is None
        subjects.append({
            "household_subject_id": None,
            "relation": None if unattributed else RELATION_SELF,
            "age_band": None,
            "active": True,
            "is_account_holder": not unattributed,
            "adopted": False,
            **await _profile_payload(session, legacy),
        })

    # A profile bound to a subject this account does not own is a broken
    # invariant, and the two halves of the answer pull in opposite directions.
    #
    # The subject id names somebody in *another* household, so it must not
    # appear here at all: this file is handed to a customer, and a stranger's
    # member id is a stranger's identity. But the profile row and every child
    # row under it belong to *this* account by ``account_id``, and those are
    # the customer's own body facts. Dropping them to avoid the leak would
    # answer a data-rights request by quietly withholding data.
    #
    # So they are exported in full, with the identity stripped rather than
    # invented: no subject id, no relation, no claim about who this describes.
    # Nothing is repaired, adopted, merged or deleted — an export is not the
    # place to decide whose body facts these were.
    known_member_ids = {m.id for m in members}
    orphaned = [
        p for p in profiles
        if p.household_subject_id is not None
        and p.household_subject_id not in known_member_ids
    ]

    unattributed_profiles: list[dict[str, Any]] = []
    for profile in orphaned:
        invariant_errors.append(
            {"profile_id": str(profile.id), "error": "profile_subject_ownership_invalid"}
        )
        payload = await _profile_payload(session, profile)
        # The corrupt link is the one field that names somebody outside this
        # account. It is replaced rather than echoed.
        payload["profile"]["household_subject_id"] = None
        unattributed_profiles.append({
            "household_subject_id": None,
            "relation": None,
            "age_band": None,
            "active": True,
            "is_account_holder": False,
            "adopted": False,
            **payload,
        })

    return {
        "subjects": subjects,
        # Profiles this account owns that no subject of this account explains.
        # Separate from ``subjects`` on purpose: everything in that list is
        # attributed to a named human, and these are precisely the rows that
        # cannot be.
        "unattributed_profiles": unattributed_profiles,
        "invariant_errors": invariant_errors,
    }


async def _consent(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    rows = await _fetch(
        session,
        select(Consent).where(Consent.account_id == account_id).order_by(Consent.recorded_at.desc()),
    )
    return {"entries": [_row_dict(r, [c.name for c in Consent.__table__.columns]) for r in rows]}


async def _household(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """The household this account opened, and the people it named.

    Both tables are classified ``INCLUDED`` in the registry, which is a promise
    that the account can read them back. Profiles carry no ``account_id`` of
    their own, so they are reached through the circle that does — the same
    parent-join rule the rest of this file uses — and never through the profile
    id alone.
    """
    circles = await _fetch(
        session,
        select(FamilyCircle)
        .where(FamilyCircle.account_id == account_id)
        .order_by(FamilyCircle.created_at),
    )
    profiles = await _fetch(
        session,
        select(FamilyProfile)
        .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
        .where(FamilyCircle.account_id == account_id)
        .order_by(FamilyProfile.position),
    )
    return {
        "circles": [
            _row_dict(c, [col.name for col in FamilyCircle.__table__.columns])
            for c in circles
        ],
        "profiles": [
            _row_dict(p, [col.name for col in FamilyProfile.__table__.columns])
            for p in profiles
        ],
    }


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
    # Photo captures and what each one offered. A rejected candidate stays in
    # the export: it is a record of a guess made about this person.
    imports = await _fetch(
        session,
        select(InventoryImportJob).where(InventoryImportJob.account_id == account_id),
    )
    candidates = await _fetch(
        session,
        select(InventoryImportCandidate).where(InventoryImportCandidate.account_id == account_id),
    )
    product_links = await _fetch(
        session,
        select(InventoryProductLink).where(InventoryProductLink.account_id == account_id),
    )
    _, _members = await _household_members(session, account_id)
    member_ids = {member.id for member in _members}
    circle_created_at = await session.scalar(
        select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
    )
    preference_event_types = {
        "care_routine_paused", "care_routine_resumed", "care_routine_preferred",
        "care_routine_preference_cleared",
    }
    event_payloads: list[dict[str, Any]] = []
    care_event_payloads: list[dict[str, Any]] = []
    for event in events:
        row = _row_dict(event, [c.name for c in InventoryEvent.__table__.columns])
        if event.household_subject_id is not None and event.household_subject_id not in member_ids:
            row["household_subject_id"] = None
            row["invariant"] = "inventory_event_subject_ownership_invalid"
        event_payloads.append(row)
        if event.event_type in preference_event_types:
            if event.household_subject_id is None and circle_created_at is not None and event.created_at >= circle_created_at:
                row = dict(row)
                row["household_subject_id"] = None
                row["invariant"] = "legacy_preference_event_unattributed"
            care_event_payloads.append(row)
    care_keys = {CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY, CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY}
    physical_attrs = [row for row in attrs if row.key not in care_keys]
    legacy_care_attrs = [row for row in attrs if row.key in care_keys]
    _, members = await _household_members(session, account_id)
    circle_created_at = await session.scalar(
        select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
    )
    safe_legacy_attrs, ambiguous_legacy_attrs = _safe_legacy_split(
        legacy_care_attrs, circle_created_at=circle_created_at, timestamp="updated_at",
    )
    return {
        "items": [_row_dict(i, [c.name for c in InventoryItem.__table__.columns]) for i in items],
        "attributes": [_row_dict(a, [c.name for c in InventoryAttribute.__table__.columns]) for a in physical_attrs],
        "care_preference_history": {
            "account_holder_legacy": [
                _row_dict(a, [c.name for c in InventoryAttribute.__table__.columns])
                for a in safe_legacy_attrs
            ],
            "unattributed": [
                _row_dict(a, [c.name for c in InventoryAttribute.__table__.columns])
                for a in ambiguous_legacy_attrs
            ],
            "coverage": {
                "unattributed_legacy_preferences_present": bool(ambiguous_legacy_attrs),
                "complete_for_subject": not bool(ambiguous_legacy_attrs),
            },
        },
        "events": event_payloads,
        "care_preference_events": care_event_payloads,
        "supplement_details": [
            _row_dict(row, [c.name for c in SupplementDetail.__table__.columns])
            for row in supplement_details
        ],
        "import_jobs": [
            _row_dict(row, [c.name for c in InventoryImportJob.__table__.columns])
            for row in imports
        ],
        "import_candidates": [
            _row_dict(row, [c.name for c in InventoryImportCandidate.__table__.columns])
            for row in candidates
        ],
        "product_links": [
            _row_dict(row, [c.name for c in InventoryProductLink.__table__.columns])
            for row in product_links
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


async def _product_scans(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """Barcodes this account has scanned, and the corrections they sent us.

    Only rows linked to the account. A scan or report made before signing up
    carries no account and belongs to nobody, so it is in nobody's export.

    The report photo is referenced by its storage key, never inlined — the
    same rule the rest of this module follows for media.
    """
    rows = await _fetch(
        session,
        select(ScanEvent)
        .where(ScanEvent.account_id == account_id)
        .order_by(ScanEvent.created_at.desc()),
    )
    fields = [c.name for c in ScanEvent.__table__.columns]
    reports = await _fetch(
        session,
        select(LabelErrorReport)
        .where(LabelErrorReport.account_id == account_id)
        .order_by(LabelErrorReport.created_at.desc()),
    )
    report_fields = [c.name for c in LabelErrorReport.__table__.columns]
    memory_rows = list(await _fetch(
        session,
        select(ScanDecisionEvent)
        .where(ScanDecisionEvent.account_id == account_id)
        .order_by(ScanDecisionEvent.created_at.desc()),
    ))
    memory_fields = [c.name for c in ScanDecisionEvent.__table__.columns]

    # The scan itself stays account-level: it records that this account looked
    # at a barcode, which is true regardless of who the answer was for. What
    # somebody *decided* is about a person, so only that is grouped.
    circle_id, members = await _household_members(session, account_id)
    circle_created_at = await session.scalar(
        select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
    )
    self_rows = [m for m in members if m.relation == RELATION_SELF and m.active]
    self_row = self_rows[0] if len(self_rows) == 1 else None

    by_member, legacy, orphaned = _group_decision_rows(
        memory_rows, members,
    )
    safe, ambiguous = _safe_legacy_split(
        legacy, circle_created_at=circle_created_at, timestamp="created_at",
    )
    if self_row is None and circle_id is not None:
        ambiguous, safe = legacy, []

    invariant_errors: list[dict[str, str]] = []
    if circle_id is not None and self_row is None:
        invariant_errors.append({
            "error": (
                "household_self_profile_missing" if not self_rows
                else "household_has_multiple_self_profiles"
            ),
        })
    invariant_errors.extend(
        {"scan_decision_event_id": str(r.id), "error": "decision_subject_ownership_invalid"}
        for r in orphaned
    )

    subjects: list[dict[str, Any]] = []
    for member in members:
        is_self = self_row is not None and member.id == self_row.id
        own = list(by_member.get(member.id, [])) + (safe if is_self else [])
        subjects.append({
            "household_subject_id": str(member.id),
            "relation": member.relation,
            "age_band": member.age_band,
            "active": member.active,
            "is_account_holder": is_self,
            "scan_decision_events": [_row_dict(r, memory_fields) for r in own],
        })
    if circle_id is None:
        subjects.append({
            "household_subject_id": None,
            "relation": RELATION_SELF,
            "age_band": None,
            "active": True,
            "is_account_holder": True,
            "scan_decision_events": [_row_dict(r, memory_fields) for r in safe],
        })

    return {
        "scans": [_row_dict(r, fields) for r in rows],
        "label_error_reports": [_row_dict(r, report_fields) for r in reports],
        "subjects": subjects,
        "unattributed_scan_decision_events": (
            [_row_dict(r, memory_fields) for r in ambiguous]
            + [_unattributed_row(r, memory_fields) for r in orphaned]
        ),
        "invariant_errors": invariant_errors,
    }


async def _community(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """The person's own structured observations, and nobody else's.

    Their rows only. No other shopper's report, no moderator identity, and no
    aggregate about other accounts — those are the whole reason aggregation
    exists, and handing them out here would undo it. The supporting photo is
    referenced by asset id, following the same rule as the rest of this module.
    """
    rows = await _fetch(
        session,
        select(CommunityObservationReport)
        .where(CommunityObservationReport.account_id == account_id)
        .order_by(CommunityObservationReport.created_at.desc()),
    )
    fields = [c.name for c in CommunityObservationReport.__table__.columns]
    return {"observation_reports": [_row_dict(r, fields) for r in rows]}


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
    """Candidates stay account-wide; what people decided about them does not.

    A shopping candidate is one thing the account is considering, and several
    people in a household may consider it independently — so it is not cloned
    per subject and not grouped by one. Decision memory is the opposite: it is
    always about one human, and a file that mixed several people's decisions
    together would answer a data-rights request with a pile the customer cannot
    read.

    This is a **pure read**. Nothing here adopts a legacy row, repairs a
    malformed one, or writes anything at all: downloading your data must not
    change it.
    """
    candidates = await _fetch(session, select(ShoppingCandidate).where(ShoppingCandidate.account_id == account_id))
    evaluations = await _fetch(session, select(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id))
    decisions = list(await _fetch(session, select(PurchaseDecision).where(PurchaseDecision.account_id == account_id)))
    decision_events = list(await _fetch(session, select(PurchaseDecisionEvent).where(PurchaseDecisionEvent.account_id == account_id)))

    circle_id, members = await _household_members(session, account_id)
    circle_created_at = await session.scalar(
        select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
    )
    self_rows = [m for m in members if m.relation == RELATION_SELF and m.active]
    self_row = self_rows[0] if len(self_rows) == 1 else None
    invariant_errors: list[dict[str, str]] = []
    if circle_id is not None and self_row is None:
        # Reuses the household posture from Step 11B: with no single account
        # holder, nothing subject-less is confidently assigned to anybody.
        invariant_errors.append({
            "error": (
                "household_self_profile_missing" if not self_rows
                else "household_has_multiple_self_profiles"
            ),
        })

    decision_fields = [c.name for c in PurchaseDecision.__table__.columns]
    event_fields = [c.name for c in PurchaseDecisionEvent.__table__.columns]

    decisions_by_member, legacy_decisions, orphan_decisions = _group_decision_rows(
        decisions, members,
    )
    events_by_member, legacy_events, orphan_events = _group_decision_rows(
        decision_events, members,
    )
    # A mutable current decision is judged on when it last said something; an
    # immutable event on when it was written.
    safe_decisions, ambiguous_decisions = _safe_legacy_split(
        legacy_decisions, circle_created_at=circle_created_at, timestamp="updated_at",
    )
    safe_events, ambiguous_events = _safe_legacy_split(
        legacy_events, circle_created_at=circle_created_at, timestamp="created_at",
    )
    if self_row is None and circle_id is not None:
        # Nobody to attribute them to, so nobody gets them.
        ambiguous_decisions = legacy_decisions
        ambiguous_events = legacy_events
        safe_decisions = safe_events = []

    subjects: list[dict[str, Any]] = []
    for member in members:
        is_self = self_row is not None and member.id == self_row.id
        own_decisions = list(decisions_by_member.get(member.id, []))
        own_events = list(events_by_member.get(member.id, []))
        if is_self:
            # The account holder's own history reaches back past the household,
            # but only as far as the boundary honestly allows.
            own_decisions = own_decisions + safe_decisions
            own_events = own_events + safe_events
        subjects.append({
            "household_subject_id": str(member.id),
            "relation": member.relation,
            "age_band": member.age_band,
            "active": member.active,
            "is_account_holder": is_self,
            "decisions": [_row_dict(r, decision_fields) for r in own_decisions],
            "decision_events": [_row_dict(r, event_fields) for r in own_events],
        })

    if circle_id is None:
        # No household ever existed, so subject-less is simply how this
        # account's own decisions have always been written.
        subjects.append({
            "household_subject_id": None,
            "relation": RELATION_SELF,
            "age_band": None,
            "active": True,
            "is_account_holder": True,
            "decisions": [_row_dict(r, decision_fields) for r in safe_decisions],
            "decision_events": [_row_dict(r, event_fields) for r in safe_events],
        })

    invariant_errors.extend(
        {"purchase_decision_id": str(r.id), "error": "decision_subject_ownership_invalid"}
        for r in orphan_decisions
    )
    invariant_errors.extend(
        {"purchase_decision_event_id": str(r.id), "error": "decision_subject_ownership_invalid"}
        for r in orphan_events
    )

    return {
        "candidates": [_row_dict(r, [c.name for c in ShoppingCandidate.__table__.columns]) for r in candidates],
        "evaluations": [_row_dict(r, [c.name for c in PurchaseEvaluation.__table__.columns]) for r in evaluations],
        "subjects": subjects,
        # Owned rows that no subject of this account explains: decisions made
        # before anybody was named, and rows pointing at somebody else's
        # household with that id stripped out. Exported in full, attributed to
        # nobody.
        "unattributed_decisions": (
            [_row_dict(r, decision_fields) for r in ambiguous_decisions]
            + [_unattributed_row(r, decision_fields) for r in orphan_decisions]
        ),
        "unattributed_decision_events": (
            [_row_dict(r, event_fields) for r in ambiguous_events]
            + [_unattributed_row(r, event_fields) for r in orphan_events]
        ),
        "invariant_errors": invariant_errors,
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
    notification_preferences = await _fetch(session, select(NotificationPreference).where(NotificationPreference.account_id == account_id))
    notification_deliveries = await _fetch(session, select(NotificationDelivery).where(NotificationDelivery.account_id == account_id))
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
        "notification_preferences": [_row_dict(r, [c.name for c in NotificationPreference.__table__.columns]) for r in notification_preferences],
        # Delivery history is useful to the customer; provider token/error
        # internals are intentionally reduced to truthful status metadata.
        "notification_deliveries": [_row_dict(r, ["id", "plan_date", "notification_key", "channel", "status", "suppressed_reason", "scheduled_for", "attempted_at", "sent_at", "created_at"]) for r in notification_deliveries],
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
    manager_decision_events = await _fetch(
        session,
        select(ShelfManagerDecisionEvent).where(ShelfManagerDecisionEvent.account_id == account_id),
    )
    care_preferences = await _fetch(
        session,
        select(CareProductPreference).where(CareProductPreference.account_id == account_id),
    )
    _, members = await _household_members(session, account_id)
    member_ids = {member.id for member in members}
    circle_created_at = await session.scalar(
        select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
    )
    manager_by_subject, manager_legacy, manager_orphaned = _group_decision_rows(
        list(manager_decision_events), members,
    )
    manager_safe, manager_ambiguous = _safe_legacy_split(
        manager_legacy, circle_created_at=circle_created_at, timestamp="created_at",
    )
    preferences_by_subject: dict[str, list[dict[str, Any]]] = {str(member_id): [] for member_id in member_ids}
    unattributed_preferences: list[dict[str, Any]] = []
    for preference in care_preferences:
        row = _row_dict(preference, [c.name for c in CareProductPreference.__table__.columns])
        if preference.household_subject_id in member_ids:
            preferences_by_subject[str(preference.household_subject_id)].append(row)
        else:
            row["household_subject_id"] = None
            row["invariant"] = "preference_subject_ownership_invalid"
            unattributed_preferences.append(row)
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
        "shelf_manager_decision_events": [
            _row_dict(r, [c.name for c in ShelfManagerDecisionEvent.__table__.columns])
            for r in manager_decision_events
        ],
        "manager_history": {
            "by_subject": {
                str(member_id): [
                    _row_dict(row, [c.name for c in ShelfManagerDecisionEvent.__table__.columns])
                    for row in rows
                ] for member_id, rows in manager_by_subject.items()
            },
            "account_holder_legacy": [
                _row_dict(row, [c.name for c in ShelfManagerDecisionEvent.__table__.columns])
                for row in manager_safe
            ],
            "unattributed": [
                _row_dict(row, [c.name for c in ShelfManagerDecisionEvent.__table__.columns])
                for row in manager_ambiguous
            ] + [
                _unattributed_row(row, [c.name for c in ShelfManagerDecisionEvent.__table__.columns])
                for row in manager_orphaned
            ],
            "coverage": {
                "unattributed_legacy_events_present": bool(manager_ambiguous or manager_orphaned),
                "complete_for_subject": not bool(manager_ambiguous or manager_orphaned),
            },
        },
        "care_product_preferences": [
            _row_dict(r, [c.name for c in CareProductPreference.__table__.columns])
            for r in care_preferences
        ],
        "care_product_preferences_by_subject": preferences_by_subject,
        "unattributed_care_product_preferences": unattributed_preferences,
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
        # What an AI provider returned about this account, as recorded. Some of
        # it is wording the app refused to show — the language boundary runs on
        # the way out to a screen, and the ledger keeps what came back so the
        # record of a run is a true one. Both facts belong in an export: the
        # text is this person's data and withholding it would make the export
        # incomplete, and a sentence the product declined to say must not
        # arrive here looking like a sentence the product said.
        "ai_output_note": AI_OUTPUT_NOTE,
        "ai_runs": [_row_dict(r, [c.name for c in AIRun.__table__.columns]) for r in runs],
        "ai_run_outputs": [_ai_output_dict(r) for r in outputs],
        "audit_events": [_row_dict(r, [c.name for c in AuditEvent.__table__.columns]) for r in audit],
        "beta_usage_events": [_row_dict(r, [c.name for c in BetaUsageEvent.__table__.columns]) for r in beta],
    }


#: Shown with the AI outputs in an export. States what the rows are rather than
#: characterising them, and says plainly that the app is not their author.
#:
#: Worded around the banned-term sweep, the same way ``ROUTINE_DISCLAIMER`` is:
#: the obvious phrasing names what it rules out, and naming it is what the sweep
#: catches. Weakening the sweep so a disclaimer can pass would weaken it for
#: everything else, so the wording moves instead. The promise is unchanged.
AI_OUTPUT_NOTE = (
    "These are the replies an AI provider returned about your account, kept as they "
    "arrived. GlamGenius checks wording before showing it, so some of this was never "
    "shown to you. Each row records whether it passes that check today. None of it is "
    "medical advice, and none of it is what the app decided."
)

#: The key added to each exported AI output row.
AI_OUTPUT_BOUNDARY_KEY = "passes_language_boundary"


def _payload_strings(value: Any) -> Iterator[str]:
    """Every string anywhere in a recorded payload."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _payload_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _payload_strings(item)


def _ai_output_dict(row: AIRunOutput) -> dict[str, Any]:
    """One recorded AI output, labelled with whether the app would show it."""
    from app.domains.routines.safety import narrative_is_safe

    data = _row_dict(row, [c.name for c in AIRunOutput.__table__.columns])
    data[AI_OUTPUT_BOUNDARY_KEY] = all(
        narrative_is_safe(text) for text in _payload_strings(row.payload)
    )
    return data


DomainHandler = Callable[[AsyncSession, uuid.UUID], Any]

DOMAIN_HANDLERS: dict[str, DomainHandler] = {
    "identity": _identity,
    "profile": _profile,
    "consent": _consent,
    "household": _household,
    "inventory": _inventory,
    "media": _media,
    "scans": _scans,
    "product_scans": _product_scans,
    "community": _community,
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
        except Exception as exc:  # noqa: BLE001 — one domain must not sink the export
            # The domain and the exception type, never the exception text.
            # A database error's message carries the driver's rendering of the
            # failing value, and every value in this file is one person's own
            # data. ``hide_parameters=True`` on the engine removes SQLAlchemy's
            # parameter list; the driver's own wording is why this is not
            # ``logger.exception``.
            logger.error(
                "privacy_export_domain_failed domain=%s type=%s",
                name,
                type(exc).__name__,
            )
            # Every handler shares this session. A failed statement leaves its
            # transaction unusable, and PostgreSQL then refuses everything that
            # follows — so without this the first domain to fail took all the
            # domains after it down with it, each carrying the same marker and
            # none of them actually tried. Nothing here writes, so there is
            # nothing to lose by rolling back.
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 — a session we cannot reset is already lost
                logger.error("privacy_export_rollback_failed domain=%s", name)
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
            "secret_excluded": sorted(
                name for name, kind in REGISTRY.items()
                if kind == Classification.SECRET_EXCLUDED
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
