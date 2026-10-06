"""Durable uncertain-write authority (Model B), committed BEFORE upload.

The filing transaction holds identity/account/quota locks while this separate
transaction commits only the resource. Never commit the caller's transaction
early. A reservation commit error stops upload, even if acknowledgement alone
was lost. Restart cannot erase the resource or its quota charge.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select, update

from app.domains.media.storage import factory
from app.domains.media.storage.base import StorageObjectMissing, StorageUnavailable, StorageWriteNotStarted
from app.domains.product.models import LabelErrorReport, LabelReportResource
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)


async def reserve(*, report_id, device_id, account_id, client_report_id, key, size):
    async with get_sessionmaker()() as durable:
        durable.add(LabelReportResource(id=report_id, device_id=device_id, account_id=account_id,
            client_report_id=client_report_id, photo_key=key, photo_byte_size=size))
        await durable.commit()


async def upload_report_photo(storage, key, photo, content_type):
    # Supabase's report-specific async transport never detaches an upload
    # thread. Local/cooperative test adapters retain their existing contract.
    upload = getattr(storage, "put_label_report", storage.put)
    try:
        await upload(key, photo, content_type)
    except StorageWriteNotStarted:
        await _mark_complete(key)
        raise
    await _mark_complete(key)


async def _mark_complete(key):
    # "complete" means the mutation is terminal, not that a report was filed.
    async with get_sessionmaker()() as durable:
        await durable.execute(update(LabelReportResource).where(LabelReportResource.photo_key == key)
                              .values(write_state="complete"))
        await durable.commit()


async def _present(storage, key):
    exact = getattr(storage, "label_report_exists", None)
    if exact is not None:
        return await exact(key)
    # Exact object read, never a prefix listing (which may be paginated).
    try:
        await storage.get(key)
    except StorageObjectMissing:
        return False
    return True


async def reconcile(key: str) -> bool:
    """Only for proven-unfiled evidence; caller owns identity/lifecycle order.

    Unknown + absent is NOT terminal: the provider may still commit. Keep the
    durable resource/count/bytes. Visible completion of this single immutable
    upload, or an acknowledged complete write, licenses delete + exact absence
    verification. Provider errors never release authority. No timer/sleep/TTL.
    """
    try:
        async with get_sessionmaker()() as durable:
            resource = (await durable.execute(select(LabelReportResource).where(
                LabelReportResource.photo_key == key))).scalar_one_or_none()
            # Defence in depth: even a mistaken cleanup caller cannot remove
            # a successfully filed photo (database ambiguity stays separate).
            if await durable.scalar(select(LabelErrorReport.id).where(LabelErrorReport.photo_key == key)):
                return False
            storage = factory.get_storage()
            present = await _present(storage, key)
            if resource is not None and resource.write_state == "unknown" and not present:
                return False
            remove = getattr(storage, "delete_label_report", storage.delete)
            await remove(key)
            if await _present(storage, key):
                return False
            await durable.execute(delete(LabelReportResource).where(LabelReportResource.photo_key == key))
            await durable.commit()
            return True
    except Exception:  # noqa: BLE001 — durable state survives, original failure wins
        logger.warning("label_report_photo_compensation_failed")
        return False


async def reconcile_account(session, account_id: uuid.UUID) -> int:
    """Before any prefix purge/cascade. Never discard unknown-absent authority.

    Account deletion requested state excludes new writers; existing writers
    hold the account FOR SHARE until they end. No inverse identity/quota lock.
    """
    resources = (await session.execute(select(LabelReportResource).where(
        LabelReportResource.account_id == account_id))).scalars().all()
    remaining = 0
    for resource in resources:
        if not await reconcile(resource.photo_key):
            remaining += 1
    return remaining


async def reject_or_reconcile_retry(session, *, device_id, client_report_id):
    resource = (await session.execute(select(LabelReportResource).where(
        LabelReportResource.device_id == device_id,
        LabelReportResource.client_report_id == client_report_id))).scalar_one_or_none()
    if resource is not None and not await reconcile(resource.photo_key):
        raise StorageUnavailable("label_report_upload_unresolved")
