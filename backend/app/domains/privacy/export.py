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

Complete, or not at all
-----------------------
A successful export is the whole of this account's data, or it is not
returned. There is no row ceiling on any collection: a per-collection cap used
to cut every collection at 20 000 rows and still answer 200, which is a
successful-looking export that silently withheld the rest. The response is the
account's data, so its size is the size of that data; a hidden limit did not
make it smaller, only untrue.

Every ``INCLUDED`` table has one entry in
:data:`app.domains.privacy.coverage.EXPORT_COVERAGE`, and :func:`build_export`
holds the result to it: the entries must match the registry, every covered
table must have been read by its declared domain, and every declared path
must be in the output. A domain that fails, or a covered table that was not
proven, raises :class:`PrivacyExportIncomplete` instead of returning a partial
file — the API answers 503 and records no successful export.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from functools import cache
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
from app.domains.privacy.coverage import (
    EXPORT_COVERAGE,
    Scope,
    contract_tables,
    coverage_drift,
    export_locations,
)
from app.domains.product.models import LabelErrorReport, ProductWatch, ScanDecisionEvent, ScanEvent
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
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError

logger = logging.getLogger(__name__)


#: The one message a caller sees when the export could not be completed. It
#: states what happened and what to do; it never names a domain, a table or a
#: value, because every value here is one person's data.
PRIVACY_EXPORT_INCOMPLETE_MESSAGE = (
    "Your data export could not be completed, so nothing was sent. Please try again later."
)


class PrivacyExportIncomplete(AppError):
    """The export could not be proven complete, so none of it is returned.

    ``failed_domains`` and ``unproven`` are for the log line only — domain and
    table names, never an exception's text or a row's value. The response body
    is the fixed :data:`PRIVACY_EXPORT_INCOMPLETE_MESSAGE`.
    """

    status_code = 503
    code = ErrorCode.PRIVACY_EXPORT_INCOMPLETE
    retryable = True

    def __init__(
        self, *, failed_domains: tuple[str, ...] = (), unproven: tuple[str, ...] = (),
    ) -> None:
        super().__init__(PRIVACY_EXPORT_INCOMPLETE_MESSAGE)
        self.failed_domains = tuple(failed_domains)
        self.unproven = tuple(unproven)


#: ``(domain, table)`` for every full-row read made while an export is built.
#: :func:`build_export` compares it with :data:`EXPORT_COVERAGE`: a covered
#: table its domain never read is a table the file silently left out.
_READS: ContextVar[set[tuple[str | None, str]] | None] = ContextVar("privacy_export_reads", default=None)
_DOMAIN: ContextVar[str | None] = ContextVar("privacy_export_domain", default=None)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _row_dict(row: Any, fields: list[str]) -> dict[str, Any]:
    """Turn a subset of a row's columns into a genuinely JSON-safe dict.

    This claimed to be JSON-safe and was not. ``datetime`` was converted and a
    plain ``date`` was not — ``done_on``, ``experienced_on``, ``purchase_date``
    and a dozen others went out as Python objects — and ``Decimal`` was left
    alone too. The route survived because the web framework re-encodes whatever
    it is handed, so the only thing that ever broke was anything treating the
    export as what it says it is: a serialisable document. Checking that no
    other household's identifier appears anywhere in the file means serialising
    the file, so it has to serialise.

    ``date`` is tested after ``datetime`` because ``datetime`` is a subclass of
    it; reversing the two would turn every timestamp into a bare day.

    ``Decimal`` becomes a float rather than a string, because that is what the
    API has always emitted through its own encoder and this is not the place to
    change a money field's shape for the clients reading it.
    """
    out: dict[str, Any] = {}
    for name in fields:
        value = getattr(row, name, None)
        if isinstance(value, uuid.UUID):
            out[name] = str(value)
        elif isinstance(value, datetime | date | time):
            out[name] = value.isoformat()
        elif isinstance(value, Decimal):
            out[name] = float(value)
        else:
            out[name] = value
    return out


async def _fetch(session: AsyncSession, stmt) -> list[Any]:
    """Every row the statement selects. There is no row limit.

    The statement is already scoped to the account in SQL; this only runs it
    and notes which table was read, so :func:`build_export` can prove that
    every covered table was.
    """
    rows = list((await session.execute(stmt)).scalars().all())
    reads = _READS.get()
    if reads is not None:
        descriptions = stmt.column_descriptions
        entity = descriptions[0].get("entity") if descriptions else None
        table = getattr(entity, "__tablename__", None)
        if table is not None:
            reads.add((_DOMAIN.get(), table))
    return rows


def _chronological(model: Any) -> tuple[Any, Any]:
    """Oldest first, with the primary key as the tie-break.

    ``created_at`` is the database's ``now()``, which is the same for every row
    written in one transaction, so it cannot order those rows on its own.
    Ordering is for reading history; it never limits anything.
    """
    return model.created_at, model.id


def _account_rows(model: Any, account_id: uuid.UUID):
    """Every row of ``model`` with this ``account_id``, oldest first."""
    return select(model).where(model.account_id == account_id).order_by(*_chronological(model))


@cache
def _model_for(table: str) -> Any:
    """The ORM class mapped to ``table``."""
    from app.shared.database.registry import Base

    for mapper in Base.registry.mappers:
        if mapper.local_table.name == table:
            return mapper.class_
    raise LookupError(f"no ORM model is mapped to {table}")


def _owned_ids(table: str, account_id: uuid.UUID):
    """A subquery of the ids of ``table`` rows this account owns.

    Built from the table's coverage entry, so ownership is decided in SQL the
    same way everywhere: by the row's own ``account_id``, or by an owned parent.
    Never by an id supplied from outside, and never by filtering in Python.
    """
    entry = EXPORT_COVERAGE[table]
    model = _model_for(table)
    if entry.scope == Scope.ACCOUNT_ROW:
        return select(model.id).where(model.id == account_id)
    if entry.scope == Scope.ACCOUNT:
        return select(model.id).where(model.account_id == account_id)
    fk, parent = entry.parent
    return select(model.id).where(getattr(model, fk).in_(_owned_ids(parent, account_id)))


def _owned_rows(table: str, account_id: uuid.UUID):
    """Every row of ``table`` this account owns, in the entry's order."""
    entry = EXPORT_COVERAGE[table]
    model = _model_for(table)
    if entry.scope == Scope.ACCOUNT:
        stmt = select(model).where(model.account_id == account_id)
    elif entry.scope == Scope.PARENT:
        fk, parent = entry.parent
        stmt = select(model).where(getattr(model, fk).in_(_owned_ids(parent, account_id)))
    else:  # pragma: no cover - guarded by the coverage contract test
        raise ValueError(f"{table} cannot be exported by the contract")
    return stmt.order_by(*(getattr(model, column) for column in entry.order_by))


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _contract_collections(
    session: AsyncSession, account_id: uuid.UUID, domain: str,
) -> dict[str, list[dict[str, Any]]]:
    """Every table this domain exports straight from :data:`EXPORT_COVERAGE`.

    Each row is selected by the SQL scope its entry declares. A reference from
    it to another account-owned row — an item, a photo, a plan — is kept only
    when that row is this account's too. A reference that is not is the
    signature of a broken invariant, and the id it carries names something in
    somebody else's account, so it is replaced with ``None`` and the row is
    marked, rather than echoed into this person's file. The row itself is
    this account's and stays in.
    """
    owned: dict[str, set[uuid.UUID]] = {}

    async def owned_ids(table: str) -> set[uuid.UUID]:
        if table not in owned:
            owned[table] = set((await session.execute(_owned_ids(table, account_id))).scalars().all())
        return owned[table]

    collections: dict[str, list[dict[str, Any]]] = {}
    for table in contract_tables(domain):
        entry = EXPORT_COVERAGE[table]
        model = _model_for(table)
        fields = [c.name for c in model.__table__.columns if c.name not in entry.withheld]
        payloads: list[dict[str, Any]] = []
        for row in await _fetch(session, _owned_rows(table, account_id)):
            payload = _row_dict(row, fields)
            invalid: list[str] = []
            for column, target in entry.references:
                value = getattr(row, column)
                if value is not None and value not in await owned_ids(target):
                    payload[column] = None
                    invalid.append(column)
            for column, target in entry.reference_lists:
                values = getattr(row, column)
                if isinstance(values, list):
                    mine = await owned_ids(target)
                    kept = [value for value in values if _as_uuid(value) in mine]
                    if len(kept) != len(values):
                        payload[column] = kept
                        invalid.append(column)
            if invalid:
                payload["invariant"] = "reference_ownership_invalid"
                payload["invalid_references"] = invalid
            payloads.append(payload)
        collections[entry.key] = payloads
    return collections


# ---------------------------------------------------------------------------
# Domain handlers
# ---------------------------------------------------------------------------

async def _identity(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    accounts = await _fetch(session, select(Account).where(Account.id == account_id))
    redemptions = await _fetch(
        session,
        select(InviteRedemption)
        .where(InviteRedemption.account_id == account_id)
        .order_by(*_chronological(InviteRedemption)),
    )
    if not accounts:
        # No account row, so there is no identity to state. Returned empty
        # rather than invented; ``build_export`` then refuses to call the file
        # complete, because the account section it promises is not there.
        return {}
    account = accounts[0]
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


_ProfileChildren = dict[str, dict[uuid.UUID, list[Any]]]


async def _profile_children(session: AsyncSession, account_id: uuid.UUID) -> _ProfileChildren:
    """Every child row of every profile this account owns, by label then profile.

    One statement per child table, scoped in SQL to this account's profiles,
    rather than one per profile: a child table is read whether or not any
    profile exists, so the export can prove it was read, and nothing depends
    on how many profiles there are.
    """
    owned_profiles = select(AppearanceProfile.id).where(AppearanceProfile.account_id == account_id)
    children: _ProfileChildren = {}
    for label, model in _PROFILE_CHILD_TABLES:
        rows = await _fetch(
            session,
            select(model).where(model.profile_id.in_(owned_profiles)).order_by(*_chronological(model)),
        )
        by_profile: dict[uuid.UUID, list[Any]] = {}
        for row in rows:
            by_profile.setdefault(row.profile_id, []).append(row)
        children[label] = by_profile
    return children


def _profile_payload(profile: AppearanceProfile, children: _ProfileChildren) -> dict[str, Any]:
    """One profile and everything attached to it, by ``profile_id``."""
    payload: dict[str, Any] = {
        "profile": _row_dict(profile, [c.name for c in AppearanceProfile.__table__.columns]),
    }
    for label, model in _PROFILE_CHILD_TABLES:
        rows = children[label].get(profile.id, [])
        payload[label] = [_row_dict(r, [c.name for c in model.__table__.columns]) for r in rows]
    return payload


def _empty_profile_payload() -> dict[str, Any]:
    return {"profile": None, **{label: [] for label, _ in _PROFILE_CHILD_TABLES}}


@dataclass(frozen=True, slots=True)
class _HouseholdIdentity:
    """Everything the export needs to know about who this account contains.

    Read once per section and passed around rather than re-derived, because the
    four questions it answers — does a household exist, when did it start, who
    is in it, and which of them is the account holder — have to be answered the
    same way in every section of one file. They were previously re-derived per
    section, and two sections got the last one wrong: the Care and Manager
    sections never asked it at all, so a household whose account holder could
    not be identified still had its pre-household preferences and Manager
    answers handed to that unidentifiable person.
    """

    circle_id: uuid.UUID | None
    circle_created_at: datetime | None
    members: list[FamilyProfile]
    self_row: FamilyProfile | None
    self_row_count: int

    @property
    def exists(self) -> bool:
        """Did this account ever open a household?

        A fact about the circle, never inferred from the member count: a circle
        with no rows at all is the most alarming state there is, and inferring
        would report it as the most ordinary one.
        """
        return self.circle_id is not None

    @property
    def account_holder_identified(self) -> bool:
        """Is there exactly one human a subject-less row could belong to?

        With no household, yes — the account is one person. With a household
        and exactly one active ``self`` row, yes. With none or several, no, and
        nothing subject-less may be attributed to anybody.
        """
        return not self.exists or self.self_row is not None

    @property
    def member_ids(self) -> set[uuid.UUID]:
        return {member.id for member in self.members}

    def invariant_errors(self) -> list[dict[str, str]]:
        """The household-level problems, phrased the same way in every section."""
        if self.account_holder_identified:
            return []
        return [{
            "error": (
                "household_self_profile_missing" if self.self_row_count == 0
                else "household_has_multiple_self_profiles"
            ),
        }]


async def _household_identity(
    session: AsyncSession, account_id: uuid.UUID,
) -> _HouseholdIdentity:
    """This account's circle, its members in household order, and its holder."""
    circle = (await session.execute(
        select(FamilyCircle.id, FamilyCircle.created_at)
        .where(FamilyCircle.account_id == account_id)
    )).first()
    if circle is None:
        return _HouseholdIdentity(None, None, [], None, 0)
    circle_id, circle_created_at = circle
    members = list(await _fetch(
        session,
        select(FamilyProfile)
        .where(FamilyProfile.circle_id == circle_id)
        .order_by(FamilyProfile.position),
    ))
    # Exactly one, or none named at all. Taking the first of two would label one
    # human as the account holder by insertion order.
    self_rows = [row for row in members if row.relation == RELATION_SELF and row.active]
    return _HouseholdIdentity(
        circle_id=circle_id,
        circle_created_at=circle_created_at,
        members=members,
        self_row=self_rows[0] if len(self_rows) == 1 else None,
        self_row_count=len(self_rows),
    )


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
    rows: list[Any], *, household: _HouseholdIdentity, timestamp: str,
) -> tuple[list[Any], list[Any]]:
    """Legacy rows the account holder can honestly claim, and the rest.

    Strictly ``<``: a row written in the same instant the household was created
    is ambiguous, and ambiguity is never resolved in favour of the account
    holder. One row, one account — getting it wrong shows one person another
    person's purchase history.

    A household whose account holder cannot be identified has nobody to claim
    them, so every legacy row is ambiguous however old it is. That rule used to
    be written out again after each call, and the two Step 11D sections did not
    write it out at all. It lives here now, where a caller cannot forget it.
    """
    if not household.exists:
        return list(rows), []
    if not household.account_holder_identified:
        return [], list(rows)
    created_at = household.circle_created_at
    safe, ambiguous = [], []
    for row in rows:
        when = getattr(row, timestamp, None)
        is_safe = (
            when is not None and created_at is not None and when < created_at
        )
        (safe if is_safe else ambiguous).append(row)
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
        select(AppearanceProfile)
        .where(AppearanceProfile.account_id == account_id)
        .order_by(*_chronological(AppearanceProfile)),
    )
    children = await _profile_children(session, account_id)
    # Whether a household exists is a fact about the circle, not about how many
    # members happen to be in it; and the account holder is the one active
    # ``self`` row or nobody, never the first of two. Both rules live in
    # ``_household_identity`` so that every section of one file answers them
    # identically — the profile underneath a ``self`` label is the one legacy
    # rows are attributed to, so a section that decided it differently would
    # attach the signed-in person's history to somebody else in their household,
    # inside the file they asked for precisely to see who has what.
    household = await _household_identity(session, account_id)
    circle_id = household.circle_id
    members = household.members
    self_row = household.self_row
    invariant_errors: list[dict[str, str]] = household.invariant_errors()

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
            _profile_payload(profile, children) if profile is not None
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
            **_profile_payload(legacy, children),
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
        payload = _profile_payload(profile, children)
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
        select(InventoryItem)
        .where(InventoryItem.account_id == account_id)
        .order_by(*_chronological(InventoryItem)),
    )
    # Child rows are reached through the account's own items in SQL. A list of
    # ids bound as parameters would stop working past the driver's parameter
    # limit, which is exactly the size of account the export must not fail.
    owned_items = _owned_ids("inventory_items", account_id)
    attrs = await _fetch(
        session,
        select(InventoryAttribute)
        .where(InventoryAttribute.item_id.in_(owned_items))
        .order_by(*_chronological(InventoryAttribute)),
    )
    events = await _fetch(
        session,
        select(InventoryEvent)
        .where(InventoryEvent.account_id == account_id)
        .order_by(*_chronological(InventoryEvent)),
    )
    supplement_details = await _fetch(
        session,
        select(SupplementDetail)
        .where(SupplementDetail.item_id.in_(owned_items))
        .order_by(*_chronological(SupplementDetail)),
    )
    # Photo captures and what each one offered. A rejected candidate stays in
    # the export: it is a record of a guess made about this person.
    imports = await _fetch(
        session,
        select(InventoryImportJob)
        .where(InventoryImportJob.account_id == account_id)
        .order_by(*_chronological(InventoryImportJob)),
    )
    candidates = await _fetch(
        session,
        select(InventoryImportCandidate)
        .where(InventoryImportCandidate.account_id == account_id)
        .order_by(*_chronological(InventoryImportCandidate)),
    )
    product_links = await _fetch(
        session,
        select(InventoryProductLink)
        .where(InventoryProductLink.account_id == account_id)
        .order_by(*_chronological(InventoryProductLink)),
    )
    # Category details, photos, value, condition, expiry and usage history,
    # relationships, duplicate candidates and laundry state: exported from the
    # coverage contract, each reached through the account's own items or its
    # own account_id.
    history = await _contract_collections(session, account_id, "inventory")
    household = await _household_identity(session, account_id)
    member_ids = household.member_ids
    event_fields = [c.name for c in InventoryEvent.__table__.columns]
    invariant_errors = household.invariant_errors()

    # Every Care preference event is now attributed to the person who made it,
    # rather than listed flat with a marker. A flat list of one household's
    # pauses and preferences is the shape that made this slice necessary: the
    # customer could see that four products were paused and not which of the
    # three people in the file paused them.
    preference_event_types = {
        "care_routine_paused", "care_routine_resumed", "care_routine_preferred",
        "care_routine_preference_cleared",
    }
    event_payloads: list[dict[str, Any]] = []
    preference_events: list[InventoryEvent] = []
    for event in events:
        row = _row_dict(event, event_fields)
        if event.household_subject_id is not None and event.household_subject_id not in member_ids:
            row["household_subject_id"] = None
            row["invariant"] = "inventory_event_subject_ownership_invalid"
        event_payloads.append(row)
        if event.event_type in preference_event_types:
            preference_events.append(event)

    events_by_member, legacy_events, orphan_events = _group_decision_rows(
        preference_events, household.members,
    )
    safe_events, ambiguous_events = _safe_legacy_split(
        legacy_events, household=household, timestamp="created_at",
    )
    invariant_errors.extend(
        {"inventory_event_id": str(row.id),
         "error": "inventory_event_subject_ownership_invalid"}
        for row in orphan_events
    )

    care_keys = {CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY, CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY}
    physical_attrs = [row for row in attrs if row.key not in care_keys]
    legacy_care_attrs = [row for row in attrs if row.key in care_keys]
    safe_legacy_attrs, ambiguous_legacy_attrs = _safe_legacy_split(
        legacy_care_attrs, household=household, timestamp="updated_at",
    )
    attribute_fields = [c.name for c in InventoryAttribute.__table__.columns]
    return {
        "items": [_row_dict(i, [c.name for c in InventoryItem.__table__.columns]) for i in items],
        "attributes": [_row_dict(a, [c.name for c in InventoryAttribute.__table__.columns]) for a in physical_attrs],
        "care_preference_history": {
            "account_holder_legacy": [
                _row_dict(a, attribute_fields) for a in safe_legacy_attrs
            ],
            "unattributed": [
                _row_dict(a, attribute_fields) for a in ambiguous_legacy_attrs
            ],
            "coverage": {
                "unattributed_legacy_preferences_present": bool(ambiguous_legacy_attrs),
                "complete_for_subject": not bool(ambiguous_legacy_attrs),
            },
        },
        "events": event_payloads,
        "care_preference_events": {
            "by_subject": {
                str(member.id): [
                    _row_dict(row, event_fields)
                    for row in list(events_by_member.get(member.id, []))
                    + (safe_events if (
                        household.self_row is not None and member.id == household.self_row.id
                    ) else [])
                ] for member in household.members
            },
            # Only where no household was ever opened. With one, the account
            # holder's share is inside ``by_subject`` under their own id, and a
            # second copy out here would be the same events counted twice.
            "account_holder_legacy": [
                _row_dict(row, event_fields) for row in safe_events
            ] if not household.exists else [],
            "unattributed": (
                [_row_dict(row, event_fields) for row in ambiguous_events]
                + [_unattributed_row(row, event_fields) for row in orphan_events]
            ),
            "coverage": {
                "unattributed_legacy_events_present": bool(ambiguous_events or orphan_events),
                "complete_for_subject": not bool(ambiguous_events or orphan_events),
            },
        },
        "invariant_errors": invariant_errors,
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
        **history,
    }


async def _media(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    assets = await _fetch(
        session,
        select(MediaAsset)
        .where(MediaAsset.account_id == account_id)
        .order_by(*_chronological(MediaAsset)),
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

    A report's photo is stated, never located: ``photo_attached`` says whether
    one was sent, and the internal storage key it lives under is not exported
    (:func:`_label_error_report_row`). This JSON export carries no photo bytes.
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
    memory_rows = list(await _fetch(
        session,
        select(ScanDecisionEvent)
        .where(ScanDecisionEvent.account_id == account_id)
        .order_by(ScanDecisionEvent.created_at.desc()),
    ))
    memory_fields = [c.name for c in ScanDecisionEvent.__table__.columns]
    watches = await _fetch(
        session,
        select(ProductWatch)
        .where(ProductWatch.account_id == account_id)
        .order_by(ProductWatch.created_at.desc()),
    )

    # The scan itself stays account-level: it records that this account looked
    # at a barcode, which is true regardless of who the answer was for. What
    # somebody *decided* is about a person, so only that is grouped.
    household = await _household_identity(session, account_id)
    members = household.members
    self_row = household.self_row

    by_member, legacy, orphaned = _group_decision_rows(
        memory_rows, members,
    )
    safe, ambiguous = _safe_legacy_split(
        legacy, household=household, timestamp="created_at",
    )

    invariant_errors: list[dict[str, str]] = household.invariant_errors()
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
    if not household.exists:
        subjects.append({
            "household_subject_id": None,
            "relation": RELATION_SELF,
            "age_band": None,
            "active": True,
            "is_account_holder": True,
            "scan_decision_events": [_row_dict(r, memory_fields) for r in safe],
        })

    handoffs = await _contract_collections(session, account_id, "product_scans")

    return {
        "scans": [_row_dict(r, fields) for r in rows],
        "label_error_reports": [_label_error_report_row(r) for r in reports],
        "product_watches": [_product_watch_row(r) for r in watches],
        **handoffs,
        "subjects": subjects,
        "unattributed_scan_decision_events": (
            [_row_dict(r, memory_fields) for r in ambiguous]
            + [_unattributed_row(r, memory_fields) for r in orphaned]
        ),
        "invariant_errors": invariant_errors,
    }


def _supplement_label_component_row(row: SupplementLabelComponent) -> dict[str, Any]:
    """What a person recorded from a supplement label, and where it came from.

    Step 13. The printed facts and their provenance are the person's own data
    and leave with them. Left out on purpose: ``account_id`` (the export is
    already this account's), ``source_ai_run_id``, ``model_version`` and
    ``prompt_version`` (internal pipeline bookkeeping, following the same rule
    as ``product_watches`` below), and ``client_mutation_id`` (a retry key the
    app generated, not something the person wrote).
    """
    return _row_dict(row, [
        "id", "item_id", "raw_name", "normalized_name", "canonical_component_key",
        "amount", "unit", "serving_text", "source", "verification_state", "confidence",
        "schema_version", "created_at", "updated_at",
    ])


def _label_error_report_row(row: LabelErrorReport) -> dict[str, Any]:
    """A report somebody filed, with its photo stated rather than located.

    ``photo_key`` is the object-storage path the photo lives under — an
    account prefix and an internal object name. It tells the person nothing
    ``photo_attached`` does not, and it is exactly the kind of internal path
    this export promises to leave out.
    """
    withheld = set(EXPORT_COVERAGE["label_error_reports"].withheld)
    payload = _row_dict(row, [c.name for c in LabelErrorReport.__table__.columns if c.name not in withheld])
    payload["photo_attached"] = row.photo_key is not None
    return payload


def _product_watch_row(row: ProductWatch) -> dict[str, Any]:
    """What a person can read about a watch they set: which product, since when.

    The anchor ids and the notice cursor are left out on purpose. The ids point
    at internal rows the person cannot open, and the cursor is bookkeeping about
    which official revisions were already known — neither says anything to the
    person that the fields below do not say better.
    """
    return {
        "barcode": row.barcode,
        "watching": bool(row.active),
        "label_version": row.anchor_label_version,
        "started_at": _iso(row.started_at),
        "stopped_at": _iso(row.stopped_at),
        "last_notified_at": _iso(row.last_notified_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
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
        select(QuizSubmission).where(QuizSubmission.account_id == account_id).order_by(*_chronological(QuizSubmission)),
    )
    occasions = await _fetch(
        session,
        select(Occasion).where(Occasion.account_id == account_id).order_by(*_chronological(Occasion)),
    )
    style_requests = await _fetch(
        session,
        select(StyleRequest).where(StyleRequest.account_id == account_id).order_by(*_chronological(StyleRequest)),
    )
    runs = await _fetch(
        session,
        select(RecommendationRun).where(RecommendationRun.account_id == account_id).order_by(*_chronological(RecommendationRun)),
    )
    looks = await _fetch(
        session,
        select(Look).where(Look.account_id == account_id).order_by(*_chronological(Look)),
    )
    adjustments = await _fetch(
        session,
        select(LookAdjustment).where(LookAdjustment.account_id == account_id).order_by(*_chronological(LookAdjustment)),
    )
    feedback = await _fetch(
        session,
        select(LookFeedback).where(LookFeedback.account_id == account_id).order_by(*_chronological(LookFeedback)),
    )
    return {
        "quiz_submissions": [_row_dict(r, [c.name for c in QuizSubmission.__table__.columns]) for r in submissions],
        "occasions": [_row_dict(r, [c.name for c in Occasion.__table__.columns]) for r in occasions],
        "style_requests": [_row_dict(r, [c.name for c in StyleRequest.__table__.columns]) for r in style_requests],
        "recommendation_runs": [_row_dict(r, [c.name for c in RecommendationRun.__table__.columns]) for r in runs],
        "looks": [_row_dict(r, [c.name for c in Look.__table__.columns]) for r in looks],
        "look_adjustments": [_row_dict(r, [c.name for c in LookAdjustment.__table__.columns]) for r in adjustments],
        "look_feedback": [_row_dict(r, [c.name for c in LookFeedback.__table__.columns]) for r in feedback],
        # Run inputs, entitlements, look items, the outfit schedule and item
        # compatibility, straight from the coverage contract.
        **await _contract_collections(session, account_id, "quiz_and_styling"),
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
    candidates = await _fetch(session, _account_rows(ShoppingCandidate, account_id))
    evaluations = await _fetch(session, _account_rows(PurchaseEvaluation, account_id))
    decisions = list(await _fetch(session, _account_rows(PurchaseDecision, account_id)))
    decision_events = list(await _fetch(session, _account_rows(PurchaseDecisionEvent, account_id)))

    # The household posture is read once, here, and the "nobody to attribute
    # them to, so nobody gets them" rule lives inside ``_safe_legacy_split``.
    household = await _household_identity(session, account_id)
    members = household.members
    self_row = household.self_row
    invariant_errors: list[dict[str, str]] = household.invariant_errors()

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
        legacy_decisions, household=household, timestamp="updated_at",
    )
    safe_events, ambiguous_events = _safe_legacy_split(
        legacy_events, household=household, timestamp="created_at",
    )

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

    if not household.exists:
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
        # Each evaluation's factors, through the account's own evaluations.
        **await _contract_collections(session, account_id, "shopping"),
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
    daily = await _fetch(session, _account_rows(DailyPlan, account_id))
    weekly = await _fetch(session, _account_rows(WeeklyPlan, account_id))
    calendar = await _fetch(session, _account_rows(CalendarEvent, account_id))
    integrations = await _fetch(session, _account_rows(ExternalIntegration, account_id))
    weather = await _fetch(session, _account_rows(WeatherSnapshot, account_id))
    event_ready_plans = await _fetch(session, _account_rows(EventReadyPlan, account_id))
    event_ready_actions = await _fetch(
        session,
        select(EventReadyAction)
        .where(EventReadyAction.event_ready_plan_id.in_(_owned_ids("event_ready_plans", account_id)))
        .order_by(*_chronological(EventReadyAction)),
    )
    notification_preferences = await _fetch(session, _account_rows(NotificationPreference, account_id))
    notification_deliveries = await _fetch(session, _account_rows(NotificationDelivery, account_id))
    # Plan actions and inputs, weekly days, air quality and recalculation
    # history, straight from the coverage contract.
    plan_history = await _contract_collections(session, account_id, "planning")
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
        **plan_history,
    }


async def _routines(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    routines = await _fetch(session, _account_rows(Routine, account_id))
    steps = await _fetch(
        session,
        select(RoutineStep)
        .where(RoutineStep.routine_id.in_(_owned_ids("routines", account_id)))
        .order_by(*_chronological(RoutineStep)),
    )
    adherence = await _fetch(session, _account_rows(RoutineAdherence, account_id))
    recommendation_runs = await _fetch(session, _account_rows(RoutineRecommendationRun, account_id))
    product_ingredients = await _fetch(
        session,
        _account_rows(ProductIngredient, account_id),
    )
    observations = await _fetch(
        session,
        _account_rows(UserReportedObservation, account_id),
    )
    product_expiry_events = await _fetch(
        session,
        _account_rows(ProductExpiryEvent, account_id),
    )
    supplement_safety_flags = await _fetch(
        session,
        _account_rows(SupplementSafetyFlag, account_id),
    )
    label_components = await _fetch(
        session,
        _account_rows(SupplementLabelComponent, account_id),
    )
    nutrition_preferences = await _fetch(
        session,
        _account_rows(NutritionPreference, account_id),
    )
    hydration_preferences = await _fetch(
        session,
        _account_rows(HydrationPreference, account_id),
    )
    experience_feedback = await _fetch(
        session,
        _account_rows(CareExperienceFeedback, account_id),
    )
    manager_decision_events = await _fetch(
        session,
        _account_rows(ShelfManagerDecisionEvent, account_id),
    )
    care_preferences = await _fetch(
        session,
        _account_rows(CareProductPreference, account_id),
    )
    maintenance_preferences = await _fetch(
        session,
        _account_rows(MaintenancePreference, account_id),
    )
    maintenance_events = await _fetch(
        session,
        _account_rows(MaintenanceEvent, account_id),
    )

    household = await _household_identity(session, account_id)
    members = household.members
    member_ids = household.member_ids
    invariant_errors: list[dict[str, str]] = household.invariant_errors()

    routine_fields = [c.name for c in Routine.__table__.columns]
    step_fields = [c.name for c in RoutineStep.__table__.columns]
    adherence_fields = [c.name for c in RoutineAdherence.__table__.columns]
    run_fields = [c.name for c in RoutineRecommendationRun.__table__.columns]

    routine_by_id = {row.id: row for row in routines}
    steps_by_routine: dict[uuid.UUID, list[RoutineStep]] = {}
    for step in steps:
        steps_by_routine.setdefault(step.routine_id, []).append(step)

    adherence_by_routine: dict[uuid.UUID, list[RoutineAdherence]] = {}
    malformed_adherence: list[dict[str, Any]] = []
    for row in adherence:
        parent = routine_by_id.get(row.routine_id)
        if parent is None:
            payload = _row_dict(row, adherence_fields)
            payload["routine_id"] = None
            payload["step_id"] = None
            payload["invariant"] = "adherence_routine_ownership_invalid"
            malformed_adherence.append(payload)
            invariant_errors.append({
                "routine_adherence_id": str(row.id),
                "error": "adherence_routine_ownership_invalid",
            })
            continue
        parent_steps = {step.id for step in steps_by_routine.get(parent.id, [])}
        if row.step_id is not None and row.step_id not in parent_steps:
            payload = _row_dict(row, adherence_fields)
            payload["step_id"] = None
            payload["invariant"] = "adherence_step_ownership_invalid"
            malformed_adherence.append(payload)
            invariant_errors.append({
                "routine_adherence_id": str(row.id),
                "error": "adherence_step_ownership_invalid",
            })
            continue
        adherence_by_routine.setdefault(row.routine_id, []).append(row)

    by_subject: dict[str, dict[str, list[dict[str, Any]]]] = {
        str(member_id): {
            "routines": [], "steps": [], "adherence": [], "recommendation_runs": [],
        }
        for member_id in member_ids
    }
    account_holder_legacy = {
        "routines": [], "steps": [], "adherence": [], "recommendation_runs": [],
    }
    unattributed = {
        "routines": [], "steps": [], "adherence": list(malformed_adherence),
        "recommendation_runs": [],
    }

    def append_routine_graph(
        target: dict[str, list[dict[str, Any]]],
        routine: Routine,
        *,
        strip_subject: bool = False,
    ) -> None:
        routine_payload = _row_dict(routine, routine_fields)
        if strip_subject:
            routine_payload["household_subject_id"] = None
            routine_payload["invariant"] = "routine_subject_ownership_invalid"
        target["routines"].append(routine_payload)
        target["steps"].extend(
            _row_dict(step, step_fields)
            for step in steps_by_routine.get(routine.id, [])
        )
        target["adherence"].extend(
            _row_dict(row, adherence_fields)
            for row in adherence_by_routine.get(routine.id, [])
        )

    legacy_routines: list[Routine] = []
    explicit_self_kinds: set[str] = set()
    legacy_self_kinds: set[str] = set()
    for routine in routines:
        if routine.household_subject_id is None:
            legacy_routines.append(routine)
            legacy_self_kinds.add(routine.kind)
            continue
        if routine.household_subject_id in member_ids:
            append_routine_graph(
                by_subject[str(routine.household_subject_id)], routine,
            )
            if (
                household.self_row is not None
                and routine.household_subject_id == household.self_row.id
            ):
                explicit_self_kinds.add(routine.kind)
        else:
            append_routine_graph(unattributed, routine, strip_subject=True)
            invariant_errors.append({
                "routine_id": str(routine.id),
                "error": "routine_subject_ownership_invalid",
            })

    if not household.exists or household.account_holder_identified:
        for routine in legacy_routines:
            append_routine_graph(account_holder_legacy, routine)
    else:
        for routine in legacy_routines:
            append_routine_graph(unattributed, routine)
            invariant_errors.append({
                "routine_id": str(routine.id),
                "error": "legacy_routine_account_holder_unidentified",
            })

    for kind in sorted(explicit_self_kinds & legacy_self_kinds):
        invariant_errors.append({
            "routine_kind": kind,
            "error": "account_has_dual_self_routines",
        })

    for run in recommendation_runs:
        payload = _row_dict(run, run_fields)
        if run.household_subject_id is None:
            if not household.exists or household.account_holder_identified:
                account_holder_legacy["recommendation_runs"].append(payload)
            else:
                payload["invariant"] = "legacy_run_account_holder_unidentified"
                unattributed["recommendation_runs"].append(payload)
            continue
        if run.household_subject_id in member_ids:
            by_subject[str(run.household_subject_id)]["recommendation_runs"].append(payload)
        else:
            payload["household_subject_id"] = None
            payload["invariant"] = "routine_run_subject_ownership_invalid"
            unattributed["recommendation_runs"].append(payload)
            invariant_errors.append({
                "routine_recommendation_run_id": str(run.id),
                "error": "routine_run_subject_ownership_invalid",
            })

    manager_by_subject, manager_legacy, manager_orphaned = _group_decision_rows(
        list(manager_decision_events), members,
    )
    manager_safe, manager_ambiguous = _safe_legacy_split(
        manager_legacy, household=household, timestamp="created_at",
    )
    invariant_errors.extend(
        {
            "shelf_manager_decision_event_id": str(row.id),
            "error": "decision_subject_ownership_invalid",
        }
        for row in manager_orphaned
    )
    manager_fields = [c.name for c in ShelfManagerDecisionEvent.__table__.columns]
    preference_fields = [c.name for c in CareProductPreference.__table__.columns]
    preferences_by_subject: dict[str, list[dict[str, Any]]] = {
        str(member_id): [] for member_id in member_ids
    }
    unattributed_preferences: list[dict[str, Any]] = []
    for preference in care_preferences:
        row = _row_dict(preference, preference_fields)
        if preference.household_subject_id in member_ids:
            preferences_by_subject[str(preference.household_subject_id)].append(row)
        else:
            row["household_subject_id"] = None
            row["invariant"] = "preference_subject_ownership_invalid"
            unattributed_preferences.append(row)
            invariant_errors.append({
                "care_product_preference_id": str(preference.id),
                "error": "preference_subject_ownership_invalid",
            })

    return {
        "maintenance_preferences": [
            _row_dict(r, [c.name for c in MaintenancePreference.__table__.columns])
            for r in maintenance_preferences
        ],
        "maintenance_events": [
            _row_dict(r, [c.name for c in MaintenanceEvent.__table__.columns])
            for r in maintenance_events
        ],
        "routine_history": {
            "by_subject": by_subject,
            "account_holder_legacy": account_holder_legacy,
            "unattributed": unattributed,
        },
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
        "supplement_label_components": [_supplement_label_component_row(r) for r in label_components],
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
        "manager_history": {
            "by_subject": {
                str(member_id): [
                    _row_dict(row, manager_fields) for row in rows
                ]
                for member_id, rows in manager_by_subject.items()
            },
            "account_holder_legacy": [
                _row_dict(row, manager_fields) for row in manager_safe
            ],
            "unattributed": [
                _row_dict(row, manager_fields) for row in manager_ambiguous
            ] + [
                _unattributed_row(row, manager_fields) for row in manager_orphaned
            ],
            "coverage": {
                "unattributed_legacy_events_present": bool(
                    manager_ambiguous or manager_orphaned
                ),
                "complete_for_subject": not bool(
                    manager_ambiguous or manager_orphaned
                ),
            },
        },
        "care_product_preferences_by_subject": preferences_by_subject,
        "unattributed_care_product_preferences": unattributed_preferences,
        "invariant_errors": invariant_errors,
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

    events = await _fetch(session, _account_rows(MetricEvent, account_id))
    goals = await _fetch(session, _account_rows(ProgressGoal, account_id))
    milestones = await _fetch(session, _account_rows(Milestone, account_id))
    photos = await _fetch(session, _account_rows(ProgressPhoto, account_id))
    facts = await _fetch(session, _account_rows(MemoryFact, account_id))

    # Revisions and sources hang off the fact, not the account, so they are
    # fetched through the account's facts. Corrections and tombstones are the
    # part of memory a user most needs to see: an export that lists only the
    # current wording hides what was remembered before, and what was deleted.
    fact_ids = select(MemoryFact.id).where(MemoryFact.account_id == account_id)
    revisions = await _fetch(
        session,
        select(MemoryRevision)
        .where(MemoryRevision.fact_id.in_(fact_ids))
        .order_by(*_chronological(MemoryRevision)),
    )
    sources = await _fetch(
        session,
        select(MemorySource)
        .where(MemorySource.fact_id.in_(fact_ids))
        .order_by(*_chronological(MemorySource)),
    )
    feedback = await _fetch(session, _account_rows(FeedbackEvent, account_id))
    behaviours = await _fetch(session, _account_rows(GamificationEvent, account_id))

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
        # Goal updates, snapshots, comparisons, score explanations, streaks
        # and memory category choices, straight from the coverage contract.
        **await _contract_collections(session, account_id, "progress_and_memory"),
    }


async def _ai_and_ops(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    runs = await _fetch(
        session,
        select(AIRun).where(AIRun.account_id == account_id).order_by(AIRun.created_at.desc()),
    )
    outputs = await _fetch(
        session,
        select(AIRunOutput)
        .where(AIRunOutput.ai_run_id.in_(_owned_ids("ai_runs", account_id)))
        .order_by(*_chronological(AIRunOutput)),
    )
    audit = await _fetch(
        session,
        select(AuditEvent).where(AuditEvent.account_id == account_id).order_by(AuditEvent.created_at.desc()),
    )
    beta = await _fetch(session, _account_rows(BetaUsageEvent, account_id))
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
        # Product analytics this account generated.
        **await _contract_collections(session, account_id, "ai_and_ops"),
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


def _path_present(node: Any, segments: list[str]) -> bool:
    """Whether a coverage path exists in a domain's payload.

    ``name[*]`` is a list whose every element must carry the rest of the path.
    An empty list carries it trivially: there is nothing in it to be missing.
    """
    if not segments:
        return True
    head, rest = segments[0], segments[1:]
    if not isinstance(node, dict):
        return False
    if head.endswith("[*]"):
        items = node.get(head[:-3])
        return isinstance(items, list) and all(_path_present(item, rest) for item in items)
    return head in node and _path_present(node[head], rest)


def _unproven_coverage(
    domains: dict[str, Any], reads: set[tuple[str | None, str]],
) -> list[str]:
    """Covered tables this export cannot show it delivered, as ``table:reason``.

    A table its declared domain never read, or a declared path missing from
    that domain's payload, is a table the file would silently leave out.
    Table names and reasons only: nothing here is a customer's value.
    """
    problems: list[str] = []
    for table, entry in sorted(EXPORT_COVERAGE.items()):
        if (entry.domain, table) not in reads:
            problems.append(f"{table}:not_read")
        payload = domains.get(entry.domain)
        for path in entry.paths:
            if not _path_present(payload, path.split(".")):
                problems.append(f"{table}:path_missing")
    return problems


async def build_export(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """Build the complete privacy-export payload for ``account_id``.

    Complete or not at all. Every domain is tried, even after one fails, so
    the log names every domain that could not be exported — but if any did,
    or if any covered table cannot be shown to be in the file, this raises
    :class:`PrivacyExportIncomplete` and returns nothing. A payload with a
    domain replaced by a failure marker is not somebody's data; it is part of
    it, and it used to be returned with 200 and recorded as a completed
    export.

    A pure read. Nothing here adopts, repairs, attaches or updates anything,
    whether it succeeds or fails.
    """
    missing, stale = coverage_drift()
    if missing or stale:
        # A table classified INCLUDED with no export contract, or a contract
        # for a table that is not INCLUDED. Either way the file cannot be
        # called complete, so it is not returned at all.
        unproven = tuple(
            [f"{table}:no_export_contract" for table in sorted(missing)]
            + [f"{table}:not_included" for table in sorted(stale)]
        )
        logger.error("privacy_export_coverage_drift tables=%s", ",".join(unproven))
        raise PrivacyExportIncomplete(unproven=unproven)

    domains: dict[str, Any] = {}
    failed: list[str] = []
    reads: set[tuple[str | None, str]] = set()
    reads_token = _READS.set(reads)
    try:
        for name, handler in DOMAIN_HANDLERS.items():
            domain_token = _DOMAIN.set(name)
            try:
                domains[name] = await handler(session, account_id)
            except Exception as exc:  # noqa: BLE001 — every domain is tried before the export is refused
                # The domain and the exception type, never the exception text.
                # A database error's message carries the driver's rendering of
                # the failing value, and every value in this file is one
                # person's own data. ``hide_parameters=True`` on the engine
                # removes SQLAlchemy's parameter list; the driver's own wording
                # is why this is not ``logger.exception``.
                logger.error(
                    "privacy_export_domain_failed domain=%s type=%s",
                    name,
                    type(exc).__name__,
                )
                # Every handler shares this session. A failed statement leaves
                # its transaction unusable, and PostgreSQL then refuses
                # everything that follows — so without this the first domain to
                # fail took every domain after it down too, and the log could
                # not say which ones had really failed. Nothing here writes, so
                # there is nothing to lose by rolling back.
                try:
                    await session.rollback()
                except Exception:  # noqa: BLE001 — a session we cannot reset is already lost
                    logger.error("privacy_export_rollback_failed domain=%s", name)
                failed.append(name)
            finally:
                _DOMAIN.reset(domain_token)
    finally:
        _READS.reset(reads_token)

    if failed:
        raise PrivacyExportIncomplete(failed_domains=tuple(failed))

    unproven = _unproven_coverage(domains, reads)
    if unproven:
        logger.error("privacy_export_coverage_unproven tables=%s", ",".join(unproven))
        raise PrivacyExportIncomplete(unproven=tuple(unproven))

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
            # Derived from the coverage contract this export was just held to,
            # never a second hand-written list: equal to ``included_tables``,
            # because a file where they differ is refused above.
            "exported_tables": sorted(EXPORT_COVERAGE),
            # Where in this file each exported table's rows are.
            "export_locations": export_locations(),
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
