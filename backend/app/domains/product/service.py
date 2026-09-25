"""Looking a barcode up, and what to do when it is not there.

The order is ours, then Open Food Facts, then not found:

1. **Our record** (Store B) — the barcode, its confidence, the FSSAI licence.
2. **Open Food Facts** (Store A) — the cached copy first, then their API, and
   whatever comes back is written to Store A only.
3. **Not found** — offered as an answer rather than an error, with the label
   capture that turns it into an answer.

The two halves are paired by ``off.join``, in memory, for the length of the
response. Nothing writes the pair anywhere: that is the ODbL wall, and
``docs/architecture/ODBL_DATA_WALL.md`` says why.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity import service as identity_service
from app.domains.media.storage import factory as storage_factory
from app.domains.media.storage.base import account_prefix
from app.domains.nutrition.grading import from_scan, required_grading_data_missing
from app.domains.off import client as off_client
from app.domains.off import freshness as off_freshness
from app.domains.off import taxonomy as off_taxonomy
from app.domains.off.join import join_on_barcode, read_off_product, read_off_product_with_age
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.product.confidence import CONFIDENCE_TEXT, ProductConfidence
from app.domains.product.formula_projection import LINE_BOUNDARIES, boundary_significance
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import LabelErrorReport, LabelSnapshot, ProductRecord, ScanEvent
from app.shared.database.base import new_uuid, utcnow
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)

OUTCOME_LOCAL = "found_local"
OUTCOME_OFF = "found_off"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_LABEL = "label_captured"


def confidence_block(level: str) -> dict[str, str]:
    """Never returned empty. Every result says how far it can be trusted."""
    return {"level": level, "text": CONFIDENCE_TEXT[level]}


async def _own_record(session: AsyncSession, barcode: str) -> ProductRecord | None:
    return (await session.execute(
        select(ProductRecord).where(ProductRecord.barcode == barcode)
    )).scalar_one_or_none()


def readable_label_snapshot(snapshot: LabelSnapshot | None) -> LabelSnapshot | None:
    """The snapshot when its stored facts can be read as a fact object, else ``None``.

    ``facts`` is JSONB, so the column can hold an array, a bare string, a
    number or ``null``. No supported write path produces one, but a row that
    holds one is still served by every reader that goes looking for the latest
    snapshot — and each of those readers calls ``.get()`` on it sooner or
    later. One corrupt row therefore became a 500 on a public route.

    This is the single answer to "may this snapshot's facts be treated as facts
    at all". It exists so the question is asked in one place: a second
    ``isinstance`` written at a third call site would drift from this one, and
    the drift would only be visible as an outage.

    It is deliberately the **weakest** question in this area, and must not be
    confused with the strong one.
    :func:`app.domains.product.change_projection.project_label_change` asks
    whether a stored history satisfies every Step 12A integrity invariant —
    fingerprint agreement, chain contiguity, recomputable difference — and
    refuses far more than this does. "Readable enough to attempt ordinary
    fallback processing" and "trustworthy enough to state a change fact" are
    different questions with different answers, and neither may be used as the
    other.
    """
    return snapshot if isinstance(getattr(snapshot, "facts", None), Mapping) else None


def readable_label_facts(snapshot: LabelSnapshot | None) -> dict[str, Any]:
    """The snapshot's facts as an object, or ``{}`` when there are none to read.

    ``{}`` covers both "no confirmed observation" and "an observation nobody
    can read", because a caller assembling pack fields treats them the same
    way: every field it wanted is missing, and it says so rather than filling
    the gap from somewhere else.
    """
    readable = readable_label_snapshot(snapshot)
    return dict(readable.facts) if readable is not None else {}


async def latest_label_snapshot(session: AsyncSession, barcode: str) -> LabelSnapshot | None:
    return (await session.execute(
        select(LabelSnapshot).where(LabelSnapshot.barcode == barcode).order_by(LabelSnapshot.version_number.desc()).limit(1)
    )).scalar_one_or_none()


async def latest_label_snapshots(
    session: AsyncSession, barcodes: Sequence[str],
) -> dict[str, LabelSnapshot]:
    """The same "latest" as :func:`latest_label_snapshot`, for many barcodes at once.

    One statement, whatever the size of the set, because the alternative engine
    asks about a whole bounded candidate window and a query per candidate would
    turn one Product Result into fifty round trips.

    ``DISTINCT ON`` picks the highest ``version_number`` per barcode, which is
    the definition the single-barcode reader above uses. Keeping both orderings
    on ``version_number`` is what stops the two answers drifting apart, and the
    unique constraint on ``(barcode, version_number)`` makes the pick
    unambiguous.

    Deliberately no completeness filter: this returns the *latest* row, not the
    latest usable one. A caller that needs a gradeable snapshot checks the row
    it gets — reaching past a newer incomplete capture to an older complete one
    would answer with facts the pack no longer has.
    """
    if not barcodes:
        return {}
    rows = (await session.execute(
        select(LabelSnapshot)
        .where(LabelSnapshot.barcode.in_(list(barcodes)))
        .order_by(LabelSnapshot.barcode, LabelSnapshot.version_number.desc())
        .distinct(LabelSnapshot.barcode)
    )).scalars().all()
    return {row.barcode: row for row in rows}


def result_identity(barcode: str, source_half: dict[str, Any] | None) -> tuple[str, str | None]:
    """The name and brand the Product Result publishes for one barcode.

    Shared rather than repeated, because two surfaces now show a product's
    identity — its own verdict screen, and the "Better option" card on somebody
    else's — and a card that names a product differently from the screen it
    opens is a card the shopper cannot trust. One function means they cannot
    drift.

    An absent brand stays absent. An absent name falls back to the barcode,
    which is honest as an identifier but is not a name: a caller publishing a
    recommendation must check for that itself rather than print it.
    """
    half = source_half or {}
    name = half.get("product_name") or half.get("name") or barcode
    brand = half.get("brands") or half.get("brand") or None
    return str(name), brand

#: What a label version *is*, as opposed to what was going on when it was
#: photographed. Batch numbers and extraction metadata are observations about
#: one capture; these are the pack's content, and two captures that agree on
#: all of them are the same observed label.
#:
#: ``product_category`` is here because a formula cannot have one semantic
#: label identity while being freely reinterpreted under another category.
#: "Petrolatum, as a skin-care product" and "Petrolatum, as a hair-care
#: product" are two different things to decide about, and a fingerprint that
#: could not tell them apart would let the second silently inherit the first's
#: version, its reviewed history and its decision context. Legacy captures
#: carry no category at all; canonicalisation drops absent values, so their
#: fingerprints are unchanged by this field's existence and no category is
#: inserted into them.
CONTENT_FACT_FIELDS = (
    "product_name", "brand", "ingredients_text", "nutrition_per_100g",
    "nutrition_basis", "serving_size", "net_quantity", "fssai_licence", "veg_mark", "allergen_text",
    "product_category",
)
#: Fields whose text is handed to the Step 7B formula parser, and therefore the
#: fields where a difference in whitespace can change what the product concludes
#: rather than merely how the pack was printed.
#:
#: Only ``ingredients_text`` is parsed today. The set is named rather than
#: inlined so that the day a second field is parsed, the person wiring it up
#: finds one list to add it to instead of discovering months later that its
#: structure was being flattened.
FORMULA_SIGNIFICANT_FACT_FIELDS: frozenset[str] = frozenset({"ingredients_text"})

def _collapse_whitespace(value: str) -> str:
    """Every run of whitespace becomes one space. Presentation only."""
    return " ".join(value.split())

def _boundary_run_replacement(
    run: list[str], start: int, significance: tuple[bool, ...] | None
) -> str:
    """One space, or one newline when the run carries a boundary that matters.

    A boundary matters where the Step 7B grammar says it cannot be placed —
    outside balanced grouping. Inside grouping the parser keeps the run inside
    one entry and Step 7A collapses it to a space when producing that entry's
    canonical key, so it is printing.

    ``significance`` of ``None`` means the parser could not speak for this text
    at all, and every boundary is then kept. That is the safe direction: an
    extra version is a repeated observation nobody has to act on, while a
    missing one silently attaches an old reading to a new pack.
    """
    for offset, character in enumerate(run):
        if character in LINE_BOUNDARIES and (
            significance is None or significance[start + offset]
        ):
            return "\n"
    return " "


def _collapse_preserving_boundaries(value: str) -> str:
    """Collapse whitespace but keep every boundary the formula parser refuses.

    Two observations of one pack may be printed with different spacing and mean
    exactly the same thing — except where a line break falls at the top level
    of an ingredient list. Step 7B refuses to place one there: it cannot tell a
    visual wrap inside one long name from a break between two names, so it
    returns ``AMBIGUOUS_BOUNDARY`` and emits nothing. That makes such a break a
    fact about the formula, not about the printing.

    Flattening it was the defect this repairs. ``"Water\nGlycerin"`` and
    ``"Water Glycerin"`` collapsed to one canonical string, so they shared a
    fingerprint, shared a version, and the second confirmation was discarded as
    a duplicate of the first — while the parser read one of them as "I cannot
    compare this list" and the other as "one ingredient called Water Glycerin".
    A version authority that cannot hold those apart cannot carry Step 12.

    The distinction is exactly as wide as the grammar makes it, and no wider.
    ``"Parfum (A\nB), Water"`` and ``"Parfum (A B), Water"`` are **one**
    version: grouping wins, the parser keeps that run inside one entry, and
    Step 7A collapses it when producing the entry's canonical key. Treating
    them as two would manufacture a version out of a line wrap and move the
    exact-pack identity that decision memory and shelf links are pinned to.

    So a run of whitespace collapses to a single space when it is only spacing,
    and to a single ``\n`` when it carries a boundary the parser would refuse
    at that position. Which boundary character it was does not survive, and
    does not need to: the parser treats every one of them identically.

    Leading and trailing runs are kept when they carry such a boundary and
    dropped when they do not, because the parser draws the same distinction —
    a leading newline makes a whole list unreadable while a leading space does
    not.

    Where the grammar speaks, the result is strictly finer than plain
    collapsing: two texts equal here are equal under the old rule too, so this
    can only tell more observations apart, never fewer.
    """
    significance = boundary_significance(value)
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
            out.append(_boundary_run_replacement(run, run_start, significance))
            run = []
        out.append(character)
    if run:
        tail = _boundary_run_replacement(run, run_start, significance)
        # A trailing space is spacing and goes; a trailing boundary is not.
        if tail == "\n":
            out.append(tail)
    # A leading run was emitted only if something followed it; strip a leading
    # space, which is spacing, and keep a leading newline, which is not.
    text = "".join(out)
    return text[1:] if text.startswith(" ") else text


def _normalise(value: Any, *, preserve_boundaries: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _normalise(v, preserve_boundaries=preserve_boundaries)
            for k, v in sorted(value.items()) if v not in (None, "")
        }
    if isinstance(value, list):
        return [_normalise(v, preserve_boundaries=preserve_boundaries) for v in value]
    if isinstance(value, str):
        collapsed = (
            _collapse_preserving_boundaries(value) if preserve_boundaries
            else _collapse_whitespace(value)
        )
        return collapsed or None
    return value

def canonical_label_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Canonical content only; batch and extraction metadata are observations.

    Presentation differences are folded away — case is kept, spacing is not —
    except in the fields the formula parser reads, where a boundary it cannot
    place is content rather than printing. See
    :func:`_collapse_preserving_boundaries`.
    """
    return {
        key: _normalise(
            facts.get(key),
            preserve_boundaries=key in FORMULA_SIGNIFICANT_FACT_FIELDS,
        )
        for key in CONTENT_FACT_FIELDS
        if facts.get(key) not in (None, "")
    }

def label_content_fingerprint(facts: dict[str, Any]) -> str:
    encoded = json.dumps(canonical_label_facts(facts), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def label_completeness(facts: dict[str, Any]) -> str:
    has_identity = bool(facts.get("product_name") or facts.get("brand"))
    has_analytical_content = bool(facts.get("ingredients_text") or facts.get("nutrition_per_100g"))
    if has_identity and not has_analytical_content:
        return "identity_only"
    product = from_scan.build_confirmed_label(barcode="label-completeness", facts=facts)
    return (
        "incomplete_for_grading"
        if required_grading_data_missing(product)
        else "complete_for_grading"
    )

def label_changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old, new = canonical_label_facts(previous), canonical_label_facts(current)
    mapping = {"product_name": "product_name", "brand": "brand", "ingredients_text": "ingredients", "nutrition_per_100g": "nutrition", "nutrition_basis": "nutrition_basis", "serving_size": "serving_size", "net_quantity": "net_quantity", "fssai_licence": "fssai_licence", "veg_mark": "veg_mark", "allergen_text": "allergen_text", "product_category": "product_category"}
    return [mapping[key] for key in CONTENT_FACT_FIELDS if old.get(key) != new.get(key)]


async def lock_label_version(session: AsyncSession, barcode: str) -> None:
    """Serialize one barcode's confirmation/version transaction in PostgreSQL."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:barcode, 0))"),
        {"barcode": barcode},
    )


async def store_label_snapshot(
    session: AsyncSession, *, barcode: str, facts: dict[str, Any], device_id: uuid.UUID | None, scan_event_id: uuid.UUID,
) -> LabelSnapshot:
    fingerprint = label_content_fingerprint(facts)
    # Serialize semantic-version allocation for this barcode across processes
    # and database sessions. The transaction-scoped PostgreSQL lock releases
    # automatically on commit/rollback; the unique version constraint remains
    # the final invariant and the retry handles any pre-lock legacy writer.
    await lock_label_version(session, barcode)
    # The unique version constraint closes the race between two confirmations.
    # A savepoint lets us recover from that constraint without poisoning the
    # caller's transaction (which also contains the idempotent scan event).
    for _ in range(3):
        current = await latest_label_snapshot(session, barcode)
        if current is not None and current.content_fingerprint == fingerprint:
            return current
        row = LabelSnapshot(
            barcode=barcode, device_id=device_id, scan_event_id=scan_event_id, facts=facts,
            confidence=ProductConfidence.UNVERIFIED.value, content_fingerprint=fingerprint,
            version_number=(current.version_number + 1 if current else 1),
            previous_snapshot_id=current.id if current else None,
            changed_fields=label_changed_fields(current.facts, facts) if current else [],
            completeness=label_completeness(facts),
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            # READ COMMITTED sees the winner after the unique-index wait. The
            # next iteration re-fetches the latest semantic version: same
            # content is idempotent; different content receives the next
            # version number. Historic equal fingerprints are intentionally
            # ignored so A -> B -> A remains representable.
            continue
    raise RuntimeError("Could not allocate a unique observed label version")


async def _cache_off_product(barcode: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Write what Open Food Facts returned into Store A, and only Store A."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        existing = await session.get(OffProduct, barcode)
        if existing is None:
            existing = OffProduct(barcode=barcode)
            session.add(existing)
        existing.product_name = payload.get("product_name")
        existing.brands = payload.get("brands")
        existing.ingredients_text = payload.get("ingredients_text")
        existing.nutriments = payload.get("nutriments")
        existing.categories = payload.get("categories")
        existing.image_url = payload.get("image_url")
        existing.quantity = payload.get("quantity")
        existing.countries = payload.get("countries")
        # The non-lossy taxonomy array, stored verbatim, plus the derived
        # encodings. The fingerprint and the India flag are computed on the way
        # in so the discovery query can prune in SQL. They come from
        # ``categories_hierarchy``/``countries_tags`` alone — never from the raw
        # ``categories``/``countries`` text, which is untaxonomised prose.
        existing.categories_hierarchy = payload.get("categories_hierarchy")
        existing.countries_tags = payload.get("countries_tags")
        existing.off_category_key = off_taxonomy.category_fingerprint(payload.get("categories_hierarchy"))
        existing.off_listed_for_india = off_taxonomy.listed_for_india(payload.get("countries_tags"))
        existing.off_last_modified_t = payload.get("last_modified_t")
        existing.fetched_at = datetime.now(UTC)
        await session.commit()
        return await read_off_product(session, barcode)


#: The freshness window lives in the Open Food Facts domain, because the
#: comparable alternative reads the same policy and two copies of "30 days"
#: would eventually disagree. Re-exported here so existing callers and tests
#: keep their import.
#:
#: A refresh is best-effort in both directions on this path: the cached copy is
#: still what we answer with when their API is slow, down, or the phone is
#: offline, so re-checking costs a stale answer nothing.
OFF_CACHE_TTL = off_freshness.OFF_CACHE_TTL


def _is_stale(fetched_at: datetime | None) -> bool:
    return off_freshness.is_stale(fetched_at)


async def _off_half(barcode: str, *, allow_network: bool = True) -> tuple[dict[str, Any] | None, bool]:
    """Store A first, their API second. Returns (record, came_from_network)."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        cached, fetched_at = await read_off_product_with_age(session, barcode)
    if cached is not None and not _is_stale(fetched_at):
        return cached, False
    if not allow_network:
        # Offline, a stale copy is a better answer than none, and the response
        # already carries the confidence level that says how far to trust it.
        return cached, False
    payload = await off_client.fetch_product(barcode)
    if payload is None:
        return cached, False
    refreshed = await _cache_off_product(barcode, payload)
    return (refreshed if refreshed is not None else cached), True


async def lookup(
    session: AsyncSession,
    barcode: str,
    *,
    allow_network: bool = True,
) -> dict[str, Any]:
    """Look one barcode up. Always answers; never raises for a missing product."""
    barcode = (barcode or "").strip()
    record = await _own_record(session, barcode)
    off_record, from_network = await _off_half(barcode, allow_network=allow_network)

    if record is None and off_record is None:
        return {
            "barcode": barcode,
            "found": False,
            "outcome": OUTCOME_NOT_FOUND,
            # Said plainly. Not an error, and not an empty screen.
            "confidence": confidence_block(ProductConfidence.NOT_ENOUGH_INFORMATION.value),
            "message": "We do not know this one yet. Take a photo of the label and we will read it.",
            "can_capture_label": True,
            "open_food_facts": None,
            "attribution": None,
            "glamgenius": None,
        }

    level = record.confidence if record else ProductConfidence.UNVERIFIED.value
    joined = join_on_barcode(
        barcode,
        off_record,
        {
            "confidence": level,
            "fssai_licence": record.fssai_licence if record else None,
            "origin": record.origin if record else "off",
        } if (record or off_record) else None,
    )
    body = joined.as_dict()
    body.update({
        "found": True,
        "outcome": OUTCOME_LOCAL if record is not None else OUTCOME_OFF,
        "confidence": confidence_block(level),
        "from_network": from_network,
        # Incomplete data is still worth offering the label capture for.
        "can_capture_label": off_record is None or not off_record.get("ingredients_text"),
    })
    return body


async def _existing_scan_event(
    session: AsyncSession, *, device_id: uuid.UUID | None, client_scan_id: str,
) -> ScanEvent | None:
    """The event already recorded under this exact idempotency identity, if any.

    One definition, used both for the lookup before the insert and for the
    re-read after a unique-constraint race, so the two can never disagree about
    what "already recorded" means.
    """
    return (await session.execute(
        select(ScanEvent).where(
            ScanEvent.device_id == device_id,
            ScanEvent.client_scan_id == client_scan_id,
        )
    )).scalar_one_or_none()


async def record_scan(
    session: AsyncSession,
    *,
    barcode: str,
    outcome: str,
    client_scan_id: str,
    device_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    queued_offline: bool = False,
    scanned_at: datetime | None = None,
    label_facts: dict[str, Any] | None = None,
    ai_run_id: uuid.UUID | None = None,
) -> tuple[ScanEvent, bool]:
    """Record one scan, once.

    Returns ``(event, created)``. A replayed offline queue hits the same
    ``client_scan_id`` and gets the original event back rather than a duplicate.

    **The lookup is not enough on its own.** Two concurrent requests carrying
    the same ``(device_id, client_scan_id)`` can both find nothing and both
    reach the insert. The barcode-scoped advisory lock the confirmation routes
    hold does not help here: it is keyed by barcode, so the same idempotency key
    sent for two *different* barcodes takes two different locks and the two
    transactions never see each other. One insert then wins and the other meets
    ``uq_scan_event_device_client_id`` at flush.

    So the insert runs inside a savepoint. When the unique constraint fires, the
    savepoint alone is rolled back — the caller's outer transaction stays usable,
    which matters because the confirmation routes have already done work in it
    and still have their own mismatch policy to apply. The winner is then re-read
    and returned as an ordinary "already recorded" answer, which is exactly what
    it is.

    **Recovery is narrow on purpose.** It applies only when an exact
    ``(device_id, client_scan_id)`` row now exists; any other integrity failure —
    a bad foreign key, say — re-raises unchanged rather than being quietly
    reinterpreted as a replay. The winner is found by that exact identity, never
    by "the newest row" or any other tie-break.

    **This function decides one thing only:** whether this call created the
    event or found an existing idempotency identity. Whether a replayed key is
    carrying *different* evidence — another barcode, another AI run, other label
    facts — is a policy question, and it stays with the callers that own the
    facts in question.
    """
    existing = await _existing_scan_event(
        session, device_id=device_id, client_scan_id=client_scan_id,
    )
    if existing is not None:
        return existing, False

    event = ScanEvent(
        device_id=device_id, account_id=account_id, barcode=barcode, outcome=outcome,
        client_scan_id=client_scan_id, queued_offline=queued_offline,
        scanned_at=scanned_at or utcnow(), label_facts=label_facts, ai_run_id=ai_run_id,
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        # The insert waited on the unique index until the other transaction
        # finished, so by the time this raises the winner is committed and a
        # fresh statement in this READ COMMITTED transaction can see it.
        winner = await _existing_scan_event(
            session, device_id=device_id, client_scan_id=client_scan_id,
        )
        if winner is None:
            raise
        return winner, False
    return event, True


async def attach_scans_to_account(
    session: AsyncSession, *, device_id: uuid.UUID, account_id: uuid.UUID,
) -> int:
    """Give this device's earlier scans to the account that just claimed it.

    Only scans that belong to nobody are moved. A scan already attached to
    someone stays with them.
    """
    result = await session.execute(
        update(ScanEvent)
        .where(ScanEvent.device_id == device_id, ScanEvent.account_id.is_(None))
        .values(account_id=account_id)
    )
    return result.rowcount or 0


async def apply_confirmed_label(
    session: AsyncSession,
    *,
    barcode: str,
    facts: dict[str, Any],
    confirmed_by: str | None = None,
) -> ProductRecord:
    """Take a label a person has confirmed and update our half of the record.

    Only ProductRecord's own confidence/licence fields are written here.
    Confirmed physical-pack facts live separately in LabelSnapshot (Store B);
    Open Food Facts fields remain in Store A and are never copied across — see
    the ODbL wall.

    Anonymous captures retain label facts but do not create a community claim:
    a device identity is not a person identity.  Only an accountable reviewer
    can promote a captured fact to verified until a future, separately reviewed
    independent-identity workflow exists.
    """
    record = await _own_record(session, barcode)
    if record is None:
        record = ProductRecord(barcode=barcode, origin="label_capture")
        session.add(record)
        await session.flush()

    licence = facts.get("fssai_licence") or find_licence(facts.get("ingredients_text"))
    if licence and is_valid_licence(licence):
        record.fssai_licence = licence

    if confirmed_by:
        record.confirmation_count += 1
        record.confidence = ProductConfidence.VERIFIED.value
        record.verified_at = utcnow()
        record.verified_by = confirmed_by[:160]
    elif record.confidence != ProductConfidence.VERIFIED.value:
        record.confidence = ProductConfidence.UNVERIFIED.value
    await session.flush()
    return record


# ---------------------------------------------------------------------------
# Label-error reports and their photo evidence
# ---------------------------------------------------------------------------
#
# ``client_report_id`` is an idempotency key, chosen by the phone and unique
# only per device (``uq_label_report_device_client_id``). It is never a storage
# identity. Before this, every photo was written to
# ``label-reports/{client_report_id}.jpg``: two phones that picked the same id
# wrote the same object, the second upload overwrote the first, and the first
# report then pointed at somebody else's photograph. A replay also wrote its
# bytes before discovering the report already existed, so an idempotent retry
# could replace evidence that had already been filed. And the global namespace
# sat outside every account's storage prefix, so account erasure proved the
# prefix empty while the account's report photos stayed behind.
#
# Now the object key is built by the server from the report's own UUID, which
# the server generates, inside a namespace the server chooses:
#
# * a device claimed by an account files under that account's canonical prefix
#   (``media.storage.base.account_prefix``), so the deletion worker's prefix
#   purges and its final storage proof cover it with no new convention;
# * an unclaimed device files under a device-scoped namespace, because an
#   anonymous report belongs to no account and must not be made to look as if
#   it did.
#
# Objects are written once, under a fresh key, after idempotency is resolved,
# and are never overwritten.

#: Legacy global namespace, keyed by the caller's ``client_report_id``. Nothing
#: writes here any more; account erasure still finds and removes what is left.
LEGACY_LABEL_REPORT_PREFIX = "label-reports"
#: Unclaimed devices: ``label-reports/devices/{device_id}/{report_id}.jpg``.
#: A ``client_report_id`` is at most 64 characters, so no legacy key
#: ``label-reports/{client_report_id}.jpg`` can ever equal one of these.
ANONYMOUS_LABEL_REPORT_PREFIX = f"{LEGACY_LABEL_REPORT_PREFIX}/devices"
#: The child folder under an account's canonical storage prefix.
ACCOUNT_LABEL_REPORT_FOLDER = "label-reports"

REPORT_REASONS = (
    "wrong_number", "wrong_ingredient", "wrong_product",
    "wrong_grade", "pack_changed", "something_else",
)


class ReportAccountNotActive(Exception):
    """The device's account may not file anything new: its deletion was asked for.

    Raised before any byte is written and before any report row exists. The
    route answers it with the same 403 ``ACCOUNT_INACTIVE`` a media upload
    gives.
    """


def label_report_photo_key(
    *, report_id: uuid.UUID, device_id: uuid.UUID, account_id: uuid.UUID | None,
) -> str:
    """The one place a report photo's object key is built. Server-owned parts only.

    Both namespaces end in the server-generated report UUID, so two reports can
    never share an object, whatever ids their phones chose.
    """
    if account_id is not None:
        return f"{account_prefix(account_id)}/{ACCOUNT_LABEL_REPORT_FOLDER}/{report_id}.jpg"
    return f"{ANONYMOUS_LABEL_REPORT_PREFIX}/{device_id}/{report_id}.jpg"


async def lock_label_report_identity(
    session: AsyncSession, *, device_id: uuid.UUID, client_report_id: str,
) -> None:
    """Serialise every request carrying one ``(device_id, client_report_id)``.

    A transaction-scoped PostgreSQL advisory lock on the idempotency identity,
    so two concurrent retries of one report cannot both find nothing and both
    upload: the second waits, then finds the first one's report and writes no
    bytes at all. It releases on commit or rollback. The unique constraint
    stays the final invariant for any writer that does not take it.

    Nothing else takes this lock, and a holder of it waits on at most the
    account row (``hold_account_active``) — never a job row — so it adds no
    edge to the account-deletion lock order.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"label-report:{device_id}:{client_report_id}"},
    )


async def _existing_label_report(
    session: AsyncSession, *, device_id: uuid.UUID, client_report_id: str,
) -> LabelErrorReport | None:
    return (await session.execute(
        select(LabelErrorReport).where(
            LabelErrorReport.device_id == device_id,
            LabelErrorReport.client_report_id == client_report_id,
        )
    )).scalar_one_or_none()


async def discard_unfiled_report_photo(key: str) -> None:
    """Compensation: remove an object whose report never committed.

    Best effort by design. It runs on a failure path that is already raising,
    and a second error here must not replace the first. What it cannot remove
    is an object no report names; it can reveal nothing, and if it sits under
    an account prefix the deletion worker's purge still removes it.
    """
    try:
        await storage_factory.get_storage().delete(key)
    except Exception:  # noqa: BLE001 - never mask the failure being compensated
        logger.warning("label_report_photo_compensation_failed")


async def reconcile_label_report_commit(
    session: AsyncSession, *, report_id: uuid.UUID, device_id: uuid.UUID,
    client_report_id: str, photo_key: str | None,
) -> bool:
    """Recover an acknowledged-by-neither-side commit without destroying evidence.

    True means a fresh transaction proved this exact report durable. False
    means the caller must propagate its original commit error, not success.
    Only a proved absence permits compensation. Unknown outcomes retain bytes.
    The identity lock waits out any original transaction still completing on
    the server; a plain unlocked SELECT could race that commit.
    """
    try:
        await session.rollback()
        async with get_sessionmaker()() as verification:
            await lock_label_report_identity(
                verification, device_id=device_id, client_report_id=client_report_id,
            )
            rows = (await verification.execute(
                select(LabelErrorReport).where(or_(
                    LabelErrorReport.id == report_id,
                    (LabelErrorReport.device_id == device_id)
                    & (LabelErrorReport.client_report_id == client_report_id),
                ))
            )).scalars().all()
            if rows:
                return len(rows) == 1 and (
                    rows[0].id == report_id
                    and rows[0].device_id == device_id
                    and rows[0].client_report_id == client_report_id
                    and rows[0].photo_key == photo_key
                )
            if photo_key is not None:
                await discard_unfiled_report_photo(photo_key)
    except Exception:  # noqa: BLE001 - unknown outcome: retain bytes, caller raises original error
        logger.warning("label_report_commit_reconciliation_unavailable")
    return False


async def file_label_error_report(
    session: AsyncSession,
    *,
    device_id: uuid.UUID,
    account_id: uuid.UUID | None,
    client_report_id: str,
    subject: str,
    reason: str,
    barcode: str | None = None,
    photo: bytes | None = None,
    photo_content_type: str | None = None,
) -> tuple[LabelErrorReport, bool, str | None]:
    """File one report, once, and write its photo at most once, under its own key.

    Returns ``(report, created, written_key)``. ``written_key`` is the object
    this call wrote, if any. A commit exception requires fresh reconciliation
    before compensation: an exception can mean a lost acknowledgement of a
    durable commit. It is ``None`` for a replay, which writes nothing.

    The order is the whole contract:

    1. **Idempotency first.** :func:`lock_label_report_identity`, then the
       lookup. A replay returns the original report before any byte is
       touched, so filed evidence is never overwritten.
    2. **Account lifecycle.** A device claimed by an account files evidence
       that account owns, so it takes the same boundary as a media upload:
       :func:`identity_service.hold_account_active`, FOR SHARE on the account
       row until this transaction ends. If deletion was requested first, the
       account is not active and nothing is written, not a byte and not a
       row. If the report got there first, the deletion request waits for it
       to commit, and the deletion worker's purges then remove what it wrote.
       An unclaimed device's report belongs to no account and takes no
       account lock.
    3. **Bytes, then the row.** A storage failure leaves no row pointing at
       nothing. A database failure after the bytes deletes them again
       (:func:`discard_unfiled_report_photo`), so an ordinary failure does
       not orphan evidence.

    **No row lock before the account's, so nothing is flushed before step 2.**
    Resolving the device token marks ``last_seen_at``; that UPDATE locks the
    device row when it is flushed. The session does not autoflush, so it is
    written with the report row, after the account hold. Flushing it earlier
    would close a cycle: the deletion worker's account DELETE holds the account
    and cascades into this very device row (``claimed_by_account_id`` SET
    NULL), while this request would hold the device row and wait for the
    account. Account first, then the device — the order the deletion service
    documents.
    """
    await lock_label_report_identity(session, device_id=device_id, client_report_id=client_report_id)
    existing = await _existing_label_report(session, device_id=device_id, client_report_id=client_report_id)
    if existing is not None:
        return existing, False, None

    if account_id is not None and not await identity_service.hold_account_active(session, account_id):
        raise ReportAccountNotActive()

    report_id = new_uuid()
    key: str | None = None
    if photo:
        key = label_report_photo_key(report_id=report_id, device_id=device_id, account_id=account_id)
        # Storage errors propagate to the route before any row exists.
        await storage_factory.get_storage().put(key, photo, photo_content_type or "image/jpeg")

    row = LabelErrorReport(
        id=report_id, device_id=device_id, account_id=account_id,
        client_report_id=client_report_id, barcode=barcode, subject=subject[:200],
        reason=reason, photo_key=key,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # Only a writer that skipped the advisory lock can get here. Our object
        # goes; the report that won is the answer.
        if key is not None:
            await discard_unfiled_report_photo(key)
        winner = await _existing_label_report(session, device_id=device_id, client_report_id=client_report_id)
        if winner is None:
            raise
        return winner, False, None
    except BaseException:
        if key is not None:
            await discard_unfiled_report_photo(key)
        raise
    return row, True, key
