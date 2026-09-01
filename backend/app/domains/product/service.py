"""Looking a barcode up, and what to do when it is not there.

The order is ours, then Open Food Facts, then not found:

1. **Our record** (Store B) — the barcode, its confidence, the FSSAI licence.
2. **Open Food Facts** (Store A) — the cached copy first, then their API, and
   whatever comes back is written to Store A only.
3. **Not found** — offered as an answer rather than an error, with the label
   capture that turns it into an answer.

The two halves are paired by ``off.join``, in memory, for the length of the
response. Nothing writes the pair anywhere: that is the ODbL wall, and
``docs/architecture/ODBL_DATA_WALL.md`` says why.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.off import client as off_client
from app.domains.off.join import join_on_barcode, read_off_product, read_off_product_with_age
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.product.confidence import CONFIDENCE_TEXT, ProductConfidence
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import LabelErrorReport, LabelSnapshot, ProductRecord, ScanEvent
from app.shared.database.base import utcnow

OUTCOME_LOCAL = "found_local"
OUTCOME_OFF = "found_off"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_LABEL = "label_captured"


def confidence_block(level: str) -> dict[str, str]:
    """Never returned empty. Every result says how far it can be trusted."""
    return {"level": level, "text": CONFIDENCE_TEXT[level]}


async def _own_record(session: AsyncSession, barcode: str) -> ProductRecord | None:
    return (await session.execute(
        select(ProductRecord).where(ProductRecord.barcode == barcode)
    )).scalar_one_or_none()


async def latest_label_snapshot(session: AsyncSession, barcode: str) -> LabelSnapshot | None:
    return (await session.execute(
        select(LabelSnapshot).where(LabelSnapshot.barcode == barcode).order_by(LabelSnapshot.version_number.desc()).limit(1)
    )).scalar_one_or_none()

CONTENT_FACT_FIELDS = (
    "product_name", "brand", "ingredients_text", "nutrition_per_100g",
    "serving_size", "net_quantity", "fssai_licence", "veg_mark", "allergen_text",
)
def _normalise(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items()) if v not in (None, "")}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, str):
        collapsed = " ".join(value.split())
        return collapsed or None
    return value

def canonical_label_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Canonical content only; batch and extraction metadata are observations."""
    return _normalise({key: facts.get(key) for key in CONTENT_FACT_FIELDS})

def label_content_fingerprint(facts: dict[str, Any]) -> str:
    encoded = json.dumps(canonical_label_facts(facts), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def label_completeness(facts: dict[str, Any]) -> str:
    if not facts.get("product_name") and not facts.get("brand"):
        return "identity_only"
    return "complete_for_grading" if facts.get("ingredients_text") and facts.get("nutrition_per_100g") else "incomplete_for_grading"

def label_changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old, new = canonical_label_facts(previous), canonical_label_facts(current)
    mapping = {"product_name": "product_name", "brand": "brand", "ingredients_text": "ingredients", "nutrition_per_100g": "nutrition", "serving_size": "serving_size", "net_quantity": "net_quantity", "fssai_licence": "fssai_licence", "veg_mark": "veg_mark", "allergen_text": "allergen_text"}
    return [mapping[key] for key in CONTENT_FACT_FIELDS if old.get(key) != new.get(key)]


async def store_label_snapshot(
    session: AsyncSession, *, barcode: str, facts: dict[str, Any], device_id: uuid.UUID, scan_event_id: uuid.UUID,
) -> LabelSnapshot:
    fingerprint = label_content_fingerprint(facts)
    existing = (await session.execute(select(LabelSnapshot).where(LabelSnapshot.barcode == barcode, LabelSnapshot.content_fingerprint == fingerprint))).scalar_one_or_none()
    if existing is not None:
        return existing
    # The unique version constraint closes the race between two confirmations.
    # A savepoint lets us recover from that constraint without poisoning the
    # caller's transaction (which also contains the idempotent scan event).
    for _ in range(3):
        current = await latest_label_snapshot(session, barcode)
        row = LabelSnapshot(
            barcode=barcode, device_id=device_id, scan_event_id=scan_event_id, facts=facts,
            confidence=ProductConfidence.UNVERIFIED.value, content_fingerprint=fingerprint,
            version_number=(current.version_number + 1 if current else 1),
            previous_snapshot_id=current.id if current else None,
            changed_fields=label_changed_fields(current.facts, facts) if current else [],
            completeness=label_completeness(facts),
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            existing = (await session.execute(
                select(LabelSnapshot).where(
                    LabelSnapshot.barcode == barcode,
                    LabelSnapshot.content_fingerprint == fingerprint,
                )
            )).scalar_one_or_none()
            if existing is not None:
                return existing
    raise RuntimeError("Could not allocate a unique observed label version")


async def _cache_off_product(barcode: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Write what Open Food Facts returned into Store A, and only Store A."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        existing = await session.get(OffProduct, barcode)
        if existing is None:
            existing = OffProduct(barcode=barcode)
            session.add(existing)
        existing.product_name = payload.get("product_name")
        existing.brands = payload.get("brands")
        existing.ingredients_text = payload.get("ingredients_text")
        existing.nutriments = payload.get("nutriments")
        existing.categories = payload.get("categories")
        existing.image_url = payload.get("image_url")
        existing.quantity = payload.get("quantity")
        existing.countries = payload.get("countries")
        existing.off_last_modified_t = payload.get("last_modified_t")
        existing.fetched_at = datetime.now(UTC)
        await session.commit()
        return await read_off_product(session, barcode)


#: How long a cached Open Food Facts record is served before we look again.
#:
#: Their contributors correct records and manufacturers reformulate packs, so a
#: copy kept forever pins a product's grade to whatever the label said the first
#: time anybody scanned it. A refresh is best-effort in both directions: the
#: cached copy is still what we answer with when their API is slow, down, or
#: the phone is offline, so re-checking costs a stale answer nothing.
OFF_CACHE_TTL = timedelta(days=30)


def _is_stale(fetched_at: datetime | None) -> bool:
    if fetched_at is None:
        return True
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - fetched_at) >= OFF_CACHE_TTL


async def _off_half(barcode: str, *, allow_network: bool = True) -> tuple[dict[str, Any] | None, bool]:
    """Store A first, their API second. Returns (record, came_from_network)."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        cached, fetched_at = await read_off_product_with_age(session, barcode)
    if cached is not None and not _is_stale(fetched_at):
        return cached, False
    if not allow_network:
        # Offline, a stale copy is a better answer than none, and the response
        # already carries the confidence level that says how far to trust it.
        return cached, False
    payload = await off_client.fetch_product(barcode)
    if payload is None:
        return cached, False
    refreshed = await _cache_off_product(barcode, payload)
    return (refreshed if refreshed is not None else cached), True


async def lookup(
    session: AsyncSession,
    barcode: str,
    *,
    allow_network: bool = True,
) -> dict[str, Any]:
    """Look one barcode up. Always answers; never raises for a missing product."""
    barcode = (barcode or "").strip()
    record = await _own_record(session, barcode)
    off_record, from_network = await _off_half(barcode, allow_network=allow_network)

    if record is None and off_record is None:
        return {
            "barcode": barcode,
            "found": False,
            "outcome": OUTCOME_NOT_FOUND,
            # Said plainly. Not an error, and not an empty screen.
            "confidence": confidence_block(ProductConfidence.NOT_ENOUGH_INFORMATION.value),
            "message": "We do not know this one yet. Take a photo of the label and we will read it.",
            "can_capture_label": True,
            "open_food_facts": None,
            "attribution": None,
            "glamgenius": None,
        }

    level = record.confidence if record else ProductConfidence.UNVERIFIED.value
    joined = join_on_barcode(
        barcode,
        off_record,
        {
            "confidence": level,
            "fssai_licence": record.fssai_licence if record else None,
            "origin": record.origin if record else "off",
        } if (record or off_record) else None,
    )
    body = joined.as_dict()
    body.update({
        "found": True,
        "outcome": OUTCOME_LOCAL if record is not None else OUTCOME_OFF,
        "confidence": confidence_block(level),
        "from_network": from_network,
        # Incomplete data is still worth offering the label capture for.
        "can_capture_label": off_record is None or not off_record.get("ingredients_text"),
    })
    return body


async def record_scan(
    session: AsyncSession,
    *,
    barcode: str,
    outcome: str,
    client_scan_id: str,
    device_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    queued_offline: bool = False,
    scanned_at: datetime | None = None,
    label_facts: dict[str, Any] | None = None,
) -> tuple[ScanEvent, bool]:
    """Record one scan, once.

    Returns ``(event, created)``. A replayed offline queue hits the same
    ``client_scan_id`` and gets the original event back rather than a duplicate.
    """
    existing = (await session.execute(
        select(ScanEvent).where(
            ScanEvent.device_id == device_id,
            ScanEvent.client_scan_id == client_scan_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing, False

    event = ScanEvent(
        device_id=device_id, account_id=account_id, barcode=barcode, outcome=outcome,
        client_scan_id=client_scan_id, queued_offline=queued_offline,
        scanned_at=scanned_at or utcnow(), label_facts=label_facts,
    )
    session.add(event)
    await session.flush()
    return event, True


async def attach_scans_to_account(
    session: AsyncSession, *, device_id: uuid.UUID, account_id: uuid.UUID,
) -> int:
    """Give this device's earlier scans to the account that just claimed it.

    Only scans that belong to nobody are moved. A scan already attached to
    someone stays with them.
    """
    result = await session.execute(
        update(ScanEvent)
        .where(ScanEvent.device_id == device_id, ScanEvent.account_id.is_(None))
        .values(account_id=account_id)
    )
    return result.rowcount or 0


async def apply_confirmed_label(
    session: AsyncSession,
    *,
    barcode: str,
    facts: dict[str, Any],
    confirmed_by: str | None = None,
) -> ProductRecord:
    """Take a label a person has confirmed and update our half of the record.

    Only our fields are written here. The transcribed product name, ingredients
    and nutrition belong to Store A and are written there by the OFF path, never
    copied across — see the ODbL wall.

    Anonymous captures retain label facts but do not create a community claim:
    a device identity is not a person identity.  Only an accountable reviewer
    can promote a captured fact to verified until a future, separately reviewed
    independent-identity workflow exists.
    """
    record = await _own_record(session, barcode)
    if record is None:
        record = ProductRecord(barcode=barcode, origin="label_capture")
        session.add(record)
        await session.flush()

    licence = facts.get("fssai_licence") or find_licence(facts.get("ingredients_text"))
    if licence and is_valid_licence(licence):
        record.fssai_licence = licence

    if confirmed_by:
        record.confirmation_count += 1
        record.confidence = ProductConfidence.VERIFIED.value
        record.verified_at = utcnow()
        record.verified_by = confirmed_by[:160]
    elif record.confidence != ProductConfidence.VERIFIED.value:
        record.confidence = ProductConfidence.UNVERIFIED.value
    await session.flush()
    return record


#: Where a report photo lives. Not under an account prefix: the person who
#: notices a wrong number is often not signed in.
LABEL_REPORT_PREFIX = "label-reports"

REPORT_REASONS = (
    "wrong_number", "wrong_ingredient", "wrong_product",
    "wrong_grade", "pack_changed", "something_else",
)


async def record_label_error(
    session: AsyncSession,
    *,
    client_report_id: str,
    subject: str,
    reason: str,
    barcode: str | None = None,
    photo_key: str | None = None,
    device_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
) -> tuple[LabelErrorReport, bool]:
    """File one report, once. A replayed offline queue gets the original back."""
    existing = (await session.execute(
        select(LabelErrorReport).where(
            LabelErrorReport.device_id == device_id,
            LabelErrorReport.client_report_id == client_report_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = LabelErrorReport(
        device_id=device_id, account_id=account_id, client_report_id=client_report_id,
        barcode=barcode, subject=subject[:200], reason=reason,
        photo_key=photo_key,
    )
    session.add(row)
    await session.flush()
    return row, True
