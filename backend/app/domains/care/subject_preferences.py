"""Whose Care preference this is, and which old ones can honestly be theirs.

One household shares a bottle. Its physical existence — that the account owns
it, how much is left, when it expires — is one fact for everybody. What each
person has decided *about* it is not: one member pauses a cleanser because it
stings, another keeps using it, and the account holder never touched it. The
product is not cloned; only the relationship to it is personal.

That relationship lives in ``CareProductPreference``, keyed by subject. The
pre-household rows live where they always did, in ``InventoryAttribute``, and
they are read only while they can be attributed truthfully — the Step 11C
boundary, ``FamilyCircle.created_at``, compared strictly with ``<``.

Two rules earn their own words here.

**Nothing is ever both.** A row this person can safely claim from before the
household *and* an explicit row stored against them is a state no route can
produce. Reading it would mean choosing which of somebody's own decisions
counts, or unioning two answers into one that neither of them made. It stops
instead, on reads as well as writes.

**A member inherits nothing.** A subject-less row predates them being a
distinguishable person, so it is the account holder's or it is nobody's — never
theirs.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.models import CareProductPreference
from app.domains.care.product_preferences import (
    CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
    CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
    is_effective_user_pause,
    is_effective_user_preference,
)
from app.domains.family.decision_subject import (
    DecisionSubject,
    canonicalize_decision_subject,
)
from app.domains.inventory.models import InventoryAttribute, InventoryItem
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import IdentityInvariantError

#: Who last set a preference, as the server saw it. Never read from a request.
#:
#: ``direct_user``    the person themselves, through the explicit endpoint.
#: ``shelf_manager``  applied by accepting a Manager suggestion.
#: ``legacy_adopted`` carried over from a pre-household row with no new choice.
#:
#: The distinction is not bookkeeping. Manager give-back offers to undo what the
#: Manager did; offering to undo what somebody chose themselves would be the
#: product overruling them.
AuthoritySource = Literal["direct_user", "shelf_manager", "legacy_adopted"]

#: Named rather than spelled out at each use. Give-back asks "is this still the
#: manager's?" in a different module, and a typo in a string comparison there
#: would silently answer "no" for everybody — an offer that quietly stops being
#: made is not a failure any test notices by accident.
AUTHORITY_DIRECT_USER: AuthoritySource = "direct_user"
AUTHORITY_SHELF_MANAGER: AuthoritySource = "shelf_manager"
AUTHORITY_LEGACY_ADOPTED: AuthoritySource = "legacy_adopted"

AUTHORITY_SOURCES: tuple[AuthoritySource, ...] = (
    AUTHORITY_DIRECT_USER, AUTHORITY_SHELF_MANAGER, AUTHORITY_LEGACY_ADOPTED,
)

PREFERENCE_KINDS = ("paused", "preferred")


@dataclass(frozen=True, slots=True)
class PreferenceCoverage:
    """What this subject's preference state does and does not account for."""

    unattributed_legacy_preferences_present: bool = False

    @property
    def complete_for_subject(self) -> bool:
        return not self.unattributed_legacy_preferences_present

    def as_dict(self) -> dict[str, bool]:
        return {
            "unattributed_legacy_preferences_present": (
                self.unattributed_legacy_preferences_present
            ),
            "complete_for_subject": self.complete_for_subject,
        }


def _legacy_key(kind: str) -> str:
    return {
        "paused": CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
        "preferred": CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
    }[kind]


def _legacy_is_effective(row: InventoryAttribute) -> bool:
    """Is this legacy row a real current user preference, not a stale trace?"""
    if row.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY:
        return is_effective_user_pause(
            value=row.value, source=row.source, verification_state=row.verification_state,
        )
    return is_effective_user_preference(
        value=row.value, source=row.source, verification_state=row.verification_state,
    )


def _legacy_kind(row: InventoryAttribute) -> str:
    return "paused" if row.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY else "preferred"


async def _legacy_rows(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    item_ids: tuple[uuid.UUID, ...] | None = None,
) -> list[InventoryAttribute]:
    """Pre-household Care preference rows, joined to prove the account owns them.

    The join is not decoration. ``InventoryAttribute`` is keyed by item, and an
    item id alone says nothing about whose shelf it is on.
    """
    statement = (
        select(InventoryAttribute)
        .join(InventoryItem, InventoryItem.id == InventoryAttribute.item_id)
        .where(
            InventoryItem.account_id == account_id,
            InventoryAttribute.key.in_(
                (CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY, CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY)
            ),
        )
    )
    if item_ids is not None:
        if not item_ids:
            return []
        statement = statement.where(InventoryAttribute.item_id.in_(item_ids))
    return list((await session.execute(statement)).scalars().all())


def _legacy_is_safely_mine(row: InventoryAttribute, subject: DecisionSubject) -> bool:
    """Can this subject honestly claim a pre-household row?

    The rule itself lives on the subject and is deliberately not repeated here.
    Only the account holder can claim one, and only from the safe side of the
    household boundary — and a second copy of that sentence in this module would
    be a place for one of the two to drift without any test being able to see it,
    because the stricter copy would keep answering correctly.

    All this adds is *which* timestamp to ask about: ``updated_at``, because a
    preference is mutable current state and what matters is when it last said
    something, not when the row first appeared.
    """
    return subject.legacy_row_is_mine(row.updated_at)


def _legacy_is_ambiguous(row: InventoryAttribute, subject: DecisionSubject) -> bool:
    """Written once a household existed, so it could have been about anybody.

    Not this subject's and not another subject's — unattributable. It exists in
    the answer only as the admission that the answer is incomplete.
    """
    if subject.circle_created_at is None:
        return False
    return row.updated_at is None or row.updated_at >= subject.circle_created_at


async def _explicit_rows(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    subject: DecisionSubject,
    item_ids: tuple[uuid.UUID, ...] | None = None,
) -> list[CareProductPreference]:
    """This subject's own stored preferences, and nobody else's.

    An account with no household has no subject id and therefore no rows here
    at all: ``household_subject_id`` is NOT NULL, so their state is still the
    legacy store. Returning early keeps that honest rather than emitting
    ``household_subject_id IS NULL``, which would match nothing and read as "no
    preferences" instead of "wrong store".
    """
    if subject.subject_id is None:
        return []
    statement = select(CareProductPreference).where(
        CareProductPreference.account_id == account_id,
        CareProductPreference.household_subject_id == subject.subject_id,
    )
    if item_ids is not None:
        if not item_ids:
            return []
        statement = statement.where(CareProductPreference.inventory_item_id.in_(item_ids))
    return list((await session.execute(statement)).scalars().all())


def _refuse_dual_current(
    explicit: list[CareProductPreference],
    legacy: list[InventoryAttribute],
    subject: DecisionSubject,
) -> None:
    """One person cannot have two current answers about one product.

    A safely attributable legacy row beside an explicit row for the same item
    and kind means adoption did not happen when it should have. Choosing
    between them picks which of the customer's own decisions counts; unioning
    them invents a third. Neither is an answer, so it stops — on reads too,
    because a Shelf summary or a Manager queue built on a guess is the same
    wrong answer arriving somewhere quieter.
    """
    explicit_keys = {
        (row.inventory_item_id, row.preference_kind) for row in explicit
    }
    for row in legacy:
        if not _legacy_is_effective(row) or not _legacy_is_safely_mine(row, subject):
            continue
        if (row.item_id, _legacy_kind(row)) in explicit_keys:
            raise IdentityInvariantError("care_preference_dual_current_state")


async def read_preference_state(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject | None,
    item_ids: tuple[uuid.UUID, ...],
) -> tuple[frozenset[uuid.UUID], frozenset[uuid.UUID], PreferenceCoverage]:
    """What this subject has paused and preferred, plus what cannot be known.

    A pure read. It adopts nothing, writes nothing, and never assigns an
    ambiguous legacy row to anybody.
    """
    subject = await canonicalize_decision_subject(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    explicit = await _explicit_rows(
        session, account_id=principal_account_id, subject=subject, item_ids=item_ids,
    )
    legacy = await _legacy_rows(
        session, account_id=principal_account_id, item_ids=item_ids,
    )
    _refuse_dual_current(explicit, legacy, subject)

    paused = {row.inventory_item_id for row in explicit if row.preference_kind == "paused"}
    preferred = {
        row.inventory_item_id for row in explicit if row.preference_kind == "preferred"
    }

    ambiguous = False
    for row in legacy:
        if _legacy_is_ambiguous(row, subject):
            # Counted as doubt for every household subject, shown to none of
            # them. "We do not know whose this is" is not evidence that it was
            # not this member's.
            if _legacy_is_effective(row):
                ambiguous = True
            continue
        if not _legacy_is_effective(row) or not _legacy_is_safely_mine(row, subject):
            continue
        (paused if _legacy_kind(row) == "paused" else preferred).add(row.item_id)
    return frozenset(paused), frozenset(preferred), PreferenceCoverage(ambiguous)


async def current_authority_source(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    subject: DecisionSubject,
    item_id: uuid.UUID,
    kind: str,
) -> str | None:
    """Who set this subject's current preference, or ``None`` if it is legacy.

    Legacy rows predate the column, so they have no answer and must not be
    given one — that is why give-back falls back to Manager event history for
    them rather than assuming.
    """
    if subject.subject_id is None:
        return None
    row = (await session.execute(select(CareProductPreference).where(
        CareProductPreference.account_id == principal_account_id,
        CareProductPreference.household_subject_id == subject.subject_id,
        CareProductPreference.inventory_item_id == item_id,
        CareProductPreference.preference_kind == kind,
    ))).scalar_one_or_none()
    return None if row is None else row.authority_source


async def _same_slot_conflicts(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    subject: DecisionSubject,
    item_id: uuid.UUID,
) -> tuple[list[CareProductPreference], list[InventoryAttribute]]:
    """This subject's other preferred products in the same canonical slot.

    Both stores, because a person who preferred one cleanser before the
    household and another one after would otherwise end up with two current
    preferences for one slot — the exact state the slot rule exists to prevent.

    Scoped to *this* subject. Another member preferring their own cleanser for
    the same slot is not a conflict; it is the point of the whole step. And the
    match is category *and* canonical slot, so two categories that happen to
    name a slot the same way stay apart.
    """
    from app.domains.routines import shelf as shelf_domain

    context = await shelf_domain.gather(
        session, account_id=principal_account_id, decision_subject=subject,
    )
    placed: dict[uuid.UUID, tuple[str, str]] = {}
    for category in ("beauty", "hair"):
        for product in shelf_domain.build(context, category):
            if product.slot is not None:
                placed[product.item.id] = (category, product.slot)
    target = placed.get(item_id)
    if target is None:
        return [], []

    explicit = [
        row for row in await _explicit_rows(
            session, account_id=principal_account_id, subject=subject,
        )
        if row.preference_kind == "preferred"
        and row.inventory_item_id != item_id
        and placed.get(row.inventory_item_id) == target
    ]
    legacy = [
        row for row in await _legacy_rows(session, account_id=principal_account_id)
        if _legacy_kind(row) == "preferred"
        and row.item_id != item_id
        and _legacy_is_effective(row)
        and _legacy_is_safely_mine(row, subject)
        and placed.get(row.item_id) == target
    ]
    return explicit, legacy


async def apply_subject_preference(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    subject: DecisionSubject,
    item_id: uuid.UUID,
    kind: str,
    active: bool,
    authority_source: AuthoritySource,
) -> None:
    """Store one subject's preference in the household store.

    Only reached once a household exists, because ``household_subject_id`` is
    NOT NULL: an account with no household still keeps its state where it
    always did, and the caller owns that choice. Called with no subject id this
    refuses rather than attempting a row the schema forbids.

    ``authority_source`` is server-derived at every call site and is never read
    from a request. A client that could name itself ``shelf_manager`` could
    make its own choice look like the product's and hand itself back a
    give-back offer.

    Authority is assumed to be held already: this is reached only from a caller
    that has taken ``canonicalize_decision_subject_for_write``, so the account
    and, for a member, their household row are locked.
    """
    if kind not in PREFERENCE_KINDS:
        raise ValueError("unsupported Care preference kind")
    if subject.subject_id is None:
        raise IdentityInvariantError("care_preference_requires_household_subject")

    item = (await session.execute(select(InventoryItem).where(
        InventoryItem.id == item_id,
        InventoryItem.account_id == principal_account_id,
    ))).scalar_one_or_none()
    if item is None:
        raise IdentityInvariantError("care_preference_item_ownership_invalid")

    existing = (await session.execute(select(CareProductPreference).where(
        CareProductPreference.account_id == principal_account_id,
        CareProductPreference.household_subject_id == subject.subject_id,
        CareProductPreference.inventory_item_id == item_id,
        CareProductPreference.preference_kind == kind,
    ))).scalar_one_or_none()
    legacy = (await session.execute(
        select(InventoryAttribute)
        .join(InventoryItem, InventoryItem.id == InventoryAttribute.item_id)
        .where(
            InventoryAttribute.item_id == item_id,
            InventoryAttribute.key == _legacy_key(kind),
            InventoryItem.account_id == principal_account_id,
        )
    )).scalar_one_or_none()
    safe_legacy = (
        legacy is not None
        and _legacy_is_effective(legacy)
        and _legacy_is_safely_mine(legacy, subject)
    )
    if existing is not None and safe_legacy:
        raise IdentityInvariantError("care_preference_dual_current_state")

    if active:
        if kind == "preferred":
            explicit_conflicts, legacy_conflicts = await _same_slot_conflicts(
                session,
                principal_account_id=principal_account_id,
                subject=subject,
                item_id=item_id,
            )
            for row in explicit_conflicts:
                await session.delete(row)
            for row in legacy_conflicts:
                # Retired rather than left standing: it was this person's
                # current preference for this slot, and they have just chosen a
                # different product for it.
                await session.delete(row)
        if existing is None:
            session.add(CareProductPreference(
                account_id=principal_account_id,
                household_subject_id=subject.subject_id,
                inventory_item_id=item_id,
                preference_kind=kind,
                # A row created purely by carrying a pre-household state across
                # says so. Nobody made a new choice, and give-back must not
                # treat it as one.
                authority_source=AUTHORITY_LEGACY_ADOPTED if safe_legacy else authority_source,
            ))
        else:
            existing.authority_source = authority_source
            existing.updated_at = utcnow()
        if safe_legacy and legacy is not None:
            await session.delete(legacy)
    else:
        if existing is not None:
            await session.delete(existing)
        if safe_legacy and legacy is not None:
            await session.delete(legacy)
    await session.flush()


async def claim_preference_ownership(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    subject: DecisionSubject,
    item_id: uuid.UUID,
    kind: str,
    effective: bool,
    authority_source: AuthoritySource,
) -> bool:
    """Settle who owns a preference whose effective value is not changing.

    Two different nothings happen here, and both matter.

    Pausing something the Manager already paused changes nothing a customer can
    see, and changes who it belongs to. Until they say it themselves the
    Manager may offer to undo it; once they have, it is theirs and that offer
    would be the product arguing with them.

    And a state that is effective with no row in the household store is one the
    pre-household store is still holding. Saying it again is the one moment that
    row can be attributed truthfully, so it is adopted here rather than left
    behind — a legacy row that survives a household is a row that becomes
    ambiguous the moment anybody else touches the shelf. It is adopted as
    ``legacy_adopted``, decided inside :func:`apply_subject_preference`: the
    state is what it always was and nobody made a new choice, so the Manager
    must not read it as its own to offer back.

    Returns whether anything was written, so the caller can keep saying
    ``changed: false`` about the effective state while this quietly settles
    ownership underneath.
    """
    if subject.subject_id is None:
        return False
    row = (await session.execute(select(CareProductPreference).where(
        CareProductPreference.account_id == principal_account_id,
        CareProductPreference.household_subject_id == subject.subject_id,
        CareProductPreference.inventory_item_id == item_id,
        CareProductPreference.preference_kind == kind,
    ))).scalar_one_or_none()
    if row is not None:
        if row.authority_source == authority_source:
            return False
        row.authority_source = authority_source
        row.updated_at = utcnow()
        await session.flush()
        return True
    if not effective:
        return False
    await apply_subject_preference(
        session, principal_account_id=principal_account_id, subject=subject,
        item_id=item_id, kind=kind, active=True, authority_source=authority_source,
    )
    return True


__all__ = [
    "AUTHORITY_DIRECT_USER",
    "AUTHORITY_LEGACY_ADOPTED",
    "AUTHORITY_SHELF_MANAGER",
    "AUTHORITY_SOURCES",
    "AuthoritySource",
    "PreferenceCoverage",
    "apply_subject_preference",
    "claim_preference_ownership",
    "current_authority_source",
    "read_preference_state",
]
