"""Regression coverage for the consent domain.

Consent rows are append-only. A grant plus a later revoke must not delete
the earlier row. Version bumps must invalidate old agreements. The audit
trail must record both grant and revoke actions.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.config import CONSENT_VERSION
from app.domains.audit.models import (
    ACTION_CONSENT_GRANTED,
    ACTION_CONSENT_REVOKED,
    AuditEvent,
)
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS, Consent
from app.domains.identity import service as identity
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ConsentRequiredError


pytestmark = pytest.mark.asyncio


async def _register(account_id: uuid.UUID) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()


async def test_grant_writes_row_and_marks_current(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    await _register(account_id)

    async with factory() as session:
        await consent_service.record(
            session, account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=True, source="pytest",
        )
        await session.commit()

    async with factory() as session:
        assert await consent_service.has_consent(
            session, account_id, CONSENT_PHOTO_ANALYSIS
        )
        summary = await consent_service.summary(session, account_id)
    assert summary["photo_analysis"]["granted"] is True
    assert summary["photo_analysis"]["version"] == CONSENT_VERSION


async def test_revoke_appends_a_new_row_and_flips_state(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    await _register(account_id)

    async with factory() as session:
        await consent_service.record(
            session, account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=True, source="pytest",
        )
        await session.commit()

    async with factory() as session:
        await consent_service.record(
            session, account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=False, source="pytest",
        )
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(
            select(Consent)
            .where(Consent.account_id == account_id)
            .order_by(Consent.recorded_at.asc())
        )).scalars().all()
        # Append-only: both rows must exist.
        assert len(rows) == 2
        assert rows[0].granted is True and rows[1].granted is False
        # Current state is not granted.
        assert not await consent_service.has_consent(
            session, account_id, CONSENT_PHOTO_ANALYSIS
        )


async def test_older_version_grant_does_not_count_as_current(db_clean):
    """A grant against an older version becomes ``needs_refresh``."""
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    await _register(account_id)

    async with factory() as session:
        # Simulate a legacy grant on an older wording by writing the row
        # directly with a different version.
        from app.shared.database.base import utcnow

        session.add(Consent(
            account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS,
            granted=True,
            version="v0-legacy",
            source="pytest",
            recorded_at=utcnow(),
        ))
        await session.commit()

    async with factory() as session:
        assert not await consent_service.has_consent(
            session, account_id, CONSENT_PHOTO_ANALYSIS
        )
        summary = await consent_service.summary(session, account_id)
    assert summary["photo_analysis"]["needs_refresh"] is True


async def test_require_analysis_consent_raises_when_missing(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    await _register(account_id)

    async with factory() as session:
        with pytest.raises(ConsentRequiredError):
            await consent_service.require_analysis_consent(session, account_id)


async def test_grant_and_revoke_emit_audit_entries(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    await _register(account_id)

    async with factory() as session:
        await consent_service.record(
            session, account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=True,
        )
        await consent_service.record(
            session, account_id=account_id,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=False,
        )
        await session.commit()

    async with factory() as session:
        actions = (await session.execute(
            select(AuditEvent.action)
            .where(AuditEvent.account_id == account_id)
            .order_by(AuditEvent.created_at.asc())
        )).scalars().all()
    assert ACTION_CONSENT_GRANTED in actions
    assert ACTION_CONSENT_REVOKED in actions


async def test_consent_scoped_per_account(db_clean):
    factory = get_sessionmaker()
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    await _register(account_a)
    await _register(account_b)

    async with factory() as session:
        await consent_service.record(
            session, account_id=account_a,
            consent_type=CONSENT_PHOTO_ANALYSIS, granted=True,
        )
        await session.commit()

    async with factory() as session:
        assert await consent_service.has_consent(
            session, account_a, CONSENT_PHOTO_ANALYSIS
        )
        assert not await consent_service.has_consent(
            session, account_b, CONSENT_PHOTO_ANALYSIS
        )
        # B has no rows at all.
        count_b = (await session.execute(
            select(func.count(Consent.id)).where(Consent.account_id == account_b)
        )).scalar_one()
    assert count_b == 0
