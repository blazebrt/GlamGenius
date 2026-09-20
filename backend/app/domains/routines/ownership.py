"""Which persisted routine rows belong to which human.

One sentence, written down once. Before Step 11E a persisted ``Routine`` was
the account's, and the question did not exist; now it is a person's, and the
answer decides what a customer is shown, what they may complete, whose
consistency is whose, and what a downgrade would erase.

A rule like that written down twice is a rule with a spare copy that can drift
while the stricter one keeps answering correctly — and a test removing one copy
still passes, which is how a missing guard stays invisible. Step 11D learned
that the expensive way on the legacy-attribution rule. This module exists so
there is exactly one place to remove.

The legacy rule and why it needs no timestamp
---------------------------------------------
Step 11C and Step 11D compare a legacy row's timestamp against
``FamilyCircle.created_at``, because those rows could have been written after a
household existed and therefore could have been about anybody in it. Persisted
routines are different: no route could write a member's routine before Step
11E, so a ``household_subject_id IS NULL`` routine is structurally the account
holder's however recently it was touched. A named member never inherits one.
"""
from __future__ import annotations

from sqlalchemy import or_

from app.domains.family.decision_subject import DecisionSubject
from app.domains.routines.models import Routine


def routine_subject_filter(subject: DecisionSubject):
    """Rows owned by one logical routine subject.

    Three cases, and the third is the only interesting one:

    * a named member owns exactly the rows stored against them;
    * an account holder with no household owns the legacy rows, because that is
      the only kind that exists for them;
    * an account holder *with* a household owns both their own rows and the
      legacy ones — deliberately, so that a household which has not yet adopted
      its pre-household routines still reads them, and so that a state holding
      both for one kind is visible to the invariant check rather than silently
      half-read.
    """
    if not subject.is_account_holder:
        return Routine.household_subject_id == subject.subject_id
    if subject.subject_id is None:
        return Routine.household_subject_id.is_(None)
    return or_(
        Routine.household_subject_id.is_(None),
        Routine.household_subject_id == subject.subject_id,
    )


__all__ = ["routine_subject_filter"]
