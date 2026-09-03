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

from sqlalchemy import Boolean, DateTime, Index, MetaData, String, Text, text
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
    # The free-text fields, exactly as published. Open Food Facts documents
    # both as the last editor's own words in the last editor's own language,
    # and ``categories`` explicitly as "mostly used for debugging and testing
    # purposes". They are kept because they are what the source publishes and
    # the ODbL export must carry them; they are never read as taxonomy.
    categories: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[str | None] = mapped_column(Text)
    countries: Mapped[str | None] = mapped_column(Text)
    # The taxonomy arrays that actually carry Open Food Facts' own
    # classification, stored verbatim. ``categories_hierarchy`` is the
    # non-lossy one their schema says to use — matched ids plus unmatched
    # entries kept as-is — and it is the category-comparison authority. The
    # lossy ``categories_tags`` is deliberately NOT stored: it is documented as
    # "for search only" and comparing it can manufacture a false match, which is
    # the defect this milestone corrects. ``countries_tags`` carries the country
    # taxonomy for the India gate. See ``app/domains/off/taxonomy.py``.
    categories_hierarchy: Mapped[list[str] | None] = mapped_column(JSONB)
    countries_tags: Mapped[list[str] | None] = mapped_column(JSONB)
    # Derived encodings, stored so the discovery query can prune in SQL before
    # any candidate window is taken. Both are computed by
    # ``app/domains/off/taxonomy.py`` from the arrays above alone: no threshold,
    # score, verdict or customer fact reaches them, so neither is a proprietary
    # column and Store A stays an Open Food Facts database.
    #
    # NULL and false mean "this row cannot support a comparison". A row copied
    # before ``categories_hierarchy`` was requested has no array to canonicalise,
    # so it stays excluded until it is refreshed. Deriving these from the raw
    # ``categories``/``countries`` text is exactly the mistake this corrects.
    #
    # ``off_category_key`` is a SHA-256 fingerprint of the canonical hierarchy —
    # a *fixed 64-character* digest, so it can be a B-tree key without indexing an
    # unbounded joined string. It is only a discovery key: a candidate's stored
    # ``categories_hierarchy`` is re-compared exactly before it is ever graded, so
    # a hash collision cannot manufacture a match. The ``off_`` prefix says at a
    # glance this is Open Food Facts' answer, and keeps the name distinct from
    # ``inventory_subtype_definitions.category_key`` — our own wardrobe taxonomy
    # in Store B, which two unrelated columns sharing a name could be mistaken for.
    off_category_key: Mapped[str | None] = mapped_column(String(64))
    off_listed_for_india: Mapped[bool | None] = mapped_column(Boolean)
    # Provenance of the copy itself, not of the product.
    off_last_modified_t: Mapped[int | None] = mapped_column()
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_off_products_brands", "brands"),
        # The comparable-alternative discovery query, and nothing else. Its
        # shape is the query's shape: equality on the canonical category,
        # ascending barcode for deterministic keyset paging, restricted to the
        # rows that can ever qualify. ``fetched_at`` rides along so the
        # freshness range can be tested without a heap fetch; it cannot join
        # the partial predicate because "recent" is relative to now and a
        # partial index predicate must be immutable.
        Index(
            "ix_off_products_discovery",
            "off_category_key",
            "barcode",
            postgresql_where=text("off_category_key IS NOT NULL AND off_listed_for_india"),
            postgresql_include=["fetched_at"],
        ),
    )


OFF_TABLES: tuple[str, ...] = tuple(OffBase.metadata.tables)
