from __future__ import annotations

from typing import Any

from .source import normalise_batch, normalise_identity_text, normalise_licence

_PACK_LICENCE_KEYS = ("fssai_licence", "licence")
_PACK_BATCH_KEYS = ("batch_number", "batch_lot", "batch_lot_no")
_PACK_BRAND_KEYS = ("brand", "brand_name")
_PACK_PRODUCT_KEYS = ("product_name", "name")


def _first(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if source.get(key):
            return source[key]
    return None


def _pack_identity(pack: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        normalise_identity_text(_first(pack, _PACK_BRAND_KEYS)),
        normalise_identity_text(_first(pack, _PACK_PRODUCT_KEYS)),
    )


def _record_identity(record: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        normalise_identity_text(record.get("brand_name")),
        normalise_identity_text(record.get("product_name")),
    )


def match_recall(pack: dict[str, Any], record: dict[str, Any]) -> str:
    """One record's eligibility: exact licence and batch, and no identity conflict.

    This answers "could this pack be this record?" — never "is it". When several
    records share the same licence and batch, :func:`resolve_matches` decides.
    """
    licence = normalise_licence(_first(pack, _PACK_LICENCE_KEYS))
    batch = normalise_batch(_first(pack, _PACK_BATCH_KEYS))
    record_licence = normalise_licence(record.get("licence") or record.get("license_no"))
    record_batch = normalise_batch(record.get("batch_lot") or record.get("batch_lot_no"))
    if not licence or not batch or not record_licence or not record_batch:
        return "not_matched"
    # Licence and batch alone decide eligibility. Brand and product are a guard
    # against a real conflict, never evidence for a match on their own.
    if licence != record_licence or batch != record_batch:
        return "identity_mismatch"
    pack_identity, record_ident = _pack_identity(pack), _record_identity(record)
    for pack_side, record_side in zip(pack_identity, record_ident, strict=True):
        # Missing text on either side is missing information, not a disagreement.
        if pack_side is not None and record_side is not None and pack_side != record_side:
            return "identity_mismatch"
    return "matched"


def _corroborated(pack: dict[str, Any], record: dict[str, Any]) -> bool:
    """The pack positively states an identity this record also states.

    Absence corroborates nothing: a pack with no brand printed does not thereby
    agree with every brand in the register.
    """
    return any(
        pack_side is not None and record_side is not None and pack_side == record_side
        for pack_side, record_side in zip(_pack_identity(pack), _record_identity(record), strict=True)
    )


def resolve_matches(pack: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve the whole candidate set, because one licence and lot can name several rows.

    A licence and batch pair is not always unique in the public register. Judging
    each row alone would hand a pack that prints no brand every recall filed
    under its manufacturer's licence — a stranger's product, presented to a
    customer as theirs. So the set is resolved together:

    1. Rows whose licence and batch do not match exactly are not candidates.
    2. Rows whose brand or product explicitly conflicts with the pack are ruled
       out. A conflict is two stated identities that disagree.
    3. One survivor is the answer.
    4. Several survivors: every one of them must be positively corroborated by
       the pack, and all must name the same exact identity. Otherwise no match.

    Step 4 turns on a distinction that decides whether a customer is shown
    somebody else's recall. A row stating a different brand has been *ruled
    out*. A row stating no brand at all has not: missing official identity is
    uncertainty, never evidence against the row. So a candidate that cannot be
    corroborated is not discarded — it keeps the set unresolved, and nothing is
    published. Publishing the one row we could confirm, while a row we could
    neither confirm nor exclude sits beside it, would present a guess as a fact.

    Nothing here is fuzzy: identity is compared as exact normalised text.
    """
    eligible = [record for record in records if match_recall(pack, record) == "matched"]
    if len(eligible) <= 1:
        return eligible
    # Not "drop the ones that fail to corroborate" — one uncorroborated survivor
    # means the set was never resolved.
    if not all(_corroborated(pack, record) for record in eligible):
        return []
    if len({_record_identity(record) for record in eligible}) != 1:
        return []
    return eligible
