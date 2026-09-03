"""The wall between Store A and Store B.

Three defences, deliberately overlapping, because the cost of getting this
wrong is publishing the entire knowledge base under ODbL.

1. **By construction.** Store A tables carry only Open Food Facts fields, on
   their own metadata. There is no proprietary column to write to.
2. **By allowlist.** ``OFF_FIELDS`` names every column Store A may ever have.
   ``assert_no_proprietary_fields()`` fails if a table grows anything else, so
   a future migration or model edit is caught rather than shipped.
3. **At write time.** ``guard_off_session()`` inspects every object flushed to
   Store A and refuses anything carrying a value outside the allowlist, so a
   dictionary splatted from a proprietary record cannot slip through.

The allowlist is the honest place to argue about scope: adding a name here is a
visible, reviewable act, which is exactly what it should be.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.domains.off.models import OffBase

# Every field Open Food Facts itself publishes and Store A may hold verbatim.
# Adding to this list means asserting the field is OFF-derived. Nothing else
# belongs here — see PROPRIETARY_MARKERS for what must never appear.
OFF_PUBLISHED_FIELDS: frozenset[str] = frozenset({
    "barcode",
    "product_name",
    "brands",
    "ingredients_text",
    "nutriments",
    "categories",
    "image_url",
    "quantity",
    "countries",
    "categories_tags",
    "countries_tags",
    "off_last_modified_t",
    "fetched_at",
})

# Deterministic re-encodings of the fields above, stored so the discovery query
# can be answered and indexed in SQL. They are listed separately because they
# are the one category of column that deserves an argument rather than a
# glance, and the argument is this:
#
#   A canonical field is Open Food Facts data in a different shape. Every one
#   is computed by ``app/domains/off/taxonomy.py`` from an Open Food Facts
#   value alone — no threshold, score, grade, verdict, ruleset, customer fact
#   or anything else of ours is an input, and a test holds that module to
#   importing nothing proprietary. Publishing Store A openly, which the ODbL
#   export does, therefore still publishes only their data.
#
# What would make one of these a licence breach is the opposite direction: a
# column whose value depends on something of ours. ``off_category_key`` restated
# from our own scoring, or an ``is_better_than`` flag, would turn Store A into
# a derived database and oblige us to publish the product. That is the line,
# and it is why this list is short and stays short.
OFF_CANONICAL_FIELDS: frozenset[str] = frozenset({
    "off_category_key",
    "off_listed_for_india",
})

#: What Store A may hold, in total.
OFF_FIELDS: frozenset[str] = OFF_PUBLISHED_FIELDS | OFF_CANONICAL_FIELDS

# Words that indicate a proprietary concept. Used to give a clear failure
# message rather than to do the enforcing — the allowlist does that. A column
# matching one of these is almost certainly the mistake this module exists for.
PROPRIETARY_MARKERS: tuple[str, ...] = (
    "score", "grade", "verdict", "absorption", "threshold", "tier", "evidence",
    "claim", "confidence", "account", "user", "profile", "decision", "memory",
    "recommendation", "asli", "rating", "risk", "elemental", "bioavailability",
)


class ProprietaryFieldError(RuntimeError):
    """Raised when something proprietary is about to reach Store A.

    This is a licence boundary, not a validation error. Writing a proprietary
    value into an Open Food Facts derived record would create a combined
    database, and ODbL's share-alike clause would then oblige us to publish it.
    """


def _describe(field: str) -> str:
    marker = next((m for m in PROPRIETARY_MARKERS if m in field.lower()), None)
    if marker:
        return (
            f"{field!r} looks proprietary (it contains {marker!r}). Store A holds only "
            f"data Open Food Facts publishes. Put this in Store B and join on barcode."
        )
    return (
        f"{field!r} is not an Open Food Facts field. If it genuinely is one, add it to "
        f"OFF_FIELDS deliberately; otherwise it belongs in Store B."
    )


def assert_no_proprietary_fields() -> None:
    """Fail if any Store A table has grown a column outside the allowlist.

    Called by the tests and at Store A bootstrap, so a model edit or a stray
    migration cannot reach production unnoticed.
    """
    offenders: list[str] = []
    for table_name, table in OffBase.metadata.tables.items():
        for column in table.columns:
            if column.name not in OFF_FIELDS:
                offenders.append(f"{table_name}.{column.name} — {_describe(column.name)}")
    if offenders:
        raise ProprietaryFieldError(
            "Store A holds Open Food Facts data only. These columns break that:\n  "
            + "\n  ".join(offenders)
        )


def assert_no_cross_store_foreign_keys() -> None:
    """Fail if a Store A table points at anything outside Store A.

    A foreign key would tie the two together at the storage layer, which is the
    structural form of the same mistake.
    """
    known = set(OffBase.metadata.tables)
    offenders: list[str] = []
    for table_name, table in OffBase.metadata.tables.items():
        for fk in table.foreign_keys:
            target = fk.target_fullname.split(".")[0]
            if target not in known:
                offenders.append(f"{table_name} -> {fk.target_fullname}")
    if offenders:
        raise ProprietaryFieldError(
            "Store A must not reference anything outside itself. Join on barcode at "
            "query time instead:\n  " + "\n  ".join(offenders)
        )


def _check_instance(instance: object) -> None:
    if not isinstance(instance, OffBase):
        raise ProprietaryFieldError(
            f"{type(instance).__name__} is not an Open Food Facts model and must not be "
            f"written to Store A."
        )
    for key in vars(instance):
        if key.startswith("_"):
            continue
        if key not in OFF_FIELDS:
            raise ProprietaryFieldError(_describe(key))


def guard_off_session(session: Session) -> None:
    """Refuse a flush that would carry anything proprietary into Store A.

    The last line of defence. It runs on the real write path, so it catches a
    value set dynamically — a dictionary unpacked from a proprietary record,
    for instance — that no static check could see.
    """

    @event.listens_for(session, "before_flush")
    def _before_flush(sess, _flush_context, _instances):  # noqa: ANN001, ANN202
        for instance in (*sess.new, *sess.dirty):
            _check_instance(instance)
