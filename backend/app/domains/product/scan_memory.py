from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.models import ScanDecisionEvent

SCAN_DECISION_MEMORY_VERSION = "v1"

async def read_scan_memory(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, label_snapshot_id: uuid.UUID, label_version: int, content_fingerprint: str
) -> dict[str, Any]:
    statement = select(ScanDecisionEvent).where(
        ScanDecisionEvent.account_id == account_id,
        ScanDecisionEvent.barcode == barcode,
        ScanDecisionEvent.label_snapshot_id == label_snapshot_id,
        ScanDecisionEvent.label_version == label_version,
        ScanDecisionEvent.content_fingerprint == content_fingerprint,
    ).order_by(ScanDecisionEvent.created_at.desc(), ScanDecisionEvent.id.desc())
    
    rows = (await session.execute(statement)).scalars().all()
    row = rows[0] if rows else None
    return serialize_scan_memory(row, list(rows), barcode, label_snapshot_id, label_version, content_fingerprint)

async def record_scan_decision(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    barcode: str,
    label_snapshot_id: uuid.UUID,
    label_version: int,
    content_fingerprint: str,
    decision: str,
    idempotency_key: str,
    note: str | None = None
) -> ScanDecisionEvent:
    # Handle idempotency natively
    existing = await session.scalar(
        select(ScanDecisionEvent).where(
            ScanDecisionEvent.account_id == account_id,
            ScanDecisionEvent.idempotency_key == idempotency_key
        )
    )
    if existing:
        if (
            existing.decision == decision
            and existing.note == note
            and existing.barcode == barcode
            and existing.label_snapshot_id == label_snapshot_id
            and existing.label_version == label_version
            and existing.content_fingerprint == content_fingerprint
        ):
            return existing
        raise ValueError("idempotency_conflict")

    event = ScanDecisionEvent(
        account_id=account_id,
        barcode=barcode,
        label_snapshot_id=label_snapshot_id,
        label_version=label_version,
        content_fingerprint=content_fingerprint,
        decision=decision,
        note=note,
        idempotency_key=idempotency_key,
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(ScanDecisionEvent).where(
                ScanDecisionEvent.account_id == account_id,
                ScanDecisionEvent.idempotency_key == idempotency_key
            )
        )
        if existing and existing.decision == decision and existing.label_snapshot_id == label_snapshot_id:
            return existing
        raise ValueError("idempotency_conflict")

    return event

def serialize_scan_decision(row: ScanDecisionEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "decision": row.decision,
        "note": row.note,
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
    }

def serialize_scan_memory(row: ScanDecisionEvent | None, history: list[ScanDecisionEvent], barcode: str, label_snapshot_id: uuid.UUID, label_version: int, content_fingerprint: str) -> dict[str, Any]:
    return {
        "scan_decision_memory_version": SCAN_DECISION_MEMORY_VERSION,
        "identity": {
            "barcode": barcode,
            "label_snapshot_id": str(label_snapshot_id),
            "label_version": label_version,
            "content_fingerprint": content_fingerprint,
        },
        "decision": serialize_scan_decision(row) if row else None,
        "history": [serialize_scan_decision(r) for r in history],
    }

async def scan_decision_history(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    barcode: str,
    label_version: int,
    content_fingerprint: str,
    limit: int
) -> list[dict[str, Any]]:
    statement = select(ScanDecisionEvent).where(
        ScanDecisionEvent.account_id == account_id,
        ScanDecisionEvent.barcode == barcode,
        ScanDecisionEvent.label_version == label_version,
        ScanDecisionEvent.content_fingerprint == content_fingerprint,
    ).order_by(ScanDecisionEvent.created_at.desc(), ScanDecisionEvent.id.desc()).limit(limit)
    rows = (await session.execute(statement)).scalars().all()
    return [serialize_scan_decision(r) for r in rows]
