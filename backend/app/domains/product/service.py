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

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.off import client as off_client
from app.domains.off.join import join_on_barcode, read_off_product, read_off_product_with_age
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.product.confidence import (
    COMMUNITY_THRESHOLD,
    CONFIDENCE_TEXT,
    ProductConfidence,
)
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import (
    LabelErrorReport,
    ProductLabelFacts,
    ProductRecord,
    ScanEvent,
)
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


async def _label_facts(session: AsyncSession, barcode: str) -> ProductLabelFacts | None:
    return (await session.execute(
        select(ProductLabelFacts).where(ProductLabelFacts.barcode == barcode)
    )).scalar_one_or_none()


def label_block(row: ProductLabelFacts | None) -> dict[str, Any] | None:
    """Our confirmed reading of a pack, shaped for a response.

    Store B data throughout. It never travels into Store A and is never merged
    with the Open Food Facts half on disk — the two are still only paired for
    the length of one response.
    """
    if row is None:
        return None
    return {
        "product_name": row.printed_name,
        "brand": row.printed_brand,
        "ingredients_text": row.printed_ingredients,
        "nutrition_per_100g": row.printed_nutrition or {},
        "serving_size": row.printed_serving_size,
        "net_quantity": row.printed_net_quantity,
        "veg_mark": row.printed_veg_mark,
        "allergen_text": row.printed_allergens,
        "confidence": row.transcription_confidence,
        "uncertain_fields": (row.uncertain_fields or {}).get("fields", []),
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "source": "label_capture",
    }


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
    label = label_block(await _label_facts(session, barcode))
    off_record, from_network = await _off_half(barcode, allow_network=allow_network)

    if record is None and off_record is None and label is None:
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
            "label": None,
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
    # Ours, alongside the pair rather than merged into it. A confirmed reading
    # answers for a pack Open Food Facts has never heard of, and completes one
    # whose record is missing the ingredient list.
    body["label"] = label
    have_ingredients = bool(
        (off_record or {}).get("ingredients_text")
        or (label or {}).get("ingredients_text")
    )
    body.update({
        "found": True,
        "outcome": OUTCOME_LOCAL if record is not None else OUTCOME_OFF,
        "confidence": confidence_block(level),
        "from_network": from_network,
        # Incomplete data is still worth offering the label capture for.
        "can_capture_label": not have_ingredients,
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
    count_confirmation: bool = True,
) -> ProductRecord:
    """Take a label a person has confirmed and update our half of the record.

    Only our fields are written here. The transcribed product name, ingredients
    and nutrition belong to Store A and are written there by the OFF path, never
    copied across — see the ODbL wall.

    Confirmations accumulate: enough independent ones promote the record from
    unverified to community. A team member confirming makes it verified outright.

    ``count_confirmation`` is how a replayed offline queue stays honest. The
    caller has already asked ``record_scan`` whether this confirmation is new;
    a repeat sends False, so one person tapping once cannot promote a record to
    community confidence by losing their connection five times.
    """
    record = await _own_record(session, barcode)
    if record is None:
        record = ProductRecord(barcode=barcode, origin="label_capture")
        session.add(record)
        await session.flush()

    licence = facts.get("fssai_licence") or find_licence(facts.get("ingredients_text"))
    if licence and is_valid_licence(licence):
        record.fssai_licence = licence

    # Keep the reading itself, not just the licence off it. Without this the
    # transcription lives only on the scan event, no later lookup can see it,
    # and the pack the person photographed stays unanswerable — which is the
    # one thing the label capture exists to fix.
    await _store_label_facts(session, barcode=barcode, facts=facts)

    if count_confirmation:
        record.confirmation_count += 1
    if confirmed_by:
        record.confidence = ProductConfidence.VERIFIED.value
        record.verified_at = utcnow()
        record.verified_by = confirmed_by[:160]
    elif record.confidence != ProductConfidence.VERIFIED.value:
        record.confidence = (
            ProductConfidence.COMMUNITY.value
            if record.confirmation_count >= COMMUNITY_THRESHOLD
            else ProductConfidence.UNVERIFIED.value
        )
    await session.flush()
    return record


async def _store_label_facts(
    session: AsyncSession, *, barcode: str, facts: dict[str, Any],
) -> ProductLabelFacts:
    """Write the confirmed reading of a pack, replacing any earlier one.

    Ours, in Store B. A person read a physical label; nothing here came from
    Open Food Facts, so no derived database is created and the wall is not
    touched. Only fields the transcription actually carried are written, so a
    confirmation that could not read the nutrition panel does not blank out a
    panel an earlier confirmation did read.
    """
    row = await _label_facts(session, barcode)
    if row is None:
        row = ProductLabelFacts(barcode=barcode)
        session.add(row)

    def _set(attribute: str, value: Any) -> None:
        if value not in (None, "", {}, []):
            setattr(row, attribute, value)

    _set("printed_name", facts.get("product_name"))
    _set("printed_brand", facts.get("brand"))
    _set("printed_ingredients", facts.get("ingredients_text"))
    _set("printed_nutrition", facts.get("nutrition_per_100g"))
    _set("printed_serving_size", facts.get("serving_size"))
    _set("printed_net_quantity", facts.get("net_quantity"))
    _set("printed_veg_mark", facts.get("veg_mark"))
    _set("printed_allergens", facts.get("allergen_text"))
    _set("transcription_confidence", facts.get("confidence"))
    uncertain = facts.get("uncertain_fields")
    if uncertain:
        row.uncertain_fields = {"fields": list(uncertain)}
    row.confirmed_at = utcnow()
    await session.flush()
    return row


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
    note: str | None = None,
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
        barcode=barcode, subject=subject[:200], reason=reason, note=note,
        photo_key=photo_key,
    )
    session.add(row)
    await session.flush()
    return row, True
