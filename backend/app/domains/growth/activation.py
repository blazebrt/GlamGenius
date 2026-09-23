"""What "activated" means, derived every time from rows that already exist.

A person is activated when GlamGenius has given them a useful answer: at least
one scan attached to their account whose outcome was a product we could speak
about. Nothing here is stored. A table that remembered "activated" would be a
second copy of a fact ``scan_events`` already holds, and the day the two
disagreed nobody could say which one was true.

What does **not** activate an account, deliberately:

* ``not_found`` — the product was not there to answer about. A scan we could
  not answer is the product failing somebody, not somebody finding it useful.
* signing up, opening the app, finishing registration — none of those is an
  answer.
* an anonymous scan that no account has claimed. Scans a device made before
  its owner signed up are attached when the device is claimed
  (``product.service.attach_scans_to_account``), and from then on they are
  that account's scans and count like any other.
"""
from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.product.models import ScanEvent

#: The outcomes that mean the scan produced an answer. ``not_found`` is absent
#: on purpose. Kept as a tuple of literals, not derived from anything, so
#: widening it is a reviewed diff.
USEFUL_SCAN_OUTCOMES: tuple[str, ...] = ("found_local", "found_off", "label_captured")


def useful_scan_filter() -> ColumnElement[bool]:
    """The one predicate every activation reader shares."""
    return ScanEvent.account_id.is_not(None) & ScanEvent.outcome.in_(USEFUL_SCAN_OUTCOMES)


async def has_useful_scan(session: AsyncSession, account_id: uuid.UUID) -> bool:
    """True when this account has at least one useful scan. Reads only."""
    return bool(await session.scalar(
        select(exists().where(useful_scan_filter(), ScanEvent.account_id == account_id))
    ))


__all__ = ["USEFUL_SCAN_OUTCOMES", "has_useful_scan", "useful_scan_filter"]
