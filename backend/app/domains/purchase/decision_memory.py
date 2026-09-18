"""Shared, strategy-neutral purchase decision memory — for one human at a time.

Until Step 11C a decision belonged to an account, which was a complete answer
while an account meant one person. It no longer does. Everything here now asks
two questions rather than one: who is allowed to see this (the authenticated
account) and whose decision is it (the subject).

The hard part is not the new rows. It is the old ones. A decision written
before the account had a household is unambiguously the account holder's; one
written after could have been about anybody in it and stored nothing that says
which. :mod:`app.domains.family.decision_subject` holds that boundary, and this
module's job is to never quietly cross it — not in a history page, not in a
Purchase Guard count, and not by adopting a row that cannot honestly be
claimed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domains.family.decision_subject import (
    DecisionSubject,
    ambiguous_legacy_filter,
    canonicalize_decision_subject,
    canonicalize_decision_subject_for_write,
    serialize_decision_subject,
    subject_row_filter,
)
from app.domains.family.subject import subject_belongs_to_account
from app.domains.identity.service import lock_account_against_delete
from app.domains.purchase.contract import (
    CARE_PURCHASE_VERDICT_VERSION,
    FRAGRANCE_PURCHASE_VERDICT_VERSION,
    PURCHASE_DECISION_EVENT_VERSION,
    PURCHASE_DECISION_MEMORY_VERSION,
    PURCHASE_GUARD_VERSION,
    is_active_care_category,
    is_active_fragrance_category,
    resolve_purchase_strategy,
)
from app.domains.purchase.identity import identity_for_candidate
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    PurchaseEvaluation,
    ShoppingCandidate,
)
from app.shared.errors.exceptions import (
    IdentityInvariantError,
    NotFoundError,
    ValidationFailedError,
)


def serialize_purchase_decision(row: PurchaseDecision) -> dict[str, Any]:
    """Return the stable customer/read-model shape for either strategy."""
    return {
        "purchase_decision_memory_version": PURCHASE_DECISION_MEMORY_VERSION,
        "id": str(row.id),
        "candidate_id": str(row.candidate_id),
        # Whose decision this row is. NULL is truthful rather than evasive: it
        # means nobody was named when it was written, which is a different fact
        # from "the account holder".
        "household_subject_id": (
            str(row.household_subject_id) if row.household_subject_id else None
        ),
        "strategy": row.strategy_key,
        "evaluation_id": str(row.evaluation_id) if row.evaluation_id else None,
        "recommendation_at_decision": {
            "verdict": row.recommendation_verdict,
            "version": row.recommendation_version,
            "fingerprint": row.recommendation_fingerprint,
        },
        "decision": row.decision,
        "note": row.note,
        "followed_recommendation": row.followed_recommendation,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _account_rows(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    candidate_id: uuid.UUID,
    strategy_key: str | None,
) -> list[PurchaseDecision]:
    """Every current row this account holds for one candidate.

    Account-scoped, always. A subject-bound row could be found by its subject
    alone, and the foreign key would even prove the member exists — but not that
    this account owns them. Reading by subject and then trusting the answer is
    how one account reads another's memory.
    """
    statement = select(PurchaseDecision).where(
        PurchaseDecision.account_id == account_id,
        PurchaseDecision.candidate_id == candidate_id,
        PurchaseDecision.evaluation_id.is_(None),
    )
    if strategy_key is not None:
        statement = statement.where(PurchaseDecision.strategy_key == strategy_key)
    return list((await session.execute(statement)).scalars().all())


def _partition_current(
    rows: list[PurchaseDecision], decision_subject: DecisionSubject,
) -> tuple[PurchaseDecision | None, PurchaseDecision | None, PurchaseDecision | None]:
    """Split this account's rows into the three things they can be to a subject.

    ``explicit`` is a row stored against this subject by name. ``safe_legacy``
    is a subject-less row old enough to be honestly theirs. ``ambiguous`` is a
    subject-less row from after the household existed, which is nobody's that
    we can prove — it is returned only so callers can say the history is
    incomplete.
    """
    explicit = safe_legacy = ambiguous = None
    for row in rows:
        if row.household_subject_id is not None:
            if (
                decision_subject.subject_id is not None
                and row.household_subject_id == decision_subject.subject_id
            ):
                explicit = row
            continue
        if decision_subject.legacy_row_is_mine(row.updated_at):
            safe_legacy = row
        elif decision_subject.is_account_holder:
            ambiguous = row
    return explicit, safe_legacy, ambiguous


def _refuse_dual_self(
    explicit: PurchaseDecision | None, safe_legacy: PurchaseDecision | None,
) -> None:
    """One human cannot have two current answers to the same question.

    A safely attributable legacy row *and* a subject-bound row for the same
    person and candidate means adoption did not happen when it should have.
    Choosing between them would pick which of the customer's own decisions
    counts, and merging them would invent one, so it stops.
    """
    if explicit is not None and safe_legacy is not None:
        raise IdentityInvariantError("account_has_dual_current_purchase_decision")


async def current_purchase_decision_for_subject(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    candidate_id: uuid.UUID,
    strategy_key: str | None = None,
) -> PurchaseDecision | None:
    """This subject's current decision, or nothing. Never anybody else's.

    A pure read: it never adopts, never creates and never writes. An ambiguous
    legacy row is not returned as this subject's — it is not theirs, and
    showing it would be a confident answer to a question the data cannot settle.

    The subject is re-derived here rather than trusted. This is a public domain
    boundary, and a caller that is not the HTTP route — a worker, a job, a test,
    another service — can hand it any ``DecisionSubject`` it likes.
    """
    decision_subject = await canonicalize_decision_subject(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    rows = await _account_rows(
        session,
        account_id=principal_account_id,
        candidate_id=candidate_id,
        strategy_key=strategy_key,
    )
    explicit, safe_legacy, _ = _partition_current(rows, decision_subject)
    _refuse_dual_self(explicit, safe_legacy)
    return explicit or safe_legacy


async def _resolve_current_decision_for_write(
    session: AsyncSession,
    *,
    decision_subject: DecisionSubject,
    candidate_id: uuid.UUID,
    strategy_key: str,
) -> PurchaseDecision | None:
    """The row this subject's next decision should update, adopting once if it may.

    Private, and no longer exported. It takes no principal, so it cannot check
    the subject it is given — it can only be correct when its caller has already
    established write authority. A helper that cannot police itself must not be
    reachable from outside the module that guarantees the order; being in
    ``__all__`` was an invitation to call it without one.

    Called only after the write authority and the candidate lock are held, so
    what it sees cannot change underneath it.

    Adoption is one column on the row that already exists: same id, same
    candidate, same strategy, same events hanging off it, same recommendation
    snapshot. It happens at most once, only for the account holder, and only
    from the safe side of the household boundary. An ambiguous legacy row is
    left exactly where it is and a new subject-bound row is created beside it —
    the two coexisting is the honest outcome, not a conflict.
    """
    rows = await _account_rows(
        session,
        account_id=decision_subject.account_id,
        candidate_id=candidate_id,
        strategy_key=strategy_key,
    )
    explicit, safe_legacy, _ = _partition_current(rows, decision_subject)
    _refuse_dual_self(explicit, safe_legacy)
    if explicit is not None:
        return explicit
    if safe_legacy is None:
        return None
    if decision_subject.subject_id is not None:
        # First qualifying write since the household opened. The row does not
        # become a different decision; it becomes the same decision with a name.
        safe_legacy.household_subject_id = decision_subject.subject_id
        await session.flush()
    return safe_legacy


def style_recommendation_snapshot(evaluation: PurchaseEvaluation) -> dict[str, Any]:
    return {
        "strategy": "style_purchase",
        "evaluation_id": str(evaluation.id),
        "verdict": evaluation.verdict,
        "roi_version": evaluation.roi_version,
        "roi_score": evaluation.roi_score,
    }


async def _record_decision_event(
    session: AsyncSession, *, row: PurchaseDecision, candidate: ShoppingCandidate,
) -> PurchaseDecisionEvent:
    """Append only a meaningful current-state transition in this transaction.

    Private, and no longer exported. It writes to an append-only ledger and
    takes no authenticated principal, so it cannot prove that the two objects
    it is handed belong together or to the caller. A helper that cannot police
    itself must not be reachable from outside the module that guarantees the
    order; being in ``__all__`` was an invitation to call it without one.
    Callers come through :func:`record_decision_event_for_account`, which loads
    the canonical candidate under a principal first.

    It still refuses an impossible pairing rather than trusting its caller.
    Being private makes a mistake less likely, not impossible, and the cost of
    getting this wrong is an immutable row claiming a decision came from a
    product it did not — the kind of record that is believed later precisely
    because events are never rewritten.
    """
    if row.account_id != candidate.account_id or row.candidate_id != candidate.id:
        # One generic reason for both, because the difference is not the
        # caller's to learn: a cross-account pairing and a same-account
        # wrong-candidate pairing are equally impossible through any route.
        # Neither the accounts nor the candidates are named here — the request
        # id in the log is how an operator finds the rows.
        raise IdentityInvariantError("purchase_decision_candidate_identity_mismatch")
    identity = identity_for_candidate(candidate)
    # Scoped to this decision row, which is now per subject — so the "nothing
    # meaningful changed" test can only ever compare a subject against their own
    # last event. Comparing across subjects would let one member's identical
    # decision silently suppress another's, and the ledger would then be missing
    # a decision somebody actually made.
    previous = (await session.execute(
        select(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.decision_id == row.id,
            PurchaseDecisionEvent.account_id == row.account_id,
        ).order_by(
            PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc()).limit(1)
    )).scalar_one_or_none()
    if previous is not None and all((
        previous.category == candidate.category,
        previous.strategy_key == row.strategy_key,
        previous.identity_version == identity["version"],
        previous.identity_state == identity["state"],
        previous.identity_fingerprint == identity["fingerprint"],
        previous.recommendation_verdict == row.recommendation_verdict,
        previous.recommendation_version == row.recommendation_version,
        previous.recommendation_fingerprint == row.recommendation_fingerprint,
        previous.recommendation_snapshot == row.recommendation_snapshot,
        previous.decision == row.decision,
        previous.followed_recommendation == row.followed_recommendation,
    )):
        return previous
    event = PurchaseDecisionEvent(
        account_id=row.account_id,
        # Taken from the authoritative current row rather than inferred from the
        # account: the row is what the write authority already resolved, and
        # deriving it twice is how the two disagree.
        household_subject_id=row.household_subject_id,
        candidate_id=candidate.id, decision_id=row.id,
        category=candidate.category, strategy_key=row.strategy_key,
        candidate_display_name=candidate.display_name,
        identity_version=identity["version"], identity_state=identity["state"],
        identity_fingerprint=identity["fingerprint"],
        recommendation_verdict=row.recommendation_verdict,
        recommendation_version=row.recommendation_version,
        recommendation_fingerprint=row.recommendation_fingerprint,
        recommendation_snapshot=row.recommendation_snapshot,
        decision=row.decision, followed_recommendation=row.followed_recommendation,
    )
    session.add(event)
    await session.flush()
    return event


async def record_decision_event_for_account(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> PurchaseDecisionEvent:
    """Append a decision's event, deriving every field from the database.

    The one public way into the ledger, and it takes two identifiers rather
    than an object. That is the whole correction: a ``PurchaseDecision`` handed
    in by a caller is a claim in exactly the way a ``DecisionSubject`` and a
    ``ShoppingCandidate`` are. Checking ``row.account_id`` against the principal
    reads a field off the same object the caller supplied, so a detached row
    could satisfy it while carrying somebody else's subject, a different
    candidate, or a decision value nobody made — and the event appended from it
    would be an immutable record of something that never happened.

    So nothing about the decision crosses this boundary except its id. The row
    is loaded here, by that id *and* the authenticated account, and locked:
    every field the event copies is mutable current state, and it must not
    change between being read and being written down.

    A decision id belonging to another account and one that never existed are
    the same :class:`NotFoundError`, because saying which would confirm the id
    names a real decision somebody else made.

    It always returns an event or raises. The old ``None`` meant "the candidate
    was deleted", which the schema does not actually allow: ``candidate_id`` is
    NOT NULL with ``ON DELETE CASCADE``, so a deleted product takes its
    decisions with it and a canonical decision always has a candidate row.

    Lock order, and why the account comes first
    -------------------------------------------
    ::

        Account FOR KEY SHARE
        -> PurchaseDecision FOR UPDATE
        -> stored subject ownership
        -> canonical ShoppingCandidate
        -> PurchaseDecisionEvent

    The account lock is not here merely to stop the account disappearing, and
    saying so would miss the point. ``purchase_decision_events.account_id`` is
    an immediate foreign key, so the insert at the end of this function makes
    PostgreSQL check the parent and take ``FOR KEY SHARE`` on that same account
    row *by itself*. This function's real order therefore always included the
    account — the only question was whether it came before or after the
    decision row.

    After is the reverse of account deletion, which takes the account and then
    cascades down into ``purchase_decisions``. Two transactions going opposite
    ways round the same pair deadlock: one holds the decision and waits for the
    account, the other holds the account and waits for the decision. Taking it
    explicitly, first, puts the application's order where the database was going
    to go anyway.

    ``FOR KEY SHARE`` and not ``FOR UPDATE``: this appends immutable history to
    a decision that already exists. It creates no household, adopts no legacy
    identity, and changes no membership — so it has no reason to serialise
    against every other write on the same account, and two holders of this lock
    do not block each other.

    Re-taking it is free. A transaction does not conflict with a row lock it
    already holds, so a caller that established the same protection earlier
    pays nothing here, and no caller is asked to declare which locks it is
    holding — a claim of that kind would be exactly the sort of caller
    assertion this module spent three corrections removing.
    """
    if await lock_account_against_delete(session, principal_account_id) is None:
        # Already gone, and nothing below would be meaningful: the cascades have
        # taken this account's decisions with it. Answered as a missing decision
        # rather than a missing account, which is the same sentence a foreign or
        # invented decision id gets and says nothing about what exists.
        raise NotFoundError("We could not find that decision.")

    row = (await session.execute(
        select(PurchaseDecision)
        .where(
            PurchaseDecision.id == decision_id,
            PurchaseDecision.account_id == principal_account_id,
        )
        .with_for_update()
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that decision.")

    # The stored row can itself be impossible, and an append-only ledger is the
    # worst place to discover that later. The foreign key proves the profile
    # exists; it does not prove whose household it is in, and those are
    # different facts. A decision saying account A decided for account B's
    # member fails closed here rather than being carried forward into a record
    # nothing rewrites. It is not repaired: deciding what that row should have
    # said is a separate, deliberate act.
    if row.household_subject_id is not None and not await subject_belongs_to_account(
        session,
        account_id=principal_account_id,
        subject_id=row.household_subject_id,
    ):
        raise IdentityInvariantError("purchase_decision_subject_ownership_invalid")

    candidate = (await session.execute(
        select(ShoppingCandidate).where(
            ShoppingCandidate.id == row.candidate_id,
            ShoppingCandidate.account_id == principal_account_id,
        )
    )).scalar_one_or_none()
    if candidate is None:
        # Not "the product was deleted". ``candidate_id`` is NOT NULL with
        # ``ON DELETE CASCADE``, so deleting a product deletes its decisions
        # with it, and the row is locked — a canonical decision always has a
        # candidate row. A miss here can therefore only mean that row belongs
        # to another account, which is the same class of stored corruption as a
        # foreign subject and gets the same treatment. Named at the boundary
        # rather than left for the inner append to notice, so the error says
        # what was found. Not repaired.
        raise IdentityInvariantError("purchase_decision_candidate_ownership_invalid")
    return await _record_decision_event(session, row=row, candidate=candidate)


def serialize_decision_event(row: PurchaseDecisionEvent) -> dict[str, Any]:
    return {
        "id": str(row.id), "candidate_id": str(row.candidate_id), "category": row.category,
        "household_subject_id": (
            str(row.household_subject_id) if row.household_subject_id else None
        ),
        "strategy": row.strategy_key, "candidate_display_name": row.candidate_display_name,
        "identity": {"version": row.identity_version, "state": row.identity_state,
                     "fingerprint": row.identity_fingerprint},
        "recommendation_at_decision": {"verdict": row.recommendation_verdict,
            "version": row.recommendation_version, "fingerprint": row.recommendation_fingerprint},
        "decision": row.decision, "followed_recommendation": row.followed_recommendation,
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
    }


async def decision_history(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    limit: int,
    before: uuid.UUID | None = None,
) -> list[PurchaseDecisionEvent]:
    """One human's history, newest first. Nobody else's, in either direction.

    The cursor is checked against the same filter as the page, not merely
    against the account. A cursor belonging to another member — or to
    unattributed legacy history — has to be answered exactly like an invented
    one, because "that cursor exists but is not yours" would confirm that
    somebody else in the household has a decision event.

    Public boundary: the subject is re-derived before the page is built.
    """
    decision_subject = await canonicalize_decision_subject(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    mine = subject_row_filter(PurchaseDecisionEvent, decision_subject)
    statement = select(PurchaseDecisionEvent).where(
        PurchaseDecisionEvent.account_id == principal_account_id, mine,
    )
    if before is not None:
        cursor = await session.scalar(select(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.id == before,
            PurchaseDecisionEvent.account_id == principal_account_id,
            mine,
        ))
        if cursor is None:
            raise NotFoundError("We could not find that decision history cursor.")
        statement = statement.where(or_(
            PurchaseDecisionEvent.created_at < cursor.created_at,
            and_(PurchaseDecisionEvent.created_at == cursor.created_at, PurchaseDecisionEvent.id < cursor.id),
        ))
    return list((await session.execute(statement.order_by(
        PurchaseDecisionEvent.created_at.desc(), PurchaseDecisionEvent.id.desc()).limit(limit))).scalars().all())


async def _unattributed_events_exist(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    extra: Any = None,
) -> bool:
    """Is there history here that belongs to nobody we can name?

    Asked for *every* household subject, and that is the correction. This used
    to answer only for the account holder, on the reasoning that a member's
    history begins when they were named so nothing older could be theirs. True
    of rows from before the household — those are positively the account
    holder's. Not true of rows from after it.

    A subject-less row written once a household existed could have been about
    anybody in it. "We do not know whose this is" is not evidence that it was
    not this member's, and telling a member their history is complete while
    holding a decision that might be theirs is a confident answer to a question
    the data cannot settle. So its existence makes the answer incomplete for
    whoever is asking, without ever being shown to them or counted as theirs.

    With no household at all there is nothing to be ambiguous against: a
    subject-less row is simply how this account's own history has always been
    written, and nothing here is unattributed.
    """
    if not decision_subject.has_household:
        return False
    where = [
        PurchaseDecisionEvent.account_id == principal_account_id,
        ambiguous_legacy_filter(PurchaseDecisionEvent, decision_subject),
    ]
    if extra is not None:
        where.extend(extra)
    return (await session.scalar(
        select(PurchaseDecisionEvent.id).where(*where).limit(1)
    )) is not None


async def history_coverage(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
) -> dict[str, Any]:
    """What this history does and does not contain, said plainly.

    A page that quietly omitted the decisions it could not attribute would look
    exactly like a complete one. Saying so is the difference between "you have
    no earlier decisions" and "we cannot tell whose some of the earlier
    decisions were".

    Public boundary: the subject is re-derived before the answer is composed.
    """
    decision_subject = await canonicalize_decision_subject(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    unattributed = await _unattributed_events_exist(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    return {
        "state": "step_11c_subject_scoped",
        "legacy_current_decisions_included": False,
        "legacy_self_events_included": (
            decision_subject.is_account_holder and decision_subject.has_household
        ),
        "unattributed_legacy_events_present": unattributed,
        "complete_for_subject": not unattributed,
    }


async def purchase_guard(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    candidate: ShoppingCandidate,
) -> dict[str, Any]:
    """Project exact prior facts, for one human, without recalculating a verdict.

    This is the read Step 11C changes most. Before it, a household shared one
    guard: if anybody had already skipped a product, everybody was told they had
    considered it. "You looked at this before and skipped it" is a sentence
    about a person, and saying it to the wrong one is both wrong and a leak —
    it discloses what somebody else in the household decided.

    So every count, the most recent event and the state come from this subject's
    attributable history alone. Another member's identical product, identical
    fingerprint and identical strategy contribute nothing.

    Public boundary, and *both* halves of it are re-derived rather than read.

    The subject was the obvious one. The candidate is the same problem one
    parameter over: a ``ShoppingCandidate`` is an ORM object the caller fetched,
    and fetching one by primary key finds another account's product just as
    readily as this account's. A caller that passed principal A beside account
    B's candidate would have had this guard derive identity from B's product,
    answer with B's candidate id, and query A's history using B's fingerprint.
    The route prevented that; the domain boundary has to prevent it too.

    So only the supplied id is used, and the row behind it is loaded here under
    the authenticated account. ``candidate.account_id`` is not consulted: it is
    a field on the same caller-supplied object, so trusting it would be
    checking the claim against itself.
    """
    decision_subject = await canonicalize_decision_subject(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    candidate = await _owned_candidate(
        session, account_id=principal_account_id, candidate_id=candidate.id,
    )
    strategy = resolve_purchase_strategy(candidate.category)
    if strategy is None or strategy.state != "active":
        # The route already refuses this before calling; said again here so a
        # direct caller gets the same governed answer rather than an
        # AttributeError on the strategy lookup below.
        raise ValidationFailedError(
            "This candidate is not eligible for a purchase guard.", field="category",
        )
    identity = identity_for_candidate(candidate)
    base = {"purchase_guard_version": PURCHASE_GUARD_VERSION, "candidate_id": str(candidate.id), "identity": identity,
            "subject": serialize_decision_subject(decision_subject),
            "history_coverage": {"state": "step_11c_subject_scoped",
                                 "legacy_current_decisions_included": False,
                                 "unattributed_legacy_events_present": False,
                                 "complete_for_subject": True},
            "prior_consideration_count": 0, "most_recent": None, "guard_state": "no_step9a_prior_event",
            "owned_redundancy": None}
    if identity["state"] != "exact":
        base["guard_state"] = "identity_insufficient"
        return base
    strategy_key = strategy.key
    identity_match = (
        PurchaseDecisionEvent.category == candidate.category,
        PurchaseDecisionEvent.strategy_key == strategy_key,
        PurchaseDecisionEvent.identity_version == identity["version"],
        PurchaseDecisionEvent.identity_fingerprint == identity["fingerprint"],
        PurchaseDecisionEvent.identity_state == "exact",
    )
    # Two different kinds of "we might not know everything". A current row with
    # no event behind it is this subject's own pre-Step-9 gap; an unattributed
    # legacy event is a decision somebody made about this exact product that
    # cannot be assigned to anybody. Either makes the coverage incomplete, and
    # neither is ever counted as this subject's.
    legacy_incomplete = await _has_incomplete_legacy_context(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
        candidate_id=candidate.id,
    )
    unattributed = await _unattributed_events_exist(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
        extra=identity_match,
    )
    if legacy_incomplete or unattributed:
        base["history_coverage"]["unattributed_legacy_events_present"] = unattributed
        base["history_coverage"]["complete_for_subject"] = False
    where = (PurchaseDecisionEvent.account_id == principal_account_id,
             subject_row_filter(PurchaseDecisionEvent, decision_subject),
             *identity_match)
    # One statement gives READ COMMITTED one database snapshot for both the
    # distinct-candidate count and newest event.  Separate statements could
    # otherwise observe a just-committed event only on the second read.
    matching = select(PurchaseDecisionEvent).where(*where).cte("matching_events")
    latest = select(matching).order_by(
        matching.c.created_at.desc(), matching.c.id.desc(),
    ).limit(1).cte("latest_event")
    latest_event = aliased(PurchaseDecisionEvent, latest)
    count = select(
        func.count(func.distinct(matching.c.candidate_id)).label("consideration_count"),
    ).cte("consideration_count")
    result = await session.execute(
        select(count.c.consideration_count, latest_event)
        .select_from(count.outerjoin(latest, true()))
    )
    count_value, event = result.one()
    # Ambiguous rows are never counted. A number that quietly included
    # decisions nobody can attribute would be the most confident lie here.
    base["prior_consideration_count"] = int(count_value or 0)
    if event is None:
        if legacy_incomplete or unattributed:
            base["guard_state"] = "historical_context_incomplete"
        return base
    base["most_recent"] = serialize_decision_event(event)
    state = {"bought": "exact_prior_bought", "waiting": "exact_prior_waiting", "skipped": "exact_prior_skipped"}.get(event.decision, "exact_prior_consideration")
    base["guard_state"] = state
    # Historical snapshots remain historical provenance only. They never prove
    # something is owned now; current strategy-owned context is unavailable
    # here until the strategy exposes its own current projection.
    return base


async def has_incomplete_legacy_context(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    candidate_id: uuid.UUID,
) -> bool:
    """A pre-Step-9 current row is not evidence that no history exists.

    Subject-aware now, and that is the correction: this used to ask whether the
    *account* had a current row without an event, so one member's untracked row
    made every other member's guard say their history was incomplete. A
    household would have been permanently unsure about itself.

    Only rows this subject could honestly claim count — their own subject-bound
    row, or a safely attributable legacy one.

    Public boundary: the subject is re-derived before anything is read.
    """
    return await _has_incomplete_legacy_context(
        session,
        principal_account_id=principal_account_id,
        decision_subject=await canonicalize_decision_subject(
            session,
            principal_account_id=principal_account_id,
            decision_subject=decision_subject,
        ),
        candidate_id=candidate_id,
    )


async def _has_incomplete_legacy_context(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    candidate_id: uuid.UUID,
) -> bool:
    """The same question, for a subject an inner caller has already canonicalised."""
    rows = await _account_rows(
        session,
        account_id=principal_account_id,
        candidate_id=candidate_id,
        strategy_key=None,
    )
    explicit, safe_legacy, _ = _partition_current(rows, decision_subject)
    mine = explicit or safe_legacy
    if mine is None:
        return False
    event = await session.scalar(select(PurchaseDecisionEvent.id).where(
        PurchaseDecisionEvent.decision_id == mine.id,
        PurchaseDecisionEvent.account_id == principal_account_id,
    ))
    return event is None


async def _owned_candidate(
    session: AsyncSession, *, account_id: uuid.UUID, candidate_id: uuid.UUID,
) -> ShoppingCandidate:
    """The canonical candidate row, or the same not-found as an invented id.

    Read-only: the reading boundaries need the account's own product facts, not
    a lock on them. ``ShoppingCandidate`` stays account-owned — several people
    in one household consider the same one — so the predicate is the account,
    never a subject.

    A candidate belonging to another account and a candidate that never existed
    are the same answer, because saying which would confirm that the id names a
    real product somebody else is considering.
    """
    candidate = (await session.execute(
        select(ShoppingCandidate).where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        )
    )).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError("We could not find that shopping item.")
    return candidate


async def _locked_candidate(
    session: AsyncSession, *, account_id: uuid.UUID, candidate_id: uuid.UUID,
) -> ShoppingCandidate:
    """Freeze the candidate's facts — after the subject authority, never before.

    The candidate is account-owned and stays that way: several people in one
    household consider the same one, and it is not cloned per subject. Locking
    it is about freezing what it says, so a concurrent confirmation cannot pair
    an old recommendation snapshot with a new identity fingerprint.

    The order matters as much as the lock. The account and, for a member, their
    ``family_profiles`` row are already held by the time this runs. Taking the
    candidate first and reaching back for them afterwards is the inversion that
    deadlocks against account deletion.
    """
    candidate = (await session.execute(
        select(ShoppingCandidate)
        .where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        )
        .with_for_update()
    )).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError("We could not find that shopping item.")
    return candidate


async def save_care_decision(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    candidate_id: uuid.UUID,
    check: dict[str, Any],
    decision: str,
    note: str | None,
) -> PurchaseDecision:
    """Upsert one subject's current Care memory row from one canonical check.

    Write authority is taken here, first, and this service owns it. A route
    having resolved the same subject a moment ago is not the property this
    depends on: a forged ``DecisionSubject`` handed straight to this function
    must fail before the candidate is locked and before any memory row is
    chosen, not after. Re-taking a row lock this transaction already holds
    costs nothing, so a route that established the same authority before
    locking the candidate is not paying for it twice.
    """
    account_id = principal_account_id
    decision_subject = await canonicalize_decision_subject_for_write(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    candidate = await _locked_candidate(
        session, account_id=account_id, candidate_id=candidate_id,
    )
    if not is_active_care_category(candidate.category):
        raise ValidationFailedError(
            "This candidate is not eligible for the active Care purchase strategy.",
            field="category",
        )
    verdict = check["verdict"]
    verdict_key = verdict["verdict"]
    row = await _resolve_current_decision_for_write(
        session,
        decision_subject=decision_subject,
        candidate_id=candidate_id,
        strategy_key="care_purchase",
    )
    followed = {"buy": "bought", "wait": "waiting", "skip": "skipped"}.get(verdict_key) == decision
    snapshot = {
        "strategy": "care_purchase",
        "care_purchase_verdict_version": CARE_PURCHASE_VERDICT_VERSION,
        "plan_date": str(check["assessment"]["plan_date"]),
        "verdict": verdict_key,
        "headline": verdict.get("headline"),
        "primary_reason_code": verdict.get("primary_reason_code"),
        "reason_codes": verdict.get("reason_codes", []),
        "supporting_reason_codes": verdict.get("supporting_reason_codes", []),
        "decision_fingerprint": verdict.get("decision_fingerprint"),
        "assessment_fingerprint": verdict.get("assessment_fingerprint"),
        "evidence_projection_fingerprint": verdict.get("evidence_projection_fingerprint"),
        "value_fingerprint": verdict.get("value_fingerprint"),
        "environment": verdict.get("environment"),
    }
    if row is None:
        row = PurchaseDecision(
            evaluation_id=None,
            account_id=account_id,
            household_subject_id=decision_subject.subject_id,
            candidate_id=candidate_id,
            strategy_key="care_purchase",
            recommendation_verdict=verdict_key,
            recommendation_version=CARE_PURCHASE_VERDICT_VERSION,
            recommendation_fingerprint=verdict.get("decision_fingerprint"),
            recommendation_snapshot=snapshot,
            decision=decision,
            note=note,
            followed_recommendation=followed,
        )
        session.add(row)
    else:
        row.recommendation_verdict = verdict_key
        row.recommendation_version = CARE_PURCHASE_VERDICT_VERSION
        row.recommendation_fingerprint = verdict.get("decision_fingerprint")
        row.recommendation_snapshot = snapshot
        row.decision = decision
        row.note = note
        row.followed_recommendation = followed
    await session.flush()
    await session.refresh(row)
    await _record_decision_event(session, row=row, candidate=candidate)
    return row


async def save_fragrance_decision(
    session: AsyncSession,
    *, principal_account_id: uuid.UUID, decision_subject: DecisionSubject,
    candidate_id: uuid.UUID,
    check: dict[str, Any], decision: str, note: str | None,
) -> PurchaseDecision:
    """Upsert one subject's candidate-backed memory for a Fragrance check.

    Write authority first, owned here, for the reason ``save_care_decision``
    gives: the service cannot depend on its caller having checked.
    """
    account_id = principal_account_id
    decision_subject = await canonicalize_decision_subject_for_write(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
    )
    candidate = await _locked_candidate(
        session, account_id=account_id, candidate_id=candidate_id,
    )
    if not is_active_fragrance_category(candidate.category):
        raise ValidationFailedError("This candidate is not eligible for the active Fragrance purchase strategy.", field="category")
    verdict = check["verdict"]
    verdict_key = verdict["verdict"]
    row = await _resolve_current_decision_for_write(
        session, decision_subject=decision_subject,
        candidate_id=candidate_id, strategy_key="fragrance_purchase",
    )
    followed = {"buy": "bought", "wait": "waiting", "skip": "skipped"}.get(verdict_key) == decision
    snapshot = {
        "strategy": "fragrance_purchase",
        "fragrance_purchase_verdict_version": FRAGRANCE_PURCHASE_VERDICT_VERSION,
        "verdict": verdict_key,
        "primary_reason_code": verdict.get("primary_reason_code"),
        "supporting_reason_codes": verdict.get("supporting_reason_codes", []),
        "decision_fingerprint": verdict.get("decision_fingerprint"),
        "candidate_id": str(candidate.id),
        "owned_perfume_count": check.get("collection_context", {}).get("owned_perfume_count"),
        "exact_owned_count": len(check.get("collection_context", {}).get("exact_owned", [])),
        "covered_contexts": check.get("collection_context", {}).get("coverage", {}).get("covered", []),
        "unknown_contexts": check.get("collection_context", {}).get("coverage", {}).get("unknown", []),
        "uncovered_contexts": check.get("collection_context", {}).get("coverage", {}).get("uncovered", []),
    }
    if row is None:
        row = PurchaseDecision(
            evaluation_id=None, account_id=account_id,
            household_subject_id=decision_subject.subject_id,
            candidate_id=candidate_id,
            strategy_key="fragrance_purchase", recommendation_verdict=verdict_key,
            recommendation_version=FRAGRANCE_PURCHASE_VERDICT_VERSION,
            recommendation_fingerprint=verdict.get("decision_fingerprint"),
            recommendation_snapshot=snapshot, decision=decision, note=note,
            followed_recommendation=followed,
        )
        session.add(row)
    else:
        row.recommendation_verdict = verdict_key
        row.recommendation_version = FRAGRANCE_PURCHASE_VERDICT_VERSION
        row.recommendation_fingerprint = verdict.get("decision_fingerprint")
        row.recommendation_snapshot = snapshot
        row.decision = decision
        row.note = note
        row.followed_recommendation = followed
    await session.flush()
    await session.refresh(row)
    await _record_decision_event(session, row=row, candidate=candidate)
    return row


__all__ = [
    "PURCHASE_DECISION_EVENT_VERSION",
    "current_purchase_decision_for_subject",
    "has_incomplete_legacy_context",
    "history_coverage",
    "decision_history",
    "purchase_guard",
    "record_decision_event_for_account",
    "serialize_decision_event",
    "save_care_decision",
    "save_fragrance_decision",
    "serialize_purchase_decision",
    "style_recommendation_snapshot",
]
