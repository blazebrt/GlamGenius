import re

content = open('app/api/v2/product.py', 'r').read()

new_routes = '''
class ScanDecisionInput(BaseModel):
    decision: str
    label_version: int
    content_fingerprint: str
    note: str | None = None

@router.get("/scan/verdict/{barcode}/memory")
async def get_scan_decision_memory(
    barcode: str = BARCODE_PATH,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read memory for the exact current scanned product version."""
    from app.domains.product import scan_memory
    snapshot = await service.latest_label_snapshot(session, barcode)
    if not snapshot:
        # Product exists but no valid exact LabelSnapshot/version identity, fail closed
        return {"decision": None, "history": []}
    
    latest_decision = await scan_memory.read_scan_memory(
        session,
        account_id=current.account_id,
        barcode=barcode,
        label_version=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
    )
    history = await scan_memory.scan_decision_history(
        session,
        account_id=current.account_id,
        barcode=barcode,
        label_version=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
        limit=20,
    )
    
    return {
        "decision": latest_decision,
        "history": history,
        "scan_decision_memory_version": scan_memory.SCAN_DECISION_MEMORY_VERSION,
    }


@router.post("/scan/verdict/{barcode}/memory")
async def record_scan_decision_event(
    body: ScanDecisionInput,
    barcode: str = BARCODE_PATH,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Save BUY/WAIT/SKIP for the exact current scanned product version."""
    from app.domains.product import scan_memory
    from app.shared.errors import ValidationFailedError
    snapshot = await service.latest_label_snapshot(session, barcode)
    if not snapshot:
        raise ValidationFailedError("Cannot save memory without a valid label snapshot.")
        
    if snapshot.version_number != body.label_version or snapshot.content_fingerprint != body.content_fingerprint:
        raise ValidationFailedError("Product version has changed; cannot save memory for stale identity.", field="content_fingerprint")
        
    # Idempotency / Retry protection
    latest = await scan_memory.read_scan_memory(
        session,
        account_id=current.account_id,
        barcode=barcode,
        label_version=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
    )
    if latest and latest["decision"] == body.decision and latest["note"] == body.note:
        return latest
        
    event = await scan_memory.record_scan_decision(
        session,
        account_id=current.account_id,
        barcode=barcode,
        label_snapshot_id=snapshot.id,
        label_version=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
        decision=body.decision,
        note=body.note,
    )
    await session.commit()
    return scan_memory.serialize_scan_decision(event)
'''

if "def get_scan_decision_memory" not in content:
    content += new_routes
    open('app/api/v2/product.py', 'w').write(content)
    print("Added memory routes")
