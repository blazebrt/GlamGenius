"""Two boundaries Care has to hold before it reads anybody, and the versions.

The first is the hardest rule this product has: it does not answer for a child
under twelve. "Does not answer" has to include not looking — a Care context that
read a child's recorded skin and hair attributes into memory and handed off
afterwards was refusing at the wrong moment.

The second is subtler and was hiding inside a helper. The subject resolution and
the preference read were both skipped when the session did not look like a real
one (``hasattr(session, "scalar")``), which made the strength of an identity
check depend on the shape of an object the caller supplied. An authority that
can be avoided by passing something unusual is not an authority.

The versions are here because they are a promise to a client rather than an
internal detail, and because the shapes below did not change while their meaning
did — which is exactly the case where nobody notices a missing bump.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from app.domains.care.product_preferences import (
    CARE_PRODUCT_PAUSE_VERSION,
    CARE_PRODUCT_SELECTION_PREFERENCE_VERSION,
)
from app.domains.care.schemas import CARE_CONTEXT_VERSION
from app.domains.care.service import build_care_context
from app.domains.care.subject_preferences import PreferenceCoverage
from app.domains.family.decision_subject import DecisionSubject
from app.domains.family.subject import (
    AGE_BAND_UNDER_12,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
    account_holder_subject,
)
from app.domains.planning.context import DayContext
from app.domains.routines.manager import MANAGER_CONTRACT_VERSION
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ValidationFailedError

from tests.conftest import auth
from tests.test_v3_03_3_integration import _seed

pytestmark = pytest.mark.asyncio

PROFILES_URL = "/api/v2/family-circle/profiles"


def _day(account_id: uuid.UUID) -> DayContext:
    return DayContext(
        account_id=account_id,
        plan_date=date(2026, 8, 12),
        timezone_name="UTC",
        now_local=datetime(2026, 8, 12, 9, tzinfo=UTC),
        weather=None,
        weather_snapshot_id=None,
        air_quality=None,
        air_quality_snapshot_id=None,
        events=[],
        profile={"city": "Mumbai"},
    )


async def test_the_handoff_fires_before_a_childs_profile_is_read(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """Not answering for a child means not reading about them either.

    The gate used to sit further down, inside the shelf gather, so the refusal
    was correct and the reading was not. It also failed open in one direction: a
    profile read that raised for its own reasons produced some other error
    instead of the handoff, which is precisely the uncertain case this gate is
    supposed to resolve toward handing over.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    created = await app_client.post(
        PROFILES_URL, headers=auth(token),
        json={"relation": "child", "age_band": AGE_BAND_UNDER_12},
    )
    assert created.status_code == 201, created.text
    child = uuid.UUID(created.json()["id"])

    read_attempts: list[str] = []

    async def _must_not_read(*args, **kwargs):
        read_attempts.append("subject_profile")
        raise AssertionError("a child's profile was read before the handoff")

    monkeypatch.setattr(
        "app.domains.care.service.resolve_subject_profile_for_read", _must_not_read,
    )
    monkeypatch.setattr(
        "app.domains.care.service.resolve_self_profile_for_read", _must_not_read,
    )

    claim = DecisionSubject(
        subject=ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=account_id, subject_id=child,
            relation="child", age_band=AGE_BAND_UNDER_12,
        ),
        circle_created_at=None,
    )
    async with get_sessionmaker()() as session:
        with pytest.raises(ValidationFailedError) as raised:
            await build_care_context(
                session, account_id, day_context=_day(account_id),
                decision_subject=claim,
            )

    assert read_attempts == []
    assert raised.value.extra == {"field": "subject_id"}
    # The sentence states the fact and hands over. It names no condition, offers
    # no judgement and gives no advice.
    message = str(raised.value)
    assert "doctor" in message or "pharmacist" in message
    for banned in ("diagnos", "condition", "treat", "prescri"):
        assert banned not in message.lower()


async def test_the_care_context_always_resolves_the_subject_it_is_about(
    db_clean, registered_supabase_user, monkeypatch,
):
    """Identity is resolved on every call, whatever the session looks like.

    The old code asked the session which methods it had and took the
    unauthorised branch when the answer disappointed, which meant the strength
    of the check depended on the shape of an object the caller supplied. Passing
    a double is not the proof — a double that cannot answer raises for its own
    reasons and looks like a refusal. The proof is that the resolver is actually
    called, so this counts the calls rather than watching for an exception.

    The account boundary still comes first: a mismatched day context is refused
    before any database work, and that is the one short-circuit there is.
    """
    _token, account_id = await registered_supabase_user()

    with pytest.raises(ValueError, match="does not match"):
        await build_care_context(object(), uuid.uuid4(), day_context=_day(account_id))

    resolved: list[uuid.UUID] = []

    async def _canonical(session, *, principal_account_id, decision_subject):
        resolved.append(principal_account_id)
        return DecisionSubject(
            subject=account_holder_subject(principal_account_id), circle_created_at=None,
        )

    read: list[str] = []

    async def _no_profile(*args, **kwargs):
        read.append("profile")
        return None

    async def _preferences(*args, **kwargs):
        return frozenset(), frozenset(), PreferenceCoverage()

    class _EmptyShelf:
        allergies: list[str] = []
        draft_count = 0

    async def _gather(*args, **kwargs):
        return _EmptyShelf()

    monkeypatch.setattr("app.domains.care.service.canonicalize_decision_subject", _canonical)
    monkeypatch.setattr("app.domains.care.service.resolve_self_profile_for_read", _no_profile)
    monkeypatch.setattr("app.domains.care.service.read_preference_state", _preferences)
    monkeypatch.setattr("app.domains.care.service.shelf.gather", _gather)
    monkeypatch.setattr("app.domains.care.service.shelf.build", lambda *_: [])

    context = await build_care_context(object(), account_id, day_context=_day(account_id))

    assert resolved == [account_id], (
        "the Care context produced a subject without resolving one"
    )
    assert read == ["profile"]
    assert context.account_id == account_id


def test_the_contract_versions_moved_with_their_meaning() -> None:
    """Same shape, different subject — which is when a version has to move.

    A client that cached a Step 10B queue cached one household's answers as
    everybody's, and a stored Care context assembled under the account-wide
    meaning of "paused" must not be compared with one assembled under this. None
    of that is visible in the response shape, so the version is the only place it
    can be said.
    """
    assert MANAGER_CONTRACT_VERSION == "step-11d-v1"
    assert CARE_CONTEXT_VERSION == "step-11d-v1"
    assert CARE_PRODUCT_PAUSE_VERSION == "step-11d-v1"
    assert CARE_PRODUCT_SELECTION_PREFERENCE_VERSION == "step-11d-v1"


def test_the_redundant_preference_index_is_gone() -> None:
    """One index, and it is not a prefix of the unique constraint.

    ``(account_id, household_subject_id, inventory_item_id)`` was covered by the
    constraint's own index already. It cost a write on every preference change
    and was read by nothing.
    """
    from app.domains.care.models import CareProductPreference

    indexes = {index.name: [c.name for c in index.columns] for index in
               CareProductPreference.__table__.indexes}
    assert indexes == {
        "ix_care_product_preferences_subject_kind": [
            "account_id", "household_subject_id", "preference_kind",
        ],
    }
    unique = next(
        constraint for constraint in CareProductPreference.__table__.constraints
        if constraint.name == "uq_care_product_preference_subject_item_kind"
    )
    unique_columns = [column.name for column in unique.columns]
    for name, columns in indexes.items():
        assert unique_columns[:len(columns)] != columns, (
            f"{name} is a prefix of the unique constraint and buys nothing"
        )
