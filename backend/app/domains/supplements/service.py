"""Persistence wrapper around the pure supplement utility engine."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryItem, SupplementDetail
from app.domains.supplements.engine import build_utility, component_identity
from app.domains.supplements.models import SupplementLabelComponent
from app.domains.supplements.schemas import LabelComponentCreate, LabelComponentPatch
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError


def _amount_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


async def owned_supplement_item(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
    item = (await session.execute(select(InventoryItem).where(
        InventoryItem.id == item_id,
        InventoryItem.account_id == account_id,
        InventoryItem.category == "supplements",
        InventoryItem.status != "archived",
    ))).scalar_one_or_none()
    if item is None:
        raise NotFoundError("We could not find that supplement.")
    return item


async def _facts(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID | None = None) -> list[SupplementLabelComponent]:
    stmt = select(SupplementLabelComponent).where(SupplementLabelComponent.account_id == account_id)
    if item_id is not None:
        stmt = stmt.where(SupplementLabelComponent.item_id == item_id)
    return list((await session.execute(stmt.order_by(SupplementLabelComponent.raw_name.asc(), SupplementLabelComponent.id.asc()))).scalars().all())


def serialize_fact(row: SupplementLabelComponent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "inventory_item_id": str(row.item_id),
        "raw_name": row.raw_name,
        "normalized_name": row.normalized_name,
        "canonical_component_key": row.canonical_component_key,
        "amount": _amount_text(row.amount),
        "unit": row.unit,
        "serving_text": row.serving_text,
        "source": row.source,
        "verification_state": row.verification_state,
        "confidence": row.confidence,
        "schema_version": row.schema_version,
    }


async def list_facts(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID) -> list[dict[str, Any]]:
    await owned_supplement_item(session, account_id, item_id)
    return [serialize_fact(row) for row in await _facts(session, account_id, item_id)]


async def create_fact(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID, body: LabelComponentCreate) -> SupplementLabelComponent:
    item = await owned_supplement_item(session, account_id, item_id)
    canonical, _display = component_identity(body.raw_name)
    values = {
        "account_id": account_id, "item_id": item.id, "raw_name": body.raw_name.strip(),
        "normalized_name": canonical, "canonical_component_key": canonical or None,
        "amount": body.amount, "unit": body.unit.strip() if body.unit else None,
        "serving_text": body.serving_text.strip() if body.serving_text else None,
        # The public endpoint is manual entry only; provenance is server-owned.
        "source": "user_declared", "verification_state": "confirmed", "confidence": 1.0,
        "source_ai_run_id": None, "model_version": None, "prompt_version": None,
        "client_mutation_id": body.client_mutation_id,
    }
    if body.client_mutation_id is None:
        row = SupplementLabelComponent(**values)
        session.add(row)
        await session.flush()
        return row

    statement = pg_insert(SupplementLabelComponent).values(**values).on_conflict_do_nothing(
        index_elements=["account_id", "client_mutation_id"]
    ).returning(SupplementLabelComponent.id)
    created_id = (await session.execute(statement)).scalar_one_or_none()
    if created_id is not None:
        return (await session.execute(select(SupplementLabelComponent).where(
            SupplementLabelComponent.id == created_id,
        ))).scalar_one()

    replay = (await session.execute(select(SupplementLabelComponent).where(
        SupplementLabelComponent.account_id == account_id,
        SupplementLabelComponent.client_mutation_id == body.client_mutation_id,
    ))).scalar_one()
    replay_values = (replay.item_id, replay.raw_name, replay.normalized_name,
                     replay.canonical_component_key, replay.amount, replay.unit, replay.serving_text)
    requested_values = (item.id, values["raw_name"], values["normalized_name"],
                        values["canonical_component_key"], values["amount"], values["unit"], values["serving_text"])
    if replay_values != requested_values:
        raise ValidationFailedError("This label-fact submission key is already used for different data.", field="client_mutation_id")
    return replay


async def update_fact(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID, fact_id: uuid.UUID, body: LabelComponentPatch) -> SupplementLabelComponent:
    await owned_supplement_item(session, account_id, item_id)
    row = (await session.execute(select(SupplementLabelComponent).where(
        SupplementLabelComponent.id == fact_id,
        SupplementLabelComponent.account_id == account_id,
        SupplementLabelComponent.item_id == item_id,
    ))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that label fact.")
    values = body.model_dump(exclude_unset=True)
    if "raw_name" in values:
        canonical, _display = component_identity(values["raw_name"])
        row.raw_name = values["raw_name"].strip()
        row.normalized_name = canonical
        row.canonical_component_key = canonical or None
        values.pop("raw_name")
    for key, value in values.items():
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    await session.flush()
    return row


async def delete_fact(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID, fact_id: uuid.UUID) -> None:
    await owned_supplement_item(session, account_id, item_id)
    result = await session.execute(delete(SupplementLabelComponent).where(
        SupplementLabelComponent.id == fact_id,
        SupplementLabelComponent.account_id == account_id,
        SupplementLabelComponent.item_id == item_id,
    ))
    if not result.rowcount:
        raise NotFoundError("We could not find that label fact.")


async def confirm_fact(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID, fact_id: uuid.UUID, confirmed: bool = True) -> SupplementLabelComponent:
    await owned_supplement_item(session, account_id, item_id)
    row = (await session.execute(select(SupplementLabelComponent).where(
        SupplementLabelComponent.id == fact_id,
        SupplementLabelComponent.account_id == account_id,
        SupplementLabelComponent.item_id == item_id,
    ))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that label fact.")
    row.verification_state = "confirmed" if confirmed else "draft"
    await session.flush()
    return row


async def summary(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    items = list((await session.execute(select(InventoryItem).where(
        InventoryItem.account_id == account_id,
        InventoryItem.category == "supplements",
        InventoryItem.status != "archived",
    ).order_by(InventoryItem.display_name.asc(), InventoryItem.id.asc()))).scalars().all())
    item_ids = [item.id for item in items]
    facts = await _facts(session, account_id)
    facts_by_item: dict[uuid.UUID, list[SupplementLabelComponent]] = {item_id: [] for item_id in item_ids}
    for fact in facts:
        if fact.item_id in facts_by_item:
            facts_by_item[fact.item_id].append(fact)
    details = list((await session.execute(select(SupplementDetail).where(SupplementDetail.item_id.in_(item_ids)))).scalars().all()) if item_ids else []
    detail_by_item = {row.item_id: row for row in details}
    payload_items = []
    for item in items:
        detail = detail_by_item.get(item.id)
        payload_items.append({
            "id": str(item.id), "display_name": item.display_name, "brand": item.brand,
            "verification_state": item.verification_state,
            "user_entered_purpose": detail.user_entered_purpose if detail else None,
            "expiry_date": detail.expiry_date if detail else None,
            "use_frequency": detail.use_frequency if detail else None,
            "facts": facts_by_item[item.id],
        })
    return build_utility(payload_items)
