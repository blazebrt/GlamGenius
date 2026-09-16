"""Exact, private scan-to-shelf ownership.

This layer intentionally has no knowledge of purchase decisions or public
Product Truth.  It only records a user's explicit statement that the physical
pack proven for their current device belongs on their shelf.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem, InventoryProductLink
from app.domains.inventory.schemas import ItemCreate, ScanOwnershipCreate
from app.domains.product import pack_context
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDevice

CONTRACT_VERSION = "step-10a-v1"
ELIGIBLE_CATEGORIES = frozenset({"beauty", "hair", "perfumes"})


class OwnershipConflict(ValueError):
    """The submitted exact identity does not match server-held provenance."""


def _text(facts: dict[str, Any], key: str, maximum: int) -> str | None:
    value = facts.get(key)
    return value.strip()[:maximum] if isinstance(value, str) and value.strip() else None


def _category_and_identity(facts: dict[str, Any]) -> tuple[str, str, str | None]:
    """Map only confirmed Store-B facts; never product/reference catalog data."""
    category = _text(facts, "product_category", 32)
    name = _text(facts, "product_name", 160)
    brand = _text(facts, "brand", 120)
    if category not in ELIGIBLE_CATEGORIES:
        raise ValueError("This confirmed pack cannot be added to your shelf yet.")
    if name is None:
        raise ValueError("Not enough information to add this product to your shelf.")
    return category, name, brand


async def _resolve_exact(
    session: AsyncSession, body: ScanOwnershipCreate,
) -> tuple[ProductRecord, LabelSnapshot]:
    product = (await session.execute(
        select(ProductRecord).where(ProductRecord.barcode == body.barcode)
    )).scalar_one_or_none()
    snapshot = await session.get(LabelSnapshot, body.label_snapshot_id)
    if product is None or snapshot is None:
        raise OwnershipConflict("This exact scan is no longer available.")
    if (
        snapshot.barcode != body.barcode
        or snapshot.version_number != body.label_version
        or snapshot.content_fingerprint != body.content_fingerprint
    ):
        raise OwnershipConflict("This exact scan no longer matches the product label.")
    return product, snapshot


async def _assert_pack_authority(
    session: AsyncSession, *, account_id: uuid.UUID, device: ScanDevice, snapshot: LabelSnapshot,
) -> dict[str, Any]:
    """The stored device's newest proven capture, never a client boolean."""
    if device.claimed_by_account_id != account_id:
        raise OwnershipConflict("This device is not connected to your account.")
    pack = await pack_context.current_pack(session, barcode=snapshot.barcode, device_id=device.id)
    if not pack.is_proven or pack.scan_event is None or pack.scan_event.id != snapshot.scan_event_id:
        raise OwnershipConflict("We cannot verify this exact physical pack for your account.")
    if pack.scan_event.account_id != account_id:
        raise OwnershipConflict("We cannot verify this exact physical pack for your account.")
    return pack.label_facts or {}


def _same_identity(link: InventoryProductLink, body: ScanOwnershipCreate) -> bool:
    return (
        link.barcode == body.barcode
        and link.label_snapshot_id == body.label_snapshot_id
        and link.label_version == body.label_version
        and link.content_fingerprint == body.content_fingerprint
    )


def _serialize(status: str, *, body: ScanOwnershipCreate, item: InventoryItem | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "identity": {
            "barcode": body.barcode,
            "label_snapshot_id": str(body.label_snapshot_id),
            "label_version": body.label_version,
            "content_fingerprint": body.content_fingerprint,
        },
        "inventory_item_id": str(item.id) if item is not None else None,
    }


async def add_from_scan(
    session: AsyncSession, *, account_id: uuid.UUID, device: ScanDevice, body: ScanOwnershipCreate,
) -> dict[str, Any]:
    product, snapshot = await _resolve_exact(session, body)
    facts = await _assert_pack_authority(session, account_id=account_id, device=device, snapshot=snapshot)
    category, name, brand = _category_and_identity(facts)
    existing = (await session.execute(select(InventoryItem).where(
        InventoryItem.account_id == account_id,
        InventoryItem.client_mutation_id == body.client_mutation_id,
    ))).scalar_one_or_none()
    if existing is not None:
        link = (await session.execute(select(InventoryProductLink).where(
            InventoryProductLink.inventory_item_id == existing.id
        ))).scalar_one_or_none()
        if link is None or not _same_identity(link, body):
            raise OwnershipConflict("This add-to-shelf request was already used for a different scan.")
        return _serialize("owned", body=body, item=existing)
    try:
        # The account/mutation uniqueness constraint is the final authority.
        # A savepoint means a same-key concurrent insert does not roll back the
        # caller's whole request transaction before we can read its replay.
        async with session.begin_nested():
            item = await inventory_service.create_item(
                session, account_id,
                ItemCreate(category=category, display_name=name, brand=brand, client_mutation_id=body.client_mutation_id),
                source="explicit_scan", verification_state="confirmed", confidence=1.0,
            )
            link = InventoryProductLink(
                account_id=account_id, inventory_item_id=item.id, product_record_id=product.id,
                barcode=body.barcode, label_snapshot_id=snapshot.id, label_version=snapshot.version_number,
                content_fingerprint=snapshot.content_fingerprint, source="explicit_scan",
            )
            session.add(link)
            await session.flush()
    except IntegrityError:
        item = (await session.execute(select(InventoryItem).where(
            InventoryItem.account_id == account_id,
            InventoryItem.client_mutation_id == body.client_mutation_id,
        ))).scalar_one_or_none()
        if item is None:
            raise
        link = (await session.execute(select(InventoryProductLink).where(
            InventoryProductLink.inventory_item_id == item.id
        ))).scalar_one_or_none()
        if link is None or not _same_identity(link, body):
            raise OwnershipConflict("This add-to-shelf request was already used for a different scan.")
    return _serialize("owned", body=body, item=item)


async def status_from_scan(
    session: AsyncSession, *, account_id: uuid.UUID, device: ScanDevice, body: ScanOwnershipCreate,
) -> dict[str, Any]:
    _, snapshot = await _resolve_exact(session, body)
    facts = await _assert_pack_authority(session, account_id=account_id, device=device, snapshot=snapshot)
    try:
        _category_and_identity(facts)
    except ValueError as exc:
        return {**_serialize("not_enough_information", body=body), "message": str(exc)}
    item = (await session.execute(
        select(InventoryItem).join(InventoryProductLink, InventoryProductLink.inventory_item_id == InventoryItem.id)
        .where(InventoryProductLink.account_id == account_id, InventoryProductLink.label_snapshot_id == snapshot.id,
               InventoryProductLink.label_version == body.label_version,
               InventoryProductLink.content_fingerprint == body.content_fingerprint,
               InventoryItem.status == "active")
    )).scalars().first()
    return _serialize("owned" if item is not None else "eligible_not_owned", body=body, item=item)
