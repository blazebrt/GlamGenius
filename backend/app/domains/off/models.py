"""Store A tables.

Declared on their own ``MetaData``, deliberately not on the application's
``Base``. Three things follow from that, and all three are the point:

* these tables can live in a different schema, a different database or a
  different server without any code change;
* nothing in the application can declare a foreign key into them, because
  SQLAlchemy cannot resolve a target in another metadata; and
* the main Alembic chain does not manage them, so a migration written for the
  product can never accidentally reach in and add a column here.

Every column is a field Open Food Facts itself publishes. Adding anything else
is what ``wall.py`` exists to prevent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, MetaData, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Store A's own PostgreSQL schema. Even when both stores share one server —
#: the development fallback — they are separate namespaces, and the application
#: Alembic chain is told to leave this schema alone.
OFF_SCHEMA = "off_data"


class OffBase(DeclarativeBase):
    """A metadata of its own. Store B's Base must never appear in this module."""

    metadata = MetaData(schema=OFF_SCHEMA)


class OffProduct(OffBase):
    """One product as Open Food Facts publishes it.

    The barcode is the primary key because it is the only thing the two stores
    share, and it is the only thing they are allowed to share.
    """

    __tablename__ = "off_products"

    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_name: Mapped[str | None] = mapped_column(Text)
    brands: Mapped[str | None] = mapped_column(Text)
    ingredients_text: Mapped[str | None] = mapped_column(Text)
    # Nutrition exactly as published, untouched. Interpreting these numbers is
    # Store B's job and the interpretation stays there.
    nutriments: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    categories: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[str | None] = mapped_column(Text)
    countries: Mapped[str | None] = mapped_column(Text)
    # Provenance of the copy itself, not of the product.
    off_last_modified_t: Mapped[int | None] = mapped_column()
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_off_products_brands", "brands"),
    )


OFF_TABLES: tuple[str, ...] = tuple(OffBase.metadata.tables)
