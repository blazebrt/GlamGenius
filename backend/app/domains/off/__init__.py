"""Store A — everything derived from Open Food Facts, and nothing else.

Open Food Facts is published under the Open Database License. ODbL carries a
share-alike clause: if we combine their database with ours into one derived
database, we are obliged to publish that combined database under ODbL too.
That would mean giving away the absorption knowledge base, the thresholds, the
scores and the decision memory — the whole product.

So the two never become one database. This package is the only place Open Food
Facts data lives. It has its own engine, its own metadata, its own tables, and
no foreign key to anything in the rest of the system. Proprietary values are
never written here, and Open Food Facts fields are never copied out into a
proprietary table. The two meet in application code, at query time, on the
barcode — a join performed in memory and thrown away, which creates no derived
database and triggers no obligation.
"""
from app.domains.off.models import OFF_TABLES, OffBase, OffProduct
from app.domains.off.wall import (
    OFF_FIELDS,
    ProprietaryFieldError,
    assert_no_proprietary_fields,
)

__all__ = [
    "OffBase",
    "OffProduct",
    "OFF_TABLES",
    "OFF_FIELDS",
    "ProprietaryFieldError",
    "assert_no_proprietary_fields",
]
