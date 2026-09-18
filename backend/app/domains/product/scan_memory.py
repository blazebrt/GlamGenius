"""What one human decided about one exact scanned product version.

Before Step 11C a scan decision belonged to an account. It now belongs to a
person, and the same label snapshot can honestly carry three different answers
at once — the account holder waiting, one member buying, another skipping.

The old rows are the interesting part. A decision written before the account had
a household is unambiguously the account holder's; one written after it could
have been about anybody in it. :mod:`app.domains.family.decision_subject` holds
that boundary and this module never crosses it quietly.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.decision_subject import (
    DecisionSubject,
    ambiguous_legacy_filter,
    serialize_decision_subject,
    subject_row_filter,
)
from app.domains.product.models import ScanDecisionEvent

# Step 11C. The envelope now answers "what did *this person* decide", which is a
# different question from the one v1 answered, so a client that cannot tell the
# versions apart must not assume the old meaning.
SCAN_DECISION_MEMORY_VERSION = "step-11c-v1"


class ScanDecisionConflict(ValueError):
    """The idempotency key has been used for something else.

    Deliberately says no more than that. Which subject used it, and whether that
    subject even exists, is exactly what a caller must not be able to learn by
    probing keys.
    """


async def _unattributed_scan_events_exist(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    extra: tuple[Any, ...] = (),
) -> bool:
    """Is there scan history here that belongs to nobody we can name?"""
    if not decision_subject.is_account_holder or not decision_subject.has_household:
        return False
    return (await session.scalar(
        select(ScanDecisionEvent.id).where(
            ScanDecisionEvent.account_id == principal_account_id,
            ambiguous_legacy_filter(ScanDecisionEvent, decision_subject),
            *extra,
        ).limit(1)
    )) is not None


async def read_scan_memory(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    barcode: str,
    label_snapshot_id: uuid.UUID,
    label_version: int,
    content_fingerprint: str,
) -> dict[str, Any]:
    """This subject's memory for the exact current scanned product version.

    Account-scoped as well as subject-scoped, always. A subject-bound row could
    be found by its subject alone and the foreign key would even prove the
    member exists — but never that this account owns them.
    """
    identity = (
        ScanDecisionEvent.barcode == barcode,
        ScanDecisionEvent.label_snapshot_id == label_snapshot_id,
        ScanDecisionEvent.label_version == label_version,
        ScanDecisionEvent.content_fingerprint == content_fingerprint,
    )
    statement = select(ScanDecisionEvent).where(
        ScanDecisionEvent.account_id == principal_account_id,
        subject_row_filter(ScanDecisionEvent, decision_subject),
        *identity,
    ).order_by(ScanDecisionEvent.created_at.desc(), ScanDecisionEvent.id.desc())

    rows = list((await session.execute(statement)).scalars().all())
    unattributed = await _unattributed_scan_events_exist(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
        extra=identity,
    )
    return serialize_scan_memory(
        rows[0] if rows else None, rows, barcode, label_snapshot_id,
        label_version, content_fingerprint,
        decision_subject=decision_subject, unattributed=unattributed,
    )


def _same_scan(
    existing: ScanDecisionEvent,
    *,
    decision: str,
    note: str | None,
    barcode: str,
    label_snapshot_id: uuid.UUID,
    label_version: int,
    content_fingerprint: str,
) -> bool:
    return (
        existing.decision == decision
        and existing.note == note
        and existing.barcode == barcode
        and existing.label_snapshot_id == label_snapshot_id
        and existing.label_version == label_version
        and existing.content_fingerprint == content_fingerprint
    )


def _same_logical_subject(
    existing: ScanDecisionEvent, decision_subject: DecisionSubject,
) -> bool:
    """Was the stored event about the same human as this request?

    An explicitly attributed event is easy: the ids match or they do not. A
    subject-less one is the whole difficulty. It is the same human only if it
    predates the household — before that there was only one person, so it was
    necessarily theirs. After that it could have been anybody's, and a retry
    cannot prove otherwise, so it is refused rather than claimed.
    """
    if existing.household_subject_id is not None:
        return existing.household_subject_id == decision_subject.subject_id
    return decision_subject.legacy_row_is_mine(existing.created_at)


async def record_scan_decision(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    barcode: str,
    label_snapshot_id: uuid.UUID,
    label_version: int,
    content_fingerprint: str,
    decision: str,
    idempotency_key: str,
    note: str | None = None,
) -> ScanDecisionEvent:
    """Record one subject's decision, or return the one this retry already made.

    Idempotency stays **account-global**, deliberately. A client mutation key
    names one operation, so reusing it for a different human is a mistake to
    refuse rather than a second decision to record — and keeping it account-wide
    is what makes retries unambiguous across the moment a household appears.

    A retry therefore has to match on more than the payload now: same scan
    identity, same decision, same note, *and* the same logical subject.
    """
    def _retry_or_conflict(existing: ScanDecisionEvent | None) -> ScanDecisionEvent:
        if (
            existing is not None
            and _same_scan(
                existing, decision=decision, note=note, barcode=barcode,
                label_snapshot_id=label_snapshot_id, label_version=label_version,
                content_fingerprint=content_fingerprint,
            )
            and _same_logical_subject(existing, decision_subject)
        ):
            return existing
        raise ScanDecisionConflict("idempotency_conflict")

    existing = await session.scalar(
        select(ScanDecisionEvent).where(
            ScanDecisionEvent.account_id == principal_account_id,
            ScanDecisionEvent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return _retry_or_conflict(existing)

    event = ScanDecisionEvent(
        account_id=principal_account_id,
        household_subject_id=decision_subject.subject_id,
        barcode=barcode,
        label_snapshot_id=label_snapshot_id,
        label_version=label_version,
        content_fingerprint=content_fingerprint,
        decision=decision,
        note=note,
        idempotency_key=idempotency_key,
    )
    try:
        # A savepoint, so that losing this race costs the insert and not the
        # whole request: without it the unique violation would poison the
        # transaction and take everything else down with it.
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        return _retry_or_conflict(await session.scalar(
            select(ScanDecisionEvent).where(
                ScanDecisionEvent.account_id == principal_account_id,
                ScanDecisionEvent.idempotency_key == idempotency_key,
            )
        ))

    return event


def serialize_scan_decision(row: ScanDecisionEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "household_subject_id": (
            str(row.household_subject_id) if row.household_subject_id else None
        ),
        "decision": row.decision,
        "note": row.note,
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_scan_memory(
    row: ScanDecisionEvent | None,
    history: list[ScanDecisionEvent],
    barcode: str,
    label_snapshot_id: uuid.UUID,
    label_version: int,
    content_fingerprint: str,
    *,
    decision_subject: DecisionSubject,
    unattributed: bool = False,
) -> dict[str, Any]:
    return {
        "scan_decision_memory_version": SCAN_DECISION_MEMORY_VERSION,
        "subject": serialize_decision_subject(decision_subject),
        "identity": {
            "barcode": barcode,
            "label_snapshot_id": str(label_snapshot_id),
            "label_version": label_version,
            "content_fingerprint": content_fingerprint,
        },
        # Said rather than implied. A history that silently dropped the
        # decisions it could not attribute would look exactly like a complete
        # one, and "you have decided nothing about this" is a different claim
        # from "we cannot tell whose some of these were".
        "history_coverage": {
            "state": "step_11c_subject_scoped",
            "legacy_self_events_included": (
                decision_subject.is_account_holder and decision_subject.has_household
            ),
            "unattributed_legacy_events_present": unattributed,
            "complete_for_subject": not unattributed,
        },
        "decision": serialize_scan_decision(row) if row else None,
        "history": [serialize_scan_decision(r) for r in history],
    }


async def scan_decision_history(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject,
    barcode: str,
    label_version: int,
    content_fingerprint: str,
    limit: int,
) -> list[dict[str, Any]]:
    statement = select(ScanDecisionEvent).where(
        ScanDecisionEvent.account_id == principal_account_id,
        subject_row_filter(ScanDecisionEvent, decision_subject),
        ScanDecisionEvent.barcode == barcode,
        ScanDecisionEvent.label_version == label_version,
        ScanDecisionEvent.content_fingerprint == content_fingerprint,
    ).order_by(ScanDecisionEvent.created_at.desc(), ScanDecisionEvent.id.desc()).limit(limit)
    rows = (await session.execute(statement)).scalars().all()
    return [serialize_scan_decision(r) for r in rows]
