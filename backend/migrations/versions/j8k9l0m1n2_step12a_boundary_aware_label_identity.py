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
import unicodedata
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
LINE_BOUNDARIES = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")

#: The canonical field name each fact contributes to a difference under.
CHANGED_FIELD_NAMES = {
    "product_name": "product_name", "brand": "brand",
    "ingredients_text": "ingredients", "nutrition_per_100g": "nutrition",
    "nutrition_basis": "nutrition_basis", "serving_size": "serving_size",
    "net_quantity": "net_quantity", "fssai_licence": "fssai_licence",
    "veg_mark": "veg_mark", "allergen_text": "allergen_text",
    "product_category": "product_category",
}

# ---------------------------------------------------------------------------
# The Step 7B structural grammar, frozen
#
# The new rule keeps a line boundary only where the formula parser refuses to
# place one — outside balanced grouping. Deciding that needs the parser's own
# view of structure, including the compatibility folds Step 7A will apply, so
# the part of that grammar this revision depends on is copied here in full.
#
# It is copied rather than imported for the reason every constant above is: a
# migration has to keep doing what it did on the day it ran, and an import
# would let this file change meaning the next time the parser is edited. The
# cost of the copy is paid by a test that asserts it still agrees with the
# parser across grouping, nesting and compatibility forms.
# ---------------------------------------------------------------------------
GROUPING_PAIRS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = frozenset(GROUPING_PAIRS.values())
AMBIGUOUS_SEPARATORS = frozenset(";")
ASCII_DIGITS = frozenset("0123456789")
HETEROATOM_LOCANTS = frozenset("NOSPnosp")
PRIMES = frozenset("'\u2032")
STRUCTURAL_CHARACTERS = (
    frozenset(",")
    | AMBIGUOUS_SEPARATORS
    | LINE_BOUNDARIES
    | frozenset(GROUPING_PAIRS)
    | CLOSERS
    | frozenset("-")
    | PRIMES
)


def _lexical_properties(character: str) -> tuple[Any, ...]:
    return (
        character if character in STRUCTURAL_CHARACTERS else None,
        character in ASCII_DIGITS,
        character in HETEROATOM_LOCANTS,
        character.isspace(),
        character.isalnum(),
    )


def _structural_view(text: str) -> str | None:
    """A length-preserving view of the text under Step 7A's text transforms."""
    pieces: list[str] = []
    for character in text:
        folded = unicodedata.normalize("NFKC", character).casefold()
        if len(folded) == 1:
            pieces.append(folded)
            continue
        raw = _lexical_properties(character)
        if any(_lexical_properties(one) != raw for one in folded):
            return None
        pieces.append(character)

    view = "".join(pieces)
    normalized = unicodedata.normalize("NFKC", text).casefold()

    def _collapsed(value: str) -> list[tuple[Any, ...]]:
        stream = [_lexical_properties(character) for character in value]
        return [
            properties
            for index, properties in enumerate(stream)
            if index == 0 or properties != stream[index - 1]
        ]

    if _collapsed(view) != _collapsed(normalized):
        return None
    return view


def _boundary_significance(text: str) -> tuple[bool, ...] | None:
    """Per character, whether a line boundary there is one the parser refuses.

    ``None`` when this grammar cannot speak for the text — a compatibility form
    that would relocate structure, or grouping that never balances — and every
    boundary is then kept, which is the safe direction.
    """
    view = _structural_view(text)
    if view is None:
        return None
    stack: list[str] = []
    significance: list[bool] = []
    for character in view:
        significance.append(not stack)
        if character in GROUPING_PAIRS:
            stack.append(GROUPING_PAIRS[character])
        elif character in CLOSERS:
            if not stack or stack[-1] != character:
                return None
            stack.pop()
    if stack:
        return None
    return tuple(significance)


def _run_replacement(
    run: list[str], start: int, significance: tuple[bool, ...] | None
) -> str:
    for offset, character in enumerate(run):
        if character in LINE_BOUNDARIES and (
            significance is None or significance[start + offset]
        ):
            return "\n"
    return " "


def _collapse(value: str, *, preserve_boundaries: bool) -> str:
    if not preserve_boundaries:
        return " ".join(value.split())
    significance = _boundary_significance(value)
    out: list[str] = []
    run: list[str] = []
    run_start = 0
    for index, character in enumerate(value):
        if character.isspace():
            if not run:
                run_start = index
            run.append(character)
            continue
        if run:
            out.append(_run_replacement(run, run_start, significance))
            run = []
        out.append(character)
    if run:
        tail = _run_replacement(run, run_start, significance)
        if tail == "\n":
            out.append(tail)
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


def _canonical(facts: dict[str, Any], *, boundary_aware: bool) -> dict[str, Any]:
    return {
        key: _normalise(
            facts.get(key),
            preserve_boundaries=boundary_aware and key in FORMULA_SIGNIFICANT_FACT_FIELDS,
        )
        for key in CONTENT_FACT_FIELDS
        if facts.get(key) not in (None, "")
    }


def _changed_fields(
    previous: dict[str, Any], current: dict[str, Any], *, boundary_aware: bool
) -> list[str]:
    old = _canonical(previous, boundary_aware=boundary_aware)
    new = _canonical(current, boundary_aware=boundary_aware)
    return [
        CHANGED_FIELD_NAMES[key]
        for key in CONTENT_FACT_FIELDS
        if old.get(key) != new.get(key)
    ]


def _fingerprint(facts: dict[str, Any], *, boundary_aware: bool) -> str:
    encoded = json.dumps(
        _canonical(facts, boundary_aware=boundary_aware),
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _rederive(connection: sa.engine.Connection, *, boundary_aware: bool) -> None:
    """Recompute every derived label value under the named rule.

    Two things are derived from ``facts``, and both move when the rule does:

    * ``content_fingerprint`` — a pure function of one row's own immutable
      facts. There is exactly one correct value and no pairing decision to
      make, so it is recomputed unconditionally.
    * ``changed_fields`` — a statement about *two* rows. It is migrated only
      where the stored value is exactly what the source rule derived from the
      row and its recorded predecessor. A row that already disagreed with its
      own history was not produced by any supported write path, and rewriting
      it here would launder that into a clean-looking history nobody can see
      any more. Those rows are left exactly as found, for the Step 12A
      integrity check to refuse in the open.

    The predecessor is always the row's explicit ``previous_snapshot_id``.
    Never the previous version number, never the same barcode, never a
    matching fingerprint — pairing rows by anything other than the link they
    actually carry would invent a history.

    The connection is a parameter rather than something this function reaches
    for, so the body can be run against a real table in a test instead of only
    ever running once, unobserved, on the day of the release.
    """
    rows = connection.execute(sa.text(
        "SELECT id, facts, content_fingerprint, version_number, "
        "previous_snapshot_id, changed_fields FROM product_label_snapshots"
    )).mappings().all()

    facts_by_id = {
        row["id"]: row["facts"] for row in rows if isinstance(row["facts"], dict)
    }

    fingerprints: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    for row in rows:
        facts = facts_by_id.get(row["id"])
        if facts is None:
            # Nothing a supported write path can produce; left exactly as found
            # rather than guessed at, and the Step 12A integrity check will
            # refuse it in the open.
            continue

        expected = _fingerprint(facts, boundary_aware=boundary_aware)
        if expected != row["content_fingerprint"]:
            fingerprints.append({"row_id": row["id"], "fingerprint": expected})

        stored = row["changed_fields"]
        if not isinstance(stored, list):
            continue

        if row["version_number"] == 1:
            # The first observation of a label differs from nothing. Its value
            # does not depend on the rule, so there is nothing to migrate.
            continue

        predecessor = facts_by_id.get(row["previous_snapshot_id"])
        if row["previous_snapshot_id"] is None or predecessor is None:
            # A later version with no readable predecessor. The difference it
            # records cannot be recomputed from anything, and inventing one
            # would be fabricating the history this migration exists to keep.
            continue

        source = _changed_fields(predecessor, facts, boundary_aware=not boundary_aware)
        if stored != source:
            continue  # already inconsistent with its own history; not ours to fix
        target = _changed_fields(predecessor, facts, boundary_aware=boundary_aware)
        if target != stored:
            differences.append({"row_id": row["id"], "changed_fields": target})

    if fingerprints:
        connection.execute(
            sa.text(
                "UPDATE product_label_snapshots SET content_fingerprint = :fingerprint "
                "WHERE id = :row_id"
            ),
            fingerprints,
        )
    if differences:
        connection.execute(
            sa.text(
                "UPDATE product_label_snapshots "
                "SET changed_fields = CAST(:changed_fields AS jsonb) WHERE id = :row_id"
            ),
            [
                {"row_id": row["row_id"], "changed_fields": json.dumps(row["changed_fields"])}
                for row in differences
            ],
        )
    if not fingerprints:
        return
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
    _rederive(op.get_bind(), boundary_aware=True)


def downgrade() -> None:
    # Faithful, not lossy: both rules are pure functions of the immutable facts
    # and of the predecessor each row already names, so the older identity and
    # the older difference are recomputed rather than remembered.
    _rederive(op.get_bind(), boundary_aware=False)
