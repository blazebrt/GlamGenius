"""The other half of Step 11C: proving whose product a decision is about.

Step 11C's first correction stopped the domain trusting a caller-supplied
``DecisionSubject``. The same argument applies one parameter over. A
``ShoppingCandidate`` is an ORM object the caller fetched, and fetching one by
primary key finds another account's product exactly as readily as this
account's — so a boundary that accepts a candidate object and reads its fields
is trusting whoever called it, just as surely as one that read
``subject.account_id``.

Two things follow, and both are proved here against a real database.

The **Purchase Guard** loads the candidate itself, under the authenticated
account, and uses only that row. Passed principal A beside account B's
candidate, it would otherwise derive identity from B's product, answer with B's
candidate id, and search A's history using B's fingerprint.

The **event append** refuses an impossible pairing. A decision row and a
candidate that do not belong together must never produce an event, because
events are append-only: a row claiming a decision came from a product it did
not is believed later precisely because nothing rewrites them.

Step 11C now has to prove both halves of one sentence::

    Account owns Candidate
    Subject owns Decision Memory about Candidate

Neither replaces the other, and the closing tests check both as database
invariants rather than as return values.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.family.decision_subject import canonical_decision_subject
from app.domains.family.subject import account_holder_subject
from app.domains.purchase import decision_memory
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    ShoppingCandidate,
)
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import (
    IdentityInvariantError,
    NotFoundError,
)
from sqlalchemy import func, select, text

from tests.conftest import auth
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate

pytestmark = pytest.mark.asyncio

PROFILES_URL = "/api/v2/family-circle/profiles"


async def _member(client, token, *, relation="adult") -> uuid.UUID:
    response = await client.post(
        PROFILES_URL, headers=auth(token), json={"relation": relation},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _candidate(account_id: uuid.UUID, *, name="Gentle Cleanser") -> uuid.UUID:
    candidate_id = await _seed_db_candidate(account_id)
    async with get_sessionmaker()() as session:
        row = await session.get(ShoppingCandidate, candidate_id)
        row.brand = "Example Labs"
        row.display_name = name
        row.details = {
            "product_type": "cleanser", "purpose": "cleanse",
            "active_ingredients": ["Niacinamide"],
        }
        await session.commit()
    return candidate_id


async def _detached(candidate_id: uuid.UUID) -> ShoppingCandidate:
    """The candidate as a caller would hold it: fetched, then handed on.

    Loaded in its own session and expunged, so the object passed to the domain
    is exactly what a worker or another service would have — a populated ORM
    instance with no live identity map behind it, and no account scoping
    anywhere in how it was obtained.
    """
    async with get_sessionmaker()() as session:
        candidate = (await session.execute(
            select(ShoppingCandidate).where(ShoppingCandidate.id == candidate_id)
        )).scalar_one()
        session.expunge(candidate)
        return candidate


async def _subject_for(account_id: uuid.UUID, subject_id: uuid.UUID | None = None):
    """A legitimate, canonical DecisionSubject for this account."""
    from app.domains.family.subject import resolve_subject

    async with get_sessionmaker()() as session:
        claim = (
            account_holder_subject(account_id) if subject_id is None
            else await resolve_subject(
                session, account_id=account_id, subject_id=subject_id,
            )
        )
        return await canonical_decision_subject(
            session, principal_account_id=account_id, subject=claim,
        )


async def _decision_for(account_id: uuid.UUID, candidate_id: uuid.UUID) -> uuid.UUID:
    """A real current decision row, written straight to the database."""
    async with get_sessionmaker()() as session:
        row = PurchaseDecision(
            account_id=account_id, household_subject_id=None,
            candidate_id=candidate_id, strategy_key="care_purchase",
            recommendation_verdict="wait", recommendation_version="v",
            recommendation_snapshot={}, decision="waiting",
            followed_recommendation=True,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _cross_account_events() -> list[str]:
    """Events whose candidate belongs to a different account than the event.

    The invariant asked of the database rather than of the code meant to hold
    it. An event is an immutable claim about one account's product; one whose
    candidate is owned elsewhere is a record that will be believed and is
    false.
    """
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text(
            """
            SELECT e.id::text
            FROM purchase_decision_events e
            JOIN shopping_candidates c ON c.id = e.candidate_id
            WHERE c.account_id <> e.account_id
            """
        ))).all()
        return [row[0] for row in rows]


async def _cross_account_subjects() -> list[tuple[str, str]]:
    """The Step 11C invariant this correction must not replace.

    A row carrying one account's id beside another account's household member.
    Kept alongside the candidate check because they are different mistakes:
    one gets the product wrong, the other gets the person wrong.
    """
    statement = """
        SELECT '{table}' AS source, m.id::text
        FROM {table} m
        JOIN family_profiles p ON p.id = m.household_subject_id
        JOIN family_circles c ON c.id = p.circle_id
        WHERE c.account_id <> m.account_id
    """
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
# 1. The Purchase Guard proves whose candidate it was given
# ---------------------------------------------------------------------------
class TestGuardCandidateOwnership:
    async def test_another_accounts_candidate_is_refused_before_anything_is_projected(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Nothing about B's product may appear in an answer given to A.

        Not the candidate id, not the identity fingerprint, not a state derived
        from B's category. The refusal has to come before the projection is
        built, so it is the absence of a return value that is asserted rather
        than the contents of one.
        """
        a_token, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        b_candidate_id = await _candidate(b_account, name="Somebody Else's Serum")
        b_candidate = await _detached(b_candidate_id)
        a_subject = await _subject_for(a_account)

        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError) as refusal:
                await decision_memory.purchase_guard(
                    session,
                    principal_account_id=a_account,
                    decision_subject=a_subject,
                    candidate=b_candidate,
                )
        # And the refusal itself says nothing about what was asked for.
        assert str(b_candidate_id) not in str(refusal.value)
        assert "Somebody Else's Serum" not in str(refusal.value)

    async def test_an_invented_candidate_is_the_same_refusal(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Indistinguishable from a real one owned elsewhere, deliberately.

        Telling the two apart would confirm that an id names a real product
        somebody else is considering.
        """
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        b_candidate = await _detached(await _candidate(b_account))
        a_subject = await _subject_for(a_account)

        invented = await _detached(await _candidate(a_account))
        invented.id = uuid.uuid4()

        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError) as foreign:
                await decision_memory.purchase_guard(
                    session, principal_account_id=a_account,
                    decision_subject=a_subject, candidate=b_candidate,
                )
            with pytest.raises(NotFoundError) as unreal:
                await decision_memory.purchase_guard(
                    session, principal_account_id=a_account,
                    decision_subject=a_subject, candidate=invented,
                )
        assert str(foreign.value) == str(unreal.value)

    async def test_a_forged_account_id_on_the_candidate_object_changes_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The field that would be checked if the object were believed.

        Setting ``account_id`` to the principal is the obvious forgery, and it
        works against any implementation that compares the object to the
        principal instead of going back to the database.
        """
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        b_candidate = await _detached(await _candidate(b_account))
        b_candidate.account_id = a_account

        a_subject = await _subject_for(a_account)
        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError):
                await decision_memory.purchase_guard(
                    session, principal_account_id=a_account,
                    decision_subject=a_subject, candidate=b_candidate,
                )

    async def test_the_canonical_row_is_used_rather_than_the_supplied_object(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Own candidate, tampered fields. The database wins every one.

        This is the positive case: the call succeeds, and what comes back is
        derived from the stored row rather than from the object handed in. A
        guard that used the supplied object would answer with the altered brand
        and a fingerprint computed from it.
        """
        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        subject = await _subject_for(account_id)

        async with get_sessionmaker()() as session:
            truthful = await decision_memory.purchase_guard(
                session, principal_account_id=account_id,
                decision_subject=subject,
                candidate=await _detached(candidate_id),
            )

        tampered = await _detached(candidate_id)
        tampered.brand = "Different Labs"
        tampered.display_name = "Different Product"
        tampered.details = {
            "product_type": "serum", "purpose": "treat",
            "active_ingredients": ["Retinol"],
        }
        async with get_sessionmaker()() as session:
            answered = await decision_memory.purchase_guard(
                session, principal_account_id=account_id,
                decision_subject=subject, candidate=tampered,
            )

        assert answered["candidate_id"] == str(candidate_id)
        assert answered["identity"] == truthful["identity"]

    async def test_the_route_keeps_working_unchanged(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Re-loading inside the domain must not change the answer, only prove it."""
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        candidate_id = await _candidate(account_id)

        written = await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision"
            f"?on=2026-08-20&subject_id={member}",
            headers=auth(token), json={"decision": "bought"},
        )
        assert written.status_code == 200, written.text

        guard = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard"
            f"?subject_id={member}",
            headers=auth(token),
        )
        assert guard.status_code == 200, guard.text
        assert guard.json()["guard_state"] == "exact_prior_bought"
        assert guard.json()["candidate_id"] == str(candidate_id)
        assert guard.json()["prior_consideration_count"] == 1


# ---------------------------------------------------------------------------
# 2. An event never claims a product the decision did not name
# ---------------------------------------------------------------------------
class TestEventAppendInvariants:
    async def test_a_cross_account_pairing_is_refused_and_writes_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        a_token, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_candidate = await _candidate(a_account)
        b_candidate = await _candidate(b_account)
        a_decision = await _decision_for(a_account, a_candidate)

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, a_decision)
            with pytest.raises(IdentityInvariantError) as refusal:
                await decision_memory._record_decision_event(
                    session, row=row, candidate=await _detached(b_candidate),
                )
            # Committed rather than rolled back, so the assertion below cannot
            # be satisfied merely by the transaction unwinding.
            await session.commit()

        assert refusal.value.reason == "purchase_decision_candidate_identity_mismatch"
        # The customer-facing sentence names nothing.
        detail = refusal.value.to_detail()
        for forbidden in (str(a_account), str(b_account), str(a_candidate), str(b_candidate)):
            assert forbidden not in str(detail)

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
        assert await _cross_account_events() == []

    async def test_a_matching_id_on_another_accounts_candidate_is_still_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Both halves of the invariant, and why neither is redundant.

        Comparing ids alone catches every pairing real rows can produce, because
        ids are unique — so a test built from real rows proves nothing about the
        account half. The case it is actually for is a forged object: B's
        candidate with its ``id`` set to the one A's decision names. The ids
        then agree, and only the account comparison is left standing between an
        immutable event and a claim that A decided about B's product.
        """
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_candidate = await _candidate(a_account, name="Mine")
        b_candidate = await _candidate(b_account, name="Theirs")
        a_decision = await _decision_for(a_account, a_candidate)

        forged = await _detached(b_candidate)
        forged.id = a_candidate

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, a_decision)
            assert row.candidate_id == forged.id, "the ids must agree for this test"
            assert row.account_id != forged.account_id
            with pytest.raises(IdentityInvariantError) as refusal:
                await decision_memory._record_decision_event(
                    session, row=row, candidate=forged,
                )
            await session.commit()

        assert refusal.value.reason == "purchase_decision_candidate_identity_mismatch"
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
        assert await _cross_account_events() == []

    async def test_the_same_accounts_wrong_candidate_is_refused_too(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """One account, two products, and an event that would name the wrong one.

        No privacy boundary is crossed here — both products are this customer's
        — and it is still wrong. An immutable event that says a decision came
        from a product it did not is a false record of what somebody chose, and
        the Purchase Guard would later project it as prior consideration of a
        product they never considered.
        """
        token, account_id = await registered_supabase_user()
        first = await _candidate(account_id, name="First Cleanser")
        second = await _candidate(account_id, name="Second Cleanser")
        decision = await _decision_for(account_id, first)

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, decision)
            with pytest.raises(IdentityInvariantError) as refusal:
                await decision_memory._record_decision_event(
                    session, row=row, candidate=await _detached(second),
                )
            await session.commit()

        assert refusal.value.reason == "purchase_decision_candidate_identity_mismatch"
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0

    async def test_the_matching_pairing_still_appends(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The invariant refuses impossible pairings, not ordinary ones."""
        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision = await _decision_for(account_id, candidate_id)

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, decision)
            event = await decision_memory._record_decision_event(
                session, row=row, candidate=await _detached(candidate_id),
            )
            await session.commit()
            event_id = event.id

        async with get_sessionmaker()() as session:
            stored = await session.get(PurchaseDecisionEvent, event_id)
        assert stored.candidate_id == candidate_id
        assert stored.account_id == account_id
        assert await _cross_account_events() == []

    async def test_the_public_authority_loads_the_candidate_itself(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """No candidate crosses the boundary at all — only a principal and a row."""
        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision = await _decision_for(account_id, candidate_id)

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, decision)
            event = await decision_memory.record_decision_event_for_account(
                session, principal_account_id=account_id, decision_id=row.id,
            )
            await session.commit()
        assert event is not None
        assert event.candidate_id == candidate_id

    async def test_the_public_authority_refuses_a_foreign_principal(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Somebody else's decision id is simply not found.

        Not an invariant error, which would say "this exists and is wrong";
        the same ``NotFoundError`` an invented id gets, because the difference
        between "no such decision" and "not yours" is what confirms that an id
        names a real decision another customer made.
        """
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_candidate = await _candidate(a_account)
        a_decision = await _decision_for(a_account, a_candidate)

        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError) as foreign:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=b_account, decision_id=a_decision,
                )
            with pytest.raises(NotFoundError) as invented:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=b_account, decision_id=uuid.uuid4(),
                )
            await session.commit()
        assert str(foreign.value) == str(invented.value)
        assert str(a_decision) not in str(foreign.value)

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0


# ---------------------------------------------------------------------------
# 3. The public surface, and both database invariants
# ---------------------------------------------------------------------------
class TestPublicSurfaceAndInvariants:
    async def test_the_raw_event_writer_is_not_exported(self, db_clean):
        """A write helper that takes no principal must not be public.

        Asserted rather than assumed, because ``__all__`` is the only thing
        standing between "private by convention" and somebody importing it in a
        worker next quarter.
        """
        assert "record_decision_event" not in decision_memory.__all__
        assert "_record_decision_event" not in decision_memory.__all__
        assert "resolve_current_decision_for_write" not in decision_memory.__all__
        assert "record_decision_event_for_account" in decision_memory.__all__
        # Nothing exported may be a private name, and nothing exported may be
        # missing from the module.
        for name in decision_memory.__all__:
            assert not name.startswith("_"), name
            assert hasattr(decision_memory, name), name

    async def test_no_caller_outside_the_module_uses_the_private_writer(self, db_clean):
        """The audit §1 asks for, kept as a test rather than as a one-off check."""
        import pathlib

        backend = pathlib.Path(decision_memory.__file__).resolve().parents[3]
        offenders = []
        for path in (backend / "app").rglob("*.py"):
            if path.name == "decision_memory.py":
                continue
            if "_record_decision_event" in path.read_text():
                offenders.append(str(path.relative_to(backend)))
        assert offenders == [], offenders

    async def test_both_ownership_invariants_hold_after_real_use(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Account owns Candidate, and Subject owns Decision Memory about it.

        Two different relationships, checked separately because they fail
        separately: one would give the customer a record of the wrong product,
        the other a record belonging to the wrong person.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        other = await _member(app_client, token, relation="other")
        first = await _candidate(account_id, name="First Cleanser")
        second = await _candidate(account_id, name="Second Cleanser")

        for candidate_id, subject, outcome in (
            (first, None, "waiting"),
            (first, member, "bought"),
            (second, other, "skipped"),
        ):
            suffix = f"&subject_id={subject}" if subject else ""
            response = await app_client.post(
                f"/api/v2/shopping/candidates/{candidate_id}/decision"
                f"?on=2026-08-20{suffix}",
                headers=auth(token), json={"decision": outcome},
            )
            assert response.status_code == 200, response.text

        async with get_sessionmaker()() as session:
            events = await session.scalar(select(func.count(PurchaseDecisionEvent.id)))
        assert events == 3

        assert await _cross_account_events() == []
        assert await _cross_account_subjects() == []

        # And every event names the candidate its own decision row names.
        async with get_sessionmaker()() as session:
            mismatched = (await session.execute(text(
                """
                SELECT e.id::text
                FROM purchase_decision_events e
                JOIN purchase_decisions d ON d.id = e.decision_id
                WHERE d.candidate_id <> e.candidate_id
                   OR d.account_id <> e.account_id
                """
            ))).all()
        assert mismatched == []
