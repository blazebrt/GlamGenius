"""Add deterministic observed-label semantic versions.

Revision ID: w3x4y5z6a7
Revises: v2w3x4y5z6

The helpers in this migration are the frozen Step-3/V1 content algorithm.
They intentionally do not import application code: future runtime changes
must never change how this historical migration interprets existing rows.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "w3x4y5z6a7"
down_revision: Union[str, None] = "v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONTENT_FIELDS = (
    "product_name",
    "brand",
    "ingredients_text",
    "nutrition_per_100g",
    "serving_size",
    "net_quantity",
    "fssai_licence",
    "veg_mark",
    "allergen_text",
)
_DIFF_NAMES = {
    "product_name": "product_name",
    "brand": "brand",
    "ingredients_text": "ingredients",
    "nutrition_per_100g": "nutrition",
    "serving_size": "serving_size",
    "net_quantity": "net_quantity",
    "fssai_licence": "fssai_licence",
    "veg_mark": "veg_mark",
    "allergen_text": "allergen_text",
}


def _normalise_v1(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalise_v1(item)
            for key, item in sorted(value.items())
            if item not in (None, "")
        }
    if isinstance(value, list):
        return [_normalise_v1(item) for item in value]
    if isinstance(value, str):
        collapsed = " ".join(value.split())
        return collapsed or None
    return value


def _canonical_facts_v1(facts: dict[str, Any]) -> dict[str, Any]:
    return _normalise_v1({key: facts.get(key) for key in _CONTENT_FIELDS})


def _fingerprint_v1(facts: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_facts_v1(facts),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _changed_fields_v1(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old = _canonical_facts_v1(previous)
    new = _canonical_facts_v1(current)
    return [
        _DIFF_NAMES[key]
        for key in _CONTENT_FIELDS
        if old.get(key) != new.get(key)
    ]


def _decimal_v1(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        pass
    match = re.search(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?", str(value))
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _has_ingredients_v1(text: Any) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    depth = 0
    current: list[str] = []
    ingredients: list[str] = []
    for character in text:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(depth - 1, 0)
        if character in ",;" and depth == 0:
            ingredients.append("".join(current))
            current = []
            continue
        current.append(character)
    ingredients.append("".join(current))
    return any(item.strip() for item in ingredients)


def _completeness_v1(facts: dict[str, Any]) -> str:
    has_identity = bool(facts.get("product_name") or facts.get("brand"))
    nutrition = facts.get("nutrition_per_100g") or {}
    has_analytical_content = bool(facts.get("ingredients_text") or nutrition)
    if has_identity and not has_analytical_content:
        return "identity_only"

    has_required_nutrition = any(
        _decimal_v1(nutrition.get(key)) is not None
        for key in (
            "total_sugar_g",
            "sugars_g",
            "saturated_fat_g",
            "salt_g",
            "sodium_g",
        )
    )
    if _has_ingredients_v1(facts.get("ingredients_text")) and has_required_nutrition:
        return "complete_for_grading"
    return "incomplete_for_grading"


def _backfill_semantic_versions() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT id, barcode, facts, created_at
        FROM product_label_snapshots
        ORDER BY barcode, created_at, id
    """)).mappings().all()

    previous_by_barcode: dict[str, dict[str, Any]] = {}
    for row in rows:
        barcode = row["barcode"]
        facts = row["facts"] or {}
        fingerprint = _fingerprint_v1(facts)
        previous = previous_by_barcode.get(barcode)

        # Individual confirmations remain in scan_events. Only consecutive
        # duplicate semantic snapshots are collapsed here.
        if previous is not None and previous["fingerprint"] == fingerprint:
            bind.execute(
                sa.text("DELETE FROM product_label_snapshots WHERE id = :id"),
                {"id": row["id"]},
            )
            continue

        version_number = 1 if previous is None else previous["version_number"] + 1
        changed_fields = [] if previous is None else _changed_fields_v1(previous["facts"], facts)
        bind.execute(
            sa.text("""
                UPDATE product_label_snapshots
                SET content_fingerprint = :fingerprint,
                    version_number = :version_number,
                    previous_snapshot_id = :previous_snapshot_id,
                    changed_fields = CAST(:changed_fields AS jsonb),
                    completeness = :completeness
                WHERE id = :id
            """),
            {
                "id": row["id"],
                "fingerprint": fingerprint,
                "version_number": version_number,
                "previous_snapshot_id": previous["id"] if previous else None,
                "changed_fields": json.dumps(changed_fields),
                "completeness": _completeness_v1(facts),
            },
        )
        previous_by_barcode[barcode] = {
            "id": row["id"],
            "facts": facts,
            "fingerprint": fingerprint,
            "version_number": version_number,
        }


def upgrade() -> None:
    op.add_column(
        "product_label_snapshots",
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "product_label_snapshots",
        sa.Column("version_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "product_label_snapshots",
        sa.Column("previous_snapshot_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "product_label_snapshots",
        sa.Column("changed_fields", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "product_label_snapshots",
        sa.Column("completeness", sa.String(length=32), nullable=True),
    )

    _backfill_semantic_versions()

    op.alter_column("product_label_snapshots", "content_fingerprint", nullable=False)
    op.alter_column(
        "product_label_snapshots", "version_number", nullable=False, server_default="1"
    )
    op.alter_column(
        "product_label_snapshots",
        "changed_fields",
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "product_label_snapshots",
        "completeness",
        nullable=False,
        server_default="incomplete_for_grading",
    )
    op.create_foreign_key(
        "fk_product_label_snapshots_previous",
        "product_label_snapshots",
        "product_label_snapshots",
        ["previous_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_product_label_snapshots_completeness",
        "product_label_snapshots",
        "completeness IN ('complete_for_grading', 'incomplete_for_grading', 'identity_only')",
    )
    op.create_index(
        "ix_product_label_snapshots_barcode_fingerprint",
        "product_label_snapshots",
        ["barcode", "content_fingerprint"],
    )
    op.create_unique_constraint(
        "uq_product_label_snapshots_version",
        "product_label_snapshots",
        ["barcode", "version_number"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_product_label_snapshots_version",
        "product_label_snapshots",
        type_="unique",
    )
    op.drop_index(
        "ix_product_label_snapshots_barcode_fingerprint",
        table_name="product_label_snapshots",
    )
    op.drop_constraint(
        "ck_product_label_snapshots_completeness",
        "product_label_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "fk_product_label_snapshots_previous",
        "product_label_snapshots",
        type_="foreignkey",
    )
    for name in (
        "completeness",
        "changed_fields",
        "previous_snapshot_id",
        "version_number",
        "content_fingerprint",
    ):
        op.drop_column("product_label_snapshots", name)
