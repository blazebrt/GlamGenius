"""Read-only resolver for V3-05.4 Care purchase value context."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem
from app.domains.purchase.candidate_truth import build_care_candidate_truth
from app.domains.purchase.care_value import project_care_purchase_value
from app.shared.errors.exceptions import ValidationFailedError


async def resolve_care_purchase_value(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None,
) -> dict[str, Any]:
    """Resolve only account-owned, eligible low-use Care recovery rows."""
    from app.domains.purchase import service as purchase_service

    candidate = await purchase_service.owned_purchase_candidate(
        session, account_id, candidate_id
    )
    purchase_service._require_care(candidate.category)
    truth = build_care_candidate_truth(candidate)
    if not truth.facts_trusted:
        raise ValidationFailedError(
            "Review and confirm the product details first so GlamGenius does not act on an unverified label read.",
            field="verification_state",
        )

    assessment = await purchase_service.care_purchase_assessment(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=plan_date,
    )
    canonical_date = assessment["plan_date"]
    if isinstance(canonical_date, str):
        canonical_date = date.fromisoformat(canonical_date)
    redundancy = assessment.get("dimensions", {}).get("redundancy", {})
    eligible_rows = redundancy.get("eligible_owned_same_slot", ())
    eligible_ids = sorted(
        {uuid.UUID(str(row["owned_item_id"])) for row in eligible_rows if row.get("owned_item_id")},
        key=str,
    )
    items: list[InventoryItem] = []
    if eligible_ids:
        items = list(
            (
                await session.execute(
                    select(InventoryItem).where(
                        InventoryItem.account_id == account_id,
                        InventoryItem.id.in_(eligible_ids),
                    )
                )
            ).scalars().all()
        )
    item_by_id = {item.id: item for item in items}
    recovery_rows: list[dict[str, Any]] = []
    for item_id in eligible_ids:
        item = item_by_id.get(item_id)
        if item is None or not inventory_service.is_low_use(item, today=canonical_date):
            continue
        details = await inventory_service.details_for(session, item)
        recovery = inventory_service.value_to_recover(
            item, details, today=canonical_date
        )
        recovery_rows.append(recovery)

    return project_care_purchase_value(
        assessment,
        candidate_price=candidate.price,
        candidate_currency=candidate.currency,
        recovery_rows=recovery_rows,
        candidate_truth_version=truth.truth_version,
    ).as_dict()


__all__ = ["resolve_care_purchase_value"]
