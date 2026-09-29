"""What a household's data export may and may not contain.

An export is the one surface where refusing is the wrong answer: somebody
exercising a data right must receive their data, and a household whose identity
is in a state no route can produce has not stopped owning the rows underneath
it. So this file is about the two things that can still go wrong when nothing
stops.

**A foreign identifier reaching the file.** A row whose ``household_subject_id``
points into another account's household is this customer's row — ``account_id``
says so — and a stranger's id. The grouped structures strip it. The export also
used to carry the same rows a second time as flat dumps of every column, where
nothing stripped anything: the redaction was real and the leak was two keys
further up the same file. The walk below looks at every string in the whole
export rather than at the places somebody remembered to check.

**A confident answer where there is no answer.** A subject-less row belongs to
the account holder only while it provably predates the household. If the
household has no identifiable account holder, there is nobody for it to belong
to, and saying it is theirs anyway would be the export inventing the one fact it
exists to report honestly.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.domains.care.models import CareProductPreference
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.privacy import EXPORT_SCHEMA_VERSION
from app.domains.privacy.export import build_export
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select, text

from tests.conftest import auth
from tests.test_step10b_shelf_manager_api import _add
from tests.test_step11d_subject_care_authority import (
    _backdate_circle,
    _expired_shelf,
    _member,
    _queue,
    _self_subject_id,
)
from tests.test_v3_03_3_integration import _seed

pytestmark = pytest.mark.asyncio

TODAY = date.today()
RESPOND_URL = "/api/v2/shelf/manager/respond"


def _strings(value, path: str = "") -> list[tuple[str, str]]:
    """Every string in the export, with the path it was found at.

    Recursive on purpose. A leak test that names the keys it checks can only
    ever catch the leaks somebody already thought of, and the one this file
    exists for was in a key nobody was checking.
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_strings(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_strings(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        found.append((path, value))
    elif value is not None:
        found.append((path, str(value)))
    return found


async def _export(account_id: uuid.UUID) -> dict:
    async with get_sessionmaker()() as session:
        export = await build_export(session, account_id)
    # One domain failing must never sink the export, so a failure is recorded
    # rather than raised. A test that did not look would read an error marker as
    # an empty section and pass.
    broken = {
        name: section for name, section in export["domains"].items()
        if isinstance(section, dict) and "error" in section
    }
    assert broken == {}, broken
    return export["domains"]


async def _answer_as(client, token: str, member: str | None, key: str) -> None:
    primary = (await _queue(client, token, member))["primary"]
    url = RESPOND_URL if member is None else f"{RESPOND_URL}?subject_id={member}"
    response = await client.post(url, headers=auth(token), json={
        "decision_key": primary["decision_key"],
        "decision_fingerprint": primary["decision_fingerprint"],
        "choice": "accept",
        "client_mutation_id": key,
    })
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Nothing raw, and nothing foreign
# ---------------------------------------------------------------------------


async def test_the_export_no_longer_carries_unredacted_copies_of_subject_rows(
    app_client, db_clean, registered_supabase_user,
):
    """The flat dumps are gone, and every row they held is still exported."""
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    await _answer_as(app_client, token, member, "export-1")

    export = await _export(account_id)
    care = export["routines"]
    assert "shelf_manager_decision_events" not in care
    assert "care_product_preferences" not in care

    assert care["manager_history"]["by_subject"][member], "the answer left the file"
    assert care["care_product_preferences_by_subject"][member], "the preference left the file"
    assert care["unattributed_care_product_preferences"] == []
    assert care["invariant_errors"] == []


async def test_no_identifier_from_another_household_appears_anywhere_in_the_file(
    app_client, db_clean, registered_supabase_user,
):
    """A stranger's member id is a stranger's identity, wherever it is written.

    Both rows below are this customer's: their ``account_id`` is this account.
    Only the subject each one names belongs to somebody else. The data stays in
    the file and the identity is replaced — and the check walks the whole export
    rather than the keys that happened to be remembered.
    """
    token, account_id = await registered_supabase_user()
    other_token, other_account = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    await _answer_as(app_client, token, member, "export-foreign")

    stranger = await _member(app_client, other_token)
    # Point this account's rows at the other household's member. The foreign key
    # is deferrable and cross-account by nature, which is exactly why the export
    # has to defend against it rather than trust the constraint.
    async with get_sessionmaker()() as session:
        await session.execute(text(
            "UPDATE shelf_manager_decision_events SET household_subject_id = :other "
            "WHERE account_id = :account"
        ), {"other": uuid.UUID(stranger), "account": account_id})
        await session.execute(text(
            "UPDATE care_product_preferences SET household_subject_id = :other "
            "WHERE account_id = :account"
        ), {"other": uuid.UUID(stranger), "account": account_id})
        await session.execute(text(
            "UPDATE inventory_events SET household_subject_id = :other "
            "WHERE account_id = :account AND household_subject_id IS NOT NULL"
        ), {"other": uuid.UUID(stranger), "account": account_id})
        await session.commit()

    export = await _export(account_id)
    leaks = [
        (path, value) for path, value in _strings(export)
        if stranger in value or str(other_account) in value
    ]
    assert leaks == [], f"another household's identity reached the export at {leaks}"

    care = export["routines"]
    unattributed = care["manager_history"]["unattributed"]
    assert len(unattributed) == 1
    assert unattributed[0]["household_subject_id"] is None
    assert len(care["unattributed_care_product_preferences"]) == 1
    assert care["unattributed_care_product_preferences"][0]["invariant"] == (
        "preference_subject_ownership_invalid"
    )
    assert {row["error"] for row in care["invariant_errors"]} == {
        "decision_subject_ownership_invalid", "preference_subject_ownership_invalid",
    }
    # The rows themselves are still the customer's, and still in their file.
    assert str(item_id) in {value for _path, value in _strings(export)}
    assert member is not None


# ---------------------------------------------------------------------------
# A household with no identifiable account holder
# ---------------------------------------------------------------------------


async def test_a_household_with_no_account_holder_attributes_nothing_to_anybody(
    app_client, db_clean, registered_supabase_user,
):
    """Nobody to attribute them to, so nobody gets them — in every section.

    Two sections of this file used not to ask the question at all, so a
    household in this state still had its pre-household Care preferences and
    Manager answers handed to an account holder the export could not identify.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    other_id = await _add(
        app_client, token, name="Spare Moisturiser", product_type="moisturiser",
        expiry=TODAY + timedelta(days=400),
    )

    # Pre-household state, written by the pre-household store: one answer to the
    # manager, and one pause the person made themselves on a different product.
    await _answer_as(app_client, token, None, "export-legacy-answer")
    assert (await app_client.post(
        f"/api/v2/routines/products/{other_id}/pause", headers=auth(token),
    )).status_code == 200

    await _member(app_client, token)
    await _backdate_circle(account_id, seconds=5)
    healthy = await _export(account_id)
    assert healthy["inventory"]["care_preference_history"]["account_holder_legacy"], (
        "the account holder could not claim their own pre-household pause"
    )
    assert healthy["routines"]["manager_history"]["account_holder_legacy"], (
        "the account holder could not claim their own pre-household answer"
    )

    # Now take the account holder out of the household.
    async with get_sessionmaker()() as session:
        await session.execute(
            text("UPDATE family_profiles SET active = false WHERE id = :id"),
            {"id": await _self_subject_id(account_id)},
        )
        await session.commit()

    export = await _export(account_id)
    inventory = export["inventory"]
    care = export["routines"]

    assert inventory["care_preference_history"]["account_holder_legacy"] == []
    assert inventory["care_preference_history"]["unattributed"], (
        "a pre-household preference was dropped instead of being exported "
        "without a name on it"
    )
    assert inventory["care_preference_history"]["coverage"] == {
        "unattributed_legacy_preferences_present": True,
        "complete_for_subject": False,
    }
    assert care["manager_history"]["account_holder_legacy"] == []
    assert care["manager_history"]["unattributed"]
    assert care["manager_history"]["coverage"]["complete_for_subject"] is False

    # And every section says the same thing about why.
    for section in (inventory, care, export["profile"], export["product_scans"], export["shopping"]):
        assert {row["error"] for row in section["invariant_errors"]} >= {
            "household_self_profile_missing"
        }, section.keys()


# ---------------------------------------------------------------------------
# Care preference events, attributed
# ---------------------------------------------------------------------------


async def test_care_preference_events_are_grouped_by_the_person_who_made_them(
    app_client, db_clean, registered_supabase_user,
):
    """A flat list of one household's pauses is the shape this slice replaced.

    The customer could see that a product was paused and not which of the people
    in the file paused it, which is the same disclosure failure the whole step
    exists to fix — arriving in the file they asked for precisely to find out.
    """
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    item_id = await _expired_shelf(app_client, token)
    first = await _member(app_client, token)
    second = await _member(app_client, token)
    await _answer_as(app_client, token, first, "export-events-1")
    await _answer_as(app_client, token, second, "export-events-2")

    events = (await _export(account_id))["inventory"]["care_preference_events"]
    assert set(events["by_subject"]) == {
        first, second, str(await _self_subject_id(account_id)),
    }
    for member in (first, second):
        rows = events["by_subject"][member]
        assert [row["event_type"] for row in rows] == ["care_routine_paused"]
        assert rows[0]["item_id"] == item_id
        assert rows[0]["household_subject_id"] == member
    assert events["by_subject"][str(await _self_subject_id(account_id))] == []
    assert events["unattributed"] == []
    assert events["coverage"]["complete_for_subject"] is True


async def test_the_export_schema_version_still_says_what_this_shape_is(
    app_client, db_clean, registered_supabase_user,
):
    """Step 11E moved the export to 1.4 for subject-owned routine history;
    Lane E moves it to 1.5 for the completed export coverage; Step 15 moves it
    to 1.6 for the growth domain's referral history. The Step 11D shape is
    unchanged inside it."""
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    async with get_sessionmaker()() as session:
        export = await build_export(session, account_id)
    assert export["schema_version"] == EXPORT_SCHEMA_VERSION == "1.6"


async def test_erasure_leaves_no_preference_and_no_subject_behind(
    app_client, db_clean, registered_supabase_user,
):
    """Exported with them, erased with them — including a member's own rows."""
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    await _expired_shelf(app_client, token)
    member = await _member(app_client, token)
    await _answer_as(app_client, token, member, "export-erase")

    async with get_sessionmaker()() as session:
        await session.execute(
            text("DELETE FROM accounts WHERE id = :id"), {"id": account_id},
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        assert (await session.execute(select(CareProductPreference).where(
            CareProductPreference.account_id == account_id,
        ))).scalars().all() == []
        assert (await session.execute(
            select(FamilyProfile)
            .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
            .where(FamilyCircle.account_id == account_id)
        )).scalars().all() == []
