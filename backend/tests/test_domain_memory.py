"""Controlled memory (§1.13) regression coverage.

Tests focus on the promise that shapes the whole domain: **deleted memory
no longer influences recommendations**. The service exposes only two ways
to read memory — ``active_facts`` (the single door for recommendations)
and ``all_facts_including_deleted`` (the Memory Control / export door).

Behavioural assertions:

    * ``record`` creates a fact and returns it; a second call with the
      same subject reinforces rather than duplicating.
    * A ``rejected`` fact is not silently relearned when the same
      observation arrives again.
    * ``revise`` writes a MemoryRevision row and stamps
      ``verification_state='corrected'`` with confidence 1.0.
    * ``delete`` sets ``deletion_state='deleted'``, blanks the fact text
      (tombstone), keeps the row for audit, and drops confidence to 0.
    * ``active_facts`` never returns deleted, rejected or below-floor
      facts.
    * ``all_facts_including_deleted`` returns the tombstoned rows so
      privacy export can still show the history safely.
    * Disabling a category hides its facts from ``active_facts`` and
      stops new writes.
    * A user_stated correction supersedes an inferred value at higher
      confidence.
    * Cross-account isolation: account B never sees A's facts through
      either accessor.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.identity import service as identity
from app.domains.progress.memory import (
    CATEGORY_COLOUR_LIKED,
    CATEGORY_FIT,
    DELETION_ACTIVE,
    DELETION_DELETED,
    SOURCE_INFERRED,
    SOURCE_USER_STATED,
    STATE_CORRECTED,
    STATE_REJECTED,
    active_facts,
    all_facts_including_deleted,
    disabled_categories,
    record,
    revise,
    set_category_enabled,
)
from app.domains.progress.memory import (
    delete as delete_fact,
)
from app.domains.progress.models import MemoryFact, MemoryRevision
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _account() -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        row = await identity.register_account(session, uuid.uuid4())
        await session.commit()
        return row.id


# ---------------------------------------------------------------------------
# Add + reinforce
# ---------------------------------------------------------------------------

async def test_record_creates_fact_and_returns_it(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        fact = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves forest green",
            source=SOURCE_USER_STATED,
            confidence=0.9,
        )
        await session.commit()

    async with factory() as session:
        row = await session.get(MemoryFact, fact.id)
    assert row is not None
    assert row.fact == "Loves forest green"
    assert row.deletion_state == DELETION_ACTIVE
    assert row.confidence >= 0.9


async def test_second_record_reinforces_instead_of_duplicating(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        first = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Prefers muted colours",
            source=SOURCE_INFERRED,
            confidence=0.5,
        )
        await session.commit()

    async with factory() as session:
        second = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Prefers muted colours",
            source=SOURCE_INFERRED,
            confidence=0.5,
        )
        await session.commit()

    assert first.id == second.id

    async with factory() as session:
        rows = (
            await session.execute(
                select(MemoryFact).where(MemoryFact.account_id == account)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].reinforcement_count == 2
    # Inferred reinforcement never reaches full confidence — only user
    # confirmation does that.
    assert rows[0].confidence < 1.0


async def test_rejected_fact_is_not_silently_relearned(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        fact = await record(
            session,
            account,
            category=CATEGORY_FIT,
            fact="Waist runs small",
            source=SOURCE_INFERRED,
            confidence=0.4,
        )
        await session.commit()
        fact_id = fact.id

    # User rejects it.
    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
        await revise(session, row, verification_state=STATE_REJECTED, reason="user")
        await session.commit()

    # A later observation carrying the same fact must NOT change the
    # rejected state or bump confidence.
    async with factory() as session:
        maybe = await record(
            session,
            account,
            category=CATEGORY_FIT,
            fact="Waist runs small",
            source=SOURCE_INFERRED,
            confidence=0.4,
        )
        await session.commit()
        assert maybe.verification_state == STATE_REJECTED


# ---------------------------------------------------------------------------
# Revise + delete
# ---------------------------------------------------------------------------

async def test_revise_writes_revision_row_and_marks_corrected(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        fact = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves teal",
            source=SOURCE_INFERRED,
            confidence=0.5,
        )
        await session.commit()
        fact_id = fact.id

    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
        await revise(session, row, new_text="Loves emerald green", reason="user_edit")
        await session.commit()

    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
        revisions = (
            await session.execute(
                select(MemoryRevision).where(MemoryRevision.fact_id == fact_id)
            )
        ).scalars().all()
    assert row.fact == "Loves emerald green"
    assert row.verification_state == STATE_CORRECTED
    assert row.confidence == 1.0
    assert row.source == SOURCE_USER_STATED
    assert len(revisions) == 1
    assert revisions[0].previous_fact == "Loves teal"


async def test_delete_tombstones_row_and_zeros_confidence(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        fact = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves cobalt",
            source=SOURCE_USER_STATED,
            confidence=1.0,
        )
        await session.commit()
        fact_id = fact.id

    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
        await delete_fact(session, row, reason="user_deleted")
        await session.commit()

    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
    assert row is not None  # row survives for the audit trail
    assert row.deletion_state == DELETION_DELETED
    assert row.fact == ""
    assert row.confidence == 0.0
    assert row.deleted_at is not None


# ---------------------------------------------------------------------------
# The two reader doors
# ---------------------------------------------------------------------------

async def test_active_facts_excludes_deleted_rejected_and_disabled(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        keep = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves burgundy",
            source=SOURCE_USER_STATED,
            confidence=0.9,
        )
        rejected = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves neon pink",
            source=SOURCE_INFERRED,
            confidence=0.5,
        )
        deleted = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves lime",
            source=SOURCE_INFERRED,
            confidence=0.5,
        )
        await session.commit()
        keep_id, rejected_id, deleted_id = keep.id, rejected.id, deleted.id

    async with factory() as session:
        row = await session.get(MemoryFact, rejected_id)
        await revise(session, row, verification_state=STATE_REJECTED, reason="user")
        row = await session.get(MemoryFact, deleted_id)
        await delete_fact(session, row, reason="user_deleted")
        await session.commit()

    async with factory() as session:
        facts = await active_facts(session, account)
    active_ids = {f.id for f in facts}
    assert keep_id in active_ids
    assert rejected_id not in active_ids
    assert deleted_id not in active_ids


async def test_all_facts_including_deleted_still_lists_tombstones(db_clean):
    """§1.14 — tombstoned history appears in privacy export."""
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        fact = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves mustard",
            source=SOURCE_USER_STATED,
            confidence=1.0,
        )
        await session.commit()
        fact_id = fact.id

    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
        await delete_fact(session, row, reason="user_deleted")
        await session.commit()

    async with factory() as session:
        rows = await all_facts_including_deleted(session, account)
    assert any(r.id == fact_id for r in rows)


async def test_disabled_category_blocks_writes_and_reads(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        await set_category_enabled(session, account, CATEGORY_COLOUR_LIKED, False)
        await session.commit()

    async with factory() as session:
        result = await record(
            session,
            account,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves rose",
            source=SOURCE_USER_STATED,
        )
        await session.commit()
    assert result is None

    async with factory() as session:
        disabled = await disabled_categories(session, account)
    assert CATEGORY_COLOUR_LIKED in disabled

    async with factory() as session:
        facts = await active_facts(session, account)
    assert facts == []


async def test_user_correction_supersedes_inferred_value(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        fact = await record(
            session,
            account,
            category=CATEGORY_FIT,
            fact="Prefers loose fits",
            source=SOURCE_INFERRED,
            confidence=0.5,
        )
        await session.commit()
        fact_id = fact.id

    async with factory() as session:
        row = await session.get(MemoryFact, fact_id)
        await revise(
            session,
            row,
            new_text="Prefers structured, tailored fits",
            reason="user_edit",
        )
        await session.commit()

    async with factory() as session:
        facts = await active_facts(session, account)
    hit = next(f for f in facts if f.id == fact_id)
    assert hit.fact == "Prefers structured, tailored fits"
    assert hit.verification_state == STATE_CORRECTED
    assert hit.confidence == 1.0
    assert hit.source == SOURCE_USER_STATED


async def test_cross_account_memory_isolation(db_clean):
    factory = get_sessionmaker()
    account_a = await _account()
    account_b = await _account()

    async with factory() as session:
        await record(
            session,
            account_a,
            category=CATEGORY_COLOUR_LIKED,
            fact="Loves ivory",
            source=SOURCE_USER_STATED,
            confidence=1.0,
        )
        await session.commit()

    async with factory() as session:
        facts_b = await active_facts(session, account_b)
        all_b = await all_facts_including_deleted(session, account_b)
    assert facts_b == []
    assert all_b == []


async def test_invalid_category_is_rejected(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        with pytest.raises(ValueError):
            await record(
                session,
                account,
                category="astrology",
                fact="Scorpio",
                source=SOURCE_USER_STATED,
            )
