"""Stage 1 and 2 of the orchestrator: gather context, then resolve constraints.

Only **confirmed** facts become constraints.

* A profile attribute the user has not confirmed is an observation, and an
  observation must not silently decide what someone wears. Phase 2 built that
  boundary; this reads the same side of it.
* An inventory item the user has not confirmed is a draft extracted from a
  photo. Drafts are reported back so the user can confirm them, but they are
  never treated as owned — "you own this" has to be true.

Everything gathered is written to ``recommendation_inputs`` by the orchestrator,
so a run can be explained from the database alone.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem
from app.domains.profile import service as profile_service
from app.domains.profile.models import AppearanceProfile
from app.domains.recommendation.models import Look, LookFeedback, LookItem, OccasionRecord
from app.domains.recommendation.occasions import Occasion, dress_code_formality, get_occasion

# Profile attributes that legitimately shape a look. Listing them keeps the
# engine from quietly depending on some unrelated attribute later.
STYLE_ATTRIBUTES = ("preferred_style", "favourite_colours", "disliked_colours", "formality_preference", "style_experimentation", "brand_preferences")
FIT_ATTRIBUTES = ("usual_top_size", "usual_bottom_size", "preferred_coverage", "sleeve_preferences", "neckline_preferences", "footwear_comfort", "silhouettes_liked", "silhouettes_avoided")
LIFESTYLE_ATTRIBUTES = ("city", "climate", "work_context", "budget", "activity_level")
APPEARANCE_ATTRIBUTES = ("skin_tone", "undertone")


@dataclass
class OwnedItem:
    """One confirmed inventory item, flattened for scoring."""

    id: uuid.UUID
    category: str
    subcategory: Optional[str]
    display_name: str
    brand: Optional[str]
    details: dict[str, Any]
    condition: str
    usage_count: int
    last_used_at: Optional[date]
    purchase_price: Optional[float]
    currency: str

    @property
    def colour(self) -> Any:
        return self.details.get("colour")


@dataclass
class StyleContext:
    """Everything the deterministic stages are allowed to read."""

    account_id: uuid.UUID
    occasion_record: OccasionRecord
    occasion: Occasion
    confirmed_attributes: dict[str, Any] = field(default_factory=dict)
    owned: list[OwnedItem] = field(default_factory=list)
    draft_count: int = 0
    preferred_item_ids: list[uuid.UUID] = field(default_factory=list)
    disliked_item_ids: list[uuid.UUID] = field(default_factory=list)
    recently_used_item_ids: list[uuid.UUID] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)

    # --- Resolved constraints ---
    target_formality: int = 3
    dress_code: Optional[str] = None
    weather: Optional[str] = None
    setting: Optional[str] = None
    comfort_preference: Optional[str] = None
    optional_budget: Optional[float] = None

    def by_category(self, category: str) -> list[OwnedItem]:
        return [item for item in self.owned if item.category == category]

    @property
    def favourite_colours(self) -> Sequence[str]:
        return self.confirmed_attributes.get("favourite_colours") or []

    @property
    def disliked_colours(self) -> Sequence[str]:
        return self.confirmed_attributes.get("disliked_colours") or []

    @property
    def fit_preferences(self) -> dict[str, Any]:
        return {key: self.confirmed_attributes.get(key) for key in FIT_ATTRIBUTES}


async def confirmed_attributes(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    profile = (await session.execute(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))).scalar_one_or_none()
    if profile is None:
        return {}
    rows = await profile_service.attributes_for(session, profile.id)
    wanted = set(STYLE_ATTRIBUTES + FIT_ATTRIBUTES + LIFESTYLE_ATTRIBUTES + APPEARANCE_ATTRIBUTES)
    return {
        row.key: row.value
        for row in rows
        if row.key in wanted and row.verification_state == "confirmed"
    }


async def confirmed_inventory(session: AsyncSession, account_id: uuid.UUID) -> tuple[list[OwnedItem], int]:
    rows = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.account_id == account_id,
            InventoryItem.status == "active",
        )
    )).scalars().all()
    owned: list[OwnedItem] = []
    drafts = 0
    for row in rows:
        if row.verification_state != "confirmed":
            drafts += 1
            continue
        owned.append(OwnedItem(
            id=row.id, category=row.category, subcategory=row.subcategory,
            display_name=row.display_name, brand=row.brand,
            details=await inventory_service.details_for(session, row),
            condition=row.condition, usage_count=row.usage_count, last_used_at=row.last_used_at,
            purchase_price=float(row.purchase_price) if row.purchase_price is not None else None,
            currency=row.currency,
        ))
    return owned, drafts


async def _feedback_signals(session: AsyncSession, account_id: uuid.UUID) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Items the user has pushed back on, and items they recently wore.

    Rejection is a soft signal, not a ban: something disliked for a wedding may
    be exactly right for the office. It lowers a score, it does not remove an
    item.
    """
    rows = (await session.execute(
        select(LookFeedback.rating, LookItem.inventory_item_id)
        .join(Look, Look.id == LookFeedback.look_id)
        .join(LookItem, LookItem.look_id == Look.id)
        .where(LookFeedback.account_id == account_id, LookItem.inventory_item_id.is_not(None))
        .order_by(LookFeedback.created_at.desc())
        .limit(400)
    )).all()
    disliked: list[uuid.UUID] = []
    recent: list[uuid.UUID] = []
    for rating, item_id in rows:
        if rating == "not_for_me" and item_id not in disliked:
            disliked.append(item_id)
        elif rating in ("worn", "loved") and item_id not in recent:
            recent.append(item_id)
    return disliked, recent


async def gather(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    occasion_record: OccasionRecord,
    preferred_item_ids: Sequence[uuid.UUID] = (),
) -> StyleContext:
    """Stage 1: read every confirmed fact this run is allowed to use."""
    occasion = get_occasion(occasion_record.occasion_key)
    attributes = await confirmed_attributes(session, account_id)
    owned, drafts = await confirmed_inventory(session, account_id)
    disliked, recent = await _feedback_signals(session, account_id)

    owned_ids = {item.id for item in owned}
    # A preferred item the caller does not own is dropped rather than honoured.
    # Ownership is checked here, not taken from the request.
    preferred = [item_id for item_id in preferred_item_ids if item_id in owned_ids]

    context = StyleContext(
        account_id=account_id,
        occasion_record=occasion_record,
        occasion=occasion,
        confirmed_attributes=attributes,
        owned=owned,
        draft_count=drafts,
        preferred_item_ids=preferred,
        disliked_item_ids=disliked,
        recently_used_item_ids=recent,
    )
    return resolve_constraints(context)


def resolve_constraints(context: StyleContext) -> StyleContext:
    """Stage 2: turn answers and defaults into the numbers the ranker uses."""
    record = context.occasion_record
    occasion = context.occasion

    context.dress_code = record.dress_code or occasion.dress_codes[0]
    context.target_formality = dress_code_formality(record.dress_code, occasion)
    context.setting = record.setting or occasion.default_setting
    context.weather = record.weather
    context.comfort_preference = record.comfort_preference
    context.optional_budget = float(record.optional_budget) if record.optional_budget is not None else None

    # A stated formality preference nudges, but never overrides an explicit
    # dress code — being told "business formal" outranks a general preference.
    preference = context.confirmed_attributes.get("formality_preference")
    if not record.dress_code and isinstance(preference, str):
        from app.domains.recommendation.compatibility import FORMALITY_WORDS

        mapped = FORMALITY_WORDS.get(" ".join(preference.lower().split()))
        if mapped is not None:
            context.target_formality = max(1, min(5, round((context.target_formality * 2 + mapped) / 3)))

    missing: list[str] = []
    if not record.weather:
        missing.append("Expected weather was not given, so nothing was ruled out on that basis.")
    if not context.confirmed_attributes.get("favourite_colours") and not context.confirmed_attributes.get("disliked_colours"):
        missing.append("No colour preferences confirmed on your profile yet.")
    if not any(key in context.confirmed_attributes for key in ("usual_top_size", "usual_bottom_size")):
        missing.append("No usual sizes confirmed, so fit risks are limited to what each item records.")
    if context.draft_count:
        missing.append(f"{context.draft_count} inventory draft{'s are' if context.draft_count > 1 else ' is'} waiting to be confirmed and was not used.")
    context.missing_information = missing
    return context


def input_rows(context: StyleContext) -> list[dict[str, Any]]:
    """The audit trail written to ``recommendation_inputs``."""
    rows: list[dict[str, Any]] = [
        {"input_type": "occasion", "input_key": "occasion_key", "value": context.occasion.key, "source": "user_declared"},
        {"input_type": "constraint", "input_key": "target_formality", "value": context.target_formality, "source": "derived"},
        {"input_type": "constraint", "input_key": "dress_code", "value": context.dress_code, "source": "user_declared" if context.occasion_record.dress_code else "derived"},
        {"input_type": "constraint", "input_key": "setting", "value": context.setting, "source": "user_declared" if context.occasion_record.setting else "derived"},
        {"input_type": "constraint", "input_key": "weather", "value": context.weather, "source": "user_declared"},
        {"input_type": "constraint", "input_key": "comfort_preference", "value": context.comfort_preference, "source": "user_declared"},
        {"input_type": "inventory", "input_key": "confirmed_item_count", "value": len(context.owned), "source": "inventory"},
        {"input_type": "inventory", "input_key": "unconfirmed_draft_count", "value": context.draft_count, "source": "inventory"},
        {"input_type": "feedback", "input_key": "previously_rejected_item_count", "value": len(context.disliked_item_ids), "source": "feedback"},
    ]
    for key, value in sorted(context.confirmed_attributes.items()):
        rows.append({"input_type": "profile", "input_key": key, "value": value, "source": "profile_confirmed"})
    if context.preferred_item_ids:
        rows.append({"input_type": "preference", "input_key": "preferred_item_ids", "value": [str(v) for v in context.preferred_item_ids], "source": "user_declared"})
    return rows
