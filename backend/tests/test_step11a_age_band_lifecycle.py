"""Step 11A — an authority nobody can correct is a trap, not an authority.

Storing the age band on the profile is what makes the under-12 hand-over
something the server knows rather than something the phone volunteers. The
first cut of that got the hard part right and the ordinary part wrong: the band
could be written once and never again. ``PATCH`` accepted only ``active``, and
the automatically created ``self`` row could not be touched at all.

Nothing is wrong with that on the day a member is added. It goes wrong later.
A child turns twelve and the product still refuses to answer about them. A
parent taps the wrong band on the way in and there is no way back, because
``safety_for()`` is deliberately built so that no request can argue a stored
band downwards. The safeguard and the mistake become indistinguishable, and the
only escape is deleting the person and creating them again — which in a later
Step 11 slice would take their Decision Memory, shelf and Manager state with
them.

So the household's own API gains one deliberate, authorised way to correct the
fact. Everything here is about that correction being real where it should be
and refused where it should not.
"""
from __future__ import annotations

import asyncio
import uuid

from app.domains.family import service as family_service
from app.domains.family.models import FamilyProfile
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    AGE_BAND_NOT_STATED,
    AGE_BAND_TEEN,
    AGE_BAND_UNDER_12,
)
from app.domains.privacy import export as export_service
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_step11a_for_you_subject import (
    CIRCLE_URL,
    PROFILES_URL,
    _answerable_account,
    _ask,
    _member,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _patch(client, token, profile_id, **changes):
    return await client.patch(
        f"{PROFILES_URL}/{profile_id}", headers=auth(token), json=changes,
    )


async def _stored_band(profile_id) -> str:
    async with get_sessionmaker()() as session:
        return await session.scalar(
            select(FamilyProfile.age_band).where(FamilyProfile.id == uuid.UUID(str(profile_id)))
        )


async def _self_profile(client, token) -> dict:
    circle = (await client.get(CIRCLE_URL, headers=auth(token))).json()
    return next(p for p in circle["profiles"] if p["relation"] == "self")


def _handed_off(body: dict) -> bool:
    return body["result"]["status"] == "handoff_required"


# ---------------------------------------------------------------------------
# 1. The lifecycle hole itself: a child turning twelve
# ---------------------------------------------------------------------------
class TestAChildGrowsUp:
    async def test_the_stored_band_can_be_corrected_upwards(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The whole defect, start to finish, in one test.

        Hand-over while the band says under twelve. A per-request claim of 45
        that still cannot move it. The household's own correction to
        ``teen_12_17``. And then the same question, answered.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )

        # 1. The hand-over fires on the stored band alone.
        before = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).json()
        assert _handed_off(before)
        assert before["result"]["handoff"]["reason"] == "age_under_minimum"

        # 2. The request still cannot talk it down. This is the guarantee the
        #    correction must not quietly buy its way out of.
        claimed = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
            safety={"subject_is_child": False, "stated_age": 45},
        )).json()
        assert _handed_off(claimed)

        # 3. The household corrects the fact through its own authority.
        patched = await _patch(
            app_client, owner["token"], child, age_band=AGE_BAND_TEEN,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["age_band"] == AGE_BAND_TEEN
        assert await _stored_band(child) == AGE_BAND_TEEN

        # 4. The same question, now answered rather than handed over.
        after = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).json()
        assert not _handed_off(after)

    async def test_the_correction_is_read_fresh_on_every_decision(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """No cached subject survives the correction.

        The band is resolved from the row on each request rather than carried
        anywhere, so a correction takes effect on the very next question. A test
        that only checked one request afterwards would not notice a cache.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        for _ in range(3):
            assert _handed_off((await _ask(
                app_client, owner["headers"], owner["token"], subject_id=child,
            )).json())
        await _patch(app_client, owner["token"], child, age_band=AGE_BAND_ADULT)
        for _ in range(3):
            assert not _handed_off((await _ask(
                app_client, owner["headers"], owner["token"], subject_id=child,
            )).json())

    async def test_a_correction_the_other_way_starts_handing_over_again(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Correcting a wrong band must work in the direction that costs us.

        An adult band entered on a child is the dangerous mistake, and the same
        route has to fix it. A correction that only ever loosened the boundary
        would be worse than none.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_ADULT,
        )
        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).json())

        assert (await _patch(
            app_client, owner["token"], member, age_band=AGE_BAND_UNDER_12,
        )).status_code == 200
        after = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).json()
        assert _handed_off(after)
        assert after["result"]["handoff"]["reason"] == "age_under_minimum"

    async def test_teenager_to_adult(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_TEEN,
        )
        response = await _patch(app_client, token, member, age_band=AGE_BAND_ADULT)
        assert response.status_code == 200, response.text
        assert response.json()["age_band"] == AGE_BAND_ADULT
        assert await _stored_band(member) == AGE_BAND_ADULT

    async def test_clearing_the_claim_back_to_not_stated(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """"We should not have said" is a legitimate correction too.

        ``not_stated`` is a value in the vocabulary rather than a missing one,
        so withdrawing a claim is an ordinary update. Afterwards the server
        asserts nothing about that person's age, which is exactly what it knew
        before anybody answered.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="other", age_band=AGE_BAND_UNDER_12,
        )
        response = await _patch(
            app_client, owner["token"], member, age_band=AGE_BAND_NOT_STATED,
        )
        assert response.status_code == 200, response.text
        assert await _stored_band(member) == AGE_BAND_NOT_STATED
        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).json())


# ---------------------------------------------------------------------------
# 2. What the correction must not buy
# ---------------------------------------------------------------------------
class TestTheRequestStillCannotOverrideStorage:
    async def test_a_corrected_subject_is_still_governed_by_storage(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Correcting once does not make the band negotiable afterwards.

        The route that allows an authorised correction must not have opened a
        second door where the per-decision request gets a say. So: correct
        upwards, correct back down, then try to claim 45 again.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        await _patch(app_client, owner["token"], member, age_band=AGE_BAND_TEEN)
        await _patch(app_client, owner["token"], member, age_band=AGE_BAND_UNDER_12)

        claimed = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
            safety={"subject_is_child": False, "stated_age": 45},
        )).json()
        assert _handed_off(claimed)
        assert claimed["result"]["handoff"]["reason"] == "age_under_minimum"

    async def test_the_other_hard_handoff_facts_are_untouched(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """A corrected age must not switch the rest of the boundary off.

        Pregnancy, breastfeeding, medication and a diagnosed condition are
        disclosed per request and have nothing to do with a band. After a
        correction that removes the age reason, every one of them must still
        hand over on its own.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        await _patch(app_client, owner["token"], member, age_band=AGE_BAND_ADULT)

        for flag, reason in (
            ("pregnancy", "pregnancy"),
            ("breastfeeding", "breastfeeding"),
            ("medication_involved", "medication"),
            ("diagnosed_condition_involved", "clinical_condition"),
        ):
            body = (await _ask(
                app_client, owner["headers"], owner["token"], subject_id=member,
                safety={flag: True},
            )).json()
            assert _handed_off(body), flag
            assert body["result"]["handoff"]["reason"] == reason

        # And a volunteered age below the minimum is still heard, even though
        # the stored band now says adult: a request may always add.
        younger = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
            safety={"stated_age": 9},
        )).json()
        assert _handed_off(younger)
        assert younger["result"]["handoff"]["reason"] == "age_under_minimum"


# ---------------------------------------------------------------------------
# 3. The account holder's own row
# ---------------------------------------------------------------------------
class TestTheSelfProfile:
    async def test_the_account_holder_can_correct_their_own_band(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The circle creates this row; nobody was ever asked about it.

        It is born ``not_stated``, and before this correction there was no way
        to say anything else — the account holder's own age was the one fact
        the household could never record.
        """
        token, _ = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        me = await _self_profile(app_client, token)
        assert me["age_band"] == AGE_BAND_NOT_STATED

        response = await _patch(app_client, token, me["id"], age_band=AGE_BAND_ADULT)
        assert response.status_code == 200, response.text
        assert response.json()["age_band"] == AGE_BAND_ADULT
        assert response.json()["relation"] == "self"
        assert response.json()["active"] is True
        assert await _stored_band(me["id"]) == AGE_BAND_ADULT

    async def test_the_account_holder_still_cannot_be_deactivated(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The prohibition that was always right, kept exactly as it was.

        The circle is anchored to this row. Letting it go inactive would leave a
        household whose owner is not in it. The status code is deliberately the
        same one this route returned before it learned about bands.
        """
        token, _ = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        me = await _self_profile(app_client, token)

        refused = await _patch(app_client, token, me["id"], active=False)
        assert refused.status_code == 404, refused.text
        assert refused.json()["detail"]["code"] == "self_profile_cannot_be_changed"

        after = await _self_profile(app_client, token)
        assert after["active"] is True

    async def test_deactivation_is_refused_even_beside_a_valid_correction(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """One request, one allowed change and one forbidden one.

        The whole patch is refused and nothing is written. Applying the half
        that was permitted would leave the caller with a 404 and a changed row,
        which is the worst of both answers.
        """
        token, _ = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        me = await _self_profile(app_client, token)

        refused = await _patch(
            app_client, token, me["id"], active=False, age_band=AGE_BAND_ADULT,
        )
        assert refused.status_code == 404, refused.text

        after = await _self_profile(app_client, token)
        assert after["active"] is True
        assert after["age_band"] == AGE_BAND_NOT_STATED

    async def test_the_self_row_cannot_be_given_another_relation(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """``relation`` is not a field this route accepts, on any row."""
        token, _ = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        me = await _self_profile(app_client, token)

        response = await _patch(app_client, token, me["id"], relation="child")
        assert response.status_code == 422, response.text
        assert (await _self_profile(app_client, token))["relation"] == "self"

    async def test_a_corrected_self_band_reaches_the_decision(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The account holder is a subject like any other.

        Naming their own profile and recording ``under_12`` on it must hand
        over, or the boundary would have a hole exactly where the customer's own
        child uses the customer's own account.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        await _member(app_client, owner["token"], relation="adult")
        me = await _self_profile(app_client, owner["token"])

        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"], subject_id=me["id"],
        )).json())

        assert (await _patch(
            app_client, owner["token"], me["id"], age_band=AGE_BAND_UNDER_12,
        )).status_code == 200
        after = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=me["id"],
        )).json()
        assert _handed_off(after)
        assert after["result"]["handoff"]["reason"] == "age_under_minimum"


# ---------------------------------------------------------------------------
# 4. Who may correct
# ---------------------------------------------------------------------------
class TestOnlyTheHouseholdOwnerMayCorrect:
    async def test_another_account_cannot_correct_a_members_band(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The one that matters most.

        A band governs a medical boundary. If a stranger could set somebody
        else's member to ``adult_18_plus``, they could switch off the hand-over
        protecting a child in a household they have never seen.
        """
        mine_token, _ = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        theirs = await _member(
            app_client, theirs_token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        # The attacker has a household of their own. Without it this test
        # passes for the wrong reason: an account with no circle is turned away
        # before the ownership check is ever reached, so the check itself would
        # go unproven and could be deleted without a single test noticing.
        await _member(app_client, mine_token, relation="adult")

        refused = await _patch(
            app_client, mine_token, theirs, age_band=AGE_BAND_ADULT,
        )
        assert refused.status_code == 404, refused.text
        assert await _stored_band(theirs) == AGE_BAND_UNDER_12

    async def test_another_account_cannot_deactivate_a_members_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The same predicate, on the other field it guards."""
        mine_token, _ = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        theirs = await _member(app_client, theirs_token, relation="adult")
        await _member(app_client, mine_token, relation="adult")

        refused = await _patch(app_client, mine_token, theirs, active=False)
        assert refused.status_code == 404, refused.text

        circle = (await app_client.get(CIRCLE_URL, headers=auth(theirs_token))).json()
        assert next(p for p in circle["profiles"] if p["id"] == theirs)["active"] is True

    async def test_an_account_with_no_household_cannot_correct_anybody(
        self, db_clean, app_client, registered_supabase_user,
    ):
        stranger_token, _ = await registered_supabase_user()
        owner_token, _ = await registered_supabase_user()
        member = await _member(
            app_client, owner_token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        refused = await _patch(
            app_client, stranger_token, member, age_band=AGE_BAND_ADULT,
        )
        assert refused.status_code == 404, refused.text
        assert await _stored_band(member) == AGE_BAND_UNDER_12

    async def test_a_foreign_member_and_a_fictional_one_are_refused_alike(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """No membership oracle on the write path either.

        The read path already answers these two identically. A write that
        distinguished them would give the same information away by another
        route.
        """
        mine_token, _ = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        theirs = await _member(app_client, theirs_token, relation="adult")
        # Again: a caller who owns a household, so the ownership predicate is
        # what refuses them rather than the absence of a circle.
        await _member(app_client, mine_token, relation="adult")

        foreign = await _patch(app_client, mine_token, theirs, age_band=AGE_BAND_ADULT)
        invented = await _patch(
            app_client, mine_token, uuid.uuid4(), age_band=AGE_BAND_ADULT,
        )
        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()

    async def test_an_unauthenticated_request_cannot_correct_a_band(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        response = await app_client.patch(
            f"{PROFILES_URL}/{member}", json={"age_band": AGE_BAND_ADULT},
        )
        assert response.status_code in (401, 403), response.text
        assert await _stored_band(member) == AGE_BAND_UNDER_12


# ---------------------------------------------------------------------------
# 5. The boundary of the patch itself
# ---------------------------------------------------------------------------
class TestThePatchContract:
    async def test_the_old_shape_still_works(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Backward compatibility, asserted rather than assumed.

        A client that only knows how to send ``active`` must not have to learn
        about age bands to keep working, and must not have its untouched band
        overwritten by a default.
        """
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="adult", age_band=AGE_BAND_TEEN,
        )
        response = await _patch(app_client, token, member, active=False)
        assert response.status_code == 200, response.text
        assert response.json()["active"] is False
        assert response.json()["age_band"] == AGE_BAND_TEEN
        assert await _stored_band(member) == AGE_BAND_TEEN

    async def test_correcting_a_band_does_not_disturb_active(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The mirror image: one field named, the other left exactly alone."""
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="adult", age_band=AGE_BAND_TEEN,
        )
        await _patch(app_client, token, member, active=False)
        response = await _patch(app_client, token, member, age_band=AGE_BAND_ADULT)
        assert response.status_code == 200, response.text
        assert response.json()["active"] is False
        assert response.json()["age_band"] == AGE_BAND_ADULT

    async def test_both_fields_at_once(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        response = await _patch(
            app_client, token, member, active=False, age_band=AGE_BAND_ADULT,
        )
        assert response.status_code == 200, response.text
        assert response.json()["active"] is False
        assert response.json()["age_band"] == AGE_BAND_ADULT

    async def test_reactivation_still_works(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        await _patch(app_client, token, member, active=False)
        response = await _patch(app_client, token, member, active=True)
        assert response.status_code == 200, response.text
        assert response.json()["active"] is True

    async def test_a_patch_that_changes_nothing_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """An empty body was refused before this route grew a second field.

        It still is. A 200 for a request that did nothing reads as confirmation
        that something was recorded.
        """
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        response = await _patch(app_client, token, member)
        assert response.status_code == 422, response.text

    async def test_a_null_is_not_a_way_to_change_something(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Silence and ``null`` must not mean the same thing here.

        Elsewhere a null in a patch is treated as "leave it alone". A caller who
        writes ``{"age_band": null}`` meaning "clear it" would then be told 200
        while a stored ``under_12`` stayed exactly where it was — the same
        silent failure this whole correction is about.
        """
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        for payload in ({"age_band": None}, {"active": None}, {"active": None, "age_band": None}):
            response = await app_client.patch(
                f"{PROFILES_URL}/{member}", headers=auth(token), json=payload,
            )
            assert response.status_code == 422, (payload, response.text)
        assert await _stored_band(member) == AGE_BAND_UNDER_12

    async def test_a_band_outside_the_vocabulary_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        for band in ("adult", "UNDER_12", "under_13", "", "12", "toddler"):
            response = await _patch(app_client, token, member, age_band=band)
            assert response.status_code == 422, (band, response.text)
        assert await _stored_band(member) == AGE_BAND_UNDER_12

    async def test_a_misspelled_field_is_not_a_silent_success(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """``age_bands`` is a typo, not a correction.

        Ignoring unknown keys would answer 200 while the stored band stayed put,
        and the caller would believe a child had been aged up.
        """
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        response = await app_client.patch(
            f"{PROFILES_URL}/{member}", headers=auth(token),
            json={"age_bands": AGE_BAND_ADULT},
        )
        assert response.status_code == 422, response.text
        assert await _stored_band(member) == AGE_BAND_UNDER_12

    async def test_a_misspelled_field_is_not_a_silent_success_on_create_either(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The create direction is the more dangerous of the two.

        Adding a child and mistyping the key would otherwise answer 201 with a
        member stored as ``not_stated`` — a child the server then has no reason
        to hand over for, created by a request that said it was one.
        """
        token, _ = await registered_supabase_user()
        for payload in (
            {"relation": "child", "age_bands": AGE_BAND_UNDER_12},
            {"relation": "child", "age": 9},
            {"relation": "child", "date_of_birth": "2018-01-01"},
        ):
            response = await app_client.post(
                PROFILES_URL, headers=auth(token), json=payload,
            )
            assert response.status_code == 422, (payload, response.text)

    async def test_no_date_of_birth_or_free_text_is_accepted(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The vocabulary stays closed and the precision stays coarse."""
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="child")
        for payload in (
            {"date_of_birth": "2015-04-01"},
            {"age": 9},
            {"note": "turns 12 in March"},
            {"age_band": AGE_BAND_ADULT, "date_of_birth": "2015-04-01"},
        ):
            response = await app_client.patch(
                f"{PROFILES_URL}/{member}", headers=auth(token), json=payload,
            )
            assert response.status_code == 422, (payload, response.text)

    async def test_the_service_refuses_a_band_the_constraint_would_reject(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The schema is the first line; this is the second.

        An in-process caller that bypasses the boundary must still get a domain
        error rather than an integrity failure that poisons the transaction.
        """
        import pytest

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="child")
        async with get_sessionmaker()() as session:
            with pytest.raises(family_service.FamilyProfileError):
                await family_service.update_profile(
                    session, account_id, uuid.UUID(member), age_band="toddler",
                )
            with pytest.raises(family_service.FamilyProfileError):
                await family_service.update_profile(session, account_id, uuid.UUID(member))


# ---------------------------------------------------------------------------
# 6. Two corrections at once
# ---------------------------------------------------------------------------
class TestConcurrentCorrections:
    async def test_two_band_corrections_at_once_settle_on_one_of_them(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Last write wins, which is what every partial update here does.

        There is no unique constraint on a band and no counter to lose, so this
        needs no version column and no idempotency key — it needs to not be a
        500. Both requests are released together against a real database and the
        row afterwards must hold one of the two values and nothing else.
        """
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )

        first, second = await asyncio.gather(
            _patch(app_client, token, member, age_band=AGE_BAND_TEEN),
            _patch(app_client, token, member, age_band=AGE_BAND_ADULT),
        )
        for response in (first, second):
            assert response.status_code == 200, response.text
            assert "IntegrityError" not in response.text
            assert "UniqueViolation" not in response.text
        assert await _stored_band(member) in {AGE_BAND_TEEN, AGE_BAND_ADULT}

    async def test_a_band_correction_and_a_deactivation_do_not_erase_each_other(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Two different columns, two different requests, both kept.

        Only the named columns are written, so neither request carries a stale
        copy of the other's field back over it. A patch that wrote the whole row
        would lose one of these.
        """
        token, _ = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="adult", age_band=AGE_BAND_UNDER_12,
        )

        banded, deactivated = await asyncio.gather(
            _patch(app_client, token, member, age_band=AGE_BAND_ADULT),
            _patch(app_client, token, member, active=False),
        )
        for response in (banded, deactivated):
            assert response.status_code == 200, response.text

        circle = (await app_client.get(CIRCLE_URL, headers=auth(token))).json()
        stored = next(p for p in circle["profiles"] if p["id"] == member)
        assert stored["age_band"] == AGE_BAND_ADULT
        assert stored["active"] is False


# ---------------------------------------------------------------------------
# 7. The corrected fact downstream
# ---------------------------------------------------------------------------
class TestTheCorrectionReachesPrivacy:
    async def test_the_export_returns_the_corrected_band(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """What the customer downloads is what the server currently believes.

        An export carrying the band somebody corrected away from would be the
        product telling them a fact about their own child that it no longer acts
        on.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        await _patch(app_client, token, member, age_band=AGE_BAND_TEEN)

        async with get_sessionmaker()() as session:
            payload = await export_service.build_export(session, account_id)
        household = payload["domains"]["household"]
        assert household.get("error") is None, household
        row = next(r for r in household["profiles"] if str(r["id"]) == member)
        assert row["age_band"] == AGE_BAND_TEEN
        assert AGE_BAND_UNDER_12 not in str(row)

    async def test_a_corrected_household_is_still_erased_with_the_account(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.family.models import FamilyCircle
        from app.domains.privacy import deletion_service
        from sqlalchemy import func

        token, account_id = await registered_supabase_user()
        member = await _member(
            app_client, token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        await _patch(app_client, token, member, age_band=AGE_BAND_ADULT)

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()
        async with get_sessionmaker()() as session:
            assert await deletion_service.drain_all(session) >= 1
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(
                select(func.count(FamilyCircle.id)).where(FamilyCircle.account_id == account_id)
            ) == 0
            assert await session.scalar(select(func.count(FamilyProfile.id))) == 0

    async def test_the_correction_is_not_echoed_into_the_decision(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """A shared household phone still learns nothing from the answer."""
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        await _patch(app_client, owner["token"], member, age_band=AGE_BAND_TEEN)
        text = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).text
        assert member not in text
        assert AGE_BAND_TEEN not in text
        assert "age_band" not in text
