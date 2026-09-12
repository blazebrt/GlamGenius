"""Two devices editing one item at the same time.

``ItemPatch`` carries ``expected_version`` and ``update_item`` refuses when it
does not match, answering "This item changed on another device. Refresh it
before saving." That is a promise about concurrency, and a check made in Python
against a row read without a lock cannot keep it. Under READ COMMITTED — the
PostgreSQL default this product runs on — both requests read version 1, both
find the version they expected, both write version 2, and both commit:

    for_update=False  outcomes=['committed', 'committed']  version 1->2  <- lost
    for_update=True   outcomes=['committed', 'refused']    version 1->2

One person's edit is gone with no error, and the counter says one edit
happened. A phone is exactly where this occurs — the same account on a tablet
and a phone, or one device retrying a request it thought had failed.

The interleaving below is forced rather than hoped for. Two requests fired at
once usually do not overlap: the first finishes before the second starts, and
the test passes while proving nothing. The first writer holds its transaction
open while the second reads.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import pytest
from app.domains.identity import service as identity
from app.domains.inventory import service as inventory
from app.domains.inventory.models import InventoryItem
from app.domains.inventory.schemas import ItemCreate, ItemPatch
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ConflictError
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

ROUTES = Path(__file__).parents[1] / "app/api/v2/inventory.py"


async def _an_item() -> tuple[uuid.UUID, uuid.UUID, int]:
    factory = get_sessionmaker()
    async with factory() as session:
        account = await identity.register_account(session, str(uuid.uuid4()))
        await session.flush()
        item = await inventory.create_item(
            session,
            account_id=account.id,
            body=ItemCreate(category="wardrobe", display_name="Base"),
        )
        await session.commit()
        return account.id, item.id, item.version


async def _two_overlapping_edits(account_id, item_id, start) -> list[str]:
    """One writer holds its transaction open while the other reads."""
    factory = get_sessionmaker()

    async def first() -> str:
        async with factory() as session:
            item = await inventory.owned_item(session, account_id, item_id, for_update=True)
            await asyncio.sleep(0.35)  # the second writer reads during this
            await inventory.update_item(
                session, item, ItemPatch(display_name="phone", expected_version=start)
            )
            await session.commit()
            return "committed"

    async def second() -> str:
        await asyncio.sleep(0.1)
        async with factory() as session:
            try:
                item = await inventory.owned_item(session, account_id, item_id, for_update=True)
                await inventory.update_item(
                    session, item, ItemPatch(display_name="tablet", expected_version=start)
                )
                await session.commit()
                return "committed"
            except ConflictError:
                return "refused"

    return list(await asyncio.gather(first(), second()))


async def test_the_second_writer_is_refused_rather_than_silently_dropped(db_clean):
    account_id, item_id, start = await _an_item()

    outcomes = await _two_overlapping_edits(account_id, item_id, start)

    assert outcomes == ["committed", "refused"], (
        "both edits committed: the second overwrote the first with no error, "
        "which is the lost update this lock exists to prevent"
    )


async def test_the_version_counts_exactly_the_edits_that_landed(db_clean):
    account_id, item_id, start = await _an_item()

    await _two_overlapping_edits(account_id, item_id, start)

    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )).scalar_one()
    assert row.version == start + 1, "one edit landed, so the version moved by one"
    assert row.display_name == "phone", "the writer that was not refused is the one that won"


async def test_an_edit_that_sends_no_version_still_works(db_clean):
    """``expected_version`` is optional and stays optional: a client that does
    not track versions is not broken by the lock."""
    account_id, item_id, _ = await _an_item()

    factory = get_sessionmaker()
    async with factory() as session:
        item = await inventory.owned_item(session, account_id, item_id, for_update=True)
        await inventory.update_item(session, item, ItemPatch(display_name="No version sent"))
        await session.commit()

    async with factory() as session:
        row = (await session.execute(
            select(InventoryItem).where(InventoryItem.id == item_id)
        )).scalar_one()
    assert row.display_name == "No version sent"


async def test_every_route_that_writes_an_item_takes_the_lock():
    """The lock lives at the call site, so a new write route can forget it.

    Reads deliberately do not lock — a GET that blocks behind someone else's
    edit would be a worse product — so this cannot simply require it everywhere.
    """
    source = ROUTES.read_text()
    unlocked = []
    for match in re.finditer(r"@router\.(get|post|patch|delete)\((.*?)\n(?:async )?def (\w+)\(", source, re.S):
        method, _path, handler = match.groups()
        body = source[match.end():]
        body = body[: body.find("\n@router.")] if "\n@router." in body else body
        if "service.owned_item(" not in body:
            continue
        writes = any(token in body for token in ("session.commit()", "update_item", "archive_item"))
        if writes and "for_update=True" not in body:
            unlocked.append(f"{method.upper()} {handler}")
    assert not unlocked, (
        "these routes read an item and then write it, without the row lock that "
        f"makes expected_version mean anything: {unlocked}"
    )
