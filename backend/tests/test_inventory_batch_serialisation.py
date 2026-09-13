"""Serialising a list of items must cost a fixed number of queries, and must
produce exactly what serialising them one by one produced.

``serialize_item`` costs three queries per item — the detail row, the
attributes, the images — and the inventory reports looped over every item an
account owns. Measured on an account with two hundred items:

    GET /inventory/items (one page of 24)     74 queries    63.5 ms
    GET /inventory/expiring                  201 queries   118.7 ms
    GET /inventory/value-to-recover          201 queries   143.2 ms
    GET /inventory/summary                   404 queries   261.2 ms

``/inventory/summary`` is the worst because it calls three of the others.

The batched path replaces the loop, and the risk in that trade is not
performance but drift: a list screen that quietly renders items differently
from the detail screen. So the first test here is not about speed at all — it
compares the two paths field for field on items that actually have details,
attributes and images, across several categories at once. The speed tests
then hold the bound that made the change worth making.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.domains.identity import service as identity
from app.domains.inventory import service as inv
from app.domains.inventory.models import DuplicateCandidate, InventoryItem
from app.domains.inventory.schemas import ItemCreate
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import event, select

pytestmark = pytest.mark.asyncio


class _QueryCounter:
    """Counts statements actually sent to PostgreSQL."""

    def __init__(self) -> None:
        self.count = 0
        self._handler = None

    def __enter__(self) -> _QueryCounter:
        engine = get_engine().sync_engine

        def handler(conn, cursor, statement, params, context, executemany):
            self.count += 1

        self._handler = handler
        event.listen(engine, "before_cursor_execute", handler)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(get_engine().sync_engine, "before_cursor_execute", self._handler)


async def _account() -> uuid.UUID:
    account_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await identity.register_account(session, account_id)
        await session.commit()
    return account_id


# One per category, each carrying the kind of detail its own table holds, so
# the grouped lookup has to get all seven right rather than one.
_SPECIMENS = [
    ("wardrobe", "Blue cotton shirt", {"colour": "blue", "fabric": "cotton"}),
    ("shoes", "Running shoes", {"colour": "black"}),
    ("accessories", "Leather belt", {"colour": "brown"}),
    ("beauty", "Vitamin C serum", {"expiry_date": "2027-01-31", "opened_date": "2026-01-05"}),
    ("hair", "Argan hair oil", {"expiry_date": "2026-11-30"}),
    ("perfumes", "Citrus eau de parfum", {}),
    ("supplements", "Vitamin D3", {"expiry_date": "2027-06-30"}),
]


async def _seed_one_of_each(account_id: uuid.UUID) -> list[uuid.UUID]:
    created: list[uuid.UUID] = []
    async with get_sessionmaker()() as session:
        for category, name, details in _SPECIMENS:
            item = await inv.create_item(
                session,
                account_id,
                ItemCreate(
                    category=category,
                    display_name=name,
                    brand="Test Brand",
                    purchase_date=date(2026, 1, 1),
                    purchase_price=499,
                    details=details,
                ),
            )
            created.append(item.id)
        await session.commit()
    return created


async def _load(session, account_id: uuid.UUID) -> list[InventoryItem]:
    return list((await session.execute(
        select(InventoryItem)
        .where(InventoryItem.account_id == account_id)
        .order_by(InventoryItem.created_at)
    )).scalars().all())


# ---------------------------------------------------------------------------
# The property that matters: the two paths agree
# ---------------------------------------------------------------------------


async def test_the_batched_path_returns_exactly_what_the_loop_returned(db_clean):
    account_id = await _account()
    await _seed_one_of_each(account_id)

    async with get_sessionmaker()() as session:
        items = await _load(session, account_id)
        one_at_a_time = [await inv.serialize_item(session, item) for item in items]
        batched = await inv.serialize_items(session, items)

    assert batched == one_at_a_time


async def test_details_agree_for_every_category(db_clean):
    """The grouped lookup picks a different table per category; all seven."""
    account_id = await _account()
    await _seed_one_of_each(account_id)

    async with get_sessionmaker()() as session:
        items = await _load(session, account_id)
        one_at_a_time = {item.id: await inv.details_for(session, item) for item in items}
        batched = await inv.details_for_many(session, items)

    assert batched == one_at_a_time
    assert len({item_id for item_id in batched}) == len(_SPECIMENS)


async def test_an_item_with_no_detail_row_still_appears(db_clean):
    """A missing detail row is an empty dict, not a dropped item."""
    account_id = await _account()
    async with get_sessionmaker()() as session:
        await inv.ensure_categories(session)
        item = InventoryItem(
            account_id=account_id, category="wardrobe", display_name="Bare",
            source="user_declared", verification_state="confirmed", status="active",
            usage_count=0, version=1,
        )
        session.add(item)
        await session.commit()

    async with get_sessionmaker()() as session:
        items = await _load(session, account_id)
        batched = await inv.serialize_items(session, items)
        looped = [await inv.serialize_item(session, row) for row in items]

    assert len(batched) == 1
    assert batched == looped
    assert batched[0]["details"] == {}


async def test_no_items_is_not_a_query(db_clean):
    account_id = await _account()
    async with get_sessionmaker()() as session:
        with _QueryCounter() as counter:
            assert await inv.serialize_items(session, []) == []
            assert await inv.details_for_many(session, []) == {}
    assert counter.count == 0


async def test_items_keep_the_order_they_were_given_in(db_clean):
    account_id = await _account()
    await _seed_one_of_each(account_id)

    async with get_sessionmaker()() as session:
        items = await _load(session, account_id)
        reversed_items = list(reversed(items))
        batched = await inv.serialize_items(session, reversed_items)

    assert [body["id"] for body in batched] == [str(item.id) for item in reversed_items]


# ---------------------------------------------------------------------------
# The bound that made it worth doing
# ---------------------------------------------------------------------------


async def _seed_many(account_id: uuid.UUID, count: int) -> None:
    async with get_sessionmaker()() as session:
        await inv.ensure_categories(session)
        for index in range(count):
            session.add(InventoryItem(
                account_id=account_id, category="beauty",
                display_name=f"Item {index}", brand="B", source="user_declared",
                verification_state="confirmed", status="active", usage_count=0, version=1,
            ))
        await session.commit()


async def test_serialising_more_items_does_not_mean_more_queries(db_clean):
    """The point of the change, stated as the invariant rather than a timing."""
    small = await _account()
    large = await _account()
    await _seed_many(small, 3)
    await _seed_many(large, 60)

    async with get_sessionmaker()() as session:
        items = await _load(session, small)
        with _QueryCounter() as few:
            await inv.serialize_items(session, items)

    async with get_sessionmaker()() as session:
        items = await _load(session, large)
        with _QueryCounter() as many:
            await inv.serialize_items(session, items)

    assert few.count == many.count, (
        f"{few.count} queries for 3 items but {many.count} for 60 — the batch "
        "path has an N+1 in it again"
    )
    assert many.count <= 4


async def test_the_whole_inventory_reports_stay_flat(db_clean):
    """expiring, low-use, value-to-recover and the summary that calls all three."""
    account_id = await _account()
    await _seed_many(account_id, 60)

    async with get_sessionmaker()() as session:
        with _QueryCounter() as expiring:
            await inv.expiring_items(session, account_id)
        with _QueryCounter() as low_use:
            await inv.low_use_items(session, account_id)
        with _QueryCounter() as value:
            await inv.value_report(session, account_id)
        with _QueryCounter() as summary:
            await inv.summary(session, account_id)

    # Generous ceilings: the numbers before this change were 61, 61, 61 and 184
    # on this fixture, and a regression would land back in that range.
    assert expiring.count <= 5, expiring.count
    assert low_use.count <= 5, low_use.count
    assert value.count <= 5, value.count
    assert summary.count <= 12, summary.count


async def test_one_page_of_a_large_inventory_stays_flat(db_clean):
    account_id = await _account()
    await _seed_many(account_id, 120)

    async with get_sessionmaker()() as session:
        with _QueryCounter() as counter:
            page = await inv.list_items(session, account_id, page=1, page_size=24)

    assert len(page["items"]) == 24
    assert page["pagination"]["total"] == 120
    assert counter.count <= 8, counter.count


async def test_filtering_by_expiry_does_not_query_per_item(db_clean):
    """This path inspects every item before cutting the page, so it was worst."""
    account_id = await _account()
    await _seed_many(account_id, 120)

    async with get_sessionmaker()() as session:
        with _QueryCounter() as counter:
            await inv.list_items(
                session, account_id, page=1, page_size=24, expiry_status="missing",
            )

    assert counter.count <= 8, counter.count


# ---------------------------------------------------------------------------
# Duplicates: the batch must not become a way to see somebody else's items
# ---------------------------------------------------------------------------


async def test_duplicate_pairs_are_still_scoped_to_the_account(db_clean):
    """``owned_item`` per side enforced ownership; the batched ``where`` must too."""
    mine = await _account()
    theirs = await _account()
    my_ids = await _seed_one_of_each(mine)
    their_ids = await _seed_one_of_each(theirs)

    async with get_sessionmaker()() as session:
        # A candidate row pointing at somebody else's item.
        session.add(DuplicateCandidate(
            account_id=mine, item_a_id=my_ids[0], item_b_id=their_ids[0],
            confidence=0.9, reason="test", status="pending",
        ))
        await session.commit()

    from app.shared.errors.exceptions import NotFoundError

    async with get_sessionmaker()() as session:
        with pytest.raises(NotFoundError):
            await inv.duplicates(session, mine)


async def test_duplicate_pairs_serialise_the_same_as_before(db_clean):
    account_id = await _account()
    ids = await _seed_one_of_each(account_id)

    async with get_sessionmaker()() as session:
        session.add(DuplicateCandidate(
            account_id=account_id, item_a_id=ids[0], item_b_id=ids[1],
            confidence=0.8, reason="same brand", status="pending",
        ))
        await session.commit()

    async with get_sessionmaker()() as session:
        pairs = await inv.duplicates(session, account_id)
        a = await inv.owned_item(session, account_id, ids[0])
        b = await inv.owned_item(session, account_id, ids[1])
        expected_a = await inv.serialize_item(session, a)
        expected_b = await inv.serialize_item(session, b)

    assert len(pairs) == 1
    assert pairs[0]["item_a"] == expected_a
    assert pairs[0]["item_b"] == expected_b
    assert pairs[0]["reason"] == "same brand"


# ---------------------------------------------------------------------------
# The same per-item detail lookup, on the two decision paths
# ---------------------------------------------------------------------------
#
# ``details_for`` per item also sat inside the loops that build the owned-shelf
# context for the routine compiler and for styling. Those are not list screens:
# the shelf context is read on every Today compile and once per account per
# hour by the notification worker, and the styling context on every request.
# Measured on a shelf of two hundred items:
#
#     routines shelf context             204 queries   153.7 ms
#     recommendation confirmed_inventory 201 queries   118.2 ms


async def _seed_shelf(account_id: uuid.UUID, count: int) -> None:
    categories = ["beauty", "hair", "wardrobe", "shoes"]
    async with get_sessionmaker()() as session:
        await inv.ensure_categories(session)
        for index in range(count):
            session.add(InventoryItem(
                account_id=account_id, category=categories[index % len(categories)],
                display_name=f"Shelf item {index}", source="user_declared",
                verification_state="confirmed", status="active", usage_count=1, version=1,
            ))
        await session.commit()


async def test_the_shelf_context_does_not_query_per_item(db_clean):
    from datetime import date as _date

    from app.domains.routines import shelf

    account_id = await _account()
    await _seed_shelf(account_id, 60)

    async with get_sessionmaker()() as session:
        with _QueryCounter() as counter:
            context = await shelf.gather(session, account_id=account_id, today=_date.today())

    assert len(context.owned) == 60
    assert counter.count <= 12, counter.count


async def test_the_styling_context_does_not_query_per_item(db_clean):
    from app.domains.recommendation import context as rec_context

    account_id = await _account()
    await _seed_shelf(account_id, 60)

    async with get_sessionmaker()() as session:
        with _QueryCounter() as counter:
            owned, drafts = await rec_context.confirmed_inventory(session, account_id)

    assert len(owned) == 60
    assert drafts == 0
    assert counter.count <= 8, counter.count


async def test_unconfirmed_items_are_still_counted_but_not_returned(db_clean):
    """The batch pre-filters to confirmed items; the draft count must survive.

    Batching changed what the loop iterates, and the easy mistake is to count
    drafts against the filtered list and always report zero.
    """
    from app.domains.recommendation import context as rec_context

    account_id = await _account()
    async with get_sessionmaker()() as session:
        await inv.ensure_categories(session)
        for state in ("confirmed", "confirmed", "unconfirmed"):
            session.add(InventoryItem(
                account_id=account_id, category="beauty", display_name=f"X {state}",
                source="photo_extracted", verification_state=state, status="active",
                usage_count=0, version=1,
            ))
        await session.commit()

    async with get_sessionmaker()() as session:
        owned, drafts = await rec_context.confirmed_inventory(session, account_id)

    assert len(owned) == 2
    assert drafts == 1


async def test_the_shelf_context_also_still_counts_its_drafts(db_clean):
    from datetime import date as _date

    from app.domains.routines import shelf

    account_id = await _account()
    async with get_sessionmaker()() as session:
        await inv.ensure_categories(session)
        for state in ("confirmed", "unconfirmed", "unconfirmed"):
            session.add(InventoryItem(
                account_id=account_id, category="hair", display_name=f"Y {state}",
                source="photo_extracted", verification_state=state, status="active",
                usage_count=0, version=1,
            ))
        await session.commit()

    async with get_sessionmaker()() as session:
        context = await shelf.gather(session, account_id=account_id, today=_date.today())

    assert len(context.owned) == 1
    assert context.draft_count == 2


async def test_a_duplicate_pair_naming_an_archived_item_is_not_served(db_clean):
    """``owned_item`` excluded archived items; the batched query must too.

    Easy to lose when a per-row helper is replaced by one ``IN`` query: the
    account filter is the obvious one to carry over, the ``status`` filter is
    the one that gets forgotten.
    """
    from app.shared.errors.exceptions import NotFoundError

    account_id = await _account()
    ids = await _seed_one_of_each(account_id)

    async with get_sessionmaker()() as session:
        session.add(DuplicateCandidate(
            account_id=account_id, item_a_id=ids[0], item_b_id=ids[1],
            confidence=0.8, reason="same brand", status="pending",
        ))
        archived = await inv.owned_item(session, account_id, ids[1])
        archived.status = "archived"
        await session.commit()

    async with get_sessionmaker()() as session:
        with pytest.raises(NotFoundError):
            await inv.duplicates(session, account_id)
