"""Inventory ownership, capture, search and deterministic intelligence."""
from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import (
    AccessoryItemDetail, BeautyProductDetail, DuplicateCandidate, HairProductDetail,
    InventoryAttribute, InventoryCategory, InventoryEvent, InventoryItem,
    InventoryItemImage, InventoryValueEvent, ItemConditionEvent, ItemExpiryEvent,
    ItemRelationship, ItemUsageEvent, PerfumeDetail, ShoeItemDetail, SupplementDetail,
    WardrobeItemDetail,
)
from app.domains.inventory.schemas import ItemCreate, ItemPatch
from app.domains.inventory.taxonomy import CATEGORIES, SUPPLEMENT_DISCLAIMER, iter_search_values, normalise_text, validate_attribute_keys, validate_details
from app.domains.media import service as media_service
from app.domains.media.models import MEDIA_PURPOSE_INVENTORY
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.shared.events import outbox

DETAIL_MODELS = {
    "wardrobe": WardrobeItemDetail, "shoes": ShoeItemDetail,
    "accessories": AccessoryItemDetail, "beauty": BeautyProductDetail,
    "hair": HairProductDetail, "perfumes": PerfumeDetail,
    "supplements": SupplementDetail,
}
LIST_SORTS = {
    "newest": InventoryItem.created_at.desc(), "oldest": InventoryItem.created_at.asc(),
    "name": InventoryItem.display_name.asc(), "most_used": InventoryItem.usage_count.desc(),
    "least_used": InventoryItem.usage_count.asc(), "recently_used": InventoryItem.last_used_at.desc().nullslast(),
}


async def ensure_categories(session: AsyncSession) -> None:
    existing = set((await session.execute(select(InventoryCategory.key))).scalars().all())
    for position, (key, label) in enumerate(CATEGORIES.items(), start=1):
        if key not in existing:
            session.add(InventoryCategory(key=key, display_name=label, position=position))
    await session.flush()


async def owned_item(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID, *, include_archived: bool = False) -> InventoryItem:
    stmt = select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.account_id == account_id)
    if not include_archived:
        stmt = stmt.where(InventoryItem.status != "archived")
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise NotFoundError("We could not find that inventory item.")
    return item


async def _detail_row(session: AsyncSession, item: InventoryItem, *, create: bool = False):
    model = DETAIL_MODELS[item.category]
    row = (await session.execute(select(model).where(model.item_id == item.id))).scalar_one_or_none()
    if row is None and create:
        fields: Dict[str, Any] = {"item_id": item.id}
        if item.category == "supplements":
            fields["safety_disclaimer"] = SUPPLEMENT_DISCLAIMER
        row = model(**fields)
        session.add(row)
        await session.flush()
    return row


def _coerce_detail(key: str, value: Any) -> Any:
    if key in {"opened_date", "expiry_date"} and isinstance(value, str):
        return date.fromisoformat(value)
    return value


async def sync_details(session: AsyncSession, item: InventoryItem, details: Dict[str, Any]) -> None:
    cleaned = validate_details(item.category, details)
    if not cleaned:
        return
    row = await _detail_row(session, item, create=True)
    for key, value in cleaned.items():
        coerced = _coerce_detail(key, value)
        if key == "expiry_date" and coerced and getattr(row, key) != coerced:
            session.add(ItemExpiryEvent(item_id=item.id, expiry_date=coerced, source=item.source))
        setattr(row, key, coerced)
    if item.category == "supplements":
        row.safety_disclaimer = SUPPLEMENT_DISCLAIMER
    await session.flush()


async def _attribute_map(session: AsyncSession, item_id: uuid.UUID) -> Dict[str, InventoryAttribute]:
    rows = (await session.execute(select(InventoryAttribute).where(InventoryAttribute.item_id == item_id))).scalars().all()
    return {row.key: row for row in rows}


async def sync_attributes(
    session: AsyncSession,
    item: InventoryItem,
    values: Iterable[tuple[str, Any]],
    *,
    source: str,
    confidence: float,
    verification_state: str,
) -> None:
    pairs = list(values)
    validate_attribute_keys(item.category, (key for key, _ in pairs))
    existing = await _attribute_map(session, item.id)
    for key, value in pairs:
        if value in (None, "", []):
            continue
        row = existing.get(key)
        if row is None:
            row = InventoryAttribute(item_id=item.id, key=key, value=value, source=source, confidence=confidence, verification_state=verification_state, source_ai_run_id=item.source_ai_run_id, model_version=item.model_version, prompt_version=item.prompt_version, schema_version=item.schema_version)
            session.add(row); existing[key] = row
        else:
            row.value = value; row.source = source; row.confidence = confidence; row.verification_state = verification_state
            row.source_ai_run_id = item.source_ai_run_id; row.model_version = item.model_version; row.prompt_version = item.prompt_version; row.schema_version = item.schema_version


async def set_images(session: AsyncSession, item: InventoryItem, account_id: uuid.UUID, image_ids: Sequence[uuid.UUID]) -> None:
    current = list((await session.execute(select(InventoryItemImage).where(InventoryItemImage.item_id == item.id))).scalars().all())
    wanted = set(image_ids)
    for link in current:
        if link.media_asset_id not in wanted:
            await session.delete(link)
    present = {link.media_asset_id for link in current}
    for position, asset_id in enumerate(image_ids):
        asset = await media_service.get_owned_asset(session, account_id=account_id, asset_id=asset_id)
        if asset.purpose != MEDIA_PURPOSE_INVENTORY:
            raise ValidationFailedError("Only inventory photos can be attached.", field="image_ids")
        if asset_id not in present:
            session.add(InventoryItemImage(item_id=item.id, media_asset_id=asset_id, position=position))


async def record_event(session: AsyncSession, item: InventoryItem, event_type: str, payload: Optional[Dict[str, Any]] = None, actor: str = "user") -> None:
    session.add(InventoryEvent(account_id=item.account_id, item_id=item.id, event_type=event_type, actor=actor, payload=payload or {}))
    await outbox.publish(session, aggregate_type="inventory_item", aggregate_id=str(item.id), event_type=f"inventory.{event_type}", payload={"account_id": str(item.account_id), **(payload or {})})


async def detect_duplicates(session: AsyncSession, item: InventoryItem) -> None:
    name = normalise_text(item.display_name); brand = normalise_text(item.brand)
    candidates = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == item.account_id, InventoryItem.category == item.category, InventoryItem.id != item.id, InventoryItem.status != "archived"))).scalars().all()
    for other in candidates:
        if name and name == normalise_text(other.display_name) and brand == normalise_text(other.brand):
            a, b = sorted([item.id, other.id], key=str)
            exists_row = (await session.execute(select(DuplicateCandidate).where(DuplicateCandidate.account_id == item.account_id, DuplicateCandidate.item_a_id == a, DuplicateCandidate.item_b_id == b))).scalar_one_or_none()
            if exists_row is None:
                session.add(DuplicateCandidate(account_id=item.account_id, item_a_id=a, item_b_id=b, confidence=0.95 if brand else 0.85, reason="Same category, name and brand", status="pending"))


async def create_item(
    session: AsyncSession,
    account_id: uuid.UUID,
    body: ItemCreate,
    *, source: str = "user_declared", verification_state: str = "confirmed", confidence: float = 1.0,
    ai_run_id: Optional[uuid.UUID] = None, model_version: Optional[str] = None,
    prompt_version: Optional[str] = None, schema_version: Optional[str] = None,
) -> InventoryItem:
    await ensure_categories(session)
    if body.client_mutation_id:
        replay = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == account_id, InventoryItem.client_mutation_id == body.client_mutation_id))).scalar_one_or_none()
        if replay is not None:
            return replay
    item = InventoryItem(account_id=account_id, category=body.category, subcategory=body.subcategory, display_name=body.display_name.strip(), brand=body.brand.strip() if body.brand else None, source=source, verification_state=verification_state, confidence=confidence, purchase_date=body.purchase_date, purchase_price=body.purchase_price, currency=body.currency.upper(), condition=body.condition, replacement_priority=body.replacement_priority, client_mutation_id=body.client_mutation_id, source_ai_run_id=ai_run_id, model_version=model_version, prompt_version=prompt_version, schema_version=schema_version)
    session.add(item); await session.flush()
    await sync_details(session, item, body.details)
    attr_values = [(row.key, row.value) for row in body.attributes]
    attr_values.extend(iter_search_values(body.details)); attr_values.extend([("brand", item.brand), ("condition", item.condition)])
    await sync_attributes(session, item, attr_values, source=source, confidence=confidence, verification_state=verification_state)
    await set_images(session, item, account_id, body.image_ids)
    await detect_duplicates(session, item)
    await record_event(session, item, "created", {"category": item.category, "verification_state": verification_state}, actor="ai" if source == "photo_extracted" else "user")
    await session.flush(); return item


async def update_item(session: AsyncSession, item: InventoryItem, body: ItemPatch) -> InventoryItem:
    if body.expected_version is not None and body.expected_version != item.version:
        raise ConflictError("This item changed on another device. Refresh it before saving.", current_version=item.version)
    fields = body.model_dump(exclude_unset=True, exclude={"expected_version", "details", "attributes", "image_ids"})
    for key, value in fields.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    if body.details:
        await sync_details(session, item, body.details)
    attr_values = [(row.key, row.value) for row in body.attributes]
    attr_values.extend(iter_search_values(body.details))
    if "brand" in fields: attr_values.append(("brand", item.brand))
    if "condition" in fields: attr_values.append(("condition", item.condition))
    await sync_attributes(session, item, attr_values, source="user_declared", confidence=1.0, verification_state="confirmed")
    if body.image_ids is not None:
        await set_images(session, item, item.account_id, body.image_ids)
    item.version += 1; item.updated_at = utcnow()
    await record_event(session, item, "updated", {"version": item.version, "fields": sorted(fields.keys() | body.details.keys())})
    await detect_duplicates(session, item); await session.flush(); return item


async def confirm_item(session: AsyncSession, item: InventoryItem) -> InventoryItem:
    if item.verification_state == "confirmed":
        return item
    item.verification_state = "confirmed"; item.confidence = 1.0; item.version += 1; item.updated_at = utcnow()
    rows = (await session.execute(select(InventoryAttribute).where(InventoryAttribute.item_id == item.id))).scalars().all()
    for row in rows:
        row.verification_state = "confirmed"
    await record_event(session, item, "confirmed", {"version": item.version})
    await session.flush(); return item


async def archive_item(session: AsyncSession, item: InventoryItem) -> None:
    item.status = "archived"; item.version += 1; item.updated_at = utcnow()
    await record_event(session, item, "archived")


async def log_usage(session: AsyncSession, item: InventoryItem, used_on: date, quantity: int, note: Optional[str]) -> None:
    session.add(ItemUsageEvent(item_id=item.id, used_on=used_on, quantity=quantity, note=note))
    item.usage_count += quantity
    if item.last_used_at is None or used_on > item.last_used_at: item.last_used_at = used_on
    item.version += 1; item.updated_at = utcnow(); await record_event(session, item, "usage_logged", {"used_on": used_on.isoformat(), "quantity": quantity})


async def log_condition(session: AsyncSession, item: InventoryItem, condition: str, note: Optional[str]) -> None:
    session.add(ItemConditionEvent(item_id=item.id, condition=condition, note=note)); item.condition = condition; item.version += 1; item.updated_at = utcnow()
    await sync_attributes(session, item, [("condition", condition)], source="user_declared", confidence=1.0, verification_state="confirmed")
    await record_event(session, item, "condition_changed", {"condition": condition})


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months; year = value.year + month_index // 12; month = month_index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def effective_expiry_from_details(details: Dict[str, Any]) -> Optional[date]:
    explicit = details.get("expiry_date")
    if isinstance(explicit, str): explicit = date.fromisoformat(explicit)
    opened = details.get("opened_date")
    if isinstance(opened, str): opened = date.fromisoformat(opened)
    pao = details.get("period_after_opening_months")
    computed = add_months(opened, int(pao)) if opened and pao else None
    return min(v for v in [explicit, computed] if v is not None) if explicit or computed else None


def is_low_use(item: InventoryItem, today: Optional[date] = None) -> bool:
    now = today or date.today(); created = item.created_at.date() if item.created_at else now
    return item.status == "active" and (now - created).days >= 30 and item.usage_count <= 2 and (item.last_used_at is None or (now - item.last_used_at).days >= 30)


def value_to_recover(item: InventoryItem, details: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    now = today or date.today(); expiry = effective_expiry_from_details(details)
    remaining = details.get("remaining_percent")
    remaining_ratio = Decimal(str(remaining / 100 if remaining is not None else max(0.1, 1 - min(item.usage_count, 30) / 30)))
    condition_factor = Decimal(str({"excellent": 1, "good": .8, "fair": .55, "worn": .3, "needs_attention": .4}.get(item.condition, .7)))
    days_idle = (now - item.last_used_at).days if item.last_used_at else max(30, (now - item.created_at.date()).days if item.created_at else 30)
    inactivity_factor = Decimal("1") if days_idle >= 90 else Decimal("0.7") if days_idle >= 30 else Decimal("0.3")
    days_to_expiry = (expiry - now).days if expiry else None
    expiry_factor = Decimal("1") if days_to_expiry is not None and days_to_expiry <= 30 else Decimal("0.8") if days_to_expiry is not None and days_to_expiry <= 90 else Decimal("0.5")
    inputs = {"purchase_price": float(item.purchase_price) if item.purchase_price is not None else None, "remaining_estimate": round(float(remaining_ratio), 3), "usage_count": item.usage_count, "days_since_last_use": days_idle, "condition": item.condition, "days_to_expiry": days_to_expiry}
    if item.purchase_price is None:
        return {"item_id": str(item.id), "display_name": item.display_name, "estimated_value": None, "currency": item.currency, "is_estimate": True, "metric_version": "v1", "inputs": inputs, "missing_inputs": ["purchase_price"], "explanation": "Add a purchase price to estimate Value to Recover. This estimate is never exact."}
    value = (Decimal(item.purchase_price) * remaining_ratio * condition_factor * inactivity_factor * expiry_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"item_id": str(item.id), "display_name": item.display_name, "estimated_value": float(value), "currency": item.currency, "is_estimate": True, "metric_version": "v1", "inputs": inputs, "missing_inputs": [], "explanation": "Estimated from entered price, remaining amount or usage, condition, time since use and expiry proximity. It is not an exact resale or waste value."}


def _plain_details(row: Any) -> Dict[str, Any]:
    if row is None: return {}
    hidden = {"id", "item_id", "created_at", "updated_at", "safety_disclaimer"}
    result: Dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name in hidden: continue
        value = getattr(row, column.name)
        if isinstance(value, (date, datetime)): value = value.isoformat()
        if value not in (None, [], ""): result[column.name] = value
    if hasattr(row, "safety_disclaimer"): result["safety_disclaimer"] = row.safety_disclaimer
    return result


async def details_for(session: AsyncSession, item: InventoryItem) -> Dict[str, Any]:
    return _plain_details(await _detail_row(session, item))


async def serialize_item(session: AsyncSession, item: InventoryItem, *, include_history: bool = False) -> Dict[str, Any]:
    details = await details_for(session, item)
    attributes = (await session.execute(select(InventoryAttribute).where(InventoryAttribute.item_id == item.id).order_by(InventoryAttribute.key))).scalars().all()
    images = (await session.execute(select(InventoryItemImage).where(InventoryItemImage.item_id == item.id).order_by(InventoryItemImage.position))).scalars().all()
    body: Dict[str, Any] = {
        "id": str(item.id), "category": item.category, "subcategory": item.subcategory, "display_name": item.display_name,
        "brand": item.brand, "source": item.source, "verification_state": item.verification_state, "confidence": item.confidence,
        "status": item.status, "purchase_date": item.purchase_date.isoformat() if item.purchase_date else None,
        "purchase_price": float(item.purchase_price) if item.purchase_price is not None else None, "currency": item.currency,
        "usage_count": item.usage_count, "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
        "condition": item.condition, "replacement_priority": item.replacement_priority, "version": item.version,
        "details": details, "effective_expiry": effective_expiry_from_details(details).isoformat() if effective_expiry_from_details(details) else None,
        "low_use": is_low_use(item), "image_ids": [str(link.media_asset_id) for link in images],
        "attributes": [{"key": row.key, "value": row.value, "source": row.source, "confidence": row.confidence, "verification_state": row.verification_state, "model_version": row.model_version, "prompt_version": row.prompt_version, "schema_version": row.schema_version, "source_ai_run_id": str(row.source_ai_run_id) if row.source_ai_run_id else None} for row in attributes],
        "created_at": item.created_at.isoformat() if item.created_at else None, "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    if include_history:
        events = (await session.execute(select(InventoryEvent).where(InventoryEvent.item_id == item.id).order_by(InventoryEvent.created_at.desc()).limit(100))).scalars().all()
        body["history"] = [{"event_type": event.event_type, "actor": event.actor, "payload": event.payload, "created_at": event.created_at.isoformat() if event.created_at else None} for event in events]
    return body


def _attr_filter(key: str, value: str):
    return exists(select(InventoryAttribute.id).where(InventoryAttribute.item_id == InventoryItem.id, InventoryAttribute.key == key, cast(InventoryAttribute.value, String).ilike(f"%{value}%")))


async def list_items(session: AsyncSession, account_id: uuid.UUID, *, page: int = 1, page_size: int = 24, q: Optional[str] = None, category: Optional[str] = None, brand: Optional[str] = None, colour: Optional[str] = None, ingredient: Optional[str] = None, occasion: Optional[str] = None, season: Optional[str] = None, condition: Optional[str] = None, expiry_status: Optional[str] = None, usage_level: Optional[str] = None, verification_state: Optional[str] = None, sort: str = "newest") -> Dict[str, Any]:
    stmt = select(InventoryItem).where(InventoryItem.account_id == account_id, InventoryItem.status != "archived")
    if q: stmt = stmt.where(or_(InventoryItem.display_name.ilike(f"%{q}%"), InventoryItem.brand.ilike(f"%{q}%"), InventoryItem.subcategory.ilike(f"%{q}%"), _attr_filter("ingredients_text", q), _attr_filter("active_ingredients", q)))
    if category: stmt = stmt.where(InventoryItem.category == category)
    if brand: stmt = stmt.where(InventoryItem.brand.ilike(f"%{brand}%"))
    if colour: stmt = stmt.where(_attr_filter("colour", colour))
    if ingredient: stmt = stmt.where(or_(_attr_filter("ingredients_text", ingredient), _attr_filter("active_ingredients", ingredient)))
    if occasion: stmt = stmt.where(_attr_filter("occasion", occasion))
    if season: stmt = stmt.where(_attr_filter("season", season))
    if condition: stmt = stmt.where(InventoryItem.condition == condition)
    if verification_state: stmt = stmt.where(InventoryItem.verification_state == verification_state)
    if usage_level == "unused": stmt = stmt.where(InventoryItem.usage_count == 0)
    elif usage_level == "low": stmt = stmt.where(InventoryItem.usage_count <= 2)
    elif usage_level == "regular": stmt = stmt.where(InventoryItem.usage_count >= 3)
    ordered = stmt.order_by(LIST_SORTS.get(sort, LIST_SORTS["newest"]))
    if expiry_status:
        candidates = list((await session.execute(ordered)).scalars().all()); today = date.today(); rows = []
        for item in candidates:
            expiry = effective_expiry_from_details(await details_for(session, item)); days = (expiry - today).days if expiry else None
            matches = expiry_status == "missing" and expiry is None or expiry_status == "expired" and days is not None and days < 0 or expiry_status == "expiring_soon" and days is not None and 0 <= days <= 90 or expiry_status == "current" and days is not None and days > 90
            if matches: rows.append(item)
        total = len(rows); rows = rows[(page - 1) * page_size:page * page_size]
    else:
        total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
        rows = (await session.execute(ordered.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [await serialize_item(session, row) for row in rows], "pagination": {"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size}}


async def expiring_items(session: AsyncSession, account_id: uuid.UUID, days: int = 90) -> List[Dict[str, Any]]:
    rows = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == account_id, InventoryItem.status != "archived"))).scalars().all(); today = date.today(); result = []
    for item in rows:
        details = await details_for(session, item); expiry = effective_expiry_from_details(details)
        if expiry and (expiry - today).days <= days:
            body = await serialize_item(session, item); body["days_to_expiry"] = (expiry - today).days; body["expiry_status"] = "expired" if expiry < today else "expiring_soon"; result.append(body)
    return sorted(result, key=lambda row: row["effective_expiry"])


async def low_use_items(session: AsyncSession, account_id: uuid.UUID) -> List[Dict[str, Any]]:
    rows = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == account_id, InventoryItem.status != "archived"))).scalars().all()
    return [await serialize_item(session, item) for item in rows if is_low_use(item)]


async def value_report(session: AsyncSession, account_id: uuid.UUID, *, record: bool = False) -> Dict[str, Any]:
    rows = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == account_id, InventoryItem.status != "archived"))).scalars().all(); estimates = []
    for item in rows:
        estimate = value_to_recover(item, await details_for(session, item)); estimates.append(estimate)
        if record: session.add(InventoryValueEvent(item_id=item.id, metric_version="v1", estimated_value=estimate["estimated_value"], currency=item.currency, inputs=estimate["inputs"], explanation=estimate["explanation"]))
    known = [Decimal(str(row["estimated_value"])) for row in estimates if row["estimated_value"] is not None]
    return {"label": "Value to Recover", "estimated_total": float(sum(known, Decimal("0"))), "currency": "INR", "is_estimate": True, "metric_version": "v1", "items": estimates, "explanation": "A transparent estimate using only entered price, remaining amount or usage, condition, inactivity and expiry. Missing prices are excluded; this is never exact."}


async def duplicates(session: AsyncSession, account_id: uuid.UUID) -> List[Dict[str, Any]]:
    rows = (await session.execute(select(DuplicateCandidate).where(DuplicateCandidate.account_id == account_id, DuplicateCandidate.status == "pending").order_by(DuplicateCandidate.created_at.desc()))).scalars().all(); result = []
    for row in rows:
        a = await owned_item(session, account_id, row.item_a_id); b = await owned_item(session, account_id, row.item_b_id)
        result.append({"id": str(row.id), "confidence": row.confidence, "reason": row.reason, "status": row.status, "item_a": await serialize_item(session, a), "item_b": await serialize_item(session, b)})
    return result


async def resolve_duplicate(session: AsyncSession, account_id: uuid.UUID, candidate_id: uuid.UUID, resolution: str, canonical_item_id: Optional[uuid.UUID]) -> DuplicateCandidate:
    row = (await session.execute(select(DuplicateCandidate).where(DuplicateCandidate.id == candidate_id, DuplicateCandidate.account_id == account_id))).scalar_one_or_none()
    if row is None: raise NotFoundError("We could not find that duplicate candidate.")
    if resolution == "merge":
        if canonical_item_id not in {row.item_a_id, row.item_b_id}: raise ValidationFailedError("Choose one of the candidate items to keep.", field="canonical_item_id")
        other_id = row.item_b_id if canonical_item_id == row.item_a_id else row.item_a_id; other = await owned_item(session, account_id, other_id); other.status = "archived"
        session.add(ItemRelationship(account_id=account_id, from_item_id=other_id, to_item_id=canonical_item_id, relationship_type="merged_into"))
    row.status = "resolved"; row.resolution = resolution; row.resolved_at = utcnow(); return row


async def summary(session: AsyncSession, account_id: uuid.UUID) -> Dict[str, Any]:
    rows = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == account_id, InventoryItem.status != "archived"))).scalars().all(); counts = {key: 0 for key in CATEGORIES}
    for item in rows: counts[item.category] += 1
    low = sum(1 for item in rows if is_low_use(item)); expiring = len(await expiring_items(session, account_id)); dupes = len(await duplicates(session, account_id)); values = await value_report(session, account_id)
    known_prices = sum(1 for item in rows if item.purchase_price is not None); used = sum(1 for item in rows if item.usage_count > 0)
    return {"total_items": len(rows), "categories": counts, "low_use_products": low, "products_expiring_soon": expiring, "products_needing_attention": low + expiring + dupes, "duplicate_candidates": dupes, "at_risk_value": values["estimated_total"], "currency": "INR", "inventory_balance": {"metric_version": "v1", "visible_inputs": counts, "explanation": "Category counts show where your inventory is concentrated; no universal score is assigned."}, "purchase_efficiency": {"metric_version": "v1", "items_used": used, "items_with_price": known_prices, "explanation": "Shows how many catalogued items have been used and how complete price data is; it is not a judgement."}}
