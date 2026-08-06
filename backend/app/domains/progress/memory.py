"""Long-term memory, and the controls over it.

Every fact the app remembers carries the eight things the brief requires:
the fact itself, where it came from, how confident we are, when it was created,
when it was last reinforced, whether the user has verified it, the evidence it
is linked to, and its deletion state.

Two design decisions carry the phase's hardest promise — *deleted memory no
longer affects recommendations*.

**One door.** :func:`active_facts` is the only way to read memory for any
purpose that influences what a user is shown. It filters on deletion state and
on disabled categories at the query level, so a consumer cannot accidentally
read a deleted fact by forgetting a filter — there is no unfiltered accessor to
forget. :func:`all_facts_including_deleted` exists for the Memory Control screen
alone, is named so nobody reaches for it by accident, and its rows are never
passed to anything that generates a recommendation.

**Deletion is a tombstone, not an UPDATE flag.** A deleted fact keeps its row so
the audit trail survives, but its ``fact`` text is blanked at deletion time.
Even a consumer that bypassed :func:`active_facts` entirely would find nothing
to act on. That is belt and braces on purpose: the promise is worth more than
the row.

A disabled *category* behaves the same way at read time without destroying
anything, so a user can switch a category off, see recommendations stop using
it, and switch it back on later with their data intact.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.progress.models import (
    MemoryCategoryPreference,
    MemoryFact,
    MemoryRevision,
    MemorySource,
)
from app.shared.database.base import utcnow

# The categories of thing the app may remember. A fact must belong to one, and a
# user can switch any of them off.
CATEGORY_PREFERENCE = "preference"
CATEGORY_REJECTION = "rejected_recommendation"
CATEGORY_FAVOURITE = "favourite_outfit"
CATEGORY_COMPLIMENT = "compliment"
CATEGORY_PURCHASE = "purchase"
CATEGORY_RETURN = "returned_item"
CATEGORY_ROUTINE_CHANGE = "routine_change"
CATEGORY_SEASONAL = "seasonal_preference"
CATEGORY_FIT = "fit_issue"
CATEGORY_COLOUR_LIKED = "favourite_colour"
CATEGORY_COLOUR_AVOIDED = "avoided_colour"
CATEGORY_OCCASION = "repeated_occasion_choice"
CATEGORY_FEEDBACK = "feedback"

CATEGORIES: tuple = (
    CATEGORY_PREFERENCE, CATEGORY_REJECTION, CATEGORY_FAVOURITE, CATEGORY_COMPLIMENT,
    CATEGORY_PURCHASE, CATEGORY_RETURN, CATEGORY_ROUTINE_CHANGE, CATEGORY_SEASONAL,
    CATEGORY_FIT, CATEGORY_COLOUR_LIKED, CATEGORY_COLOUR_AVOIDED, CATEGORY_OCCASION,
    CATEGORY_FEEDBACK,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_PREFERENCE: "Things you prefer",
    CATEGORY_REJECTION: "Suggestions you turned down",
    CATEGORY_FAVOURITE: "Outfits you liked",
    CATEGORY_COMPLIMENT: "Compliments you recorded",
    CATEGORY_PURCHASE: "Things you bought",
    CATEGORY_RETURN: "Things you returned",
    CATEGORY_ROUTINE_CHANGE: "Changes to your routine",
    CATEGORY_SEASONAL: "What you wear in which season",
    CATEGORY_FIT: "Fit problems you mentioned",
    CATEGORY_COLOUR_LIKED: "Colours you like",
    CATEGORY_COLOUR_AVOIDED: "Colours you avoid",
    CATEGORY_OCCASION: "What you tend to wear for what",
    CATEGORY_FEEDBACK: "Feedback you gave us",
}

# Where a fact came from. Ordered loosely by how much weight it deserves.
SOURCE_USER_STATED = "user_stated"
SOURCE_USER_ACTION = "user_action"
SOURCE_FEEDBACK = "feedback"
SOURCE_INVENTORY = "inventory"
SOURCE_INFERRED = "inferred"
SOURCES: tuple = (SOURCE_USER_STATED, SOURCE_USER_ACTION, SOURCE_FEEDBACK, SOURCE_INVENTORY, SOURCE_INFERRED)

SOURCE_LABELS: dict[str, str] = {
    SOURCE_USER_STATED: "You told us",
    SOURCE_USER_ACTION: "From something you did in the app",
    SOURCE_FEEDBACK: "From feedback you gave",
    SOURCE_INVENTORY: "From what you catalogued",
    SOURCE_INFERRED: "We worked this out from a pattern",
}

# Whether the user has looked at a fact and said yes or no to it.
STATE_UNVERIFIED = "unverified"
STATE_CONFIRMED = "confirmed"
STATE_CORRECTED = "corrected"
STATE_REJECTED = "rejected"
VERIFICATION_STATES: tuple = (STATE_UNVERIFIED, STATE_CONFIRMED, STATE_CORRECTED, STATE_REJECTED)

DELETION_ACTIVE = "active"
DELETION_DELETED = "deleted"

# A fact the user rejected is kept — so we do not "learn" it again next week —
# but it must never influence anything. Same for anything deleted.
INFLUENCING_STATES: tuple = (STATE_UNVERIFIED, STATE_CONFIRMED, STATE_CORRECTED)

# An inferred fact starts here. Below this it is a hunch, and a hunch should not
# quietly steer somebody's recommendations.
MIN_INFLUENCE_CONFIDENCE = 0.35

# What each reinforcement is worth, and the ceiling an inferred fact can reach.
# It never reaches 1.0: only a person confirming it does that.
REINFORCEMENT_STEP = 0.08
INFERRED_CONFIDENCE_CEILING = 0.9


async def disabled_categories(session: AsyncSession, account_id: uuid.UUID) -> set:
    rows = (await session.execute(
        select(MemoryCategoryPreference.category).where(
            MemoryCategoryPreference.account_id == account_id,
            MemoryCategoryPreference.enabled.is_(False),
        )
    )).scalars().all()
    return set(rows)


async def active_facts(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    categories: Sequence[str] | None = None,
    minimum_confidence: float = MIN_INFLUENCE_CONFIDENCE,
) -> list[MemoryFact]:
    """The **only** way to read memory that will influence what a user sees.

    Filters, at the query level, everything that must not influence anything:
    deleted facts, rejected facts, facts in a category the user switched off,
    and inferred hunches below the confidence floor.

    There is deliberately no "give me everything" variant of this function for
    recommendation code to reach for by mistake.
    """
    disabled = await disabled_categories(session, account_id)

    statement = select(MemoryFact).where(
        MemoryFact.account_id == account_id,
        MemoryFact.deletion_state == DELETION_ACTIVE,
        MemoryFact.verification_state.in_(INFLUENCING_STATES),
        MemoryFact.confidence >= minimum_confidence,
    )
    if categories:
        statement = statement.where(MemoryFact.category.in_(list(categories)))
    if disabled:
        statement = statement.where(MemoryFact.category.notin_(list(disabled)))

    rows = (await session.execute(statement.order_by(MemoryFact.last_reinforced_at.desc()))).scalars().all()
    return list(rows)


async def all_facts_including_deleted(
    session: AsyncSession, account_id: uuid.UUID
) -> list[MemoryFact]:
    """For the Memory Control screen and the export. **Never** for recommendations.

    Named at this length on purpose. Anything that shapes what a user is shown
    must call :func:`active_facts`; the awkwardness here is the point.
    """
    rows = (await session.execute(
        select(MemoryFact)
        .where(MemoryFact.account_id == account_id)
        .order_by(MemoryFact.created_at.desc())
    )).scalars().all()
    return list(rows)


async def record(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    category: str,
    fact: str,
    source: str,
    confidence: float = 0.5,
    evidence: dict[str, Any] | None = None,
    subject_key: str | None = None,
    verification_state: str = STATE_UNVERIFIED,
) -> MemoryFact | None:
    """Remember something, or reinforce it if we already knew it.

    Returns ``None`` when the category is switched off — a disabled category
    means we do not write the fact at all, not that we write it and hide it.

    Reinforcement rather than duplication is what makes memory useful: the same
    observation seen five times becomes one fact we are more sure of, not five
    rows that all say the same thing.
    """
    if category not in CATEGORIES:
        raise ValueError(f"'{category}' is not a memory category.")
    if source not in SOURCES:
        raise ValueError(f"'{source}' is not a memory source.")
    if category in await disabled_categories(session, account_id):
        return None

    key = subject_key or fact.strip().lower()[:200]
    existing = (await session.execute(
        select(MemoryFact).where(
            MemoryFact.account_id == account_id,
            MemoryFact.category == category,
            MemoryFact.subject_key == key,
            MemoryFact.deletion_state == DELETION_ACTIVE,
        )
    )).scalar_one_or_none()

    now = utcnow()
    if existing is not None:
        # A fact the user rejected is not quietly relearned. Their answer stands
        # until they change it themselves.
        if existing.verification_state == STATE_REJECTED:
            return existing
        existing.last_reinforced_at = now
        existing.reinforcement_count += 1
        if existing.verification_state != STATE_CONFIRMED:
            ceiling = 1.0 if source == SOURCE_USER_STATED else INFERRED_CONFIDENCE_CEILING
            existing.confidence = min(ceiling, existing.confidence + REINFORCEMENT_STEP)
        session.add(MemorySource(
            fact_id=existing.id, source=source, evidence=evidence or {}, observed_at=now,
        ))
        return existing

    row = MemoryFact(
        account_id=account_id, category=category, subject_key=key, fact=fact.strip(),
        source=source, confidence=confidence, verification_state=verification_state,
        last_reinforced_at=now, reinforcement_count=1, deletion_state=DELETION_ACTIVE,
    )
    session.add(row)
    await session.flush()
    session.add(MemorySource(
        fact_id=row.id, source=source, evidence=evidence or {}, observed_at=now,
    ))
    return row


async def sources_for(session: AsyncSession, fact_id: uuid.UUID) -> list[MemorySource]:
    rows = (await session.execute(
        select(MemorySource)
        .where(MemorySource.fact_id == fact_id)
        .order_by(MemorySource.observed_at.desc())
    )).scalars().all()
    return list(rows)


async def revise(
    session: AsyncSession,
    fact: MemoryFact,
    *,
    new_text: str | None = None,
    verification_state: str | None = None,
    reason: str = "user_edit",
) -> MemoryFact:
    """Edit or confirm a fact, keeping what it used to say.

    A correction from the user is worth more than anything we inferred, so it
    goes to full confidence — they are the authority on themselves.
    """
    session.add(MemoryRevision(
        fact_id=fact.id, previous_fact=fact.fact,
        previous_confidence=fact.confidence,
        previous_verification_state=fact.verification_state,
        reason=reason,
    ))
    if new_text is not None and new_text.strip() and new_text.strip() != fact.fact:
        fact.fact = new_text.strip()
        fact.verification_state = STATE_CORRECTED
        fact.confidence = 1.0
        fact.source = SOURCE_USER_STATED
    if verification_state is not None:
        fact.verification_state = verification_state
        if verification_state == STATE_CONFIRMED:
            fact.confidence = 1.0
            fact.source = SOURCE_USER_STATED
    fact.last_reinforced_at = utcnow()
    return fact


async def delete(session: AsyncSession, fact: MemoryFact, *, reason: str = "user_deleted") -> MemoryFact:
    """Forget something, for good.

    The row survives so the audit trail does, but the text does not. A consumer
    that somehow bypassed :func:`active_facts` would find an empty string rather
    than something it could act on.
    """
    session.add(MemoryRevision(
        fact_id=fact.id, previous_fact=fact.fact,
        previous_confidence=fact.confidence,
        previous_verification_state=fact.verification_state,
        reason=reason,
    ))
    fact.deletion_state = DELETION_DELETED
    fact.deleted_at = utcnow()
    fact.fact = ""
    fact.subject_key = f"deleted:{fact.id}"
    fact.confidence = 0.0
    return fact


async def set_category_enabled(
    session: AsyncSession, account_id: uuid.UUID, category: str, enabled: bool
) -> MemoryCategoryPreference:
    if category not in CATEGORIES:
        raise ValueError(f"'{category}' is not a memory category.")
    row = (await session.execute(
        select(MemoryCategoryPreference).where(
            MemoryCategoryPreference.account_id == account_id,
            MemoryCategoryPreference.category == category,
        )
    )).scalar_one_or_none()
    if row is None:
        row = MemoryCategoryPreference(account_id=account_id, category=category, enabled=enabled)
        session.add(row)
        await session.flush()
    else:
        row.enabled = enabled
    return row


def serialize(fact: MemoryFact, sources: Sequence[MemorySource] = ()) -> dict[str, Any]:
    """One fact, with everything the brief requires shown to the user."""
    return {
        "id": str(fact.id),
        "category": fact.category,
        "category_label": CATEGORY_LABELS.get(fact.category, fact.category),
        "fact": fact.fact,
        "source": fact.source,
        "source_label": SOURCE_LABELS.get(fact.source, fact.source),
        "confidence": round(fact.confidence, 3),
        "created_at": fact.created_at.isoformat() if fact.created_at else None,
        "last_reinforced_at": fact.last_reinforced_at.isoformat() if fact.last_reinforced_at else None,
        "reinforcement_count": fact.reinforcement_count,
        "verification_state": fact.verification_state,
        "deletion_state": fact.deletion_state,
        "deleted_at": fact.deleted_at.isoformat() if fact.deleted_at else None,
        "influences_recommendations": (
            fact.deletion_state == DELETION_ACTIVE
            and fact.verification_state in INFLUENCING_STATES
            and fact.confidence >= MIN_INFLUENCE_CONFIDENCE
        ),
        "linked_evidence": [
            {
                "source": row.source,
                "source_label": SOURCE_LABELS.get(row.source, row.source),
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "evidence": row.evidence or {},
            }
            for row in sources
        ],
    }


def why_we_remember(fact: MemoryFact) -> str:
    """Plain English for the Memory Control screen."""
    if fact.source == SOURCE_USER_STATED:
        return "You told us this directly."
    if fact.source == SOURCE_USER_ACTION:
        return f"We noticed this from what you did in the app, {fact.reinforcement_count} time(s)."
    if fact.source == SOURCE_FEEDBACK:
        return "This came from feedback you gave on a suggestion."
    if fact.source == SOURCE_INVENTORY:
        return "This came from what you recorded in your inventory."
    return (
        f"We worked this out from a pattern we saw {fact.reinforcement_count} time(s). "
        "If it is wrong, correct it or delete it and we will stop using it."
    )
