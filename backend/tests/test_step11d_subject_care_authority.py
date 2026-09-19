"""Step 11D: one shelf, several people, and whose Care state is whose.

The account owns the bottle. What each person has decided about it — paused,
preferred, answered in the Shelf Manager — is theirs. That sentence is the whole
slice, and everything below is a way of catching it being untrue.

Three families of failure are what these tests exist for.

**Silent divergence.** The household path was originally a second copy of the
pause and prefer algorithms, and a copy loses things quietly: the eligibility
checks, the "you already did that" answer, the same-slot replacement, the
before-and-after fingerprints. None of those failures raise; they just make a
member's Care work slightly differently from everybody else's. So the matrices
here run the *same* assertions for the account holder and for a member.

**Leaking across people.** One member's pause showing up in another member's
routine is not a cosmetic bug — it is the product telling somebody their own
shelf says something it does not.

**Inheriting the past.** Rows written before a household existed belong to the
one person who could have written them, and only while that is provably true.
A member must never inherit them, and neither must anybody once the household
makes the answer ambiguous.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import date, timedelta

import pytest
from app.domains.care.models import CareProductPreference
from app.domains.care.product_preferences import (
    CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
    CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
)
from app.domains.family.models import FamilyCircle
from app.domains.inventory.models import InventoryAttribute, InventoryEvent
from app.domains.routines import manager
from app.domains.routines.models import Routine, RoutineStep
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select, text

from tests.conftest import auth
from tests.test_step10b_shelf_manager_api import _add
from tests.test_v3_03_3_integration import _seed

pytestmark = pytest.mark.asyncio

TODAY = date.today()
PROFILES_URL = "/api/v2/family-circle/profiles"
MANAGER_URL = "/api/v2/shelf/manager"
RESPOND_URL = "/api/v2/shelf/manager/respond"


async def test_public_care_writers_are_self_only_and_raw_storage_is_not_exported():
    """No caller-supplied Python object is an authority boundary.

    This deliberately checks the public domain surface, not just the HTTP
    route.  Internal Manager application is allowed to carry a subject claim,
    but it must enter through its private wrapper and be canonicalized again.
    """
    from app.domains.care import subject_preferences
    from app.domains.routines import service

    for writer in (
        service.pause_care_product,
        service.resume_care_product,
        service.prefer_care_product,
        service.unprefer_care_product,
    ):
        assert tuple(inspect.signature(writer).parameters) == (
            "session", "account_id", "account_id_str", "item_id",
        )
    assert {
        "apply_subject_preference",
        "claim_preference_ownership",
        "current_authority_source",
    }.isdisjoint(subject_preferences.__all__)


async def test_manager_care_boundary_rejects_a_foreign_subject_claim_before_writing(
    app_client, db_clean, registered_supabase_user,
):
    """Manager's second canonicalization is a write boundary, not ceremony."""
    from app.domains.family.decision_subject import DecisionSubject
    from app.domains.family.subject import (
        AGE_BAND_NOT_STATED,
        SUBJECT_ACCOUNT_HOLDER,
        ResolvedSubject,
        SubjectNotFound,
    )
    from app.domains.routines.service import _pause_care_product_for_manager

    token_a, account_a = await registered_supabase_user()
    token_b, _account_b = await registered_supabase_user()
    await _seed(app_client)
    await _member(app_client, token_a)
    foreign_member = await _member(app_client, token_b)
    item_id = await _add(
        app_client, token_a, name="A Cleanser", product_type="cleanser",
        expiry=TODAY + timedelta(days=400),
    )
    forged = DecisionSubject(
        subject=ResolvedSubject(
            kind=SUBJECT_ACCOUNT_HOLDER, account_id=account_a,
            subject_id=uuid.UUID(foreign_member), relation="self",
            age_band=AGE_BAND_NOT_STATED,
        ),
        circle_created_at=None,
    )

    async with get_sessionmaker()() as session:
        with pytest.raises(SubjectNotFound) as raised:
            await _pause_care_product_for_manager(
                session, account_id=account_a, account_id_str=str(account_a),
                item_id=uuid.UUID(item_id), subject_claim=forged,
            )
    assert foreign_member not in str(raised.value)
    assert await _preferences(account_a) == []
    assert await _care_events(item_id) == []
    assert await _routine_fingerprint(account_a) == []


async def test_manager_care_boundary_canonicalizes_a_member_forged_as_self(
    app_client, db_clean, registered_supabase_user,
):
    """A named member cannot turn their mutation into an account-holder write."""
    from app.domains.family.decision_subject import DecisionSubject
    from app.domains.family.subject import (
        AGE_BAND_NOT_STATED,
        SUBJECT_ACCOUNT_HOLDER,
        ResolvedSubject,
    )
    from app.domains.routines.service import _pause_care_product_for_manager

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    member = await _member(app_client, token)
    item_id = await _add(
        app_client, token, name="Member Cleanser", product_type="cleanser",
        expiry=TODAY + timedelta(days=400),
    )
    forged = DecisionSubject(
        subject=ResolvedSubject(
            kind=SUBJECT_ACCOUNT_HOLDER, account_id=account_id,
            subject_id=uuid.UUID(member), relation="self",
            age_band=AGE_BAND_NOT_STATED,
        ),
        circle_created_at=None,
    )
    before = await _routine_fingerprint(account_id)
    async with get_sessionmaker()() as session:
        result = await _pause_care_product_for_manager(
            session, account_id=account_id, account_id_str=str(account_id),
            item_id=uuid.UUID(item_id), subject_claim=forged,
        )
        await session.commit()
    assert result["changed"] is True
    assert await _preferences(account_id) == [(member, item_id, "paused", "shelf_manager")]
    assert await _care_events(item_id) == [
        ("care_routine_paused", member, {"from_state": "active", "to_state": "paused"}),
    ]
    assert await _routine_fingerprint(account_id) == before


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _member(client, token: str, *, relation: str = "adult") -> str:
    response = await client.post(
        PROFILES_URL, headers=auth(token), json={"relation": relation},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _for(subject_id: str | None, url: str) -> str:
    return url if subject_id is None else f"{url}?subject_id={subject_id}"


async def _queue(client, token: str, subject_id: str | None = None) -> dict:
    response = await client.get(_for(subject_id, MANAGER_URL), headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _answer(
    client, token: str, primary: dict, choice: str, *,
    subject_id: str | None = None, key: str | None = None,
):
    return await client.post(
        _for(subject_id, RESPOND_URL),
        headers=auth(token),
        json={
            "decision_key": primary["decision_key"],
            "decision_fingerprint": primary["decision_fingerprint"],
            "choice": choice,
            "client_mutation_id": key or f"mut-{uuid.uuid4().hex[:12]}",
        },
    )


async def _expired_shelf(client, token: str) -> str:
    """One expired product plus enough around it to be a realistic shelf."""
    item_id = await _add(
        client, token, name="Expired Cleanser", product_type="cleanser",
        expiry=TODAY - timedelta(days=30),
    )
    await _add(client, token, name="Good Moisturiser", product_type="moisturiser",
               expiry=TODAY + timedelta(days=400))
    await _add(client, token, name="Good Sunscreen", product_type="sunscreen",
               expiry=TODAY + timedelta(days=400))
    return item_id


async def _preferences(account_id: uuid.UUID) -> list[tuple[str, str, str, str]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(CareProductPreference)
            .where(CareProductPreference.account_id == account_id)
            .order_by(CareProductPreference.created_at, CareProductPreference.id)
        )).scalars().all()
    return [
        (str(row.household_subject_id), str(row.inventory_item_id),
         row.preference_kind, row.authority_source)
        for row in rows
    ]


async def _legacy_rows(item_id: str, key: str) -> list[InventoryAttribute]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(select(InventoryAttribute).where(
            InventoryAttribute.item_id == uuid.UUID(item_id),
            InventoryAttribute.key == key,
        ))).scalars().all())


async def _care_events(item_id: str) -> list[tuple[str, str | None, dict]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(InventoryEvent)
            .where(
                InventoryEvent.item_id == uuid.UUID(item_id),
                InventoryEvent.event_type.in_((
                    "care_routine_paused", "care_routine_resumed",
                    "care_routine_preferred", "care_routine_preference_cleared",
                )),
            )
            .order_by(InventoryEvent.created_at, InventoryEvent.id)
        )).scalars().all()
    return [
        (row.event_type,
         str(row.household_subject_id) if row.household_subject_id else None,
         row.payload)
        for row in rows
    ]


async def _routine_fingerprint(account_id: uuid.UUID) -> list[tuple[str, int, str]]:
    """The persisted routine, as a comparable shape."""
    async with get_sessionmaker()() as session:
        routines = (await session.execute(
            select(Routine).where(Routine.account_id == account_id).order_by(Routine.kind)
        )).scalars().all()
        out: list[tuple[str, int, str]] = []
        for routine in routines:
            steps = (await session.execute(
                select(RoutineStep).where(RoutineStep.routine_id == routine.id)
                .order_by(RoutineStep.position)
            )).scalars().all()
            out.append((
                routine.kind, routine.version,
                "|".join(f"{step.position}:{step.inventory_item_id}" for step in steps),
            ))
    return out


async def _active_keys(account_id: uuid.UUID, subject_id: str | None) -> set[str]:
    """Every decision in one subject's queue, not only the one at the front.

    The route deliberately returns a single decision — one at a time is the
    manager's whole premise — so a test that needs to see the rest compiles the
    queue through the same domain entry point the route uses.
    """
    from app.domains.family.decision_subject import DecisionSubject
    from app.domains.family.subject import (
        AGE_BAND_NOT_STATED,
        SUBJECT_HOUSEHOLD_MEMBER,
        ResolvedSubject,
    )

    claim = None if subject_id is None else DecisionSubject(
        subject=ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=account_id,
            subject_id=uuid.UUID(subject_id), relation="adult",
            age_band=AGE_BAND_NOT_STATED,
        ),
        circle_created_at=None,
    )
    async with get_sessionmaker()() as session:
        queue = await manager.build_queue(
            session, account_id=account_id, decision_subject=claim,
        )
    return {row.decision_key for row in queue.active}


async def _backdate_circle(account_id: uuid.UUID, *, seconds: int) -> None:
    """Move this account's household start back, so "before" is reachable.

    Legacy attribution turns on ``FamilyCircle.created_at``, and in a test both
    the attribute row and the circle are written in the same second. Moving the
    circle rather than the attribute keeps the row exactly as the pre-household
    product wrote it.
    """
    async with get_sessionmaker()() as session:
        circle = (await session.execute(
            select(FamilyCircle).where(FamilyCircle.account_id == account_id)
        )).scalar_one()
        circle.created_at = utcnow() + timedelta(seconds=seconds)
        await session.commit()


# ---------------------------------------------------------------------------
# The member's routine boundary
# ---------------------------------------------------------------------------


async def test_a_member_answering_their_manager_does_not_rewrite_the_stored_routine(
    app_client, db_clean, registered_supabase_user,
):
    """Persisted routines belong to the account holder, and stay that way.

    A member pausing a product changes what *they* are shown. Step 11D does not
    give them a stored ``Routine`` of their own, so the one that exists must not
    move: the account holder follows it, and rewriting it would mean one
    person's decision silently editing another person's morning.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    member = await _member(app_client, token)

    # Give the account holder a stored routine to protect.
    generated = await app_client.post(
        "/api/v2/routines/generate", headers=auth(token), json={"explain": False},
    )
    assert generated.status_code == 200, generated.text
    before = await _routine_fingerprint(account_id)
    assert before, "the account holder should have a stored routine to protect"

    primary = (await _queue(app_client, token, member))["primary"]
    assert primary["action"]["inventory_item_id"] == item_id
    response = await _answer(app_client, token, primary, "accept", subject_id=member)
    assert response.status_code == 200, response.text
    assert response.json()["applied"]["action_applied"] is True

    assert await _routine_fingerprint(account_id) == before
    # And it really did land — for the member, in the member's own store.
    assert await _preferences(account_id) == [
        (member, item_id, "paused", "shelf_manager"),
    ]


async def test_the_account_holder_answering_their_manager_still_regenerates_the_routine(
    app_client, db_clean, registered_supabase_user,
):
    """The Step 10B behaviour survives the household, unchanged.

    The account holder's stored routine is reconciled after their own Care
    change, exactly as before. If sharing one algorithm between the two paths
    had cost this, a household would have quietly stopped keeping the routine
    the person actually follows up to date.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    await _member(app_client, token)
    await app_client.post(
        "/api/v2/routines/generate", headers=auth(token), json={"explain": False},
    )
    before = await _routine_fingerprint(account_id)

    primary = (await _queue(app_client, token))["primary"]
    assert primary["action"]["inventory_item_id"] == item_id
    response = await _answer(app_client, token, primary, "accept")
    assert response.status_code == 200, response.text

    after = await _routine_fingerprint(account_id)
    assert after != before, "the account holder's stored routine was not reconciled"
    # Their own preference is stored against them by name, because a household
    # exists — not left in the pre-household attribute store.
    assert await _preferences(account_id) == [
        (str(await _self_subject_id(account_id)), item_id, "paused", "shelf_manager"),
    ]
    assert await _legacy_rows(item_id, CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY) == []


async def _self_subject_id(account_id: uuid.UUID) -> uuid.UUID:
    from app.domains.family.models import FamilyProfile
    from app.domains.family.subject import RELATION_SELF

    async with get_sessionmaker()() as session:
        return (await session.execute(
            select(FamilyProfile.id)
            .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
            .where(
                FamilyCircle.account_id == account_id,
                FamilyProfile.relation == RELATION_SELF,
                FamilyProfile.active.is_(True),
            )
        )).scalar_one()


# ---------------------------------------------------------------------------
# Isolation between people
# ---------------------------------------------------------------------------


async def test_an_unnamed_request_is_still_about_a_person_the_server_resolved(
    app_client, db_clean, registered_supabase_user,
):
    """Omitting the subject means "me", and the answer says who "me" was.

    A response that left the subject out when none was asked for would make the
    default case the one the client cannot reason about: it could not tell
    whether it was reading the account holder's queue or an account-wide one,
    and in a household those are different things. The coverage statement has
    the same problem — "your history may be incomplete" is exactly the sentence
    the person browsing without a selector needs.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    await _member(app_client, token)

    queue = await _queue(app_client, token)
    assert queue["subject"] == {
        "household_subject_id": str(await _self_subject_id(account_id)),
        "is_account_holder": True,
    }
    assert queue["manager_history_coverage"] == {
        "unattributed_legacy_events_present": False,
        "complete_for_subject": True,
    }


async def test_one_members_pause_is_invisible_to_everybody_else(
    app_client, db_clean, registered_supabase_user,
):
    """The bottle is shared. The decision about it is not."""
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    first = await _member(app_client, token)
    second = await _member(app_client, token)

    primary = (await _queue(app_client, token, first))["primary"]
    assert (await _answer(app_client, token, primary, "accept", subject_id=first)).status_code == 200

    # The second member and the account holder are both still being asked.
    for subject in (second, None):
        queue = await _queue(app_client, token, subject)
        assert queue["primary"] is not None
        assert queue["primary"]["decision_key"] == primary["decision_key"], subject

    # And the shelf summary agrees with the queue, per person.
    for subject, expected in ((first, True), (second, False), (None, False)):
        summary = await app_client.get(
            _for(subject, "/api/v2/shelf/summary"), headers=auth(token),
        )
        assert summary.status_code == 200, summary.text

    assert await _preferences(account_id) == [(first, item_id, "paused", "shelf_manager")]


async def test_one_members_override_does_not_silence_anybody_elses_queue(
    app_client, db_clean, registered_supabase_user,
):
    """Saying "not now" is a personal answer, not a household setting."""
    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    first = await _member(app_client, token)
    second = await _member(app_client, token)

    primary = (await _queue(app_client, token, first))["primary"]
    assert (await _answer(
        app_client, token, primary, "override", subject_id=first,
    )).status_code == 200

    quiet = await _queue(app_client, token, first)
    assert quiet["primary"] is None or quiet["primary"]["decision_key"] != primary["decision_key"]

    for subject in (second, None):
        still_asked = await _queue(app_client, token, subject)
        assert still_asked["primary"] is not None, subject
        assert still_asked["primary"]["decision_key"] == primary["decision_key"], subject


async def test_an_allergy_pause_stays_the_account_holders_own(
    app_client, db_clean, registered_supabase_user,
):
    """A confirmed allergy belongs to a body, and bodies are not shared.

    The account holder declares it; the member's queue is compiled from the
    member's own profile and must not inherit it.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _add(app_client, token, name="Fragranced Serum", product_type="serum",
               expiry=TODAY + timedelta(days=400), actives=["fragrance"])
    await _add(app_client, token, name="Good Moisturiser", product_type="moisturiser",
               expiry=TODAY + timedelta(days=400))
    member = await _member(app_client, token)

    declared = await app_client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "allergies", "value": ["fragrance"]}]},
    )
    assert declared.status_code == 200, declared.text

    holder_keys = await _active_keys(account_id, None)
    member_keys = await _active_keys(account_id, member)
    allergy = {key for key in holder_keys if "allergy" in key}
    assert allergy, "the account holder's own allergy decision did not appear"
    assert not (allergy & member_keys), (
        "a member inherited the account holder's declared allergy"
    )


# ---------------------------------------------------------------------------
# Idempotence, the same for everybody
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("household", [False, True])
async def test_the_pause_idempotency_matrix_is_identical_with_and_without_a_household(
    app_client, db_clean, registered_supabase_user, household,
):
    """Same questions, same answers, whichever store the state lives in.

    ``changed`` and ``status`` are what a client branches on. If the household
    path answered them differently — or stopped answering them at all, which is
    what a duplicated implementation quietly does — the same tap would mean two
    different things to the same person before and after they added somebody.
    """
    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(
        app_client, token, name="Plain Moisturiser", product_type="moisturiser",
        expiry=TODAY + timedelta(days=400),
    )
    if household:
        await _member(app_client, token)

    resumed_first = await app_client.post(
        f"/api/v2/routines/products/{item_id}/resume", headers=auth(token),
    )
    assert resumed_first.status_code == 200, resumed_first.text
    assert resumed_first.json()["changed"] is False
    assert resumed_first.json()["status"] == "already_active"

    paused = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["changed"] is True
    assert paused.json()["status"] == "paused"

    again = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert again.status_code == 200, again.text
    assert again.json()["changed"] is False
    assert again.json()["status"] == "already_paused"

    resumed = await app_client.post(
        f"/api/v2/routines/products/{item_id}/resume", headers=auth(token),
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["changed"] is True
    assert resumed.json()["status"] == "active"


@pytest.mark.parametrize("household", [False, True])
async def test_the_eligibility_rules_are_identical_with_and_without_a_household(
    app_client, db_clean, registered_supabase_user, household,
):
    """The refusals are part of the contract, and a copy is where they vanish."""
    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    if household:
        await _member(app_client, token)

    perfume = await app_client.post(
        "/api/v2/inventory/items", headers=auth(token),
        json={"category": "perfumes", "display_name": "A Scent", "subcategory": "eau_de_parfum"},
    )
    assert perfume.status_code in (200, 201), perfume.text
    refused = await app_client.post(
        f"/api/v2/routines/products/{perfume.json()['id']}/pause", headers=auth(token),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["field"] == "item_id"

    # A Care product with no confirmed routine role cannot be preferred.
    unplaced = await _add(
        app_client, token, name="Unplaced Thing", product_type="mask",
        expiry=TODAY + timedelta(days=400),
    )
    preferred = await app_client.post(
        f"/api/v2/routines/products/{unplaced}/prefer", headers=auth(token),
    )
    assert preferred.status_code == 422, preferred.text
    assert preferred.json()["detail"]["field"] == "item_id"


async def test_preferring_in_a_household_still_clears_the_same_slot(
    app_client, db_clean, registered_supabase_user,
):
    """One slot, one preference — in the household store too.

    This is the rule a duplicated implementation lost first, because it is the
    only part of preferring that touches a row other than the one named.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    first = await _add(app_client, token, name="Cleanser One", product_type="cleanser",
                       expiry=TODAY + timedelta(days=400))
    second = await _add(app_client, token, name="Cleanser Two", product_type="cleanser",
                        expiry=TODAY + timedelta(days=400))
    await _member(app_client, token)
    me = str(await _self_subject_id(account_id))

    one = await app_client.post(
        f"/api/v2/routines/products/{first}/prefer", headers=auth(token),
    )
    assert one.status_code == 200, one.text
    assert one.json()["changed"] is True
    two = await app_client.post(
        f"/api/v2/routines/products/{second}/prefer", headers=auth(token),
    )
    assert two.status_code == 200, two.text
    assert two.json()["cleared_preferred_item_ids"] == [first]

    assert await _preferences(account_id) == [
        (me, second, "preferred", "direct_user"),
    ]


# ---------------------------------------------------------------------------
# The pre-household past
# ---------------------------------------------------------------------------


async def test_a_pre_household_pause_stays_the_account_holders_and_nobody_elses(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Old Moisturiser",
                         product_type="moisturiser", expiry=TODAY + timedelta(days=400))
    paused = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert paused.status_code == 200, paused.text
    assert await _legacy_rows(item_id, CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY)

    member = await _member(app_client, token)
    await _backdate_circle(account_id, seconds=5)

    from app.domains.care.subject_preferences import read_preference_state
    from app.domains.family.decision_subject import DecisionSubject
    from app.domains.family.subject import (
        AGE_BAND_NOT_STATED,
        SUBJECT_HOUSEHOLD_MEMBER,
        ResolvedSubject,
    )

    async with get_sessionmaker()() as session:
        mine, _preferred, coverage = await read_preference_state(
            session, principal_account_id=account_id, decision_subject=None,
            item_ids=(uuid.UUID(item_id),),
        )
        theirs, _their_preferred, their_coverage = await read_preference_state(
            session, principal_account_id=account_id,
            decision_subject=DecisionSubject(
                subject=ResolvedSubject(
                    kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=account_id,
                    subject_id=uuid.UUID(member), relation="adult",
                    age_band=AGE_BAND_NOT_STATED,
                ),
                circle_created_at=None,
            ),
            item_ids=(uuid.UUID(item_id),),
        )

    assert mine == frozenset({uuid.UUID(item_id)})
    assert coverage.complete_for_subject is True
    assert theirs == frozenset(), "a member inherited a pre-household pause"
    assert their_coverage.complete_for_subject is True


async def test_a_pause_written_after_the_household_belongs_to_nobody(
    app_client, db_clean, registered_supabase_user,
):
    """Ambiguity is admitted, never resolved in the account holder's favour.

    The row was written when several people already existed, so it is not
    evidence about any of them. The product says its answer is incomplete
    rather than showing a confident half of it.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Old Moisturiser",
                         product_type="moisturiser", expiry=TODAY + timedelta(days=400))
    await _member(app_client, token)
    await _backdate_circle(account_id, seconds=-5)
    # Written by the pre-household store, but after the household existed.
    async with get_sessionmaker()() as session:
        session.add(InventoryAttribute(
            item_id=uuid.UUID(item_id), key=CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
            value=True, source="user_declared", confidence=1.0,
            verification_state="confirmed",
        ))
        await session.commit()

    from app.domains.care.subject_preferences import read_preference_state

    async with get_sessionmaker()() as session:
        paused, _preferred, coverage = await read_preference_state(
            session, principal_account_id=account_id, decision_subject=None,
            item_ids=(uuid.UUID(item_id),),
        )
    assert paused == frozenset()
    assert coverage.complete_for_subject is False
    assert coverage.unattributed_legacy_preferences_present is True


async def test_a_pause_written_in_the_very_instant_the_household_began_is_nobodys(
    app_client, db_clean, registered_supabase_user,
):
    """Equality is doubt, not ownership.

    The comparison is strictly ``<``. A row whose timestamp is the household's
    creation instant could have been written by the person before they added
    anybody or by the household a microsecond later, and the two are
    indistinguishable. Resolving that toward the account holder would be the one
    place the product guesses about whose decision it is looking at — so the
    instant itself is pinned here rather than left to a test that happens to sit
    a few seconds on the safe side of it.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Old Moisturiser",
                         product_type="moisturiser", expiry=TODAY + timedelta(days=400))
    assert (await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )).status_code == 200
    await _member(app_client, token)

    async with get_sessionmaker()() as session:
        circle_created_at = await session.scalar(
            select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
        )
        # Written with SQL rather than through the ORM: ``updated_at`` carries
        # an ``onupdate``, so assigning it and flushing would quietly replace it
        # with the current time and this test would pin nothing.
        await session.execute(
            text(
                "UPDATE inventory_attributes SET updated_at = :when "
                "WHERE item_id = :item AND key = :key"
            ),
            {"when": circle_created_at, "item": uuid.UUID(item_id),
             "key": CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY},
        )
        await session.commit()
        stored = await session.scalar(
            text("SELECT updated_at FROM inventory_attributes "
                 "WHERE item_id = :item AND key = :key"),
            {"item": uuid.UUID(item_id), "key": CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY},
        )
    assert stored == circle_created_at, "the equality instant was not actually set"

    from app.domains.care.subject_preferences import read_preference_state

    async with get_sessionmaker()() as session:
        paused, _preferred, coverage = await read_preference_state(
            session, principal_account_id=account_id, decision_subject=None,
            item_ids=(uuid.UUID(item_id),),
        )
    assert paused == frozenset(), (
        "a row written in the same instant the household began was handed to the "
        "account holder"
    )
    assert coverage.complete_for_subject is False


async def test_a_preference_cannot_be_stored_against_another_accounts_product(
    app_client, db_clean, registered_supabase_user,
):
    """The item is re-derived under the principal, never taken on trust.

    ``_apply_subject_preference`` is an internal authority, and internal is
    exactly where an id arrives having been checked by somebody else. A caller
    that had already resolved the wrong item would otherwise write one account's
    person against another account's bottle, and the row would look ordinary
    from either side.
    """
    from app.domains.care.subject_preferences import _apply_subject_preference
    from app.domains.family.decision_subject import canonicalize_decision_subject_for_write
    from app.shared.errors.exceptions import IdentityInvariantError

    token, account_id = await registered_supabase_user()
    other_token, _other_account = await registered_supabase_user()
    await _seed(app_client)
    await _member(app_client, token)
    theirs = await _add(
        app_client, other_token, name="Their Moisturiser", product_type="moisturiser",
        expiry=TODAY + timedelta(days=400),
    )

    async with get_sessionmaker()() as session:
        subject = await canonicalize_decision_subject_for_write(
            session, principal_account_id=account_id, decision_subject=None,
        )
        with pytest.raises(IdentityInvariantError) as raised:
            await _apply_subject_preference(
                session, principal_account_id=account_id, subject=subject,
                item_id=uuid.UUID(theirs), kind="paused", active=True,
                authority_source="direct_user",
            )
    assert raised.value.reason == "care_preference_item_ownership_invalid"
    assert await _preferences(account_id) == []


async def test_give_back_is_offered_for_the_managers_pause_and_not_for_your_own(
    app_client, db_clean, registered_supabase_user,
):
    """The same state, two owners, two different answers.

    Read at the level the rule lives at, because the queue also suppresses a
    give-back for other reasons — an expired product stays paused whoever paused
    it — and a test that could not tell those apart would pass while the rule
    did nothing. The pause here is identical in both halves: same product, same
    person, same effective state. Only ``authority_source`` differs.
    """
    from app.domains.family.decision_subject import canonicalize_decision_subject
    from app.domains.routines import shelf as shelf_domain

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    await _member(app_client, token)

    primary = (await _queue(app_client, token))["primary"]
    assert primary["action"]["kind"] == manager.ACTION_PAUSE_PRODUCT
    assert (await _answer(app_client, token, primary, "accept")).status_code == 200
    event_count = len(await _care_events(item_id))

    async def _candidates() -> set[uuid.UUID]:
        async with get_sessionmaker()() as session:
            checked = await canonicalize_decision_subject(
                session, principal_account_id=account_id, decision_subject=None,
            )
            context = await shelf_domain.gather(
                session, account_id=account_id, decision_subject=checked,
            )
            products = {
                product.id: product
                for category in ("beauty", "hair")
                for product in shelf_domain.build(context, category)
            }
            rows = await manager.give_back_candidates(
                session, account_id=account_id, products=products,
                paused_item_ids=frozenset({uuid.UUID(item_id)}),
                decision_subject=checked,
            )
        return {row.item_id for row in rows}

    assert uuid.UUID(item_id) in await _candidates(), (
        "the manager would not offer back a pause it applied itself"
    )

    # Same state, said by the person. Nothing they can see changes.
    mine = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["changed"] is False
    changed_events = await _care_events(item_id)
    assert len(changed_events) == event_count + 1
    assert changed_events[-1] == (
        "care_routine_paused", str(await _self_subject_id(account_id)),
        {"effective_state_unchanged": True, "authority_source": "direct_user"},
    )

    assert uuid.UUID(item_id) not in await _candidates(), (
        "the manager offered to undo a pause the person made themselves"
    )
    exact_repeat = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert exact_repeat.status_code == 200, exact_repeat.text
    assert exact_repeat.json()["changed"] is False
    assert len(await _care_events(item_id)) == event_count + 1


async def test_saying_it_again_in_a_household_adopts_the_old_row_rather_than_doubling_it(
    app_client, db_clean, registered_supabase_user,
):
    """One person, one current answer per product — across both stores.

    Pausing something already paused changes nothing a customer can see. What it
    does change is where the answer lives: the pre-household row is retired into
    the household store under the person's name, because this is the one moment
    the row can be attributed truthfully.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Old Moisturiser",
                         product_type="moisturiser", expiry=TODAY + timedelta(days=400))
    assert (await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )).status_code == 200
    await _member(app_client, token)
    await _backdate_circle(account_id, seconds=5)
    me = str(await _self_subject_id(account_id))

    again = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert again.status_code == 200, again.text
    assert again.json()["changed"] is False
    assert again.json()["status"] == "already_paused"

    # The claim moved stores rather than being written twice, and it is labelled
    # for what it is: the state that was already there, carried across. Nobody
    # made a new choice, so the Manager must not read it as its own to undo.
    assert await _preferences(account_id) == [(me, item_id, "paused", "legacy_adopted")]
    assert await _legacy_rows(item_id, CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY) == []


async def test_one_person_holding_two_current_answers_stops_rather_than_guessing(
    app_client, db_clean, registered_supabase_user,
):
    """A state no route can produce, and the read refuses it anyway.

    Choosing between the two would pick which of somebody's own decisions
    counts; unioning them would invent a third answer they never gave. Neither
    is an answer, so it fails closed — on reads as well as writes, because a
    Shelf summary built on a guess is the same wrong answer arriving somewhere
    quieter.
    """
    from app.domains.care.subject_preferences import read_preference_state
    from app.shared.errors.exceptions import IdentityInvariantError

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Old Moisturiser",
                         product_type="moisturiser", expiry=TODAY + timedelta(days=400))
    assert (await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )).status_code == 200
    await _member(app_client, token)
    await _backdate_circle(account_id, seconds=5)
    me = await _self_subject_id(account_id)

    async with get_sessionmaker()() as session:
        session.add(CareProductPreference(
            account_id=account_id, household_subject_id=me,
            inventory_item_id=uuid.UUID(item_id), preference_kind="paused",
            authority_source="direct_user",
        ))
        await session.commit()

    async with get_sessionmaker()() as session:
        with pytest.raises(IdentityInvariantError) as raised:
            await read_preference_state(
                session, principal_account_id=account_id, decision_subject=None,
                item_ids=(uuid.UUID(item_id),),
            )
    # The customer sentence deliberately says nothing; the reason is log-only.
    assert raised.value.reason == "care_preference_dual_current_state"
    assert "care_preference" not in str(raised.value)


# ---------------------------------------------------------------------------
# Who set it, and what the manager may offer back
# ---------------------------------------------------------------------------


async def test_the_manager_offers_back_what_it_paused_and_not_what_you_paused(
    app_client, db_clean, registered_supabase_user,
):
    """Give-back reads who owns the pause, rather than comparing clocks.

    The manager may undo its own suggestion. It may not offer to undo a decision
    the person made themselves — that is the product arguing with them — and the
    difference is now recorded on the row instead of inferred from which write
    happened to land second.
    """
    from app.domains.care.subject_preferences import _current_authority_source
    from app.domains.family.decision_subject import canonicalize_decision_subject

    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    await _member(app_client, token)

    primary = (await _queue(app_client, token))["primary"]
    assert primary["action"]["kind"] == manager.ACTION_PAUSE_PRODUCT
    assert (await _answer(app_client, token, primary, "accept")).status_code == 200

    async with get_sessionmaker()() as session:
        subject = await canonicalize_decision_subject(
            session, principal_account_id=account_id, decision_subject=None,
        )
        assert await _current_authority_source(
            session, principal_account_id=account_id, subject=subject,
            item_id=uuid.UUID(item_id), kind="paused",
        ) == "shelf_manager"

    # Now the person says it themselves. Nothing they can see changes; who owns
    # it does, and that is what stops the manager offering it back.
    mine = await app_client.post(
        f"/api/v2/routines/products/{item_id}/pause", headers=auth(token),
    )
    assert mine.status_code == 200, mine.text
    assert mine.json()["changed"] is False

    async with get_sessionmaker()() as session:
        subject = await canonicalize_decision_subject(
            session, principal_account_id=account_id, decision_subject=None,
        )
        assert await _current_authority_source(
            session, principal_account_id=account_id, subject=subject,
            item_id=uuid.UUID(item_id), kind="paused",
        ) == "direct_user"

    assert (await _queue(app_client, token))["counts"]["give_back"] == 0, (
        "the manager offered to undo a pause the person made themselves"
    )


async def test_a_manager_applied_member_pause_records_the_governed_event(
    app_client, db_clean, registered_supabase_user,
):
    """The same event family, attributed — and carrying nothing about a body.

    A Care preference event names the account, the shared physical item and the
    person whose relationship to it changed. It carries no health fact, no free
    text, no age, no relation, and no identifier belonging to anybody else.
    """
    token, _account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    member = await _member(app_client, token)

    primary = (await _queue(app_client, token, member))["primary"]
    assert (await _answer(
        app_client, token, primary, "accept", subject_id=member,
    )).status_code == 200

    events = await _care_events(item_id)
    assert events == [("care_routine_paused", member, {"from_state": "active", "to_state": "paused"})]
    payload = events[0][2]
    assert set(payload) == {"from_state", "to_state"}
    for banned in ("age_band", "relation", "label", "note", "allergies", "profile"):
        assert banned not in payload


async def test_the_manager_history_coverage_is_the_same_answer_everywhere(
    app_client, db_clean, registered_supabase_user,
):
    """GET, POST and a replay of that POST cannot disagree about the past.

    The response to an answer used to assert complete history unconditionally,
    which meant a household with unattributable Manager answers was told its
    history was complete at the one moment it was looking most closely.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)

    # A pre-household-shaped answer, written after the household existed.
    await _backdate_circle(account_id, seconds=-5)
    from app.domains.routines.models import ShelfManagerDecisionEvent

    async with get_sessionmaker()() as session:
        session.add(ShelfManagerDecisionEvent(
            account_id=account_id, household_subject_id=None,
            decision_key="rule.legacy:item:x", decision_fingerprint="f" * 64,
            choice="accepted", action_kind="pause_product",
            target_inventory_item_id=None, client_mutation_id="legacy-unattributed",
        ))
        await session.commit()

    read = await _queue(app_client, token, member)
    assert read["manager_history_coverage"] == {
        "unattributed_legacy_events_present": True,
        "complete_for_subject": False,
    }

    primary = read["primary"]
    written = await _answer(
        app_client, token, primary, "override", subject_id=member, key="cover-1",
    )
    assert written.status_code == 200, written.text
    assert written.json()["manager_history_coverage"] == read["manager_history_coverage"]

    replayed = await _answer(
        app_client, token, primary, "override", subject_id=member, key="cover-1",
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["applied"]["replayed"] is True
    assert replayed.json()["manager_history_coverage"] == read["manager_history_coverage"]


async def test_preference_coverage_and_manager_coverage_stay_separate_answers(
    app_client, db_clean, registered_supabase_user,
):
    """Two different questions about two different tables.

    A household can have complete Manager history and incomplete preference
    history, or the reverse. Collapsing them into one flag would tell the
    customer the wrong thing is incomplete.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _add(app_client, token, name="Old Moisturiser",
                         product_type="moisturiser", expiry=TODAY + timedelta(days=400))
    member = await _member(app_client, token)
    await _backdate_circle(account_id, seconds=-5)
    async with get_sessionmaker()() as session:
        session.add(InventoryAttribute(
            item_id=uuid.UUID(item_id), key=CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
            value=True, source="user_declared", confidence=1.0,
            verification_state="confirmed",
        ))
        await session.commit()

    summary = await app_client.get(
        _for(member, "/api/v2/shelf/summary"), headers=auth(token),
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["preference_coverage"] == {
        "unattributed_legacy_preferences_present": True,
        "complete_for_subject": False,
    }

    queue = await _queue(app_client, token, member)
    assert queue["manager_history_coverage"] == {
        "unattributed_legacy_events_present": False,
        "complete_for_subject": True,
    }
    assert "preference_coverage" not in queue
