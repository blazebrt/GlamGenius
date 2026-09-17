"""Step 11A — who a personalised decision is for.

Until this step, ``account_id`` answered three different questions at once:
who may make this request, whose rows are being read, and whose body the
answer is about. A household separates the third from the first two, and every
test here is about that separation holding under pressure:

* a subject identifier in a request is a *claim*, checked against the
  authenticated account, never an instruction;
* a claim for somebody else's household is answered exactly like a claim for a
  person who does not exist, because any difference between the two answers is
  itself a disclosure;
* what the server stores about a person's age can be added to by a request and
  can never be argued down by one;
* the household is exportable and erasable, which the privacy registry has
  promised since the tables were created and nothing has delivered until now.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.family import service as family_service
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    AGE_BAND_NOT_STATED,
    AGE_BAND_TEEN,
    AGE_BAND_UNDER_12,
    SUBJECT_ACCOUNT_HOLDER,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
    SubjectNotFound,
    account_holder_subject,
    resolve_subject,
    safety_for,
)
from app.domains.privacy import REGISTRY, Classification
from app.domains.privacy import export as export_service
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

CIRCLE_URL = "/api/v2/family-circle"
PROFILES_URL = "/api/v2/family-circle/profiles"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _add_member(client, token, *, relation="adult", age_band=None) -> dict:
    payload: dict[str, object] = {"relation": relation}
    if age_band is not None:
        payload["age_band"] = age_band
    response = await client.post(PROFILES_URL, headers=auth(token), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _count(model, **where) -> int:
    async with get_sessionmaker()() as session:
        stmt = select(func.count(model.id))
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return await session.scalar(stmt)


# ---------------------------------------------------------------------------
# 1. The default subject: the signed-in person
# ---------------------------------------------------------------------------
class TestDefaultSubject:
    async def test_no_subject_id_means_the_account_holder(
        self, db_clean, app_client, registered_supabase_user,
    ):
        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            subject = await resolve_subject(
                session, account_id=account_id, subject_id=None,
            )
        assert subject == account_holder_subject(account_id)
        assert subject.kind == SUBJECT_ACCOUNT_HOLDER
        assert subject.is_account_holder is True
        assert subject.subject_id is None
        assert subject.age_band == AGE_BAND_NOT_STATED
        assert subject.stated_age is None
        assert subject.is_child is False

    async def test_resolving_the_default_subject_does_not_open_a_household(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Reading a decision must not quietly create a household.

        A circle row is a thing the customer opened, and every export, every
        deletion job and every later slice reads it as such. Manufacturing one
        as a side effect of somebody scanning a bottle would make that record a
        lie on the very first request.
        """
        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            await resolve_subject(session, account_id=account_id, subject_id=None)
            await session.commit()
        assert await _count(FamilyCircle) == 0
        assert await _count(FamilyProfile) == 0


# ---------------------------------------------------------------------------
# 2. A named member of this account's household
# ---------------------------------------------------------------------------
class TestOwnHouseholdMember:
    async def test_an_active_member_resolves_with_what_the_server_stores(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _add_member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        async with get_sessionmaker()() as session:
            subject = await resolve_subject(
                session, account_id=account_id, subject_id=uuid.UUID(member["id"]),
            )
        assert subject.kind == SUBJECT_HOUSEHOLD_MEMBER
        assert subject.account_id == account_id
        assert subject.subject_id == uuid.UUID(member["id"])
        assert subject.relation == "child"
        assert subject.age_band == AGE_BAND_UNDER_12
        assert subject.is_account_holder is False
        assert subject.is_child is True

    async def test_the_self_profile_is_still_the_account_holder(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Opening a household does not turn the customer into somebody else.

        The circle is created with a ``self`` row, and a request that names it
        is asking about the signed-in person by another route. It must reach
        the same answer as omitting the subject entirely, or the same person
        would get two different decisions about the same bottle.
        """
        token, account_id = await registered_supabase_user()
        await _add_member(app_client, token, relation="adult")
        circle = (await app_client.get(CIRCLE_URL, headers=auth(token))).json()
        self_row = next(p for p in circle["profiles"] if p["relation"] == "self")
        async with get_sessionmaker()() as session:
            subject = await resolve_subject(
                session, account_id=account_id, subject_id=uuid.UUID(self_row["id"]),
            )
        assert subject.is_account_holder is True


# ---------------------------------------------------------------------------
# 3. Claims this account may not make
# ---------------------------------------------------------------------------
class TestSubjectClaimsAreChecked:
    async def test_a_member_of_another_household_does_not_resolve(
        self, db_clean, app_client, registered_supabase_user,
    ):
        _, mine = await registered_supabase_user()
        other_token, _ = await registered_supabase_user()
        theirs = await _add_member(
            app_client, other_token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await resolve_subject(
                    session, account_id=mine, subject_id=uuid.UUID(theirs["id"]),
                )

    async def test_an_identifier_nobody_ever_issued_does_not_resolve(
        self, db_clean, app_client, registered_supabase_user,
    ):
        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await resolve_subject(
                    session, account_id=account_id, subject_id=uuid.uuid4(),
                )

    async def test_a_removed_member_does_not_resolve(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Deactivation is removal as far as a decision is concerned.

        The row stays for the export and the audit trail. It stops being
        somebody this account is currently asking about, which is the only
        thing resolution is deciding.
        """
        token, account_id = await registered_supabase_user()
        member = await _add_member(app_client, token, relation="adult")
        patched = await app_client.patch(
            f"{PROFILES_URL}/{member['id']}", headers=auth(token),
            json={"active": False},
        )
        assert patched.status_code == 200, patched.text
        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await resolve_subject(
                    session, account_id=account_id, subject_id=uuid.UUID(member["id"]),
                )

    async def test_a_foreign_member_and_a_fictional_one_fail_the_same_way(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Telling the two apart would confirm the other household's member.

        "Forbidden" for a real id and "not found" for an invented one is a
        membership oracle: anyone could sweep identifiers and learn which ones
        name a real person in somebody else's home.
        """
        _, mine = await registered_supabase_user()
        other_token, _ = await registered_supabase_user()
        theirs = await _add_member(app_client, other_token, relation="adult")
        async with get_sessionmaker()() as session:
            errors = []
            for claimed in (uuid.UUID(theirs["id"]), uuid.uuid4()):
                with pytest.raises(SubjectNotFound) as raised:
                    await resolve_subject(
                        session, account_id=mine, subject_id=claimed,
                    )
                errors.append(str(raised.value))
        assert errors[0] == errors[1]


# ---------------------------------------------------------------------------
# 4. Age: stored fact against volunteered claim
# ---------------------------------------------------------------------------
class TestAgeAuthority:
    def test_each_band_reports_the_top_of_its_range(self):
        """The cautious direction is the oldest the person could be.

        Reporting a band's floor would hand the gate an age below the minimum
        for a teenager and fire a hand-over that should never have fired.
        Reporting its ceiling can only ever fail to fire one — and ``under_12``
        ceilings at 11, which is the case that must fire.
        """
        bands = {
            AGE_BAND_UNDER_12: 11,
            AGE_BAND_TEEN: 17,
            AGE_BAND_ADULT: None,
            AGE_BAND_NOT_STATED: None,
        }
        for band, expected in bands.items():
            subject = ResolvedSubject(
                kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=uuid.uuid4(),
                subject_id=uuid.uuid4(), relation="other", age_band=band,
            )
            assert subject.stated_age == expected

    def test_a_request_cannot_argue_a_stored_child_upwards(self):
        subject = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=uuid.uuid4(),
            subject_id=uuid.uuid4(), relation="child", age_band=AGE_BAND_UNDER_12,
        )
        stated_age, is_child = safety_for(
            subject, stated_age=40, subject_is_child=False,
        )
        assert stated_age == 11
        assert is_child is True

    def test_a_request_may_still_add_what_the_server_does_not_know(self):
        subject = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=uuid.uuid4(),
            subject_id=uuid.uuid4(), relation="other", age_band=AGE_BAND_NOT_STATED,
        )
        assert safety_for(subject, subject_is_child=True) == (None, True)
        assert safety_for(subject, stated_age=9) == (9, False)

    def test_a_correction_downwards_is_heard(self):
        """Somebody telling us a teenager is younger than the band says.

        Nothing about a band is a promise of accuracy. The one direction that
        matters is downwards, because that is the direction in which being
        wrong means advising a child.
        """
        subject = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=uuid.uuid4(),
            subject_id=uuid.uuid4(), relation="child", age_band=AGE_BAND_TEEN,
        )
        assert safety_for(subject, stated_age=8) == (8, False)

    def test_the_account_holder_carries_no_stored_claim(self):
        subject = account_holder_subject(uuid.uuid4())
        assert safety_for(subject) == (None, False)
        assert safety_for(subject, stated_age=30) == (30, False)


# ---------------------------------------------------------------------------
# 5. The band on the wire
# ---------------------------------------------------------------------------
class TestAgeBandApi:
    async def test_a_member_added_without_a_band_claims_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _add_member(app_client, token, relation="adult")
        assert member["age_band"] == AGE_BAND_NOT_STATED

    async def test_the_band_is_stored_and_read_back(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _add_member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        assert member["age_band"] == AGE_BAND_UNDER_12
        circle = (await app_client.get(CIRCLE_URL, headers=auth(token))).json()
        stored = next(p for p in circle["profiles"] if p["id"] == member["id"])
        assert stored["age_band"] == AGE_BAND_UNDER_12

    @pytest.mark.parametrize("band", ["infant", "under12", "", "UNDER_12", "adult"])
    async def test_a_band_outside_the_vocabulary_is_refused(
        self, db_clean, app_client, registered_supabase_user, band,
    ):
        token, _ = await registered_supabase_user()
        response = await app_client.post(
            PROFILES_URL, headers=auth(token),
            json={"relation": "child", "age_band": band},
        )
        assert response.status_code == 422, response.text

    async def test_the_self_row_created_with_the_circle_claims_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        await _add_member(app_client, token, relation="adult")
        circle = (await app_client.get(CIRCLE_URL, headers=auth(token))).json()
        self_row = next(p for p in circle["profiles"] if p["relation"] == "self")
        assert self_row["age_band"] == AGE_BAND_NOT_STATED

    async def test_the_service_refuses_a_band_the_constraint_would_reject(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The database check is the backstop, not the error message.

        A caller inside the process that skips the schema still must not be
        able to write a band the vocabulary does not contain, and must find
        that out as a domain error rather than an integrity failure.
        """
        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            with pytest.raises(family_service.FamilyProfileError):
                await family_service.add_profile(
                    session, account_id, relation="child", age_band="toddler",
                )


# ---------------------------------------------------------------------------
# 6. Two members added at once
# ---------------------------------------------------------------------------
def _release_both_before_anything_is_read(monkeypatch, barrier: asyncio.Barrier) -> None:
    """Hold both adds on the doorstep, then release them together.

    The seam is before the first statement rather than in the middle of the
    work, deliberately. Parking one request *after* it has written an
    uncommitted row makes the other wait on a lock its partner will never
    release, which is a deadlock this test invented rather than the race the
    product has. Released together at the start, both then contend for real:
    on PostgreSQL's terms, over rows, for as long as each transaction lasts.
    """
    original = family_service.add_profile
    gated: set[object] = set()

    async def _add_profile(session, account_id, **kwargs):
        task = asyncio.current_task()
        if len(gated) < 2 and task not in gated:
            gated.add(task)
            await asyncio.wait_for(barrier.wait(), timeout=60)
        return await original(session, account_id, **kwargs)

    monkeypatch.setattr(family_service, "add_profile", _add_profile)


class TestConcurrentMemberCreation:
    async def test_two_adds_at_once_take_two_different_positions(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """Somebody tapping add twice must not get a 500.

        Both requests read the same set of taken positions, so without a lock
        both pick the same number and the second violates the uniqueness of
        (circle, position). The circle row is locked before the position is
        chosen, which turns choosing and taking it into one serialised step.
        """
        token, _ = await registered_supabase_user()
        # Open the circle first so this test races the position, not the
        # circle — that race has its own test below.
        await _add_member(app_client, token, relation="adult")

        _release_both_before_anything_is_read(monkeypatch, asyncio.Barrier(2))
        first, second = await asyncio.gather(
            app_client.post(PROFILES_URL, headers=auth(token), json={"relation": "adult"}),
            app_client.post(PROFILES_URL, headers=auth(token), json={"relation": "other"}),
        )

        for response in (first, second):
            assert response.status_code == 201, response.text
            assert "UniqueViolation" not in response.text
        positions = sorted(r.json()["position"] for r in (first, second))
        assert len(set(positions)) == 2, positions
        circle = (await app_client.get(CIRCLE_URL, headers=auth(token))).json()
        stored = sorted(p["position"] for p in circle["profiles"])
        assert stored == sorted(set(stored))
        assert len(stored) == 4

    async def test_two_first_adds_at_once_open_exactly_one_household(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """The very first add from two devices at once.

        ``account_id`` is unique on the circle, so the loser's insert fails.
        It has to recover by reading the circle the winner created rather than
        surfacing an integrity error, and there must still be exactly one
        household and one ``self`` row at the end.
        """
        token, account_id = await registered_supabase_user()

        _release_both_before_anything_is_read(monkeypatch, asyncio.Barrier(2))
        first, second = await asyncio.gather(
            app_client.post(PROFILES_URL, headers=auth(token), json={"relation": "adult"}),
            app_client.post(PROFILES_URL, headers=auth(token), json={"relation": "child"}),
        )

        for response in (first, second):
            assert response.status_code == 201, response.text
            assert "IntegrityError" not in response.text
        assert await _count(FamilyCircle, account_id=account_id) == 1
        circle = (await app_client.get(CIRCLE_URL, headers=auth(token))).json()
        relations = sorted(p["relation"] for p in circle["profiles"])
        assert relations == ["adult", "child", "self"]
        positions = [p["position"] for p in circle["profiles"]]
        assert len(set(positions)) == len(positions)


# ---------------------------------------------------------------------------
# 7. The household in the privacy export
# ---------------------------------------------------------------------------
class TestHouseholdIsExportable:
    def test_both_household_tables_are_promised_to_the_customer(self):
        assert REGISTRY["family_circles"] is Classification.INCLUDED
        assert REGISTRY["family_profiles"] is Classification.INCLUDED

    async def test_the_export_carries_the_household(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The registry has said INCLUDED since these tables existed.

        Nothing delivered on it: no handler read them, so an account could
        create a household and then be handed an export that did not mention
        it. That is the registry making a promise the export did not keep.
        """
        token, account_id = await registered_supabase_user()
        member = await _add_member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        async with get_sessionmaker()() as session:
            payload = await export_service.build_export(session, account_id)

        household = payload["domains"]["household"]
        assert household.get("error") is None, household
        assert len(household["circles"]) == 1
        assert str(household["circles"][0]["account_id"]) == str(account_id)
        exported = {row["id"]: row for row in household["profiles"]}
        assert str(member["id"]) in {str(key) for key in exported}
        stored = next(
            row for row in household["profiles"] if str(row["id"]) == member["id"]
        )
        assert stored["relation"] == "child"
        assert stored["age_band"] == AGE_BAND_UNDER_12
        assert "household" in payload["registry_summary"]["included_domains"]

    async def test_the_export_carries_nobody_elses_household(
        self, db_clean, app_client, registered_supabase_user,
    ):
        _, mine = await registered_supabase_user()
        other_token, _ = await registered_supabase_user()
        theirs = await _add_member(app_client, other_token, relation="adult")
        async with get_sessionmaker()() as session:
            payload = await export_service.build_export(session, mine)
        household = payload["domains"]["household"]
        assert household["circles"] == []
        assert household["profiles"] == []
        assert theirs["id"] not in str(payload)


# ---------------------------------------------------------------------------
# 8. The household when the account is erased
# ---------------------------------------------------------------------------
class TestHouseholdIsErasable:
    async def test_deleting_the_account_removes_the_whole_household(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Behaviour, not schema inspection.

        A cascade declared on the model proves nothing if the delete path does
        not reach it, so this runs the real deletion job and then looks for
        rows. A member profile carries no ``account_id`` of its own; it is
        reachable only through the circle, which is exactly the chain that has
        to hold.
        """
        from app.domains.privacy import deletion_service

        token, account_id = await registered_supabase_user()
        await _add_member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        assert await _count(FamilyProfile) == 2

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()
        async with get_sessionmaker()() as session:
            assert await deletion_service.drain_all(session) >= 1
            await session.commit()

        assert await _count(FamilyCircle, account_id=account_id) == 0
        assert await _count(FamilyProfile) == 0

    async def test_deleting_one_account_leaves_the_other_household_alone(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.privacy import deletion_service

        token, doomed = await registered_supabase_user()
        other_token, survivor = await registered_supabase_user()
        await _add_member(app_client, token, relation="adult")
        kept = await _add_member(app_client, other_token, relation="adult")

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, doomed)
            await session.commit()
        async with get_sessionmaker()() as session:
            await deletion_service.drain_all(session)
            await session.commit()

        assert await _count(FamilyCircle, account_id=doomed) == 0
        assert await _count(FamilyCircle, account_id=survivor) == 1
        circle = (await app_client.get(CIRCLE_URL, headers=auth(other_token))).json()
        assert kept["id"] in [p["id"] for p in circle["profiles"]]
