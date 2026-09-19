"""Subject-aware Care preference authority for Step 11D.

The legacy InventoryAttribute rows are read only while they can be attributed
truthfully. New household state always lives in CareProductPreference.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.models import CareProductPreference
from app.domains.care.product_preferences import (
    CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
    CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
    is_effective_user_pause,
    is_effective_user_preference,
)
from app.domains.family.decision_subject import (
    DecisionSubject,
    canonicalize_decision_subject,
    canonicalize_decision_subject_for_write,
)
from app.domains.inventory.models import InventoryAttribute, InventoryItem
from app.shared.database.base import utcnow


@dataclass(frozen=True, slots=True)
class PreferenceCoverage:
    unattributed_legacy_preferences_present: bool = False

    @property
    def complete_for_subject(self) -> bool:
        return not self.unattributed_legacy_preferences_present

    def as_dict(self) -> dict[str, bool]:
        return {
            "unattributed_legacy_preferences_present": self.unattributed_legacy_preferences_present,
            "complete_for_subject": self.complete_for_subject,
        }


def _legacy_key(kind: str) -> str:
    return {
        "paused": CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
        "preferred": CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
    }[kind]


async def _legacy_rows(session: AsyncSession, item_ids: tuple[uuid.UUID, ...]) -> list[InventoryAttribute]:
    if not item_ids:
        return []
    return list((await session.execute(
        select(InventoryAttribute).where(
            InventoryAttribute.item_id.in_(item_ids),
            InventoryAttribute.key.in_((CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY, CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY)),
        )
    )).scalars().all())


async def read_preference_state(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject | None,
    item_ids: tuple[uuid.UUID, ...],
) -> tuple[frozenset[uuid.UUID], frozenset[uuid.UUID], PreferenceCoverage]:
    """Read current state without ever assigning ambiguous legacy rows."""
    subject = await canonicalize_decision_subject(
        session, principal_account_id=principal_account_id, decision_subject=decision_subject,
    )
    rows = (await session.execute(select(CareProductPreference).where(
        CareProductPreference.account_id == principal_account_id,
        CareProductPreference.household_subject_id == subject.subject_id,
        CareProductPreference.inventory_item_id.in_(item_ids) if item_ids else False,
    ))).scalars().all() if item_ids else []
    paused = {row.inventory_item_id for row in rows if row.preference_kind == "paused"}
    preferred = {row.inventory_item_id for row in rows if row.preference_kind == "preferred"}

    legacy = await _legacy_rows(session, item_ids)
    ambiguous = False
    for row in legacy:
        if subject.circle_created_at is not None and (
            row.updated_at is None or row.updated_at >= subject.circle_created_at
        ):
            ambiguous = True
            continue
        if not subject.is_account_holder:
            continue
        if row.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY and is_effective_user_pause(
            value=row.value, source=row.source, verification_state=row.verification_state,
        ):
            paused.add(row.item_id)
        if row.key == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY and is_effective_user_preference(
            value=row.value, source=row.source, verification_state=row.verification_state,
        ):
            preferred.add(row.item_id)
    return frozenset(paused), frozenset(preferred), PreferenceCoverage(ambiguous)


async def set_preference(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    decision_subject: DecisionSubject | None,
    item_id: uuid.UUID,
    kind: str,
    active: bool,
    authority_source: str = "direct_user",
) -> DecisionSubject:
    """Set one subject preference, adopting only safe legacy current state."""
    if kind not in {"paused", "preferred"}:
        raise ValueError("unsupported Care preference kind")
    subject = await canonicalize_decision_subject_for_write(
        session, principal_account_id=principal_account_id, decision_subject=decision_subject,
    )
    item = (await session.execute(select(InventoryItem).where(
        InventoryItem.id == item_id, InventoryItem.account_id == principal_account_id,
    ))).scalar_one_or_none()
    if item is None:
        raise ValueError("inventory item is not owned by the authenticated account")
    existing = (await session.execute(select(CareProductPreference).where(
        CareProductPreference.account_id == principal_account_id,
        CareProductPreference.household_subject_id == subject.subject_id,
        CareProductPreference.inventory_item_id == item_id,
        CareProductPreference.preference_kind == kind,
    ))).scalar_one_or_none()
    legacy = (await session.execute(select(InventoryAttribute).where(
        InventoryAttribute.item_id == item_id, InventoryAttribute.key == _legacy_key(kind),
    ))).scalar_one_or_none()
    safe_legacy = (
        legacy is not None and subject.is_account_holder and
        (subject.circle_created_at is None or (
            legacy.updated_at is not None and legacy.updated_at < subject.circle_created_at
        )) and bool(legacy.value is True) and legacy.source == "user_declared" and legacy.verification_state == "confirmed"
    )
    if existing is not None and safe_legacy:
        raise RuntimeError("Care preference has both legacy and subject-aware current state")
    if active:
        if kind == "preferred":
            # Same-slot replacement is personal state. It never clears a
            # different subject's preference for the same shared bottle.
            from app.domains.routines import shelf as shelf_domain
            context = await shelf_domain.gather(
                session, account_id=principal_account_id, decision_subject=subject,
            )
            target = next(
                (product for category in ("beauty", "hair")
                 for product in shelf_domain.build(context, category)
                 if product.item.id == item_id),
                None,
            )
            if target is not None and target.slot is not None:
                current_preferred = (await session.execute(select(CareProductPreference).where(
                    CareProductPreference.account_id == principal_account_id,
                    CareProductPreference.household_subject_id == subject.subject_id,
                    CareProductPreference.preference_kind == "preferred",
                    CareProductPreference.inventory_item_id != item_id,
                ))).scalars().all()
                for other in current_preferred:
                    other_product = next(
                        (product for category in ("beauty", "hair")
                         for product in shelf_domain.build(context, category)
                         if product.item.id == other.inventory_item_id),
                        None,
                    )
                    if other_product is not None and other_product.slot == target.slot:
                        await session.delete(other)
        if existing is None:
            existing = CareProductPreference(
                account_id=principal_account_id,
                household_subject_id=subject.subject_id,
                inventory_item_id=item_id,
                preference_kind=kind,
                authority_source="legacy_adopted" if safe_legacy else authority_source,
            )
            session.add(existing)
        else:
            existing.authority_source = authority_source
            existing.updated_at = utcnow()
        if safe_legacy:
            await session.delete(legacy)
    else:
        if existing is not None:
            await session.delete(existing)
        if legacy is not None and safe_legacy:
            await session.delete(legacy)
    await session.flush()
    return subject


__all__ = ["PreferenceCoverage", "read_preference_state", "set_preference"]
