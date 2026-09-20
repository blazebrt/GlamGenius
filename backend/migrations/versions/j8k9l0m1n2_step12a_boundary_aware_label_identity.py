"""Step 12A: re-fingerprint confirmed labels under boundary-aware identity.

Revision ID: j8k9l0m1n2
Revises: i7j8k9l0m1

Why a data migration exists in a step that adds no table
--------------------------------------------------------
The label version authority canonicalised every string by collapsing each run
of whitespace to a single space. That folded a top-level line break into a
space — and a top-level line break is not spacing. Step 7B refuses to place
one, because it cannot tell a visual wrap inside one long name from a break
between two names, so ``"Water\\nGlycerin"`` is ``AMBIGUOUS_BOUNDARY`` with no
entries while ``"Water Glycerin"`` parses as a single ingredient. Those two
observations shared a fingerprint, therefore shared a version, therefore the
second confirmation of a pack was discarded as a duplicate of the first.

The canonicaliser now keeps those boundaries in the fields the formula parser
reads. Fingerprints already stored for such rows were computed under the old
rule, and three tables hold a copy of one:

* ``product_label_snapshots.content_fingerprint`` — recomputable, because
  ``facts`` is immutable and the rule is a pure function of it;
* ``scan_decision_events.content_fingerprint`` — a copy of the snapshot's,
  taken when a decision was recorded;
* ``inventory_product_links.content_fingerprint`` — the same copy, taken when
  a scanned product was added to a shelf.

Leaving them stale would make every one of those rows fail the Step 12A
integrity check and take the product page down with a governed 503. So they are
recomputed here.

Why this is safe
----------------
The new rule is strictly finer than the old one: two texts it considers equal
are equal under the old rule as well. It can therefore only tell observations
apart that were previously conflated — it can never merge two rows that were
distinct. No version chain can collapse, and no adjacent pair can become
identical. Rows whose text contains no boundary character hash exactly as
before and are not touched at all.

Why the rule is copied here instead of imported
-----------------------------------------------
A migration has to keep doing what it did on the day it ran. Importing the live
canonicaliser would mean this file quietly changes meaning the next time that
function is edited, which is the one thing a frozen revision must not do. The
rule is therefore written out, and a test asserts the copy still agrees with
the application's own implementation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "j8k9l0m1n2"
down_revision = "i7j8k9l0m1"
branch_labels = None
depends_on = None

#: Frozen copies of the Step 12A contract, as it stood when this ran.
CONTENT_FACT_FIELDS = (
    "product_name", "brand", "ingredients_text", "nutrition_per_100g",
    "nutrition_basis", "serving_size", "net_quantity", "fssai_licence",
    "veg_mark", "allergen_text", "product_category",
)
FORMULA_SIGNIFICANT_FACT_FIELDS = frozenset({"ingredients_text"})
LINE_BOUNDARIES = frozenset("\n\r\v\f\x1c\x1d\x1e\x85  ")


def _collapse(value: str, *, preserve_boundaries: bool) -> str:
    if not preserve_boundaries:
        return " ".join(value.split())
    out: list[str] = []
    run: list[str] = []
    for character in value:
        if character.isspace():
            run.append(character)
            continue
        if run:
            out.append("\n" if any(c in LINE_BOUNDARIES for c in run) else " ")
            run = []
        out.append(character)
    if run and any(c in LINE_BOUNDARIES for c in run):
        out.append("\n")
    text = "".join(out)
    return text[1:] if text.startswith(" ") else text


def _normalise(value: Any, *, preserve_boundaries: bool) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _normalise(v, preserve_boundaries=preserve_boundaries)
            for k, v in sorted(value.items()) if v not in (None, "")
        }
    if isinstance(value, list):
        return [_normalise(v, preserve_boundaries=preserve_boundaries) for v in value]
    if isinstance(value, str):
        return _collapse(value, preserve_boundaries=preserve_boundaries) or None
    return value


def _fingerprint(facts: dict[str, Any], *, boundary_aware: bool) -> str:
    canonical = {
        key: _normalise(
            facts.get(key),
            preserve_boundaries=boundary_aware and key in FORMULA_SIGNIFICANT_FACT_FIELDS,
        )
        for key in CONTENT_FACT_FIELDS
        if facts.get(key) not in (None, "")
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _refingerprint(*, boundary_aware: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, facts, content_fingerprint FROM product_label_snapshots"
    )).mappings().all()

    changed: list[dict[str, Any]] = []
    for row in rows:
        facts = row["facts"] or {}
        if not isinstance(facts, dict):
            # Nothing a supported write path can produce; left exactly as found
            # rather than guessed at, and the Step 12A integrity check will
            # refuse it in the open.
            continue
        expected = _fingerprint(facts, boundary_aware=boundary_aware)
        if expected != row["content_fingerprint"]:
            changed.append({"row_id": row["id"], "fingerprint": expected})

    if not changed:
        return

    connection.execute(
        sa.text(
            "UPDATE product_label_snapshots SET content_fingerprint = :fingerprint "
            "WHERE id = :row_id"
        ),
        changed,
    )
    # The two tables that copied a snapshot's fingerprint follow it, by the
    # snapshot they already name. Nothing is matched on the fingerprint value
    # itself, so a row cannot be attached to a different observation.
    for table in ("scan_decision_events", "inventory_product_links"):
        connection.execute(sa.text(
            f"UPDATE {table} AS copy SET content_fingerprint = snapshot.content_fingerprint "
            "FROM product_label_snapshots AS snapshot "
            "WHERE copy.label_snapshot_id = snapshot.id "
            "AND copy.content_fingerprint <> snapshot.content_fingerprint"
        ))


def upgrade() -> None:
    _refingerprint(boundary_aware=True)


def downgrade() -> None:
    # Faithful, not lossy: both rules are pure functions of the immutable facts,
    # so the older identity is recomputed rather than remembered.
    _refingerprint(boundary_aware=False)
