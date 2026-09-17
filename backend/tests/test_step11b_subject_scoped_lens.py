"""Step 11B — one account, several people, one profile each.

Until this slice an account had exactly one ``AppearanceProfile`` and the schema
said so. That sentence was already false in the product — a household names
several humans — and it was false in a dangerous way: the lookups that relied on
it used ``scalar_one_or_none()``, which *raises* on a second row. The first
household to record a second person would not have received a slightly wrong
answer. Care, the shelf and the recommendation context would have returned 500.

So this file is about two things at once. That each human's facts stay their
own, and that nothing which used to ask "the profile for this account" is still
asking it.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import uuid
from pathlib import Path

import pytest
from app.api.v2 import profile as profile_route
from app.domains.family import service as family_service
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.schemas import SUBJECT_CARE_ATTRIBUTE_KEYS
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    account_holder_subject,
    resolve_subject,
)
from app.domains.identity import service as identity_service
from app.domains.personal_lens.enums import PersonalLensCategory
from app.domains.personal_lens.service import build_personal_lens_context
from app.domains.privacy import export as export_service
from app.domains.profile.identity import (
    ProfileIdentityError,
    resolve_self_profile_for_read,
    resolve_self_profile_for_write,
    resolve_subject_profile_for_read,
)
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

PROFILES_URL = "/api/v2/family-circle/profiles"
CIRCLE_URL = "/api/v2/family-circle"
SKIN = "care_skin_usual_feel"
SENS = "care_skin_sensitivity"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _member(client, token, *, relation="adult", age_band=None) -> str:
    payload: dict[str, object] = {"relation": relation}
    if age_band is not None:
        payload["age_band"] = age_band
    r = await client.post(PROFILES_URL, headers=auth(token), json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _self_id(client, token) -> str:
    circle = (await client.get(CIRCLE_URL, headers=auth(token))).json()
    return next(p["id"] for p in circle["profiles"] if p["relation"] == "self")


async def _care_write(client, token, profile_id, key, value):
    return await client.patch(
        f"{PROFILES_URL}/{profile_id}/care-profile",
        headers=auth(token),
        json={"attributes": [{"key": key, "value": value}]},
    )


async def _counts() -> tuple[int, int, int]:
    async with get_sessionmaker()() as s:
        return (
            await s.scalar(select(func.count(AppearanceProfile.id))),
            await s.scalar(select(func.count(FamilyCircle.id))),
            await s.scalar(select(func.count(FamilyProfile.id))),
        )


async def _legacy_account(account_id) -> AppearanceProfile:
    """An account holder with a profile written before Family existed."""
    async with get_sessionmaker()() as s:
        await identity_service.register_account(s, account_id)
        profile = AppearanceProfile(account_id=account_id)
        s.add(profile)
        await s.flush()
        s.add(ProfileAttribute(
            profile_id=profile.id, key=SKIN, value="often_dry_or_tight",
            source="user_declared", confidence=1.0, verification_state="confirmed",
        ))
        await s.commit()
        return profile


# ---------------------------------------------------------------------------
# 1. One human, one profile — however they are named
# ---------------------------------------------------------------------------
class TestSelfIdentityConverges:
    async def test_explicit_self_adopts_the_legacy_row_rather_than_adding_one(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The ambiguity that would have produced two account holders.

        A legacy NULL-subject profile exists. A request names the canonical
        Family ``self`` row. A generic subject writer would look for a profile
        bound to that id, find none, and insert — leaving this account with a
        legacy profile *and* a subject-bound self profile for the same person.
        The delegation is what stops it.
        """
        token, account_id = await registered_supabase_user()
        legacy = await _legacy_account(uuid.uuid4()) and None  # noqa: F841 - readability
        async with get_sessionmaker()() as s:
            original = await resolve_self_profile_for_write(s, account_id)
            original_id = original.id
            await s.commit()

        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)

        response = await _care_write(app_client, token, self_id, SKIN, "comfortable")
        assert response.status_code == 200, response.text

        async with get_sessionmaker()() as s:
            rows = (await s.execute(
                select(AppearanceProfile).where(AppearanceProfile.account_id == account_id)
            )).scalars().all()
        assert len(rows) == 1, rows
        assert rows[0].id == original_id
        assert str(rows[0].household_subject_id) == self_id

    async def test_explicit_and_implicit_self_are_the_same_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        async with get_sessionmaker()() as s:
            await resolve_self_profile_for_write(s, account_id)
            await s.commit()

        async with get_sessionmaker()() as s:
            implicit = await resolve_subject_profile_for_read(
                s, account_holder_subject(account_id)
            )
            explicit = await resolve_subject_profile_for_read(
                s, await resolve_subject(s, account_id=account_id, subject_id=uuid.UUID(self_id))
            )
            direct = await resolve_self_profile_for_read(s, account_id)
        assert implicit.id == explicit.id == direct.id

    async def test_a_dual_identity_fails_closed_rather_than_choosing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Unreachable through any route, and answered anyway.

        A legacy row and an adopted row for the same human is two account
        holders. Picking one would silently decide whose body facts count.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        async with get_sessionmaker()() as s:
            s.add(AppearanceProfile(account_id=account_id, household_subject_id=uuid.UUID(self_id)))
            s.add(AppearanceProfile(account_id=account_id))
            await s.commit()

        async with get_sessionmaker()() as s:
            with pytest.raises(ProfileIdentityError):
                await resolve_self_profile_for_read(s, account_id)

    async def test_adoption_keeps_every_historical_child_row(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Adoption is one column, not a migration.

        The profile keeps its primary key, so the attribute recorded years ago
        is still the same row pointing at the same profile. A clone would have
        left the history behind on an orphan.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            s.add(ProfileAttribute(
                profile_id=profile_id, key=SKIN, value="often_dry_or_tight",
                source="user_declared", confidence=1.0, verification_state="confirmed",
            ))
            await s.commit()

        await _member(app_client, token, relation="adult")
        async with get_sessionmaker()() as s:
            adopted = await resolve_self_profile_for_write(s, account_id)
            await s.commit()
        assert adopted.id == profile_id

        async with get_sessionmaker()() as s:
            attrs = (await s.execute(
                select(ProfileAttribute).where(ProfileAttribute.profile_id == profile_id)
            )).scalars().all()
            total = await s.scalar(select(func.count(AppearanceProfile.id)))
        assert [a.key for a in attrs] == [SKIN]
        assert total == 1


# ---------------------------------------------------------------------------
# 2. Pure reads never write
# ---------------------------------------------------------------------------
class TestPureReadsNeverMutate:
    async def test_lens_and_export_reads_create_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        async with get_sessionmaker()() as s:
            await resolve_self_profile_for_write(s, account_id)
            await s.commit()
        before = await _counts()

        async with get_sessionmaker()() as s:
            for _ in range(3):
                await resolve_self_profile_for_read(s, account_id)
                await resolve_subject_profile_for_read(s, account_holder_subject(account_id))
            await export_service.build_export(s, account_id)
            await s.commit()

        assert await _counts() == before

    async def test_a_read_does_not_adopt_an_unadopted_self(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A household exists, the legacy row has not been adopted yet.

        Reading identifies it as the account holder's without binding it. An
        export that adopted as a side effect would mean downloading your data
        changed it.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            await s.commit()
        await _member(app_client, token, relation="adult")

        async with get_sessionmaker()() as s:
            found = await resolve_self_profile_for_read(s, account_id)
            await export_service.build_export(s, account_id)
            await s.commit()
        assert found.id == profile_id

        async with get_sessionmaker()() as s:
            row = await s.get(AppearanceProfile, profile_id)
            assert row.household_subject_id is None


# ---------------------------------------------------------------------------
# 3. Facts stay with their own human
# ---------------------------------------------------------------------------
class TestPersonalFactIsolation:
    async def test_three_humans_hold_three_different_answers(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        member_b = await _member(app_client, token, relation="other", age_band=AGE_BAND_ADULT)
        self_id = await _self_id(app_client, token)

        assert (await _care_write(app_client, token, self_id, SENS, "rarely_reactive")).status_code == 200
        assert (await _care_write(app_client, token, member_a, SENS, "often_reactive")).status_code == 200

        async with get_sessionmaker()() as s:
            subj_a = await resolve_subject(s, account_id=account_id, subject_id=uuid.UUID(member_a))
            subj_b = await resolve_subject(s, account_id=account_id, subject_id=uuid.UUID(member_b))
            prof_self = await resolve_self_profile_for_read(s, account_id)
            prof_a = await resolve_subject_profile_for_read(s, subj_a)
            prof_b = await resolve_subject_profile_for_read(s, subj_b)

            async def values(profile):
                if profile is None:
                    return {}
                rows = (await s.execute(
                    select(ProfileAttribute).where(ProfileAttribute.profile_id == profile.id)
                )).scalars().all()
                return {r.key: r.value for r in rows}

            self_values, a_values, b_values = (
                await values(prof_self), await values(prof_a), await values(prof_b),
            )

        assert self_values[SENS] == "rarely_reactive"
        assert a_values[SENS] == "often_reactive"
        assert b_values == {}
        assert prof_self.id != prof_a.id
        assert prof_b is None

    async def test_a_member_never_inherits_the_account_holders_facts(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        self_id = await _self_id(app_client, token) if False else None  # noqa: F841
        member = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            s.add(ProfileAttribute(
                profile_id=profile.id, key=SKIN, value="often_dry_or_tight",
                source="user_declared", confidence=1.0, verification_state="confirmed",
            ))
            await s.commit()

        async with get_sessionmaker()() as s:
            subject = await resolve_subject(s, account_id=account_id, subject_id=uuid.UUID(member))
            theirs = await resolve_subject_profile_for_read(s, subject)
        assert theirs is None


# ---------------------------------------------------------------------------
# 4. Only this account, only active members
# ---------------------------------------------------------------------------
class TestOwnership:
    async def test_another_account_cannot_write_a_members_care_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        mine_token, _ = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        theirs = await _member(app_client, theirs_token, relation="adult")
        await _member(app_client, mine_token, relation="adult")

        refused = await _care_write(app_client, mine_token, theirs, SKIN, "comfortable")
        assert refused.status_code == 404, refused.text
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0

    async def test_foreign_and_invented_members_are_refused_alike(
        self, db_clean, app_client, registered_supabase_user,
    ):
        mine_token, _ = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        theirs = await _member(app_client, theirs_token, relation="adult")
        await _member(app_client, mine_token, relation="adult")

        foreign = await _care_write(app_client, mine_token, theirs, SKIN, "comfortable")
        invented = await _care_write(app_client, mine_token, uuid.uuid4(), SKIN, "comfortable")
        assert foreign.status_code == invented.status_code == 404
        assert foreign.json() == invented.json()

    async def test_an_inactive_member_cannot_be_written(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        await app_client.patch(
            f"{PROFILES_URL}/{member}", headers=auth(token), json={"active": False},
        )
        refused = await _care_write(app_client, token, member, SKIN, "comfortable")
        assert refused.status_code == 404, refused.text

    async def test_the_seam_refuses_anything_outside_the_live_vocabulary(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Exactly the two keys the account holder may write. No wider.

        The attribute registry still holds keys from the withdrawn style
        product. A household member is not a way back into them, and the test
        suite's own widening fixture is not the authority here.
        """
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        for payload in (
            {"attributes": [{"key": "preferred_style", "value": "classic"}]},
            {"attributes": [{"key": "height_cm", "value": "170"}]},
            {"attributes": [{"key": SKIN, "value": "comfortable", "note": "free text"}]},
            {"attributes": []},
            {"attributes": [{"key": SKIN}]},
            {"note": "hello"},
        ):
            r = await app_client.patch(
                f"{PROFILES_URL}/{member}/care-profile", headers=auth(token), json=payload,
            )
            assert r.status_code == 422, (payload, r.text)

    def test_the_seam_is_exactly_what_the_account_holder_may_write(self):
        """No wider than ``/profile``, read from the file rather than at runtime.

        ``conftest`` monkeypatches ``app.api.v2.profile.ALLOWED_KEYS`` to the
        whole attribute registry so that older suites can exercise retired
        keys. That makes the *running* value useless as an authority here — a
        seam widened to match it would be a seam the test suite chose. So this
        reads what the production file declares.
        """
        tree = ast.parse(Path(profile_route.__file__).read_text(encoding="utf-8"))
        declared = next(
            {element.value for element in node.value.elts}
            for node in tree.body
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", None) == "ALLOWED_KEYS"
        )
        assert set(SUBJECT_CARE_ATTRIBUTE_KEYS) == declared == {SKIN, SENS}

    async def test_the_lens_refuses_a_subject_that_was_never_checked(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A raw household profile id is not a subject, however real it is.

        The ownership check lives in ``resolve_subject()``, so the danger is
        not a forged id — it is a *genuine* id handed straight to the lens by
        a caller who skipped the check. The type is what makes that
        impossible, and a type is only a guard if something refuses the other
        shapes. The member id used here belongs to the very account asking, so
        nothing but the missing check can be what rejects it.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")

        async with get_sessionmaker()() as s:
            for unchecked in (
                uuid.UUID(member),          # a real member of this household
                member,                     # the same id as text
                account_id,                 # the account, mistaken for a subject
                None,                       # nobody named at all
                {"subject_id": member, "is_account_holder": False},
            ):
                with pytest.raises(ValueError):
                    await build_personal_lens_context(
                        s,
                        category=PersonalLensCategory.SKIN_CARE,
                        subject=unchecked,
                    )


# ---------------------------------------------------------------------------
# 5. Races, against a real database
# ---------------------------------------------------------------------------
class TestConcurrency:
    async def test_two_first_writes_for_one_member_make_one_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")

        first, second = await asyncio.gather(
            _care_write(app_client, token, member, SKIN, "comfortable"),
            _care_write(app_client, token, member, SENS, "rarely_reactive"),
        )
        for r in (first, second):
            assert r.status_code == 200, r.text
            assert "IntegrityError" not in r.text
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1

    async def test_two_different_members_do_not_collapse_into_one(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        a = await _member(app_client, token, relation="adult")
        b = await _member(app_client, token, relation="other")

        ra, rb = await asyncio.gather(
            _care_write(app_client, token, a, SKIN, "comfortable"),
            _care_write(app_client, token, b, SKIN, "often_dry_or_tight"),
        )
        assert ra.status_code == 200 and rb.status_code == 200
        async with get_sessionmaker()() as s:
            bound = (await s.execute(
                select(AppearanceProfile.household_subject_id)
                .where(AppearanceProfile.household_subject_id.is_not(None))
            )).scalars().all()
        assert len(bound) == 2 and len(set(bound)) == 2

    async def test_household_creation_racing_a_self_write_yields_one_identity(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The race the account lock exists for.

        One transaction opens the household; another decides whether the
        account holder already has a profile. Without a shared serialisation
        point the second can decide "no household, use the legacy row" while
        the first is committing the household that would have changed its
        answer — and the account ends up with two self identities.
        """
        token, account_id = await registered_supabase_user()

        async def open_household():
            async with get_sessionmaker()() as s:
                await family_service.add_profile(s, account_id, relation="adult")
                await s.commit()

        async def self_write():
            async with get_sessionmaker()() as s:
                await resolve_self_profile_for_write(s, account_id)
                await s.commit()

        await asyncio.gather(open_household(), self_write())

        async with get_sessionmaker()() as s:
            profiles = (await s.execute(
                select(AppearanceProfile).where(AppearanceProfile.account_id == account_id)
            )).scalars().all()
        assert len(profiles) == 1, profiles

        # And whichever way it went, the account is still readable: never the
        # forbidden state of a legacy row beside an adopted one.
        async with get_sessionmaker()() as s:
            await resolve_self_profile_for_read(s, account_id)

    async def test_two_self_writes_at_once_adopt_once(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")

        async def write():
            async with get_sessionmaker()() as s:
                profile = await resolve_self_profile_for_write(s, account_id)
                await s.commit()
                return profile.id

        first, second = await asyncio.gather(write(), write())
        assert first == second
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1

    async def test_household_creation_takes_the_account_lock_before_it_decides(self):
        """Lock order, checked at the source rather than through the database.

        This one is deliberately structural, and it is worth saying why. Every
        *outcome* of removing this lock is already covered by something else:
        the unique index on ``family_circles.account_id`` decides the circle
        race, the savepoint answers the loser by re-reading, and the foreign
        key from ``family_circles`` to ``accounts`` takes its own row lock on
        the same account — so a behavioural test cannot tell a deliberate
        ``FOR UPDATE`` apart from the one PostgreSQL takes anyway. What the
        explicit lock buys is the documented *ordering*: household creation and
        self-profile adoption are two halves of one identity transition, and
        they serialise on the same row, in the same order, before either
        decides what exists. An ordering is a property of the code, so this
        asserts it there: the lock is taken, and it is taken before this path
        reads or writes anything it then acts on.
        """
        source = inspect.getsource(family_service.circle_for)
        lock_at = source.find("await lock_account(")
        assert lock_at != -1, "circle_for must take the account lock"

        body = source[source.index("if circle is not None or not create:"):]
        for after in ("session.add(", "FamilyCircle(account_id=account_id)"):
            assert body.index("await lock_account(") < body.index(after), after


# ---------------------------------------------------------------------------
# 6. The callers that used to assume one profile per account
# ---------------------------------------------------------------------------
class TestRetiredCallersSurviveASecondProfile:
    async def test_every_account_holder_surface_still_answers(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The regression that would have shipped without this slice.

        Care, the shelf and the recommendation context all resolved the profile
        by account with ``scalar_one_or_none()``. With a second profile present
        that raises, so these are 500s rather than wrong answers. Every one of
        them is exercised here on an account that has two.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        self_id = await _self_id(app_client, token)
        assert (await _care_write(app_client, token, self_id, SKIN, "comfortable")).status_code == 200
        assert (await _care_write(app_client, token, member, SKIN, "often_dry_or_tight")).status_code == 200

        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 2

        for url in ("/api/v2/profile", "/api/v2/profile/attributes", "/api/v2/onboarding/status"):
            r = await app_client.get(url, headers=auth(token))
            assert r.status_code == 200, (url, r.text)

        from app.domains.recommendation import context as rec_context
        from app.domains.routines import shelf as shelf_module

        async with get_sessionmaker()() as s:
            assert isinstance(await rec_context.confirmed_attributes(s, account_id), dict)
            assert isinstance(
                await shelf_module.shelf_attributes(s, account_id), dict
            )

    async def test_legacy_profile_get_still_initialises(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Grandfathered on purpose.

        ``GET /profile`` has always created the row when it was missing. That is
        a lazy-initialisation compatibility route, not a pure read, and turning
        it into a 404 to satisfy a new rule would break existing clients.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0

        r = await app_client.get("/api/v2/profile", headers=auth(token))
        assert r.status_code == 200, r.text
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1


# ---------------------------------------------------------------------------
# 7. Privacy
# ---------------------------------------------------------------------------
class TestExportGrouping:
    async def test_every_included_profile_table_is_represented(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        await _care_write(app_client, token, self_id, SKIN, "comfortable")

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, account_id)
        holder = next(x for x in payload["domains"]["profile"]["subjects"] if x["is_account_holder"])
        for table in (
            "attributes", "change_events", "observations", "style_preferences",
            "fit_preferences", "lifestyle_context", "user_constraints", "goals",
            "onboarding_sessions",
        ):
            assert table in holder, table
        assert payload["schema_version"] == "1.1"

    async def test_two_subjects_are_grouped_without_leaking(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        self_id = await _self_id(app_client, token)
        await _care_write(app_client, token, self_id, SENS, "rarely_reactive")
        await _care_write(app_client, token, member, SENS, "often_reactive")

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, account_id)
        subjects = {x["household_subject_id"]: x for x in payload["domains"]["profile"]["subjects"]}
        holder = subjects[self_id]
        other = subjects[member]
        assert holder["is_account_holder"] is True
        assert other["is_account_holder"] is False
        assert {a["value"] for a in holder["attributes"]} == {"rarely_reactive"}
        assert {a["value"] for a in other["attributes"]} == {"often_reactive"}
        assert payload["domains"]["profile"]["invariant_errors"] == []

    async def test_an_unadopted_self_is_labelled_without_being_adopted(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            await s.commit()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, account_id)
        holder = next(x for x in payload["domains"]["profile"]["subjects"] if x["is_account_holder"])
        assert holder["household_subject_id"] == self_id
        assert holder["adopted"] is False

        async with get_sessionmaker()() as s:
            assert (await s.get(AppearanceProfile, profile_id)).household_subject_id is None

    async def test_an_inactive_member_keeps_their_data_in_the_export(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Deactivation is not deletion.

        Their facts stop driving decisions and stay in the account's own data.
        Silently erasing body facts because somebody was switched off would be
        a deletion nobody asked for.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        await _care_write(app_client, token, member, SENS, "often_reactive")
        await app_client.patch(
            f"{PROFILES_URL}/{member}", headers=auth(token), json={"active": False},
        )

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, account_id)
        entry = next(
            x for x in payload["domains"]["profile"]["subjects"]
            if x["household_subject_id"] == member
        )
        assert entry["active"] is False
        assert {a["value"] for a in entry["attributes"]} == {"often_reactive"}


# ---------------------------------------------------------------------------
# 8. Deletion
# ---------------------------------------------------------------------------
class TestDeletion:
    async def test_account_deletion_removes_every_profile_and_family_row(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.privacy import deletion_service

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        self_id = await _self_id(app_client, token)
        await _care_write(app_client, token, self_id, SKIN, "comfortable")
        await _care_write(app_client, token, member, SKIN, "often_dry_or_tight")

        async with get_sessionmaker()() as s:
            await deletion_service.request_deletion(s, account_id)
            await s.commit()
        async with get_sessionmaker()() as s:
            assert await deletion_service.drain_all(s) >= 1
            await s.commit()

        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0
            assert await s.scalar(select(func.count(ProfileAttribute.id))) == 0
            assert await s.scalar(select(func.count(FamilyCircle.id))) == 0
            assert await s.scalar(select(func.count(FamilyProfile.id))) == 0

    async def test_deleting_a_member_with_a_lens_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The deferred foreign key, proven by behaviour.

        No route deletes a member today. When one is built it must make an
        explicit decision about that person's Personal Lens rather than
        silently taking it with them — or, worse, taking the account holder's
        through the ``self`` row.
        """
        from sqlalchemy.exc import IntegrityError

        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        await _care_write(app_client, token, member, SKIN, "comfortable")

        async with get_sessionmaker()() as s:
            await s.execute(
                FamilyProfile.__table__.delete().where(FamilyProfile.id == uuid.UUID(member))
            )
            with pytest.raises(IntegrityError):
                await s.commit()
