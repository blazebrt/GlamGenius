"""Step 11A — omitting the subject must not be a way around the subject.

Step 11A made ``family_profiles.age_band`` the server's authority for the
under-12 hand-over, and the correction after it let a household fix that band
when it goes out of date. Both were right, and together they left a hole.

``resolve_subject()`` answered "no ``subject_id``" by synthesising an account
holder with ``not_stated``, without ever looking for the stored ``self`` row. So
a household could record its own account holder as under twelve, watch the
hand-over fire when that row was named explicitly, and then get an ordinary
answer back by simply *not sending the optional field* — which is exactly what
every client written before Step 11 does.

An authority a caller can skip by omission is not an authority. The rule this
file holds up is one sentence: **naming nobody and naming yourself are the same
human, and must reach the same stored facts.**

The other half matters just as much. An account that has never opened a
household must keep working exactly as it did, and asking a question must never
create one.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    AGE_BAND_NOT_STATED,
    AGE_BAND_TEEN,
    AGE_BAND_UNDER_12,
    SUBJECT_ACCOUNT_HOLDER,
    HouseholdInvariantError,
    account_holder_subject,
    resolve_subject,
)
from app.shared.database.registry import Base
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import delete, func, select

from tests.test_step11a_age_band_lifecycle import _handed_off, _patch, _self_profile
from tests.test_step11a_for_you_subject import _answerable_account, _ask, _member


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _resolved(account_id, subject_id=None):
    async with get_sessionmaker()() as session:
        return await resolve_subject(
            session, account_id=account_id, subject_id=subject_id,
        )


async def _row_counts() -> dict[str, int]:
    """Every table in the application, counted.

    Broader than naming the tables this path could plausibly touch, and
    deliberately so: a side effect nobody predicted is the kind worth catching.
    """
    async with get_sessionmaker()() as session:
        counts: dict[str, int] = {}
        for table in Base.metadata.sorted_tables:
            counts[table.name] = await session.scalar(
                select(func.count()).select_from(table)
            )
        return counts


async def _open_household(client, token) -> dict:
    """Open a circle and return its canonical self row."""
    await _member(client, token, relation="adult")
    return await _self_profile(client, token)


# ---------------------------------------------------------------------------
# 1. An account that never opened Family
# ---------------------------------------------------------------------------
class TestNoHouseholdIsUnchanged:
    async def test_the_default_subject_is_the_synthesised_account_holder(
        self, db_clean, app_client, registered_supabase_user,
    ):
        _, account_id = await registered_supabase_user()
        subject = await _resolved(account_id)
        assert subject == account_holder_subject(account_id)
        assert subject.kind == SUBJECT_ACCOUNT_HOLDER
        assert subject.is_account_holder is True
        assert subject.subject_id is None
        assert subject.age_band == AGE_BAND_NOT_STATED
        assert subject.stated_age is None
        assert subject.is_child is False

    async def test_for_you_still_returns_the_reviewed_decision(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The pre-Step-11 path, intact.

        A full reviewed BUY needs the account holder's own stored skin facts to
        have been read. If the default subject stopped reaching them this would
        withhold the decision instead, which is how a regression here would
        show up to a customer.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        body = (await _ask(app_client, owner["headers"], owner["token"])).json()
        assert body["result"]["status"] == "decision_presentable"
        assert body["result"]["action"] == "buy"
        assert body["result"]["handoff"] is None

    async def test_reading_a_decision_opens_no_household(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        for _ in range(3):
            await _ask(app_client, owner["headers"], owner["token"])
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(FamilyCircle.id))) == 0
            assert await session.scalar(select(func.count(FamilyProfile.id))) == 0

    async def test_resolving_the_default_subject_writes_nothing_at_all(
        self, db_clean, app_client, registered_supabase_user,
    ):
        _, account_id = await registered_supabase_user()
        before = await _row_counts()
        async with get_sessionmaker()() as session:
            await resolve_subject(session, account_id=account_id, subject_id=None)
            await session.commit()
        assert await _row_counts() == before


# ---------------------------------------------------------------------------
# 2. The defect itself
# ---------------------------------------------------------------------------
class TestStoredSelfAuthorityAppliesByDefault:
    async def test_omitting_the_subject_reaches_the_stored_self_row(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The bypass, closed.

        Naming the self row explicitly hands over. Naming nobody — the request
        shape every existing client sends — must hand over too, for the same
        recorded reason. Before the correction the second call answered
        normally, because the resolver invented ``not_stated`` rather than
        reading what the household had recorded.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        assert (await _patch(
            app_client, owner["token"], me["id"], age_band=AGE_BAND_UNDER_12,
        )).status_code == 200

        explicit = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=me["id"],
        )).json()
        implicit = (await _ask(
            app_client, owner["headers"], owner["token"],
        )).json()

        assert _handed_off(explicit)
        assert _handed_off(implicit)
        assert explicit["result"]["handoff"]["reason"] == "age_under_minimum"
        assert implicit["result"]["handoff"]["reason"] == "age_under_minimum"
        assert implicit["result"]["handoff"]["message"] == (
            explicit["result"]["handoff"]["message"]
        )

    async def test_the_request_cannot_argue_the_stored_self_band_down(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The per-request guarantee, on the default path too.

        Closing the omission hole would be worth little if the same request
        could then claim to be forty-five instead.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_UNDER_12)

        body = (await _ask(
            app_client, owner["headers"], owner["token"],
            safety={"subject_is_child": False, "stated_age": 45},
        )).json()
        assert _handed_off(body)
        assert body["result"]["handoff"]["reason"] == "age_under_minimum"

    async def test_a_request_may_still_add_on_the_default_path(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """One-directional, still. A disclosure is heard; a denial is not."""
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_ADULT)

        body = (await _ask(
            app_client, owner["headers"], owner["token"], safety={"stated_age": 9},
        )).json()
        assert _handed_off(body)
        assert body["result"]["handoff"]["reason"] == "age_under_minimum"

    async def test_an_active_member_never_becomes_the_default_subject(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """"Nobody named" means the account holder, not "whoever comes back".

        The household has two active rows, and the other one is recorded as a
        child. A lookup that matched any active profile would answer with that
        child's authority for the account holder's own question, decided by
        nothing more than insertion order.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )

        subject = await _resolved(owner["account_id"])
        assert subject.subject_id == uuid.UUID(me["id"])
        assert subject.relation == "self"
        assert subject.age_band == AGE_BAND_NOT_STATED
        assert subject.is_account_holder is True

        # And the child is still governed by their own band when named.
        assert _handed_off((await _ask(
            app_client, owner["headers"], owner["token"], subject_id=child,
        )).json())
        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"],
        )).json())

    async def test_a_second_account_holder_row_fails_closed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Two account holders in one household is not a tie to break.

        No route can produce this. If it existed, choosing between them would
        decide whose body a decision is about by insertion order, so it stops
        instead.
        """
        token, account_id = await registered_supabase_user()
        await _open_household(app_client, token)
        async with get_sessionmaker()() as session:
            circle_id = await session.scalar(
                select(FamilyCircle.id).where(FamilyCircle.account_id == account_id)
            )
            session.add(FamilyProfile(
                circle_id=circle_id, position=8, relation="self",
                age_band=AGE_BAND_UNDER_12,
            ))
            await session.commit()

        async with get_sessionmaker()() as session:
            with pytest.raises(HouseholdInvariantError):
                await resolve_subject(session, account_id=account_id, subject_id=None)

    async def test_an_inactive_member_does_not_become_the_default_subject(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Only the ``self`` row answers to "nobody named".

        A deactivated child in the household must not be picked up as the
        account holder by a request that named nobody.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        child = await _member(
            app_client, owner["token"], relation="child", age_band=AGE_BAND_UNDER_12,
        )
        await _patch(app_client, owner["token"], child, active=False)

        subject = await _resolved(owner["account_id"])
        assert subject.relation == "self"
        assert subject.age_band == AGE_BAND_NOT_STATED
        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"],
        )).json())


# ---------------------------------------------------------------------------
# 3. The lifecycle, on the default path
# ---------------------------------------------------------------------------
class TestTheDefaultSubjectFollowsCorrections:
    async def test_a_correction_is_visible_to_the_very_next_default_request(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Up, then back down, with an omitted subject throughout.

        Each correction has to land on the next request. Anything cached or
        resolved once per session would pass the first half of this and fail the
        second.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])

        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_UNDER_12)
        assert _handed_off((await _ask(
            app_client, owner["headers"], owner["token"],
        )).json())

        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_TEEN)
        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"],
        )).json())

        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_UNDER_12)
        again = (await _ask(app_client, owner["headers"], owner["token"])).json()
        assert _handed_off(again)
        assert again["result"]["handoff"]["reason"] == "age_under_minimum"

    async def test_not_stated_clears_the_claim_for_the_default_subject_too(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_UNDER_12)
        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_NOT_STATED)

        subject = await _resolved(owner["account_id"])
        assert subject.age_band == AGE_BAND_NOT_STATED
        assert subject.stated_age is None
        assert not _handed_off((await _ask(
            app_client, owner["headers"], owner["token"],
        )).json())


# ---------------------------------------------------------------------------
# 4. One human, two spellings
# ---------------------------------------------------------------------------
class TestExplicitAndImplicitSelfAreOneSubject:
    @pytest.mark.parametrize(
        "band", [AGE_BAND_NOT_STATED, AGE_BAND_TEEN, AGE_BAND_ADULT, AGE_BAND_UNDER_12],
    )
    async def test_the_two_spellings_resolve_to_the_identical_subject(
        self, db_clean, app_client, registered_supabase_user, band,
    ):
        """Compared whole, not field by field.

        A later slice will hang Decision Memory, shelf ownership and Manager
        state off this subject. Two spellings of the same person that produced
        subjects differing in *any* field would be a seam for that state to
        split along.
        """
        token, account_id = await registered_supabase_user()
        me = await _open_household(app_client, token)
        await _patch(app_client, token, me["id"], age_band=band)

        explicit = await _resolved(account_id, uuid.UUID(me["id"]))
        implicit = await _resolved(account_id)
        assert explicit == implicit
        assert implicit.subject_id == uuid.UUID(me["id"])
        assert implicit.kind == SUBJECT_ACCOUNT_HOLDER
        assert implicit.age_band == band

    async def test_the_self_row_is_never_another_household_member(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Naming yourself must not withhold your own facts.

        A non-self subject deliberately gets ``not_enough_information``, because
        the stored appearance profile is the account holder's and is not theirs.
        If the self row were classified that way, naming yourself would answer
        "we do not know enough about you" to the one person we do know about.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])

        for subject_id in (me["id"], None):
            body = (await _ask(
                app_client, owner["headers"], owner["token"], subject_id=subject_id,
            )).json()
            assert body["result"]["status"] == "decision_presentable", subject_id
            assert body["result"]["action"] == "buy", subject_id

        explicit = await _resolved(owner["account_id"], uuid.UUID(me["id"]))
        assert explicit.is_account_holder is True
        assert explicit.kind == SUBJECT_ACCOUNT_HOLDER

    async def test_the_two_spellings_produce_the_same_answer(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        await _patch(app_client, owner["token"], me["id"], age_band=AGE_BAND_ADULT)

        explicit = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=me["id"],
        )).json()
        implicit = (await _ask(app_client, owner["headers"], owner["token"])).json()
        assert explicit == implicit

    async def test_another_member_is_still_not_the_account_holder(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The distinction that must survive the normalisation.

        Making explicit-self and implicit-self one subject must not have made
        *every* named subject the account holder.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        other = await _member(
            app_client, owner["token"], relation="adult", age_band=AGE_BAND_ADULT,
        )
        subject = await _resolved(owner["account_id"], uuid.UUID(other))
        assert subject.is_account_holder is False

        body = (await _ask(
            app_client, owner["headers"], owner["token"], subject_id=other,
        )).json()
        assert body["result"]["status"] == "not_enough_information"


# ---------------------------------------------------------------------------
# 5. Side effects
# ---------------------------------------------------------------------------
class TestResolutionHasNoSideEffects:
    async def test_default_subject_decisions_change_no_row_anywhere(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Every table counted before and after, not just the likely ones.

        Resolving the default subject is a read. It must not open a household,
        add a second ``self`` row, touch Product Truth, or write anything to
        ownership, Decision Memory or the Manager queue.
        """
        owner = await _answerable_account(app_client, registered_supabase_user)
        await _open_household(app_client, owner["token"])
        before = await _row_counts()

        for subject_id in (None, None, None):
            response = await _ask(
                app_client, owner["headers"], owner["token"], subject_id=subject_id,
            )
            assert response.status_code == 200, response.text

        assert await _row_counts() == before

    async def test_no_second_self_profile_is_ever_created(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        await _open_household(app_client, owner["token"])
        for _ in range(4):
            await _ask(app_client, owner["headers"], owner["token"])
        async with get_sessionmaker()() as session:
            assert await session.scalar(
                select(func.count(FamilyProfile.id)).where(
                    FamilyProfile.relation == "self",
                )
            ) == 1

    async def test_one_households_default_subject_is_not_anothers(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Two accounts, two self rows, no crossing.

        The lookup finds the ``self`` row through the circle its account owns.
        Without that scoping it would find whichever row the database returned
        first, which is a child in somebody else's home.
        """
        first = await _answerable_account(app_client, registered_supabase_user)
        second_token, second_account = await registered_supabase_user()
        first_self = await _open_household(app_client, first["token"])
        second_self = await _open_household(app_client, second_token)

        await _patch(
            app_client, second_token, second_self["id"], age_band=AGE_BAND_UNDER_12,
        )

        mine = await _resolved(first["account_id"])
        assert mine.subject_id == uuid.UUID(first_self["id"])
        assert mine.age_band == AGE_BAND_NOT_STATED
        assert not _handed_off((await _ask(
            app_client, first["headers"], first["token"],
        )).json())

        theirs = await _resolved(second_account)
        assert theirs.subject_id == uuid.UUID(second_self["id"])
        assert theirs.age_band == AGE_BAND_UNDER_12


# ---------------------------------------------------------------------------
# 6. A household with no account holder in it
# ---------------------------------------------------------------------------
class TestACorruptedHouseholdFailsClosed:
    async def test_a_circle_without_its_self_row_refuses_rather_than_guesses(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Structurally unreachable, and answered anyway.

        The circle and its ``self`` row are created in one transaction, no route
        deletes a profile, and the account holder cannot be deactivated. If the
        row goes missing regardless, falling back to the synthesised
        ``not_stated`` would answer with weaker authority than this household may
        have recorded — the exact failure the correction exists to prevent. So
        it stops.
        """
        token, account_id = await registered_supabase_user()
        me = await _open_household(app_client, token)
        async with get_sessionmaker()() as session:
            await session.execute(
                delete(FamilyProfile).where(FamilyProfile.id == uuid.UUID(me["id"]))
            )
            await session.commit()

        async with get_sessionmaker()() as session:
            with pytest.raises(HouseholdInvariantError):
                await resolve_subject(session, account_id=account_id, subject_id=None)

    async def test_the_route_answers_a_governed_unavailable_not_a_decision(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        owner = await _answerable_account(app_client, registered_supabase_user)
        me = await _open_household(app_client, owner["token"])
        async with get_sessionmaker()() as session:
            await session.execute(
                delete(FamilyProfile).where(FamilyProfile.id == uuid.UUID(me["id"]))
            )
            await session.commit()

        response = await _ask(app_client, owner["headers"], owner["token"])
        assert response.status_code == 503, response.text
        for token in ("buy", "wait", "skip", "BUY", "WAIT", "SKIP"):
            assert f'"{token}"' not in response.text
        assert "household" not in response.text.lower()
