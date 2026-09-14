from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.product.models import ScanDecisionEvent, LabelSnapshot

SCAN_DECISION_MEMORY_VERSION = "v1"

async def read_scan_memory(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str, label_version: int, content_fingerprint: str
) -> dict[str, Any] | None:
    statement = select(ScanDecisionEvent).where(
        ScanDecisionEvent.account_id == account_id,
        ScanDecisionEvent.barcode == barcode,
        ScanDecisionEvent.label_version == label_version,
        ScanDecisionEvent.content_fingerprint == content_fingerprint,
    ).order_by(ScanDecisionEvent.created_at.desc(), ScanDecisionEvent.id.desc()).limit(1)
    
    row = await session.scalar(statement)
    if not row:
        return None
    return serialize_scan_decision(row)

async def record_scan_decision(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    barcode: str,
    label_snapshot_id: uuid.UUID | None,
    label_version: int,
    content_fingerprint: str,
    decision: str,
    note: str | None = None
) -> ScanDecisionEvent:
    event = ScanDecisionEvent(
        account_id=account_id,
        barcode=barcode,
        label_snapshot_id=label_snapshot_id,
        label_version=label_version,
        content_fingerprint=content_fingerprint,
        decision=decision,
        note=note,
    )
    session.add(event)
    await session.flush()
    return event

def serialize_scan_decision(row: ScanDecisionEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "decision": row.decision,
        "note": row.note,
        "occurred_at": row.created_at.isoformat() if row.created_at else None,
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
