"""Step 11A — asking FOR YOU about somebody else in the house.

The endpoint gains one optional field, ``subject_id``, and everything here is
about what that field is *not* allowed to do.

It is a claim, not an instruction. It cannot reach into another household. It
cannot be used to sweep for members of one, because a real id belonging to
somebody else and an id nobody ever issued produce the same answer. It cannot
borrow the account holder's stored body facts for a different person. And it
cannot be talked out of the one boundary the constitution draws hardest: a
member the server has recorded as under 12 hands over to a clinician, whatever
the request says about them, including when the request says nothing at all.

The heavy Step 8K chain — capture, evidence, release, activation — is reused
rather than restaged, because the point is that the *same* governed decision
path answers differently depending only on who is being asked about.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.domains.family.subject import (
    AGE_BAND_ADULT,
    AGE_BAND_TEEN,
    AGE_BAND_UNDER_12,
)
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_step8k_current_pack_personal_decision_api import (
    BARCODE,
    FOR_YOU_URL,
    _assert_no_decision,
    _confirmed_device,
    _profile,
    _qualified_release,
)

PROFILES_URL = "/api/v2/family-circle/profiles"
CIRCLE_URL = "/api/v2/family-circle"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _member(client, token, *, relation, age_band=None) -> str:
    """Add one household member, optionally stating how old they are.

    Omitting ``age_band`` sends no band at all rather than sending
    ``not_stated``, so the caller exercises the route's own default — which is
    what a client that has not been updated will do.
    """
    payload: dict[str, Any] = {"relation": relation}
    if age_band is not None:
        payload["age_band"] = age_band
    response = await client.post(PROFILES_URL, headers=auth(token), json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _ask(client, headers, token, *, subject_id=None, safety=None, barcode=BARCODE):
    payload: dict[str, Any] = {"barcode": barcode}
    if subject_id is not None:
        payload["subject_id"] = str(subject_id)
    if safety is not None:
        payload["safety"] = safety
    return await client.post(
        FOR_YOU_URL, headers={**headers, **auth(token)}, json=payload,
    )


async def _answerable_account(app_client, registered_supabase_user) -> dict:
    """An account that would get a real reviewed BUY for itself.

    Every test below starts from a setup that *works*, so that a withheld or
    handed-over answer can only be the subject and never a half-built fixture.
    """
    owner = await _confirmed_device(app_client, registered_supabase_user)
    async with get_sessionmaker()() as session:
        await _qualified_release(session)
        await _profile(session, owner["account_id"])
        await session.commit()
    return owner


async def _product_truth_counts() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        return {
            "products": await session.scalar(select(func.count(ProductRecord.id))),
            "snapshots": await session.scalar(select(func.count(LabelSnapshot.id))),
            "scans": await session.scalar(select(func.count(ScanEvent.id))),
        }


# ---------------------------------------------------------------------------
# 1. Nothing changes for the person who has always been the subject
# ---------------------------------------------------------------------------
class TestTheAccountHolderIsUnchanged:
    async def test_omitting_the_subject_still_produces_the_reviewed_decision(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        body = (await _ask(app_client, owner["headers"], owner["token"])).json()
        assert body["result"]["status"] == "decision_presentable"
        assert body["result"]["action"] == "buy"
        assert body["result"]["handoff"] is None

    async def test_naming_the_self_profile_gives_the_same_answer_as_omitting_it(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Opening a household must not change what the customer sees.

        The circle is created with a ``self`` row. If naming it took the
        "somebody else" path, the same person would get a reviewed BUY one way
        and "we do not know enough about you" the other, for the same bottle.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        await _member(app_client, owner["token"], relation="adult",
                      age_band=AGE_BAND_ADULT)
        circle = (await app_client.get(CIRCLE_URL, headers=auth(owner["token"]))).json()
        self_id = next(p["id"] for p in circle["profiles"] if p["relation"] == "self")

        default = (await _ask(app_client, owner["headers"], owner["token"])).json()
        named = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=self_id,
        )).json()
        assert named == default


# ---------------------------------------------------------------------------
# 2. Another person in the same house is a different person
# ---------------------------------------------------------------------------
class TestAnotherMemberIsNotTheAccountHolder:
    async def test_a_household_member_does_not_inherit_the_account_holders_body(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The one failure a household must never have.

        The stored appearance profile belongs to the account holder. Reading it
        and calling it somebody else's would quietly merge two people into one
        body — and it would do so invisibly, because the answer would look
        perfectly reasonable. The honest answer is that we do not know this
        person yet.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="adult", age_band=AGE_BAND_ADULT,
        )

        mine = (await _ask(app_client, owner["headers"], owner["token"])).json()
        theirs = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).json()

        assert mine["result"]["status"] == "decision_presentable"
        assert theirs["result"]["status"] != "decision_presentable"
        assert theirs["result"]["status"] == "not_enough_information"
        assert theirs["result"]["handoff"] is None
        _assert_no_decision(theirs)

    async def test_the_member_is_told_what_is_missing_rather_than_guessed_at(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="other", age_band=AGE_BAND_ADULT,
        )
        result = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).json()["result"]
        assert result["reason_text"].strip()
        assert result["citation"] is None


# ---------------------------------------------------------------------------
# 3. A child under 12: the boundary the request cannot move
# ---------------------------------------------------------------------------
class TestStoredChildhoodIsTheAuthority:
    async def test_a_stored_under_twelve_hands_over_with_no_safety_block_at_all(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The whole point of storing the band.

        Before this, the only way the gate could learn somebody was a child was
        a flag the client volunteered on each request — fine as a disclosure,
        useless as an authority, because the same client can simply not send
        it. Here the request contains a barcode and an id and nothing else, and
        the hand-over still happens.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).json()
        assert body["result"]["status"] == "handoff_required"
        assert body["result"]["handoff"]["reason"] == "age_under_minimum"
        assert body["result"]["handoff"]["message"].strip()
        _assert_no_decision(body)

    async def test_the_handoff_is_the_safety_authoritys_own_sentence(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The gate in ``routines/hard_handoff.py``, not a lookalike.

        The constitution is explicit that a feature satisfies this rule only
        when it calls that gate. Comparing against the gate's own message is
        how this test would notice a generic safety sentence quietly taking its
        place.
        """
        from app.domains.routines.hard_handoff import HANDOFF_MESSAGES, HandoffReason

        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).json()
        assert body["result"]["handoff"]["message"] == (
            HANDOFF_MESSAGES[HandoffReason.AGE_UNDER_MINIMUM]
        )

    async def test_the_request_cannot_describe_a_stored_child_as_an_adult(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
            safety={"subject_is_child": False, "stated_age": 45},
        )).json()
        assert body["result"]["status"] == "handoff_required"
        _assert_no_decision(body)

    async def test_a_teenager_is_not_handed_over_on_age_alone(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Twelve is the line, and a band must not move it.

        A band is a range, so the age handed to the gate is the top of it. The
        floor of ``teen_12_17`` is 12, which is exactly the minimum — reporting
        the floor instead would leave this passing by luck rather than by
        design, so the case is pinned here.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        teen = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_TEEN,
        )
        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=teen,
        )).json()
        assert body["result"]["status"] != "handoff_required"

    async def test_a_request_may_still_volunteer_what_the_band_does_not_say(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="adult", age_band=AGE_BAND_ADULT,
        )
        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
            safety={"pregnancy": True},
        )).json()
        assert body["result"]["status"] == "handoff_required"
        assert body["result"]["handoff"]["reason"] == "pregnancy"


# ---------------------------------------------------------------------------
# 4. A subject this account may not ask about
# ---------------------------------------------------------------------------
class TestForeignAndForgedSubjects:
    async def test_a_member_of_another_household_is_refused(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        other_token, _ = await registered_supabase_user()
        theirs = await _member(
            app_client, other_token, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        response = await _ask(
            app_client, owner["headers"], owner["token"], subject_id=theirs,
        )
        assert response.status_code == 404, response.text
        assert theirs not in response.text

    async def test_a_forged_identifier_is_refused_identically(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """No membership oracle.

        If a real id belonging to another household answered differently from
        an invented one, anybody could sweep identifiers and learn which of
        them name a real person in somebody else's home. The two answers are
        compared whole.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        other_token, _ = await registered_supabase_user()
        theirs = await _member(
            app_client, other_token, relation="adult", age_band=AGE_BAND_ADULT,
        )
        foreign = await _ask(
            app_client, owner["headers"], owner["token"], subject_id=theirs,
        )
        invented = await _ask(
            app_client, owner["headers"], owner["token"], subject_id=uuid.uuid4(),
        )
        assert foreign.status_code == invented.status_code == 404

        def _without_correlation(response):
            detail = dict(response.json()["detail"])
            # The request id differs by construction — it is how an operator
            # finds one of these two calls in a log. Everything a caller could
            # use to tell the two situations apart is what must match.
            detail.pop("request_id", None)
            return detail

        assert _without_correlation(foreign) == _without_correlation(invented)
        assert set(foreign.json()) == set(invented.json())

    async def test_a_removed_member_is_refused(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="adult", age_band=AGE_BAND_ADULT,
        )
        patched = await app_client.patch(
            f"{PROFILES_URL}/{member}", headers=auth(owner["token"]),
            json={"active": False},
        )
        assert patched.status_code == 200, patched.text
        response = await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )
        assert response.status_code == 404, response.text

    async def test_a_refused_subject_yields_no_decision_of_any_kind(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        response = await _ask(
            app_client, owner["headers"], owner["token"], subject_id=uuid.uuid4(),
        )
        assert response.status_code == 404
        for token in ("buy", "wait", "skip", "BUY", "WAIT", "SKIP"):
            assert f'"{token}"' not in response.text

    async def test_a_subject_identifier_that_is_not_a_uuid_is_rejected(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        response = await app_client.post(
            FOR_YOU_URL, headers={**owner["headers"], **auth(owner["token"])},
            json={"barcode": BARCODE, "subject_id": "not-a-uuid"},
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# 5. Product Truth is not personal
# ---------------------------------------------------------------------------
class TestProductTruthIsUntouched:
    async def test_two_subjects_read_the_same_pack_and_the_same_label_version(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """What is in the bottle does not depend on who is asking.

        A second person asking about the same pack must not fork the record —
        no second ``ProductRecord``, no second ``LabelSnapshot``, no new scan.
        The personal part of the answer differs; the provenance underneath it
        is byte for byte the same.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="adult", age_band=AGE_BAND_ADULT,
        )
        before = await _product_truth_counts()

        mine = (await _ask(app_client, owner["headers"], owner["token"])).json()
        theirs = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=member,
        )).json()

        assert mine["pack"] == theirs["pack"]
        assert mine["barcode"] == theirs["barcode"]
        assert mine["product_category"] == theirs["product_category"]
        assert await _product_truth_counts() == before

    async def test_a_handed_over_child_still_does_not_fork_the_product(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        before = await _product_truth_counts()
        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).json()
        assert body["result"]["status"] == "handoff_required"
        assert await _product_truth_counts() == before


# ---------------------------------------------------------------------------
# 6. The question itself is not kept
# ---------------------------------------------------------------------------
class TestNothingAboutTheSubjectIsWrittenDown:
    async def test_asking_about_a_member_writes_no_household_rows(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        from app.domains.family.models import FamilyCircle, FamilyProfile

        owner = await _answerable_account(app_client, registered_supabase_user)
        member = await _member(
            app_client, owner["token"], relation="adult", age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as session:
            before = (
                await session.scalar(select(func.count(FamilyCircle.id))),
                await session.scalar(select(func.count(FamilyProfile.id))),
            )
        for _ in range(3):
            await _ask(app_client, owner["headers"], owner["token"], subject_id=member)
        async with get_sessionmaker()() as session:
            after = (
                await session.scalar(select(func.count(FamilyCircle.id))),
                await session.scalar(select(func.count(FamilyProfile.id))),
            )
        assert after == before

    async def test_asking_about_the_default_subject_opens_no_household(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        from app.domains.family.models import FamilyCircle

        owner = await _answerable_account(app_client, registered_supabase_user)
        await _ask(app_client, owner["headers"], owner["token"])
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(FamilyCircle.id))) == 0

    async def test_the_answer_never_echoes_who_it_was_about(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """A shared screen is the normal case for a household.

        The response is read on one phone that several people use. Repeating
        the member id, the relation or the age band back into it would put a
        fact about one person in front of whoever happens to be holding it.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        text = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).text
        assert child not in text
        assert "subject_id" not in text
        assert "age_band" not in text
        assert AGE_BAND_UNDER_12 not in text
