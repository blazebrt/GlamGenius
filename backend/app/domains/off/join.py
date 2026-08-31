"""Where Store A and Store B meet: in memory, on barcode, at query time.

This is the only sanctioned contact between them, and the shape matters. Each
store is read separately, the two results are held side by side in a response
object, and that object is discarded when the request ends. Nothing is written
back to either store, so no derived database comes into being and ODbL's
share-alike clause is never triggered.

What would trigger it: a table holding an Open Food Facts field next to a
proprietary one, a materialised view across the two, or a scheduled job that
writes the joined result anywhere. None of those exist, and ``wall.py`` is
there to keep it that way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.off.attribution import attribution
from app.domains.off.models import OffProduct


@dataclass(frozen=True)
class JoinedProduct:
    """One product, with each store's contribution kept separate and labelled.

    The two halves stay in their own keys rather than being merged into one
    flat object, so it is obvious at every call site which store a value came
    from — and so nobody can write the whole thing back to one table by
    accident.
    """

    barcode: str
    off: dict[str, Any] | None
    proprietary: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "barcode": self.barcode,
            # Open Food Facts data, under ODbL, attributed.
            "open_food_facts": self.off,
            "attribution": attribution() if self.off else None,
            # Ours. Not derived from the above and not redistributed with it.
            "glamgenius": self.proprietary,
        }


def _serialise(product: OffProduct) -> dict[str, Any]:
    return {
        "product_name": product.product_name,
        "brands": product.brands,
        "ingredients_text": product.ingredients_text,
        "nutriments": product.nutriments,
        "categories": product.categories,
        "image_url": product.image_url,
        "quantity": product.quantity,
        "countries": product.countries,
    }


async def read_off_product(session: AsyncSession, barcode: str) -> dict[str, Any] | None:
    """Read one product from Store A. Uses Store A's session, never Store B's."""
    record, _fetched_at = await read_off_product_with_age(session, barcode)
    return record


async def read_off_product_with_age(
    session: AsyncSession, barcode: str,
) -> tuple[dict[str, Any] | None, datetime | None]:
    """The record and when we last fetched it, for deciding whether to refresh.

    ``fetched_at`` is ours in the sense that we recorded it, but it is a fact
    about the cached copy rather than anything proprietary, so it stays in
    Store A and never travels with the record into a response.
    """
    product = (await session.execute(
        select(OffProduct).where(OffProduct.barcode == barcode)
    )).scalar_one_or_none()
    if product is None:
        return None, None
    return _serialise(product), product.fetched_at


def join_on_barcode(
    barcode: str,
    off_record: dict[str, Any] | None,
    proprietary_record: dict[str, Any] | None,
) -> JoinedProduct:
    """Hold the two side by side for the life of one response.

    Deliberately not a database operation. It takes two already-read records
    and pairs them; it cannot write, and there is nowhere for the pair to be
    stored.
    """
    return JoinedProduct(barcode=barcode, off=off_record, proprietary=proprietary_record)
