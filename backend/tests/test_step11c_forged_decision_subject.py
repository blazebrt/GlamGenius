"""A DecisionSubject is a claim. The domain must not take it as proof.

Step 11B established that the principal and the subject are two different facts
and must arrive as two arguments, because a forged ``ResolvedSubject`` is
internally consistent: re-resolving it under its own ``account_id`` finds the
row, agrees with itself, and hands over somebody else's member.

Step 11C then wrapped the checked result in a public ``DecisionSubject``
dataclass — and the domain services read its fields. That is the same mistake
one layer out. The HTTP routes build theirs correctly, but "the route already
checked it" is not a property a domain boundary can rely on: a worker, a
background job, another API, an internal orchestration or a test can call these
functions directly, and one that constructs its own object would be believed.

So every public Decision Memory boundary re-derives the subject instead of
reading it. These tests do not call the checker; they call the *services*, with
a subject forged entirely for another account, and require each one to refuse
before it reads or writes anything.

The most important assertions here are the ones about what is *not* in the
database afterwards. A row like::

    account_id            = A
    household_subject_id  = a member of B's household

is the shape this whole slice exists to make impossible, and it is checked by
looking, not by trusting the return value.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.family.decision_subject import (
    DecisionSubject,
    canonical_decision_subject,
)
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    SUBJECT_ACCOUNT_HOLDER,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
    SubjectNotFound,
    account_holder_subject,
)
from app.domains.product import scan_memory
from app.domains.product.models import LabelSnapshot, ScanDecisionEvent, ScanEvent
from app.domains.purchase import decision_memory
from app.domains.purchase.check_service import (
    resolve_care_purchase_check,
    resolve_fragrance_check,
)
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    ShoppingCandidate,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate

pytestmark = pytest.mark.asyncio

PROFILES_URL = "/api/v2/family-circle/profiles"

#: A boundary far enough in the past that, if it were believed, every
#: subject-less row in the database would look safely claimable.
FORGED_BOUNDARY = "1999-01-01T00:00:00+00:00"


async def _member(client, token, *, relation="adult") -> uuid.UUID:
    response = await client.post(
        PROFILES_URL, headers=auth(token), json={"relation": relation},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _self_id(client, token) -> uuid.UUID:
    circle = (await client.get("/api/v2/family-circle", headers=auth(token))).json()
    return uuid.UUID(next(p["id"] for p in circle["profiles"] if p["relation"] == "self"))


async def _candidate(account_id: uuid.UUID) -> uuid.UUID:
    candidate_id = await _seed_db_candidate(account_id)
    async with get_sessionmaker()() as session:
        row = await session.get(ShoppingCandidate, candidate_id)
        row.brand = "Example Labs"
        row.display_name = "Gentle Cleanser"
        row.details = {
            "product_type": "cleanser", "purpose": "cleanse",
            "active_ingredients": ["Niacinamide"],
        }
        await session.commit()
    return candidate_id


async def _snapshot(barcode: str = "8901030000011") -> LabelSnapshot:
    from app.domains.product.models import ScanDevice

    async with get_sessionmaker()() as session:
        device = ScanDevice(device_key=uuid.uuid4().hex, token_hash="x" * 64)
        session.add(device)
        await session.flush()
        event = ScanEvent(
            device_id=device.id, account_id=None, barcode=barcode,
            outcome="known", client_scan_id=uuid.uuid4().hex,
        )
        session.add(event)
        await session.flush()
        snapshot = LabelSnapshot(
            barcode=barcode, scan_event_id=event.id,
            facts={"ingredients_text": "Petrolatum"}, confidence="verified",
            content_fingerprint="c" * 64, version_number=1, changed_fields=[],
            completeness="complete_for_grading",
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot


def _forged(
    *, account_id: uuid.UUID, subject_id: uuid.UUID | None,
    kind: str = SUBJECT_HOUSEHOLD_MEMBER, relation: str = "adult",
    age_band: str = AGE_BAND_ADULT, circle_created_at=FORGED_BOUNDARY,
) -> DecisionSubject:
    """A DecisionSubject built by hand, exactly as a hostile caller would.

    Every field is a lie the caller gets to choose, which is the point: nothing
    on this object was checked by anybody, and the services must behave as if
    none of it were there.
    """
    from datetime import datetime

    return DecisionSubject(
        subject=ResolvedSubject(
            kind=kind, account_id=account_id, subject_id=subject_id,
            relation=relation, age_band=age_band,
        ),
        circle_created_at=(
            datetime.fromisoformat(circle_created_at)
            if isinstance(circle_created_at, str) else circle_created_at
        ),
    )


async def _two_households(client, registered_supabase_user):
    """Account A and account B, each with a household and one named member."""
    a_token, a_account = await registered_supabase_user()
    b_token, b_account = await registered_supabase_user()
    a_member = await _member(client, a_token)
    b_member = await _member(client, b_token)
    return {
        "a_token": a_token, "a_account": a_account, "a_member": a_member,
        "b_token": b_token, "b_account": b_account, "b_member": b_member,
    }


async def _decision_rows() -> list[tuple]:
    async with get_sessionmaker()() as session:
        return [
            tuple(row) for row in (await session.execute(
                select(
                    PurchaseDecision.account_id,
                    PurchaseDecision.household_subject_id,
                    PurchaseDecision.decision,
                ).order_by(PurchaseDecision.created_at, PurchaseDecision.id)
            )).all()
        ]


async def _cross_account_rows() -> list[tuple[str, str]]:
    """Every row whose subject belongs to a household the account does not own.

    The invariant, asked of the database directly rather than of the code that
    was supposed to maintain it. A join from each memory table through
    ``family_profiles`` to ``family_circles`` says who really owns the named
    person; anything where that is not the row's own account is a leak.
    """
    statement = """
        SELECT '{table}' AS source, m.id::text
        FROM {table} m
        JOIN family_profiles p ON p.id = m.household_subject_id
        JOIN family_circles c ON c.id = p.circle_id
        WHERE c.account_id <> m.account_id
    """
    from sqlalchemy import text

    async with get_sessionmaker()() as session:
        found: list[tuple[str, str]] = []
        for table in (
            "scan_decision_events", "purchase_decisions", "purchase_decision_events",
        ):
            found.extend(
                (row[0], row[1]) for row in
                (await session.execute(text(statement.format(table=table)))).all()
            )
        return found


# ---------------------------------------------------------------------------
# 1. The revalidation authority itself
# ---------------------------------------------------------------------------
class TestTheAuthorityReDerivesEverything:
    async def test_a_subject_belonging_entirely_to_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.family.decision_subject import canonicalize_decision_subject

        ids = await _two_households(app_client, registered_supabase_user)
        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await canonicalize_decision_subject(
                    session,
                    principal_account_id=ids["a_account"],
                    decision_subject=forged,
                )

    async def test_a_self_consistent_forgery_naming_the_principal_is_still_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The harder forgery: ``account_id`` already says A.

        Comparing the two halves of the object against each other passes here,
        because the caller made them agree. What refuses it is that the named
        member is looked up in *A's* household and is not in it.
        """
        from app.domains.family.decision_subject import canonicalize_decision_subject

        ids = await _two_households(app_client, registered_supabase_user)
        forged = _forged(account_id=ids["a_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await canonicalize_decision_subject(
                    session,
                    principal_account_id=ids["a_account"],
                    decision_subject=forged,
                )

    async def test_a_foreign_account_id_is_refused_even_with_no_member_named(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The one case where the account field is the only thing that can refuse.

        Naming another account's *member* is caught twice over: the account
        comparison rejects it, and looking the id up in this principal's
        household would not find it either. Naming another account with no
        member at all has only the first defence. Without it the claim is
        silently reinterpreted as "me" and the caller is answered for a person
        they did not ask about — no cross-account leak, but a request answered
        about somebody other than the one it named, which is how a caller ends
        up writing the wrong person's history.
        """
        from app.domains.family.decision_subject import canonicalize_decision_subject

        ids = await _two_households(app_client, registered_supabase_user)
        forged = _forged(
            account_id=ids["b_account"], subject_id=None,
            kind=SUBJECT_ACCOUNT_HOLDER, relation="self",
        )

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await canonicalize_decision_subject(
                    session,
                    principal_account_id=ids["a_account"],
                    decision_subject=forged,
                )

    async def test_the_boundary_never_reaches_the_resolver_at_all(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Structural, because that is what actually makes the forgery useless.

        ``circle_created_at`` is not checked and then discarded — it is never
        passed on. What leaves :func:`_claimed_subject` is the embedded
        ``ResolvedSubject`` alone, which has no boundary field to carry one, so
        there is nothing downstream for a forged value to influence. Asserted
        here so that a refactor which starts threading the wrapper through has
        to face this test rather than silently re-opening the hole.
        """
        from app.domains.family.decision_subject import _claimed_subject

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        forged = _forged(account_id=account_id, subject_id=member)

        claim = _claimed_subject(forged)
        assert isinstance(claim, ResolvedSubject)
        assert not hasattr(claim, "circle_created_at")

    async def test_a_forged_household_boundary_is_replaced_by_the_stored_one(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The one field that is not an id, and is just as dangerous.

        ``circle_created_at`` decides which subject-less rows a person may
        claim. A caller that could set it to 1999 would make every ambiguous
        legacy row in the account look safely theirs.
        """
        from app.domains.family.decision_subject import canonicalize_decision_subject

        token, account_id = await registered_supabase_user()
        await _member(app_client, token)
        forged = _forged(
            account_id=account_id, subject_id=None,
            kind=SUBJECT_ACCOUNT_HOLDER, relation="self",
        )

        async with get_sessionmaker()() as session:
            stored = await session.scalar(
                select(FamilyCircle.created_at)
                .where(FamilyCircle.account_id == account_id)
            )
            checked = await canonicalize_decision_subject(
                session, principal_account_id=account_id, decision_subject=forged,
            )
        assert checked.circle_created_at == stored
        assert checked.circle_created_at != forged.circle_created_at
        # And the identity was re-derived too: a household exists, so "me" is
        # the stored self row rather than the ``None`` the caller supplied.
        assert checked.subject_id == await _self_id(app_client, token)

    async def test_a_forged_account_holder_flag_does_not_promote_a_member(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """``kind`` is recomputed from the stored relation, never believed."""
        from app.domains.family.decision_subject import canonicalize_decision_subject

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        forged = _forged(
            account_id=account_id, subject_id=member,
            kind=SUBJECT_ACCOUNT_HOLDER, relation="self",
        )

        async with get_sessionmaker()() as session:
            checked = await canonicalize_decision_subject(
                session, principal_account_id=account_id, decision_subject=forged,
            )
        assert checked.is_account_holder is False
        assert checked.subject_id == member
        assert checked.subject.relation == "adult"

    async def test_revalidating_creates_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A read authority that wrote would be a read that changes the answer."""
        from app.domains.family.decision_subject import canonicalize_decision_subject
        from app.domains.profile.models import AppearanceProfile

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)

        async def counts():
            async with get_sessionmaker()() as session:
                return (
                    await session.scalar(select(func.count(FamilyCircle.id))),
                    await session.scalar(select(func.count(FamilyProfile.id))),
                    await session.scalar(select(func.count(AppearanceProfile.id))),
                    await session.scalar(select(func.count(PurchaseDecision.id))),
                    await session.scalar(select(func.count(PurchaseDecisionEvent.id))),
                    await session.scalar(select(func.count(ScanDecisionEvent.id))),
                )

        before = await counts()
        async with get_sessionmaker()() as session:
            for _ in range(3):
                await canonicalize_decision_subject(
                    session,
                    principal_account_id=account_id,
                    decision_subject=_forged(account_id=account_id, subject_id=member),
                )
            await session.commit()
        assert await counts() == before

    async def test_an_account_with_no_household_is_still_re_derived(
        self, db_clean, registered_supabase_user,
    ):
        """No circle means no boundary — and the forged one still loses."""
        from app.domains.family.decision_subject import canonicalize_decision_subject

        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            checked = await canonicalize_decision_subject(
                session,
                principal_account_id=account_id,
                decision_subject=_forged(
                    account_id=account_id, subject_id=None,
                    kind=SUBJECT_ACCOUNT_HOLDER, relation="self",
                ),
            )
        assert checked.circle_created_at is None
        assert checked.subject_id is None
        assert checked.is_account_holder is True


# ---------------------------------------------------------------------------
# 2. The services themselves, called directly with a forged subject
# ---------------------------------------------------------------------------
class TestForgedSubjectAtEveryDomainBoundary:
    async def test_every_purchase_read_refuses(
        self, db_clean, app_client, registered_supabase_user,
    ):
        ids = await _two_households(app_client, registered_supabase_user)
        candidate_id = await _candidate(ids["a_account"])
        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            candidate = await session.get(ShoppingCandidate, candidate_id)
            with pytest.raises(SubjectNotFound):
                await decision_memory.current_purchase_decision_for_subject(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, candidate_id=candidate_id,
                )
            with pytest.raises(SubjectNotFound):
                await decision_memory.decision_history(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, limit=20,
                )
            with pytest.raises(SubjectNotFound):
                await decision_memory.history_coverage(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged,
                )
            with pytest.raises(SubjectNotFound):
                await decision_memory.purchase_guard(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, candidate=candidate,
                )
            with pytest.raises(SubjectNotFound):
                await decision_memory.has_incomplete_legacy_context(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, candidate_id=candidate_id,
                )

    async def test_every_scan_read_refuses(
        self, db_clean, app_client, registered_supabase_user,
    ):
        ids = await _two_households(app_client, registered_supabase_user)
        snapshot = await _snapshot()
        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await scan_memory.read_scan_memory(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, barcode=snapshot.barcode,
                    label_snapshot_id=snapshot.id,
                    label_version=snapshot.version_number,
                    content_fingerprint=snapshot.content_fingerprint,
                )
            with pytest.raises(SubjectNotFound):
                await scan_memory.scan_decision_history(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, barcode=snapshot.barcode,
                    label_version=snapshot.version_number,
                    content_fingerprint=snapshot.content_fingerprint, limit=20,
                )

    async def test_the_scan_write_refuses_and_stores_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        ids = await _two_households(app_client, registered_supabase_user)
        snapshot = await _snapshot()
        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await scan_memory.record_scan_decision(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, barcode=snapshot.barcode,
                    label_snapshot_id=snapshot.id,
                    label_version=snapshot.version_number,
                    content_fingerprint=snapshot.content_fingerprint,
                    decision="BUY", idempotency_key="forged-key",
                )
            # Committing whatever the refused call left behind, so the check
            # below cannot be satisfied merely by the transaction rolling back.
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 0
        assert await _cross_account_rows() == []

    async def test_the_care_write_refuses_and_stores_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A real check, so the refusal is the authority rather than a KeyError.

        The check is composed for A's own account holder first and handed to the
        save service *beside* a forged subject — which is exactly the shape of a
        caller that resolved one person and then wrote for another.
        """
        ids = await _two_households(app_client, registered_supabase_user)
        candidate_id = await _candidate(ids["a_account"])
        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            check = await resolve_care_purchase_check(
                session, account_id=ids["a_account"],
                account_id_str=str(ids["a_account"]),
                candidate_id=candidate_id, plan_date=None,
            )
            with pytest.raises(SubjectNotFound):
                await decision_memory.save_care_decision(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, candidate_id=candidate_id,
                    check=check, decision="bought", note=None,
                )
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
        assert await _cross_account_rows() == []

    async def test_the_fragrance_write_refuses_and_stores_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from tests.test_v3_05_9_fragrance_purchase import _fragrance_item

        ids = await _two_households(app_client, registered_supabase_user)
        inspected = await app_client.post(
            "/api/v2/shopping/candidates/inspect",
            headers=auth(ids["a_token"]),
            json={"source": "manual", "item": _fragrance_item()},
        )
        assert inspected.status_code == 200, inspected.text
        candidate_id = uuid.UUID(inspected.json()["candidate"]["id"])
        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])

        async with get_sessionmaker()() as session:
            check = await resolve_fragrance_check(
                session, account_id=ids["a_account"], candidate_id=candidate_id,
            )
            with pytest.raises(SubjectNotFound):
                await decision_memory.save_fragrance_decision(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, candidate_id=candidate_id,
                    check=check, decision="bought", note=None,
                )
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
        assert await _cross_account_rows() == []


# ---------------------------------------------------------------------------
# 3. Neither account's real data moves
# ---------------------------------------------------------------------------
class TestNeitherAccountIsDisturbed:
    async def test_a_forged_write_leaves_both_accounts_exactly_as_they_were(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Both sides of the attack, checked: B is not read and A is not written.

        A refusal that quietly rolled back A's own unrelated work, or that
        touched B's row on the way to deciding it was not allowed to, would both
        be failures of a kind the return value cannot show.
        """
        ids = await _two_households(app_client, registered_supabase_user)
        a_candidate = await _candidate(ids["a_account"])
        b_candidate = await _candidate(ids["b_account"])

        # Real history on both sides, written legitimately through the route.
        for token, candidate, member, outcome in (
            (ids["a_token"], a_candidate, ids["a_member"], "waiting"),
            (ids["b_token"], b_candidate, ids["b_member"], "bought"),
        ):
            response = await app_client.post(
                f"/api/v2/shopping/candidates/{candidate}/decision"
                f"?on=2026-08-20&subject_id={member}",
                headers=auth(token), json={"decision": outcome},
            )
            assert response.status_code == 200, response.text

        before = await _decision_rows()
        assert len(before) == 2

        forged = _forged(account_id=ids["b_account"], subject_id=ids["b_member"])
        async with get_sessionmaker()() as session:
            check = await resolve_care_purchase_check(
                session, account_id=ids["a_account"],
                account_id_str=str(ids["a_account"]),
                candidate_id=a_candidate, plan_date=None,
            )
            with pytest.raises(SubjectNotFound):
                await decision_memory.save_care_decision(
                    session, principal_account_id=ids["a_account"],
                    decision_subject=forged, candidate_id=a_candidate,
                    check=check, decision="skipped", note="not mine to write",
                )
            await session.commit()

        assert await _decision_rows() == before
        assert await _cross_account_rows() == []

        # And B's own member still reads exactly what B recorded.
        theirs = await app_client.get(
            f"/api/v2/shopping/candidates/{b_candidate}/decision"
            f"?subject_id={ids['b_member']}",
            headers=auth(ids["b_token"]),
        )
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["decision"]["decision"] == "bought"

    async def test_a_forged_boundary_cannot_claim_an_ambiguous_legacy_row(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The forged-timestamp attack, against the service rather than the checker.

        The subject here is legitimately A's account holder. Only the household
        boundary is a lie, and it is the field that decides whether a row nobody
        can be shown to have made becomes this person's.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token)
        candidate_id = await _candidate(account_id)
        self_id = await _self_id(app_client, token)

        async with get_sessionmaker()() as session:
            circle_created = await session.scalar(
                select(FamilyCircle.created_at)
                .where(FamilyCircle.account_id == account_id)
            )
            ambiguous = PurchaseDecision(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add(ambiguous)
            await session.flush()
            from datetime import timedelta

            ambiguous.updated_at = circle_created + timedelta(seconds=60)
            await session.commit()
            ambiguous_id = ambiguous.id

        # A boundary in 1999 would make this row look safely claimable.
        forged = _forged(
            account_id=account_id, subject_id=self_id,
            kind=SUBJECT_ACCOUNT_HOLDER, relation="self",
        )
        async with get_sessionmaker()() as session:
            current = await decision_memory.current_purchase_decision_for_subject(
                session, principal_account_id=account_id,
                decision_subject=forged, candidate_id=candidate_id,
            )
        assert current is None, "a forged boundary claimed an unattributable row"

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, ambiguous_id)
        assert row.household_subject_id is None


# ---------------------------------------------------------------------------
# 4. The routes keep behaving, and keep refusing
# ---------------------------------------------------------------------------
class TestRoutesAreUnaffected:
    async def test_the_legitimate_route_path_still_works_end_to_end(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Revalidating twice must not change the answer, only confirm it."""
        ids = await _two_households(app_client, registered_supabase_user)
        candidate_id = await _candidate(ids["a_account"])

        written = await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision"
            f"?on=2026-08-20&subject_id={ids['a_member']}",
            headers=auth(ids["a_token"]), json={"decision": "bought"},
        )
        assert written.status_code == 200, written.text
        assert written.json()["subject"]["household_subject_id"] == str(ids["a_member"])

        guard = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard"
            f"?subject_id={ids['a_member']}",
            headers=auth(ids["a_token"]),
        )
        assert guard.status_code == 200, guard.text
        assert guard.json()["guard_state"] == "exact_prior_bought"

        async with get_sessionmaker()() as session:
            stored = (await session.execute(
                select(PurchaseDecision.household_subject_id)
            )).scalars().all()
        assert stored == [ids["a_member"]]
        assert await _cross_account_rows() == []

    async def test_another_accounts_member_over_http_is_the_same_refusal(
        self, db_clean, app_client, registered_supabase_user,
    ):
        ids = await _two_households(app_client, registered_supabase_user)
        candidate_id = await _candidate(ids["a_account"])

        refused = await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision"
            f"?on=2026-08-20&subject_id={ids['b_member']}",
            headers=auth(ids["a_token"]), json={"decision": "bought"},
        )
        assert refused.status_code == 404, refused.text
        assert str(ids["b_member"]) not in refused.text

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
        assert await _cross_account_rows() == []


# ---------------------------------------------------------------------------
# 5. A non-DecisionSubject is a programming error, not a silent pass
# ---------------------------------------------------------------------------
class TestMalformedInput:
    async def test_an_arbitrary_object_is_rejected_rather_than_duck_typed(
        self, db_clean, registered_supabase_user,
    ):
        """Anything with the right attribute names would otherwise be believed."""
        from app.domains.family.decision_subject import canonicalize_decision_subject

        class LooksTheSame:
            subject = ResolvedSubject(
                kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=uuid.uuid4(),
                subject_id=uuid.uuid4(), relation="adult", age_band=AGE_BAND_ADULT,
            )
            circle_created_at = None

        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            with pytest.raises(ValueError):
                await canonicalize_decision_subject(
                    session, principal_account_id=account_id,
                    decision_subject=LooksTheSame(),
                )

    async def test_omitting_the_subject_still_means_the_authenticated_person(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """``None`` is the one substitution that is safe, and it is derived."""
        from app.domains.family.decision_subject import canonicalize_decision_subject

        token, account_id = await registered_supabase_user()
        await _member(app_client, token)
        async with get_sessionmaker()() as session:
            checked = await canonicalize_decision_subject(
                session, principal_account_id=account_id, decision_subject=None,
            )
            expected = await canonical_decision_subject(
                session, principal_account_id=account_id,
                subject=account_holder_subject(account_id),
            )
        assert checked == expected
        assert checked.is_account_holder is True
