"""Whose decision this is, and which old decisions can honestly be called theirs.

Decision Memory arrived before households did. For most of this product's life
``account_id`` was the only thing a decision row carried, and that was a true
and complete answer: one account meant one person, so their history was the
account's history.

A household makes the same rows ambiguous rather than wrong. They are still
truthful records of a decision somebody made; what is no longer knowable, for
some of them, is *which* human made it. This module is about being honest about
the difference instead of guessing.

The boundary is one timestamp
-----------------------------
``FamilyCircle.created_at``, per account. Before that moment the account had one
person in it, so a subject-less decision is unambiguously the account holder's.
From that moment on it could have been made about anybody the household had
named, and nothing stored says which.

The comparison is strictly ``<``. A row written in the same instant the circle
was created is ambiguous, and ambiguity resolves to "we do not know" — never to
"probably the account holder". That is one row, one account, and getting it
wrong means showing one person another person's purchase history.

It is deliberately *this account's* circle time, not a deployment date and not
the Step 11A release. Accounts opened their households on their own schedule,
and a global cut-off would misattribute every account that was early or late.

Three consequences worth stating plainly:

* Immutable events are never rewritten. A legacy event stays exactly what it
  was, including its silence about who it was for. Assigning old rows to the
  current account holder would manufacture evidence.
* A mutable current decision *may* be adopted — one column, same row — but only
  from the safe side of the boundary.
* Where the answer is unknown, the customer is told that their history is
  incomplete rather than shown a confident half of it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.models import FamilyCircle
from app.domains.family.subject import ResolvedSubject

__all__ = [
    "DecisionSubject",
    "ambiguous_legacy_filter",
    "canonical_decision_subject",
    "decision_subject_for_write",
    "serialize_decision_subject",
    "subject_row_filter",
]


@dataclass(frozen=True, slots=True)
class DecisionSubject:
    """A checked subject, plus the one household fact old rows are read against.

    The household timestamp travels with the subject because every question
    Decision Memory asks about a legacy row needs both: who is asking, and when
    this account stopped being one person. Fetching it separately at each call
    site is how two of them end up disagreeing.
    """

    subject: ResolvedSubject
    #: When this account opened its household, or ``None`` if it never has.
    circle_created_at: datetime | None

    @property
    def account_id(self) -> uuid.UUID:
        return self.subject.account_id

    @property
    def subject_id(self) -> uuid.UUID | None:
        return self.subject.subject_id

    @property
    def is_account_holder(self) -> bool:
        return self.subject.is_account_holder

    @property
    def has_household(self) -> bool:
        return self.circle_created_at is not None

    def legacy_row_is_mine(self, when: datetime | None) -> bool:
        """Can a subject-less row written at ``when`` honestly be called mine?

        Only the account holder can ever own one: a named member's decisions
        are only the ones stored against them. And only from before the
        household existed, because after that the row could have been about
        anybody in it.
        """
        if not self.is_account_holder:
            return False
        if self.circle_created_at is None:
            return True
        if when is None:
            return False
        return when < self.circle_created_at


async def _circle_created_at(
    session: AsyncSession, account_id: uuid.UUID,
) -> datetime | None:
    return await session.scalar(
        select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
    )


async def canonical_decision_subject(
    session: AsyncSession, *, principal_account_id: uuid.UUID, subject: object,
) -> DecisionSubject:
    """Read authority. Re-derives the subject, then reads the household boundary.

    Reads only — no locks, no writes. ``principal_account_id`` is the
    authenticated account and is never taken from ``subject``, which is an
    ordinary public dataclass a caller can fill in with anything.
    """
    from app.domains.profile.identity import canonical_subject

    checked = await canonical_subject(
        session, principal_account_id=principal_account_id, subject=subject,
    )
    return DecisionSubject(
        subject=checked,
        circle_created_at=await _circle_created_at(session, principal_account_id),
    )


async def decision_subject_for_write(
    session: AsyncSession, *, principal_account_id: uuid.UUID, subject: object,
) -> DecisionSubject:
    """Write authority, in the documented lock order, before anything is decided.

    Two paths, because they are serialising against different things.

    The **account holder** takes ``Account FOR UPDATE``. Their write may adopt a
    legacy current decision, and adoption has to agree with household creation
    and with account deletion — all three meet on the account row. The subject
    is then re-resolved *under* the lock, because a household committed a moment
    ago changes who "me" is and what the boundary is.

    A **named member** takes ``Account FOR KEY SHARE`` and then their own
    ``FamilyProfile FOR UPDATE``: weak on the account so two members of one
    household stay independently writable, strong on the member so a concurrent
    deactivation cannot land between "this person is in the household" and the
    decision being recorded. The account comes first so that the implicit
    foreign-key lock the coming insert takes lands in the same order.

    Which of the two applies is decided *before* any lock is taken. The account
    holder must never hold ``FOR KEY SHARE`` and then ask to upgrade — two
    writers doing that deadlock on the upgrade itself.
    """
    from app.domains.profile.identity import (
        canonical_subject,
        canonical_subject_for_write,
        lock_account,
    )

    provisional = await canonical_subject(
        session, principal_account_id=principal_account_id, subject=subject,
    )
    if provisional.is_account_holder:
        await lock_account(session, principal_account_id)
        # Re-resolved under the lock. A household that committed while this
        # request was deciding changes both the canonical self row and the
        # boundary every legacy row is read against.
        checked = await canonical_subject(
            session, principal_account_id=principal_account_id, subject=subject,
        )
    else:
        checked = await canonical_subject_for_write(
            session, principal_account_id=principal_account_id, subject=subject,
        )
    return DecisionSubject(
        subject=checked,
        circle_created_at=await _circle_created_at(session, principal_account_id),
    )


def subject_row_filter(
    model: Any, decision_subject: DecisionSubject, *, timestamp: Any = None,
) -> ColumnElement[bool]:
    """Which rows of ``model`` are this subject's, and nobody else's.

    ``timestamp`` is the column the legacy boundary is judged against —
    ``created_at`` for an immutable event, ``updated_at`` for a mutable current
    decision, because what matters for the latter is when it last said
    something rather than when it was first written.

    A named member gets exactly their own rows. Nothing subject-less is theirs:
    a row that predates the household predates them being distinguishable, and
    it would be somebody else's history.
    """
    if not decision_subject.is_account_holder:
        return model.household_subject_id == decision_subject.subject_id

    subject_less = model.household_subject_id.is_(None)
    if decision_subject.circle_created_at is None:
        # No household ever existed, so subject-less is simply how this
        # account's own history has always been written.
        return subject_less

    boundary = timestamp if timestamp is not None else model.created_at
    return or_(
        model.household_subject_id == decision_subject.subject_id,
        and_(subject_less, boundary < decision_subject.circle_created_at),
    )


def ambiguous_legacy_filter(
    model: Any, decision_subject: DecisionSubject, *, timestamp: Any = None,
) -> ColumnElement[bool]:
    """Subject-less rows from after the household, which belong to nobody known.

    Not this subject's, not another subject's — unattributed. They exist so the
    customer can be told their history is incomplete instead of being shown a
    confident subset of it.
    """
    if decision_subject.circle_created_at is None:
        return false()
    boundary = timestamp if timestamp is not None else model.created_at
    return and_(
        model.household_subject_id.is_(None),
        boundary >= decision_subject.circle_created_at,
    )


def serialize_decision_subject(decision_subject: DecisionSubject) -> dict[str, Any]:
    """The small, stable shape every subject-aware response carries.

    Enough for a caller to prove which human it asked about, and nothing more:
    no age band, no relation, no household shape. An account holder with no
    household has no id to give, and says so with ``null`` rather than
    inventing one.
    """
    return {
        "household_subject_id": (
            str(decision_subject.subject_id) if decision_subject.subject_id else None
        ),
        "is_account_holder": decision_subject.is_account_holder,
    }
