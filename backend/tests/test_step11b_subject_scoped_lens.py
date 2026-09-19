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
import json
import uuid
from pathlib import Path

import pytest
from app.api.v2 import profile as profile_route
from app.domains.family import service as family_service
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.schemas import SUBJECT_CARE_ATTRIBUTE_KEYS
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    AGE_BAND_UNDER_12,
    RELATION_SELF,
    SUBJECT_ACCOUNT_HOLDER,
    SUBJECT_HOUSEHOLD_MEMBER,
    HouseholdInvariantError,
    ResolvedSubject,
    SubjectNotFound,
    account_holder_subject,
    resolve_subject,
)
from app.domains.personal_lens.enums import PersonalLensCategory, PersonalLensStatus
from app.domains.personal_lens.service import build_personal_lens_context
from app.domains.privacy import export as export_service
from app.domains.profile import service as profile_service
from app.domains.profile.identity import (
    ProfileIdentityError,
    canonical_subject,
    resolve_self_profile_for_read,
    resolve_self_profile_for_write,
    resolve_subject_profile_for_read,
    resolve_subject_profile_for_write,
)
from app.domains.profile.models import (
    AppearanceGoal,
    AppearanceProfile,
    AttributeObservation,
    FitPreference,
    LifestyleContext,
    OnboardingSession,
    ProfileAttribute,
    ProfileChangeEvent,
    StylePreference,
    UserConstraint,
)
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


#: Every table that hangs off ``appearance_profiles.id``. Adoption must move
#: none of them, and account deletion must take all of them.
#:
#: Some of these belong to the withdrawn Style and Wardrobe surfaces. They are
#: used here only as persistence fixtures — proving that a row pointing at a
#: profile survives the profile being named — and this file restores no customer
#: surface for any of them.
PROFILE_CHILD_MODELS = (
    ProfileAttribute, ProfileChangeEvent, AttributeObservation, StylePreference,
    FitPreference, LifestyleContext, UserConstraint, AppearanceGoal, OnboardingSession,
)


async def _seed_every_child_table(
    session, profile_id, *, attribute_key: str = SKIN,
) -> dict[str, uuid.UUID]:
    """One recognisable row in each of the nine child tables.

    ``attribute_key`` exists because ``profile_attributes`` is unique on
    ``(profile_id, key)``: a profile that already carries a Care fact needs a
    different one here rather than a second row for the same key.
    """
    rows = {
        "ProfileAttribute": ProfileAttribute(
            profile_id=profile_id, key=attribute_key, value="often_dry_or_tight",
            source="user_declared", confidence=1.0, verification_state="confirmed",
        ),
        "ProfileChangeEvent": ProfileChangeEvent(
            profile_id=profile_id, profile_version=1, attribute_key=SKIN,
            old_value=None, new_value="often_dry_or_tight",
            source="user_declared", reason="recorded",
        ),
        "AttributeObservation": AttributeObservation(
            profile_id=profile_id, key=SENS, proposed_value="rarely_reactive",
            source="user_declared", confidence=0.9, why="told us",
        ),
        "StylePreference": StylePreference(profile_id=profile_id),
        "FitPreference": FitPreference(profile_id=profile_id),
        "LifestyleContext": LifestyleContext(profile_id=profile_id, city="Pune"),
        "UserConstraint": UserConstraint(
            profile_id=profile_id, kind="avoid", value="fragrance",
        ),
        "AppearanceGoal": AppearanceGoal(profile_id=profile_id, goal="comfort"),
        "OnboardingSession": OnboardingSession(profile_id=profile_id),
    }
    for row in rows.values():
        session.add(row)
    await session.flush()
    return {name: row.id for name, row in rows.items()}


async def _child_rows(session) -> dict[str, list[tuple[uuid.UUID, uuid.UUID]]]:
    """Every child row in the database, as ``(id, profile_id)`` per table."""
    found: dict[str, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    for model in PROFILE_CHILD_MODELS:
        rows = (await session.execute(select(model.id, model.profile_id))).all()
        found[model.__name__] = [(row[0], row[1]) for row in rows]
    return found


async def _corrupt_profile_onto(session, *, account_id, subject_id, value) -> uuid.UUID:
    """The impossible row: a profile of one account bound to another's subject.

    No route can produce this. The foreign key proves the subject row exists and
    says nothing about whose account owns it, and nothing spans the two stores
    of identity, so the database will accept it. It is seeded directly for the
    same reason a fire alarm is tested with smoke.
    """
    profile = AppearanceProfile(account_id=account_id, household_subject_id=subject_id)
    session.add(profile)
    await session.flush()
    session.add(ProfileAttribute(
        profile_id=profile.id, key=SKIN, value=value,
        source="user_declared", confidence=1.0, verification_state="confirmed",
    ))
    await session.commit()
    return profile.id


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
        # The legacy state under test is *this* account's, and it is established
        # by the write below: no household exists yet, so what it creates is a
        # NULL-subject profile — exactly the row an account had before Family.
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
                s, account_holder_subject(account_id),
                principal_account_id=account_id,
            )
            explicit = await resolve_subject_profile_for_read(
                s, await resolve_subject(s, account_id=account_id, subject_id=uuid.UUID(self_id)),
                principal_account_id=account_id,
            )
            direct = await resolve_self_profile_for_read(s, account_id)
        assert implicit.id == explicit.id == direct.id

    async def test_naming_yourself_reaches_a_profile_that_is_not_adopted_yet(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Being named is not what makes the row yours.

        A household exists and the account holder's profile has not been
        adopted — nobody has written since the household was opened, and a read
        must not adopt it. Looking for a profile *bound to* the ``self`` row
        would find none and answer "we do not know enough about you" to
        somebody whose facts are sitting right there. Opening a household would
        silently empty the account holder's own Personal Lens until their next
        write.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            s.add(ProfileAttribute(
                profile_id=profile_id, key=SKIN, value="often_oily",
                source="user_declared", confidence=1.0, verification_state="confirmed",
            ))
            await s.commit()

        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))

        async with get_sessionmaker()() as s:
            named = await resolve_subject(s, account_id=account_id, subject_id=self_id)
            by_name = await resolve_subject_profile_for_read(
                s, named, principal_account_id=account_id,
            )
            by_omission = await resolve_subject_profile_for_read(
                s, account_holder_subject(account_id),
                principal_account_id=account_id,
            )
            context = await build_personal_lens_context(
                s, category=PersonalLensCategory.SKIN_CARE,
                principal_account_id=account_id, subject=named,
            )
        assert by_name is not None and by_name.id == profile_id
        assert by_omission is not None and by_omission.id == profile_id
        assert {fact.key for fact in context.body_facts} == {SKIN}

        # And reading it did not adopt it.
        async with get_sessionmaker()() as s:
            assert (await s.get(AppearanceProfile, profile_id)).household_subject_id is None

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

        Proving this with attributes alone would prove almost nothing: nine
        tables hang off ``appearance_profiles.id``, and a clone-and-repoint
        implementation would have passed an attributes-only check while quietly
        stranding somebody's goals, observations and onboarding answers on an
        orphaned row. So all nine are seeded, and all nine are checked by id.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            seeded = await _seed_every_child_table(s, profile_id)
            await s.commit()
        assert len(seeded) == len(PROFILE_CHILD_MODELS)

        await _member(app_client, token, relation="adult")
        async with get_sessionmaker()() as s:
            adopted = await resolve_self_profile_for_write(s, account_id)
            await s.commit()

        # Same profile, now named.
        assert adopted.id == profile_id
        async with get_sessionmaker()() as s:
            row = await s.get(AppearanceProfile, profile_id)
            assert row.household_subject_id is not None
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1

            after = await _child_rows(s)
        for model in PROFILE_CHILD_MODELS:
            rows = after[model.__name__]
            # Exactly one row, the one that was seeded, still pointing at the
            # same profile. More than one would be a clone; a different id would
            # be a rewrite; a different profile_id would be a re-parent.
            assert len(rows) == 1, (model.__name__, rows)
            assert rows[0][0] == seeded[model.__name__], model.__name__
            assert rows[0][1] == profile_id, model.__name__


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
                await resolve_subject_profile_for_read(
                    s, account_holder_subject(account_id),
                    principal_account_id=account_id,
                )
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
            prof_a = await resolve_subject_profile_for_read(
                s, subj_a, principal_account_id=account_id,
            )
            prof_b = await resolve_subject_profile_for_read(
                s, subj_b, principal_account_id=account_id,
            )

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
            theirs = await resolve_subject_profile_for_read(
                s, subject, principal_account_id=subject.account_id,
            )
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
                        principal_account_id=account_id,
                        subject=unchecked,
                    )


# ---------------------------------------------------------------------------
# 4b. A ResolvedSubject is a claim, not a credential
# ---------------------------------------------------------------------------
class TestForgedSubjectsAreRefused:
    """``ResolvedSubject`` is a public dataclass with a public constructor.

    Nothing stops a caller — a future route, a worker, a domain service written
    next quarter — from building one by hand with whatever fields it likes,
    including a *complete and internally consistent* identity belonging to
    somebody else. So the type cannot be the authorisation, and neither can the
    subject's own ``account_id``: re-resolving a forgery under the account it
    names confirms it rather than catching it.

    Everything here therefore passes ``principal_account_id`` separately, the
    way an authenticated route does, and proves the server compares the two
    before it reads anything.
    """

    async def test_a_whole_forged_identity_of_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The forgery that a self-consistent subject would have carried.

        Both halves agree with each other: ``account_id`` really is B's, and
        ``subject_id`` really is B's member. Nothing about the object is
        internally wrong. The only thing wrong with it is that the caller is A
        — which is knowable only by comparing it against an authority the
        caller does not supply.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        b_member = uuid.UUID(await _member(app_client, b_token, relation="adult"))
        assert (
            await _care_write(app_client, b_token, b_member, SKIN, "often_oily")
        ).status_code == 200

        async with get_sessionmaker()() as s:
            b_profile = await s.scalar(
                select(AppearanceProfile).where(AppearanceProfile.account_id == b_account)
            )
            b_profile_id = b_profile.id
            b_version = b_profile.version
            b_attributes = {
                (row.key, str(row.value)) for row in (await s.execute(
                    select(ProfileAttribute)
                    .where(ProfileAttribute.profile_id == b_profile_id)
                )).scalars()
            }
        assert b_attributes == {(SKIN, "often_oily")}

        # A perfectly well-formed subject — B's, in every field.
        forged = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=b_account,
            subject_id=b_member, relation="adult", age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as s:
            with pytest.raises(SubjectNotFound):
                await canonical_subject(
                    s, principal_account_id=a_account, subject=forged,
                )
            with pytest.raises(SubjectNotFound):
                await resolve_subject_profile_for_read(
                    s, forged, principal_account_id=a_account,
                )
            with pytest.raises(SubjectNotFound):
                await resolve_subject_profile_for_write(
                    s, forged, principal_account_id=a_account,
                )
            with pytest.raises(SubjectNotFound):
                await build_personal_lens_context(
                    s, category=PersonalLensCategory.SKIN_CARE,
                    principal_account_id=a_account, subject=forged,
                )
            await s.commit()

        # B's profile was not read into anything, and not written.
        async with get_sessionmaker()() as s:
            after = await s.get(AppearanceProfile, b_profile_id)
            assert after.account_id == b_account
            assert after.version == b_version
            assert {
                (row.key, str(row.value)) for row in (await s.execute(
                    select(ProfileAttribute)
                    .where(ProfileAttribute.profile_id == b_profile_id)
                )).scalars()
            } == b_attributes
            # And A acquired nothing by asking.
            assert await s.scalar(
                select(func.count(AppearanceProfile.id))
                .where(AppearanceProfile.account_id == a_account)
            ) == 0

    async def test_a_forged_child_of_another_account_is_refused_not_handed_off(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A refusal and a hand-over are not the same answer.

        The hard handoff fires before any profile is read — that is deliberate,
        and it is what makes this case sharp. A forged subject naming another
        household's under-twelve member would reach the gate first, and a lens
        that canonicalised under the subject's own account would answer "we are
        handing you to a clinician" instead of "no such person". That reply is
        itself the leak: it confirms the id names a real member, and that the
        household recorded them as a child.

        So the principal is compared before the gate, not after it.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        b_child = uuid.UUID(
            await _member(app_client, b_token, relation="child", age_band=AGE_BAND_UNDER_12)
        )

        forged = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=b_account,
            subject_id=b_child, relation="child", age_band=AGE_BAND_UNDER_12,
        )
        async with get_sessionmaker()() as s:
            with pytest.raises(SubjectNotFound):
                await build_personal_lens_context(
                    s, category=PersonalLensCategory.SKIN_CARE,
                    principal_account_id=a_account, subject=forged,
                )
            # B asking about their own child is the legitimate case, and it
            # still hands off rather than answering.
            legitimate = await build_personal_lens_context(
                s, category=PersonalLensCategory.SKIN_CARE,
                principal_account_id=b_account, subject=forged,
            )
        assert legitimate.status is PersonalLensStatus.HANDOFF_REQUIRED

    async def test_a_forged_account_holder_of_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The same forgery with ``subject_id=None``.

        Naming nobody means "me", and *whose* "me" is decided by the principal.
        A subject claiming to be B's account holder must not reach B's self row
        because A asked for it.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        await _member(app_client, b_token, relation="adult")
        b_self = uuid.UUID(await _self_id(app_client, b_token))
        assert (
            await _care_write(app_client, b_token, b_self, SKIN, "often_oily")
        ).status_code == 200

        forged = account_holder_subject(b_account)
        async with get_sessionmaker()() as s:
            with pytest.raises(SubjectNotFound):
                await canonical_subject(
                    s, principal_account_id=a_account, subject=forged,
                )
            assert await resolve_subject_profile_for_read(
                s, forged, principal_account_id=b_account,
            ) is not None  # sanity: it is a real subject, just not A's
            with pytest.raises(SubjectNotFound):
                await resolve_subject_profile_for_read(
                    s, forged, principal_account_id=a_account,
                )
            with pytest.raises(SubjectNotFound):
                await build_personal_lens_context(
                    s, category=PersonalLensCategory.SKIN_CARE,
                    principal_account_id=a_account, subject=forged,
                )

        async with get_sessionmaker()() as s:
            assert await s.scalar(
                select(func.count(AppearanceProfile.id))
                .where(AppearanceProfile.account_id == a_account)
            ) == 0

    async def test_the_legitimate_principal_and_subject_still_work(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The other half of a refusal is that the real thing still answers."""
        token, account_id = await registered_supabase_user()
        member = uuid.UUID(await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT))
        assert (
            await _care_write(app_client, token, member, SKIN, "often_dry_or_tight")
        ).status_code == 200

        async with get_sessionmaker()() as s:
            subject = await resolve_subject(
                s, account_id=account_id, subject_id=member,
            )
            profile = await resolve_subject_profile_for_read(
                s, subject, principal_account_id=account_id,
            )
            context = await build_personal_lens_context(
                s, category=PersonalLensCategory.SKIN_CARE,
                principal_account_id=account_id, subject=subject,
            )
        assert profile is not None
        assert profile.household_subject_id == member
        assert {fact.key for fact in context.body_facts} == {SKIN}

    async def test_explicit_and_omitted_self_still_converge(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Naming yourself and naming nobody, both under the same principal."""
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        assert (
            await _care_write(app_client, token, self_id, SKIN, "comfortable")
        ).status_code == 200

        async with get_sessionmaker()() as s:
            named = await resolve_subject(s, account_id=account_id, subject_id=self_id)
            explicit = await resolve_subject_profile_for_read(
                s, named, principal_account_id=account_id,
            )
            omitted = await resolve_subject_profile_for_read(
                s, account_holder_subject(account_id), principal_account_id=account_id,
            )
            direct = await resolve_self_profile_for_read(s, account_id)
        assert explicit is not None
        assert explicit.id == omitted.id == direct.id

    async def test_a_forged_subject_naming_another_household_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The mismatched pair: the caller's own account, somebody else's member."""
        mine_token, mine = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        theirs = await _member(app_client, theirs_token, relation="adult")
        await _care_write(app_client, theirs_token, theirs, SKIN, "often_oily")

        forged = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=mine,
            subject_id=uuid.UUID(theirs), relation="adult", age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as s:
            with pytest.raises(SubjectNotFound):
                await canonical_subject(s, principal_account_id=mine, subject=forged)
            with pytest.raises(SubjectNotFound):
                await resolve_subject_profile_for_read(
                    s, forged, principal_account_id=mine,
                )
            with pytest.raises(SubjectNotFound):
                await resolve_subject_profile_for_write(
                    s, forged, principal_account_id=mine,
                )
            with pytest.raises(SubjectNotFound):
                await build_personal_lens_context(
                    s, category=PersonalLensCategory.SKIN_CARE,
                    principal_account_id=mine, subject=forged,
                )

        async with get_sessionmaker()() as s:
            mine_profiles = await s.scalar(
                select(func.count(AppearanceProfile.id))
                .where(AppearanceProfile.account_id == mine)
            )
        assert mine_profiles == 0

    async def test_a_genuine_member_paired_with_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The reverse mismatch: somebody else's account, the caller's member.

        This is the shape a copy-paste bug produces. Neither half looks wrong on
        its own, so only the comparison against the principal decides.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        a_member = uuid.UUID(await _member(app_client, a_token, relation="adult"))

        forged = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=b_account,
            subject_id=a_member, relation="adult", age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as s:
            for principal in (a_account, b_account):
                with pytest.raises(SubjectNotFound):
                    await canonical_subject(
                        s, principal_account_id=principal, subject=forged,
                    )
                with pytest.raises(SubjectNotFound):
                    await resolve_subject_profile_for_write(
                        s, forged, principal_account_id=principal,
                    )

    async def test_a_stored_child_forged_as_an_adult_still_hands_off(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The one the whole product turns on.

        A household records a child under twelve. A caller names that real
        member — their own — and describes them as an adult. If the gate
        believed the object it was handed, the product would give personalised
        advice about a child, the single thing the constitution forbids
        outright.
        """
        token, account_id = await registered_supabase_user()
        child = await _member(app_client, token, relation="child", age_band=AGE_BAND_UNDER_12)
        assert (await _care_write(app_client, token, child, SKIN, "comfortable")).status_code == 200

        forged = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=account_id,
            subject_id=uuid.UUID(child), relation="adult", age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as s:
            # The forged band is discarded on the way in.
            canonical = await canonical_subject(
                s, principal_account_id=account_id, subject=forged,
            )
            assert canonical.age_band == AGE_BAND_UNDER_12
            context = await build_personal_lens_context(
                s, category=PersonalLensCategory.SKIN_CARE,
                principal_account_id=account_id, subject=forged,
            )
        assert context.status is PersonalLensStatus.HANDOFF_REQUIRED
        assert context.handoff is not None
        # No body facts reach a caller who tried this, handoff or not.
        assert context.body_facts == ()
        assert context.profile_id is None

    async def test_a_forged_account_holder_kind_cannot_force_self_delegation(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """``kind`` decides which resolver runs. It is not the caller's to set.

        Believing it would let a request about an ordinary member reach the
        account holder's own profile — the household's worst leak, inside one
        account.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        await _care_write(app_client, token, self_id, SKIN, "often_oily")

        forged = ResolvedSubject(
            kind=SUBJECT_ACCOUNT_HOLDER, account_id=account_id,
            subject_id=uuid.UUID(member), relation=RELATION_SELF, age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as s:
            canonical = await canonical_subject(
                s, principal_account_id=account_id, subject=forged,
            )
            assert canonical.kind == SUBJECT_HOUSEHOLD_MEMBER
            assert canonical.relation == "adult"
            assert canonical.is_account_holder is False
            # The member has no profile of their own yet, and that is the
            # answer — not the account holder's row.
            assert await resolve_subject_profile_for_read(
                s, forged, principal_account_id=account_id,
            ) is None
            context = await build_personal_lens_context(
                s, category=PersonalLensCategory.SKIN_CARE,
                principal_account_id=account_id, subject=forged,
            )
        assert context.status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT
        assert context.body_facts == ()

    async def test_a_synthesised_account_holder_cannot_bypass_a_stored_self(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Omitting the household is not a way of not having one.

        ``account_holder_subject()`` is the shape every client used before
        Family existed, and it carries ``not_stated``. Once a household exists,
        what the server knows about the account holder lives in the stored
        ``self`` row — including a band the household deliberately recorded.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)

        # Record the account holder as under twelve. Improbable, and the point:
        # the stored row must beat the synthesised one.
        async with get_sessionmaker()() as s:
            row = await s.get(FamilyProfile, uuid.UUID(self_id))
            row.age_band = AGE_BAND_UNDER_12
            await s.commit()

        synthesised = account_holder_subject(account_id)
        assert synthesised.age_band != AGE_BAND_UNDER_12
        async with get_sessionmaker()() as s:
            canonical = await canonical_subject(
                s, principal_account_id=account_id, subject=synthesised,
            )
            assert canonical.subject_id == uuid.UUID(self_id)
            assert canonical.age_band == AGE_BAND_UNDER_12
            context = await build_personal_lens_context(
                s, category=PersonalLensCategory.SKIN_CARE,
                principal_account_id=account_id, subject=synthesised,
            )
        assert context.status is PersonalLensStatus.HANDOFF_REQUIRED

    async def test_anything_that_is_not_a_subject_at_all_is_refused(
        self, db_clean, registered_supabase_user,
    ):
        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            for unchecked in (account_id, str(account_id), None, {"account_id": account_id}):
                with pytest.raises(ValueError):
                    await canonical_subject(
                        s, principal_account_id=account_id, subject=unchecked,
                    )


# ---------------------------------------------------------------------------
# 4c. A household with no account holder, or two
# ---------------------------------------------------------------------------
class TestCanonicalSelfIsExactlyOne:
    """Step 11A's rule, not a second opinion written here.

    A first-row query would have answered both of these states happily — by
    picking a human by insertion order in one case, and by treating a household
    as if it had none in the other. Both are refusals.
    """

    async def _household(self, app_client, registered_supabase_user):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        return token, account_id, self_id

    async def test_zero_active_self_rows_fails_closed_and_writes_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id, self_id = await self._household(
            app_client, registered_supabase_user,
        )
        async with get_sessionmaker()() as s:
            (await s.get(FamilyProfile, self_id)).active = False
            await s.commit()

        async with get_sessionmaker()() as s:
            for call in (resolve_self_profile_for_read, resolve_self_profile_for_write):
                with pytest.raises(HouseholdInvariantError):
                    await call(s, account_id)

        # Not a legacy profile invented to paper over it, and not a second one.
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0

    async def test_two_active_self_rows_fail_closed_rather_than_picking(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id, self_id = await self._household(
            app_client, registered_supabase_user,
        )
        async with get_sessionmaker()() as s:
            circle_id = await s.scalar(
                select(FamilyCircle.id).where(FamilyCircle.account_id == account_id)
            )
            s.add(FamilyProfile(circle_id=circle_id, position=3, relation=RELATION_SELF))
            await s.commit()

        async with get_sessionmaker()() as s:
            for call in (resolve_self_profile_for_read, resolve_self_profile_for_write):
                with pytest.raises(HouseholdInvariantError):
                    await call(s, account_id)
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0

    async def test_a_broken_household_never_adopts_an_existing_legacy_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The dangerous version: there *is* a profile to grab.

        Falling back to the legacy row here would hand the account holder's
        history to whatever the broken household turns out to have meant, and
        adopting it would make that permanent.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            await s.commit()

        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        async with get_sessionmaker()() as s:
            (await s.get(FamilyProfile, self_id)).active = False
            await s.commit()

        async with get_sessionmaker()() as s:
            with pytest.raises(HouseholdInvariantError):
                await resolve_self_profile_for_write(s, account_id)
            with pytest.raises(HouseholdInvariantError):
                await resolve_self_profile_for_read(s, account_id)

        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1
            assert (await s.get(AppearanceProfile, profile_id)).household_subject_id is None


# ---------------------------------------------------------------------------
# 4d. One account's profile bound to another account's subject
# ---------------------------------------------------------------------------
class TestCrossAccountCorruption:
    """Two columns say who a profile is for, and only one of them is a key.

    ``household_subject_id`` has a foreign key, so the subject row certainly
    exists. Nothing says it belongs to the same account, because that would
    need a composite key the household tables do not carry. So the row below is
    storable, and the only thing standing between it and one account reading —
    then overwriting — another account's body facts is the predicate on
    ``account_id``.
    """

    async def _corrupted(self, app_client, registered_supabase_user):
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        a_member = uuid.UUID(await _member(app_client, a_token, relation="adult"))
        async with get_sessionmaker()() as s:
            b_profile_id = await _corrupt_profile_onto(
                s, account_id=b_account, subject_id=a_member, value="often_oily",
            )
        return a_token, a_account, b_account, a_member, b_profile_id

    async def test_a_subject_read_never_returns_the_other_accounts_facts(
        self, db_clean, app_client, registered_supabase_user,
    ):
        a_token, a_account, _, a_member, _ = await self._corrupted(
            app_client, registered_supabase_user,
        )
        async with get_sessionmaker()() as s:
            subject = await resolve_subject(
                s, account_id=a_account, subject_id=a_member,
            )
            with pytest.raises(ProfileIdentityError):
                await resolve_subject_profile_for_read(
                    s, subject, principal_account_id=a_account,
                )

    async def test_the_lens_never_uses_the_other_accounts_facts(
        self, db_clean, app_client, registered_supabase_user,
    ):
        a_token, a_account, _, a_member, _ = await self._corrupted(
            app_client, registered_supabase_user,
        )
        async with get_sessionmaker()() as s:
            subject = await resolve_subject(
                s, account_id=a_account, subject_id=a_member,
            )
            with pytest.raises(ProfileIdentityError):
                await build_personal_lens_context(
                    s, category=PersonalLensCategory.SKIN_CARE,
                    principal_account_id=a_account, subject=subject,
                )

    async def test_a_care_write_never_modifies_the_other_accounts_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        a_token, _, _, a_member, b_profile_id = await self._corrupted(
            app_client, registered_supabase_user,
        )
        async with get_sessionmaker()() as s:
            before = await s.get(AppearanceProfile, b_profile_id)
            snapshot = (
                before.account_id, before.household_subject_id, before.version,
                before.updated_at,
            )
            before_attributes = {
                (row.key, str(row.value)) for row in
                (await s.execute(
                    select(ProfileAttribute)
                    .where(ProfileAttribute.profile_id == b_profile_id)
                )).scalars()
            }

        response = await _care_write(
            app_client, a_token, a_member, SKIN, "often_dry_or_tight",
        )
        # Governed, not a 500, and it says nothing about the other account.
        assert response.status_code == 503, response.text
        body = response.json()["detail"]
        assert body["code"] == "FEATURE_UNAVAILABLE"
        for leak in (str(b_profile_id), str(a_member), "account", "profile_subject"):
            assert leak not in response.text, leak

        async with get_sessionmaker()() as s:
            after = await s.get(AppearanceProfile, b_profile_id)
            assert (
                after.account_id, after.household_subject_id, after.version,
                after.updated_at,
            ) == snapshot
            after_attributes = {
                (row.key, str(row.value)) for row in
                (await s.execute(
                    select(ProfileAttribute)
                    .where(ProfileAttribute.profile_id == b_profile_id)
                )).scalars()
            }
        assert after_attributes == before_attributes
        assert after_attributes == {(SKIN, "often_oily")}

    async def test_the_corrupt_link_is_not_hidden_by_answering_no_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Pretending A simply has no profile would be the quiet failure.

        It reads safely — A never sees B's facts — and then the next write
        inserts a second profile for the same subject on top of a row nobody
        ever looked at again. Fail closed instead.
        """
        a_token, _, _, a_member, _ = await self._corrupted(
            app_client, registered_supabase_user,
        )
        before = await _counts()
        response = await _care_write(app_client, a_token, a_member, SENS, "rarely_reactive")
        assert response.status_code == 503
        assert await _counts() == before

    async def test_self_resolution_refuses_another_accounts_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The same corruption, aimed at the account holder's own row."""
        a_token, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        await _member(app_client, a_token, relation="adult")
        a_self = uuid.UUID(await _self_id(app_client, a_token))
        async with get_sessionmaker()() as s:
            b_profile_id = await _corrupt_profile_onto(
                s, account_id=b_account, subject_id=a_self, value="often_oily",
            )

        async with get_sessionmaker()() as s:
            with pytest.raises(ProfileIdentityError):
                await resolve_self_profile_for_read(s, a_account)
            with pytest.raises(ProfileIdentityError):
                await resolve_self_profile_for_write(s, a_account)

        # B's row is untouched, and A acquired nothing.
        async with get_sessionmaker()() as s:
            row = await s.get(AppearanceProfile, b_profile_id)
            assert row.account_id == b_account
            assert row.household_subject_id == a_self
            assert await s.scalar(
                select(func.count(AppearanceProfile.id))
                .where(AppearanceProfile.account_id == a_account)
            ) == 0


# ---------------------------------------------------------------------------
# 4e. Malformed identity reaches the customer as a governed answer
# ---------------------------------------------------------------------------
class TestGovernedIdentityFailure:
    """Not a 500, and not a word about what is wrong.

    ``ProfileIdentityError`` and ``HouseholdInvariantError`` are both
    ``IdentityInvariantError``, so they stop at the shared error boundary
    instead of escaping from whichever route happened to touch identity. The
    customer gets one sentence; the reason code goes to the log.
    """

    FORBIDDEN = ("dual", "mismatch", "invariant", "self_profile", "household_has")

    def _assert_governed(self, response, *, url, secrets=()):
        assert response.status_code == 503, (url, response.status_code, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "FEATURE_UNAVAILABLE", url
        assert detail["retryable"] is False, url
        assert detail["message"] == "This result is not available right now.", url
        # Nothing but the fixed shape and a request id an operator can follow.
        assert set(detail) == {"code", "message", "retryable", "request_id"}, url
        body = response.text
        for token in (*self.FORBIDDEN, *(str(x) for x in secrets)):
            assert token not in body, (url, token)

    async def _dual_identity(self, app_client, registered_supabase_user):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        async with get_sessionmaker()() as s:
            s.add(AppearanceProfile(account_id=account_id, household_subject_id=self_id))
            s.add(AppearanceProfile(account_id=account_id))
            await s.commit()
        return token, account_id, self_id

    @pytest.mark.parametrize(
        "url",
        ["/api/v2/profile", "/api/v2/profile/attributes", "/api/v2/onboarding/status"],
    )
    async def test_dual_identity_is_governed_on_every_legacy_surface(
        self, db_clean, app_client, registered_supabase_user, url,
    ):
        token, account_id, self_id = await self._dual_identity(
            app_client, registered_supabase_user,
        )
        response = await app_client.get(url, headers=auth(token))
        self._assert_governed(response, url=url, secrets=(account_id, self_id))

    async def test_a_broken_household_is_governed_on_every_legacy_surface(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        async with get_sessionmaker()() as s:
            (await s.get(FamilyProfile, self_id)).active = False
            await s.commit()

        for url in ("/api/v2/profile", "/api/v2/profile/attributes", "/api/v2/onboarding/status"):
            response = await app_client.get(url, headers=auth(token))
            self._assert_governed(response, url=url, secrets=(account_id, self_id))

    async def test_the_care_seam_is_governed_too(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id, self_id = await self._dual_identity(
            app_client, registered_supabase_user,
        )
        response = await _care_write(app_client, token, self_id, SKIN, "comfortable")
        self._assert_governed(
            response, url="care-profile", secrets=(account_id, self_id),
        )


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

    async def test_a_true_simultaneous_first_write_makes_exactly_one_profile(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """``gather`` is not a race. A barrier is.

        Two coroutines started together may still run one after the other —
        whichever awaits first can finish its whole transaction before the
        other begins, and then the contended state this test exists for never
        happens. So both are held until both are ready, and released together.

        What serialises them is the member row lock that
        ``canonical_subject_for_write`` takes: the second transaction waits
        inside the resolver rather than racing it. That is stronger than
        letting both reach the insert and sorting it out with the unique index,
        and it is why two resolver-driven inserts for one member cannot collide
        at all. The index and its ``ON CONFLICT`` remain the backstop for a row
        that appears by some other route, proven directly below.

        No sleeps: a sleep would be a guess about scheduling, and a guess that
        is usually right is exactly the test that stops catching the bug.
        """
        token, account_id = await registered_supabase_user()
        member = uuid.UUID(await _member(app_client, token, relation="adult"))

        barrier = asyncio.Barrier(2)

        async def write(value: str) -> uuid.UUID:
            async with get_sessionmaker()() as s:
                subject = await resolve_subject(
                    s, account_id=account_id, subject_id=member,
                )
                # Both transactions have now read, and neither has inserted.
                await barrier.wait()
                profile = await resolve_subject_profile_for_write(
                    s, subject, principal_account_id=account_id,
                )
                await profile_service.apply_attributes(
                    s, profile, [{"key": SKIN, "value": value}],
                )
                await s.commit()
                return profile.id

        first, second = await asyncio.gather(
            write("comfortable"), write("often_dry_or_tight"),
        )

        # Both callers got the same authoritative row, and there is one of it.
        assert first == second
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1
            bound = await s.scalar(
                select(AppearanceProfile.household_subject_id)
                .where(AppearanceProfile.id == first)
            )
            values = (await s.execute(
                select(ProfileAttribute.value)
                .where(ProfileAttribute.profile_id == first, ProfileAttribute.key == SKIN)
            )).scalars().all()
        assert bound == member
        # One key, one row: the existing concurrent-update contract is
        # last-writer-wins on the attribute, not two rows for one key.
        assert len(values) == 1
        assert values[0] in ("comfortable", "often_dry_or_tight")

    async def test_a_true_simultaneous_first_write_for_two_members_makes_two(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The same barrier, and the answer must be the opposite.

        A partial index that was too wide — on ``account_id``, say — would pass
        the test above and collapse these two people into one profile.
        """
        token, account_id = await registered_supabase_user()
        one = uuid.UUID(await _member(app_client, token, relation="adult"))
        two = uuid.UUID(await _member(app_client, token, relation="other"))

        barrier = asyncio.Barrier(2)

        async def write(member: uuid.UUID) -> uuid.UUID:
            async with get_sessionmaker()() as s:
                subject = await resolve_subject(
                    s, account_id=account_id, subject_id=member,
                )
                await barrier.wait()
                profile = await resolve_subject_profile_for_write(
                    s, subject, principal_account_id=account_id,
                )
                await s.commit()
                return profile.id

        first, second = await asyncio.gather(write(one), write(two))
        assert first != second
        async with get_sessionmaker()() as s:
            bound = (await s.execute(
                select(AppearanceProfile.household_subject_id)
                .where(AppearanceProfile.household_subject_id.is_not(None))
            )).scalars().all()
        assert sorted(map(str, bound)) == sorted(map(str, (one, two)))

    async def test_a_true_simultaneous_household_and_self_write(
        self, db_clean, registered_supabase_user,
    ):
        """The race the account lock exists for, held open on purpose.

        One transaction opens the household; the other decides whether the
        account holder already has a profile. Both are released at the moment
        before either has decided anything, so the lock is what orders them
        rather than whichever coroutine happened to await first.
        """
        _, account_id = await registered_supabase_user()
        barrier = asyncio.Barrier(2)

        async def open_household():
            async with get_sessionmaker()() as s:
                await barrier.wait()
                await family_service.add_profile(s, account_id, relation="adult")
                await s.commit()

        async def self_write():
            async with get_sessionmaker()() as s:
                await barrier.wait()
                await resolve_self_profile_for_write(s, account_id)
                await s.commit()

        await asyncio.gather(open_household(), self_write())

        async with get_sessionmaker()() as s:
            profiles = (await s.execute(
                select(AppearanceProfile).where(AppearanceProfile.account_id == account_id)
            )).scalars().all()
        assert len(profiles) == 1, profiles

        # Whichever way it went, the account is readable: never the forbidden
        # state of a legacy row beside an adopted one.
        async with get_sessionmaker()() as s:
            await resolve_self_profile_for_read(s, account_id)
            assert await s.scalar(select(func.count(FamilyCircle.id))) == 1

    async def test_the_conflict_backstop_answers_a_row_it_did_not_see(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """What the unique index is still for, proven without a race.

        Two writes for one member cannot collide any more — the member lock
        stops the second before it looks. So the ``ON CONFLICT`` clause guards a
        different thing: a bound profile that exists although this transaction's
        existence check said it did not. A stale read is the honest way to
        produce that state deterministically, so the check is made to return
        ``None`` exactly once while a real row sits underneath it.

        The insert must then conflict rather than raise, and the caller must be
        handed the row that actually exists rather than the one it tried to
        make.
        """
        from app.domains.profile import identity as identity_module

        token, account_id = await registered_supabase_user()
        member = uuid.UUID(await _member(app_client, token, relation="adult"))

        async with get_sessionmaker()() as s:
            planted = AppearanceProfile(account_id=account_id, household_subject_id=member)
            s.add(planted)
            await s.flush()
            planted_id = planted.id
            await s.commit()

        real_lookup = identity_module._profile_for_subject
        blinded = {"once": True}

        async def stale_once(session, *, account_id, subject_id):
            if blinded["once"] and subject_id == member:
                blinded["once"] = False
                return None
            return await real_lookup(
                session, account_id=account_id, subject_id=subject_id,
            )

        monkeypatch.setattr(identity_module, "_profile_for_subject", stale_once)

        async with get_sessionmaker()() as s:
            subject = await resolve_subject(s, account_id=account_id, subject_id=member)
            profile = await resolve_subject_profile_for_write(
                s, subject, principal_account_id=account_id,
            )
            await s.commit()

        assert blinded["once"] is False, "the stale read never ran"
        assert profile.id == planted_id
        assert profile.account_id == account_id
        assert profile.household_subject_id == member
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1

    async def test_a_member_deactivated_mid_request_answers_404_not_500(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """The gap between the route's resolve and the resolver's.

        The write resolver re-resolves on purpose. That means a member can be
        active when the route checks and gone when the resolver checks, and the
        request must answer like any other unknown member rather than as a
        server fault. Forced deterministically: the route is held between the
        two resolutions while another committed session deactivates the member.
        """
        from app.api.v2 import family as family_route

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")

        resolved = asyncio.Event()
        deactivated = asyncio.Event()
        real_write = family_route.resolve_subject_profile_for_write

        async def held(session, subject, *, principal_account_id):
            resolved.set()
            await deactivated.wait()
            return await real_write(
                session, subject, principal_account_id=principal_account_id,
            )

        monkeypatch.setattr(family_route, "resolve_subject_profile_for_write", held)

        async def deactivate():
            await resolved.wait()
            async with get_sessionmaker()() as s:
                row = await s.get(FamilyProfile, uuid.UUID(member))
                row.active = False
                await s.commit()
            deactivated.set()

        response, _ = await asyncio.gather(
            _care_write(app_client, token, member, SKIN, "comfortable"),
            deactivate(),
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == {"code": "family_profile_not_found"}
        assert member not in response.text

        # And the failed write left nothing behind.
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0
            assert await s.scalar(select(func.count(ProfileAttribute.id))) == 0

    async def test_a_care_write_holds_the_member_still_until_it_commits(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """Deactivation cannot overtake a write that already has authority.

        Once the write resolver has taken the member row, a concurrent
        deactivation waits. The two settle into one order — the attributes
        commit, and the member is deactivated after — rather than an attribute
        landing that claims an authority the household had already withdrawn.
        """
        from app.domains.profile import service as profile_service_module

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")

        authority_held = asyncio.Event()
        release = asyncio.Event()
        real_apply = profile_service_module.apply_attributes
        order: list[str] = []

        async def paused(session, profile, attributes):
            # The member row lock is already held by this transaction.
            authority_held.set()
            await release.wait()
            result = await real_apply(session, profile, attributes)
            order.append("attributes_written")
            return result

        monkeypatch.setattr(profile_service_module, "apply_attributes", paused)

        async def deactivate():
            await authority_held.wait()
            async with get_sessionmaker()() as s:
                row = await s.get(FamilyProfile, uuid.UUID(member))
                row.active = False
                # This UPDATE needs the row the Care write is holding, so it
                # cannot land until that transaction commits.
                waiting = asyncio.create_task(s.commit())
                release.set()
                await waiting
            order.append("member_deactivated")

        response, _ = await asyncio.gather(
            _care_write(app_client, token, member, SKIN, "comfortable"),
            deactivate(),
        )

        assert response.status_code == 200, response.text
        assert order == ["attributes_written", "member_deactivated"], order

        async with get_sessionmaker()() as s:
            assert (await s.get(FamilyProfile, uuid.UUID(member))).active is False
            values = (await s.execute(select(ProfileAttribute.value))).scalars().all()
        assert values == ["comfortable"]

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
        assert payload["schema_version"] == "1.3"

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

    async def test_a_household_missing_its_account_holder_is_stated_not_guessed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The export is the one surface that must not stop.

        Somebody exercising a data right still gets their data. What it must
        not do is decide, from a broken household, which human the legacy
        profile belonged to — the file exists precisely so they can see who has
        what, and a confident wrong label there is worse than no label.
        """
        token, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            await _seed_every_child_table(s, profile_id)
            await s.commit()
        member = await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        async with get_sessionmaker()() as s:
            (await s.get(FamilyProfile, self_id)).active = False
            await s.commit()

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, account_id)
        profile_domain = payload["domains"]["profile"]

        # Stated, not guessed.
        assert {e["error"] for e in profile_domain["invariant_errors"]} == {
            "household_self_profile_missing"
        }
        # Nobody is labelled the account holder, including the other member.
        assert all(x["is_account_holder"] is False for x in profile_domain["subjects"])
        assert any(x["household_subject_id"] == member for x in profile_domain["subjects"])
        # The data is still all there, under an unattributed entry.
        unattributed = next(
            x for x in profile_domain["subjects"] if x["household_subject_id"] is None
        )
        assert unattributed["relation"] is None
        assert len(unattributed["attributes"]) == 0 or unattributed["attributes"]
        for table in ("goals", "observations", "onboarding_sessions", "user_constraints"):
            assert len(unattributed[table]) == 1, table

        # And nothing was repaired, adopted or deleted on the way past.
        async with get_sessionmaker()() as s:
            assert (await s.get(AppearanceProfile, profile_id)).household_subject_id is None
            assert (await s.get(FamilyProfile, self_id)).active is False
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1

    async def test_two_account_holders_are_reported_not_chosen_between(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        async with get_sessionmaker()() as s:
            circle_id = await s.scalar(
                select(FamilyCircle.id).where(FamilyCircle.account_id == account_id)
            )
            s.add(FamilyProfile(circle_id=circle_id, position=3, relation=RELATION_SELF))
            await s.commit()

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, account_id)
        profile_domain = payload["domains"]["profile"]
        assert {e["error"] for e in profile_domain["invariant_errors"]} == {
            "household_has_multiple_self_profiles"
        }
        assert all(x["is_account_holder"] is False for x in profile_domain["subjects"])
        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(FamilyProfile.id))) == 3

    async def test_a_cross_account_profile_is_exported_whole_but_unattributed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Two duties pulling opposite ways, and both of them met.

        The subject id names somebody in *another* household, so it must not
        appear in this file at all — a stranger's member id is a stranger's
        identity. But the profile row and every child row under it belong to
        this account by ``account_id``, and those are the customer's own body
        facts. Dropping them to avoid the leak would answer a data-rights
        request by quietly withholding data.

        So: exported in full, with the identity stripped rather than invented.
        """
        a_token, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_member = uuid.UUID(await _member(app_client, a_token, relation="adult"))
        async with get_sessionmaker()() as s:
            b_profile_id = await _corrupt_profile_onto(
                s, account_id=b_account, subject_id=a_member, value="often_oily",
            )
            seeded = await _seed_every_child_table(
                s, b_profile_id, attribute_key=SENS,
            )
            await s.commit()

        async with get_sessionmaker()() as s:
            payload = await export_service.build_export(s, b_account)
        profile_domain = payload["domains"]["profile"]

        assert profile_domain["invariant_errors"] == [
            {"profile_id": str(b_profile_id), "error": "profile_subject_ownership_invalid"}
        ]
        # Not presented as one of B's people, and not presented as A's member.
        assert all(
            x["household_subject_id"] != str(a_member)
            for x in profile_domain["subjects"]
        )
        assert all(x["is_account_holder"] is False for x in profile_domain["subjects"])

        # But the data is all there, under an entry that claims nothing.
        entry = next(iter(profile_domain["unattributed_profiles"]))
        assert entry["household_subject_id"] is None
        assert entry["relation"] is None
        assert entry["is_account_holder"] is False
        assert entry["profile"]["id"] == str(b_profile_id)
        # The corrupt link itself is stripped, not echoed.
        assert entry["profile"]["household_subject_id"] is None
        for table in (
            "attributes", "change_events", "observations", "style_preferences",
            "fit_preferences", "lifestyle_context", "user_constraints", "goals",
            "onboarding_sessions",
        ):
            assert len(entry[table]) >= 1, table
        assert {a["value"] for a in entry["attributes"]} == {"often_oily", "often_dry_or_tight"}
        assert seeded

        # A's member id appears nowhere in B's file at all.
        assert str(a_member) not in json.dumps(payload, default=str)

        # And A's own export never contains B's profile or facts.
        async with get_sessionmaker()() as s:
            a_payload = await export_service.build_export(s, a_account)
        a_text = json.dumps(a_payload, default=str)
        assert str(b_profile_id) not in a_text
        assert "often_oily" not in a_text
        assert a_payload["domains"]["profile"]["unattributed_profiles"] == []

        # Nothing was repaired on the way past.
        async with get_sessionmaker()() as s:
            row = await s.get(AppearanceProfile, b_profile_id)
            assert row.account_id == b_account
            assert row.household_subject_id == a_member

    async def test_a_circle_with_no_members_is_a_broken_household_not_no_household(
        self, db_clean, registered_supabase_user,
    ):
        """``members == []`` is not the same question as "is there a circle".

        A circle whose rows have all gone is the most alarming state identity
        can be in. Inferring "this account never opened a household" from an
        empty member list would report it as the most ordinary one, and label
        the legacy profile confidently as the canonical account holder on the
        strength of that mistake.
        """
        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as s:
            profile = await resolve_self_profile_for_write(s, account_id)
            profile_id = profile.id
            await _seed_every_child_table(s, profile_id)
            s.add(FamilyCircle(account_id=account_id))
            await s.commit()

        async with get_sessionmaker()() as s:
            assert await s.scalar(select(func.count(FamilyCircle.id))) == 1
            assert await s.scalar(select(func.count(FamilyProfile.id))) == 0
            payload = await export_service.build_export(s, account_id)
        profile_domain = payload["domains"]["profile"]

        assert {e["error"] for e in profile_domain["invariant_errors"]} == {
            "household_self_profile_missing"
        }
        # Nobody is confidently named the account holder.
        assert all(x["is_account_holder"] is False for x in profile_domain["subjects"])
        entry = next(
            x for x in profile_domain["subjects"] if x["household_subject_id"] is None
        )
        assert entry["relation"] is None
        # And the account's own data is still all in the file.
        for table in (
            "attributes", "change_events", "observations", "style_preferences",
            "fit_preferences", "lifestyle_context", "user_constraints", "goals",
            "onboarding_sessions",
        ):
            assert len(entry[table]) == 1, table

        async with get_sessionmaker()() as s:
            assert (await s.get(AppearanceProfile, profile_id)).household_subject_id is None
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 1
            assert await s.scalar(select(func.count(FamilyProfile.id))) == 0

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
    @pytest.fixture
    def fake_supabase_admin(self, monkeypatch):
        """Erasure asks Supabase Auth to delete the identity too.

        That is a live call, and this suite never makes one. Stubbed exactly as
        the account-deletion state machine's own tests stub it, so the job can
        reach ``completed`` and the tombstone can be checked.
        """
        class _Admin:
            def __init__(self):
                self.deleted: list[str] = []

            @property
            def auth(self):
                return self

            @property
            def admin(self):
                return self

            def delete_user(self, user_id: str) -> None:
                self.deleted.append(user_id)

        admin = _Admin()
        monkeypatch.setattr(
            "app.domains.privacy.deletion_service.get_supabase_admin", lambda: admin,
        )
        return admin

    async def test_account_deletion_removes_all_ten_profile_tables(
        self, db_clean, app_client, registered_supabase_user, fake_supabase_admin,
    ):
        """Erasure means all ten, for every human on the account.

        Nine tables hang off ``appearance_profiles.id`` and the profile itself
        is the tenth. Checking attributes alone would have let eight tables of
        somebody's body facts survive a deletion they asked for — and now that
        an account holds several people's profiles, it would leave more behind
        than it used to.
        """
        from app.domains.privacy import deletion_service
        from app.domains.privacy.models import AccountDeletionJob
        from app.domains.product.models import (
            LabelSnapshot,
            ProductRecord,
            ScanDevice,
            ScanEvent,
        )

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult", age_band=AGE_BAND_ADULT)
        self_id = await _self_id(app_client, token)
        await _care_write(app_client, token, self_id, SKIN, "comfortable")
        await _care_write(app_client, token, member, SKIN, "often_dry_or_tight")

        # Global Product Truth: shared with every other shopper, and nothing to
        # do with this account. Erasing one person must not take it.
        barcode = "8901234567894"
        snapshot_id = uuid.uuid4()
        async with get_sessionmaker()() as s:
            stranger_device = ScanDevice(device_key="stranger", token_hash="x" * 64)
            s.add(stranger_device)
            await s.flush()
            stranger_event = ScanEvent(
                device_id=stranger_device.id, account_id=None, barcode=barcode,
                outcome="known", client_scan_id=str(uuid.uuid4()),
            )
            s.add(stranger_event)
            await s.flush()
            s.add(ProductRecord(barcode=barcode, confidence="verified", origin="community"))
            s.add(LabelSnapshot(
                id=snapshot_id, barcode=barcode, scan_event_id=stranger_event.id,
                facts={"ingredients_text": "Petrolatum"}, confidence="verified",
                content_fingerprint="7" * 64, version_number=1, changed_fields=[],
                completeness="complete_for_grading",
            ))
            await s.commit()

        # Both people's profiles, fully populated across all nine child tables.
        async with get_sessionmaker()() as s:
            profile_ids = (await s.execute(select(AppearanceProfile.id))).scalars().all()
            assert len(profile_ids) == 2
            for profile_id in profile_ids:
                # Both profiles already carry a Care fact under ``SKIN``.
                await _seed_every_child_table(s, profile_id, attribute_key=SENS)
            await s.commit()

        async with get_sessionmaker()() as s:
            before = await _child_rows(s)
        for model in PROFILE_CHILD_MODELS:
            assert before[model.__name__], model.__name__

        async with get_sessionmaker()() as s:
            await deletion_service.request_deletion(s, account_id)
            await s.commit()
        async with get_sessionmaker()() as s:
            assert await deletion_service.drain_all(s) >= 1
            await s.commit()

        async with get_sessionmaker()() as s:
            # The tenth table, and the nine.
            assert await s.scalar(select(func.count(AppearanceProfile.id))) == 0
            after = await _child_rows(s)
            for model in PROFILE_CHILD_MODELS:
                assert after[model.__name__] == [], model.__name__

            # The household itself.
            assert await s.scalar(select(func.count(FamilyCircle.id))) == 0
            assert await s.scalar(select(func.count(FamilyProfile.id))) == 0

            # The tombstone stays: it is classified LEGALLY_RETAINED precisely
            # so the audit trail does not have to hold the identity itself.
            job = (await s.execute(
                select(AccountDeletionJob)
                .where(AccountDeletionJob.account_id == account_id)
            )).scalar_one()
            assert job.state == "complete"
            assert job.completed_at is not None

            # And Product Truth is exactly as it was.
            record = (await s.execute(
                select(ProductRecord).where(ProductRecord.barcode == barcode)
            )).scalar_one()
            assert record.confidence == "verified"
            snapshot = await s.get(LabelSnapshot, snapshot_id)
            assert snapshot is not None
            assert snapshot.facts == {"ingredients_text": "Petrolatum"}

        assert fake_supabase_admin.deleted == [str(account_id)]

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
