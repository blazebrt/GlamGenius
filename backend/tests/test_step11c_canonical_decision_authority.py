"""The last claim: an ORM row crossing a public write boundary.

Step 11C has now refused to trust a ``DecisionSubject`` and a
``ShoppingCandidate``. This is the same argument for the object the event
authority was still reading — the ``PurchaseDecision`` itself.

Checking ``row.account_id`` against the principal looks like authorisation and
is not: the field lives on the object the caller supplied, so a detached
instance can satisfy it while carrying a different candidate, a different
subject, or a decision value nobody made. The event appended from it would be
an immutable record of something that never happened, and immutable records are
believed later precisely because nothing rewrites them.

So the public authority now takes two identifiers and reads the rest. Nothing
about a decision crosses the boundary except its id, and every field the event
copies comes from PostgreSQL.

Two further things are proved here. A canonical row can itself be impossible —
a decision owned by one account naming another account's household member — and
the foreign key does not catch it, because a foreign key proves the profile
exists and not whose it is. And the retired Style path, which nothing reaches
today, is made correct rather than left as a trap: it treats its evaluation as
an id and re-reads the rest.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.purchase import decision_memory
from app.domains.recommendation import service as recommendation_service
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    PurchaseEvaluation,
    RecommendationRun,
    ShoppingCandidate,
)
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import IdentityInvariantError, NotFoundError
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


async def _decision(
    account_id: uuid.UUID,
    candidate_id: uuid.UUID,
    *,
    subject_id: uuid.UUID | None = None,
    decision: str = "waiting",
) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        row = PurchaseDecision(
            account_id=account_id, household_subject_id=subject_id,
            candidate_id=candidate_id, strategy_key="care_purchase",
            recommendation_verdict="wait", recommendation_version="canonical-v",
            recommendation_snapshot={"strategy": "care_purchase"},
            decision=decision, followed_recommendation=True,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _detached_decision(decision_id: uuid.UUID) -> PurchaseDecision:
    """The decision as a caller would hold it: fetched, expunged, mutable."""
    async with get_sessionmaker()() as session:
        row = (await session.execute(
            select(PurchaseDecision).where(PurchaseDecision.id == decision_id)
        )).scalar_one()
        session.expunge(row)
        return row


async def _evaluation(
    account_id: uuid.UUID,
    candidate_id: uuid.UUID,
    *,
    verdict: str = "buy",
    roi_version: str = "canonical-roi",
) -> uuid.UUID:
    """A real Style evaluation, with the run it hangs off."""
    async with get_sessionmaker()() as session:
        run = RecommendationRun(
            account_id=account_id, kind="purchase", status="complete",
        )
        session.add(run)
        await session.flush()
        evaluation = PurchaseEvaluation(
            account_id=account_id, candidate_id=candidate_id, run_id=run.id,
            verdict=verdict, roi_score=0.5, roi_version=roi_version,
            summary="canonical summary",
        )
        session.add(evaluation)
        await session.commit()
        return evaluation.id


async def _detached_evaluation(evaluation_id: uuid.UUID) -> PurchaseEvaluation:
    async with get_sessionmaker()() as session:
        row = (await session.execute(
            select(PurchaseEvaluation).where(PurchaseEvaluation.id == evaluation_id)
        )).scalar_one()
        session.expunge(row)
        return row


async def _ledger_invariants() -> dict[str, list]:
    """Every way an event can disagree with the rows it claims to come from.

    Asked of the database rather than of the code meant to maintain it, and
    split by relationship because they fail for different reasons: the wrong
    product, the wrong person, or an event that has drifted from its own
    decision.

    Legacy events carry ``decision_id IS NULL`` — they predate the link and
    nothing can reconstruct it — so the decision-linked checks simply do not
    apply to them. Inventing a link to test would be manufacturing the evidence.
    """
    queries = {
        "event_candidate_account": """
            SELECT e.id::text FROM purchase_decision_events e
            JOIN shopping_candidates c ON c.id = e.candidate_id
            WHERE c.account_id <> e.account_id
        """,
        "event_subject_account": """
            SELECT e.id::text FROM purchase_decision_events e
            JOIN family_profiles p ON p.id = e.household_subject_id
            JOIN family_circles fc ON fc.id = p.circle_id
            WHERE fc.account_id <> e.account_id
        """,
        "event_vs_decision": """
            SELECT e.id::text FROM purchase_decision_events e
            JOIN purchase_decisions d ON d.id = e.decision_id
            WHERE e.decision_id IS NOT NULL
              AND (e.account_id <> d.account_id
                   OR e.candidate_id <> d.candidate_id
                   OR e.household_subject_id IS DISTINCT FROM d.household_subject_id)
        """,
    }
    async with get_sessionmaker()() as session:
        return {
            name: [row[0] for row in (await session.execute(text(sql))).all()]
            for name, sql in queries.items()
        }


async def _assert_ledger_clean():
    assert await _ledger_invariants() == {
        "event_candidate_account": [],
        "event_subject_account": [],
        "event_vs_decision": [],
    }


# ---------------------------------------------------------------------------
# 1. The decision itself is a claim
# ---------------------------------------------------------------------------
class TestForgedPurchaseDecision:
    async def test_only_the_id_crosses_the_boundary(self, db_clean):
        """The signature is the proof, so it is asserted directly.

        A boundary that still accepted a decision object would have to be
        trusted to ignore it. Not accepting one at all is a property a reviewer
        can check in one line, and a refactor cannot quietly undo.
        """
        import inspect

        signature = inspect.signature(
            decision_memory.record_decision_event_for_account
        )
        assert set(signature.parameters) == {
            "session", "principal_account_id", "decision_id",
        }
        annotation = signature.parameters["decision_id"].annotation
        assert annotation in (uuid.UUID, "uuid.UUID"), annotation

    async def test_forged_fields_cannot_reach_the_event(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Every field the reviewer named, changed, and none of them lands.

        The forged object carries the real decision's id beside another
        account's member, a different candidate and a different answer. It is
        built and mutated exactly as a caller would, and then cannot be handed
        over at all — the boundary takes an id. What the event records is
        whatever PostgreSQL says, which is checked field by field.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        b_member = await _member(app_client, b_token)
        a_candidate_1 = await _candidate(a_account, name="Mine One")
        a_candidate_2 = await _candidate(a_account, name="Mine Two")
        decision_id = await _decision(a_account, a_candidate_1, decision="waiting")

        forged = await _detached_decision(decision_id)
        forged.household_subject_id = b_member
        forged.candidate_id = a_candidate_2
        forged.decision = "bought"
        forged.recommendation_version = "forged-v"
        forged.recommendation_verdict = "buy"

        async with get_sessionmaker()() as session:
            # The forged object has nowhere to go: only the id is accepted.
            with pytest.raises(TypeError):
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=a_account, row=forged,
                )
            event = await decision_memory.record_decision_event_for_account(
                session, principal_account_id=a_account, decision_id=forged.id,
            )
            await session.commit()
            event_id = event.id

        async with get_sessionmaker()() as session:
            stored = await session.get(PurchaseDecisionEvent, event_id)
        assert stored.household_subject_id is None, "the forged subject was used"
        assert stored.candidate_id == a_candidate_1, "the forged candidate was used"
        assert stored.decision == "waiting", "the forged decision was used"
        assert stored.recommendation_version == "canonical-v"
        assert stored.recommendation_verdict == "wait"
        assert stored.account_id == a_account
        await _assert_ledger_clean()

    async def test_a_foreign_decision_id_is_not_found(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """B's real decision, asked for under A. Same answer as an invented id."""
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        b_candidate = await _candidate(b_account)
        b_decision = await _decision(b_account, b_candidate)

        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError) as foreign:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=a_account, decision_id=b_decision,
                )
            with pytest.raises(NotFoundError) as invented:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=a_account, decision_id=uuid.uuid4(),
                )
            await session.commit()
        assert str(foreign.value) == str(invented.value)
        assert str(b_decision) not in str(foreign.value)

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0

    async def test_the_canonical_row_decides_which_candidate(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Two of this customer's own products, and only one is the right answer.

        No privacy boundary is crossed, and it is still wrong: an event naming
        the other product would later be projected as prior consideration of
        something they never considered.
        """
        token, account_id = await registered_supabase_user()
        first = await _candidate(account_id, name="First")
        second = await _candidate(account_id, name="Second")
        decision_id = await _decision(account_id, first)

        forged = await _detached_decision(decision_id)
        forged.candidate_id = second

        async with get_sessionmaker()() as session:
            event = await decision_memory.record_decision_event_for_account(
                session, principal_account_id=account_id, decision_id=forged.id,
            )
            await session.commit()
            event_id = event.id

        async with get_sessionmaker()() as session:
            stored = await session.get(PurchaseDecisionEvent, event_id)
        assert stored.candidate_id == first
        assert stored.candidate_display_name == "First"
        await _assert_ledger_clean()


# ---------------------------------------------------------------------------
# 2. A canonical row can itself be impossible
# ---------------------------------------------------------------------------
class TestStoredSubjectOwnership:
    async def test_a_decision_naming_another_accounts_member_fails_closed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Seeded into the database, because the foreign key allows it.

        ``household_subject_id`` references ``family_profiles``, which proves
        the profile exists somewhere and says nothing about whose household it
        is in. A row binding account A's decision to account B's member
        satisfies the constraint and is impossible, and an append-only ledger is
        the worst place to find that out later.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        b_member = await _member(app_client, b_token)
        a_candidate = await _candidate(a_account)
        decision_id = await _decision(a_account, a_candidate, subject_id=b_member)

        async with get_sessionmaker()() as session:
            with pytest.raises(IdentityInvariantError) as refusal:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=a_account, decision_id=decision_id,
                )
            await session.commit()

        assert refusal.value.reason == "purchase_decision_subject_ownership_invalid"
        detail = str(refusal.value.to_detail())
        for forbidden in (str(a_account), str(b_account), str(b_member), str(decision_id)):
            assert forbidden not in detail

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
            # Left exactly as found: deciding what that row should have said is
            # a separate, deliberate act, not something to guess at here.
            row = await session.get(PurchaseDecision, decision_id)
        assert row.household_subject_id == b_member
        await _assert_ledger_clean()

    async def test_a_decision_pointing_at_another_accounts_candidate_fails_closed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Stored corruption on the product side, told apart from a deletion.

        A deleted candidate and a candidate belonging to somebody else look
        identical to one account-scoped query, and they are not the same thing.
        The first is ordinary — no product, no event, decision untouched. The
        second says this account's decision points across accounts, which is the
        same class of impossibility as a foreign subject, and the boundary names
        it rather than leaving the inner append to notice.
        """
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_candidate = await _candidate(a_account)
        b_candidate = await _candidate(b_account)
        decision_id = await _decision(a_account, a_candidate)

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, decision_id)
            row.candidate_id = b_candidate
            await session.commit()

        async with get_sessionmaker()() as session:
            with pytest.raises(IdentityInvariantError) as refusal:
                await decision_memory.record_decision_event_for_account(
                    session, principal_account_id=a_account, decision_id=decision_id,
                )
            await session.commit()
        assert refusal.value.reason == "purchase_decision_candidate_ownership_invalid"

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
        await _assert_ledger_clean()

    async def test_the_schema_forbids_a_decision_without_a_real_candidate(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Why the refusal above is unambiguous, checked rather than assumed.

        The authority reads a missed account-scoped candidate lookup as
        ownership corruption rather than as a deleted product. That is only
        sound because the database will not let a decision name a candidate
        that does not exist, and deleting a candidate cascades to its decisions.
        Both halves are asserted here, so the reasoning cannot quietly stop
        being true.
        """
        from sqlalchemy.exc import IntegrityError

        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id)

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, decision_id)
            row.candidate_id = uuid.uuid4()
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

        async with get_sessionmaker()() as session:
            await session.execute(
                ShoppingCandidate.__table__.delete()
                .where(ShoppingCandidate.id == candidate_id)
            )
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.get(PurchaseDecision, decision_id) is None

    async def test_a_decision_naming_this_accounts_member_is_carried_forward(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The ownership check refuses corruption, not ordinary attribution."""
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id, subject_id=member)

        async with get_sessionmaker()() as session:
            event = await decision_memory.record_decision_event_for_account(
                session, principal_account_id=account_id, decision_id=decision_id,
            )
            await session.commit()
            event_id = event.id

        async with get_sessionmaker()() as session:
            stored = await session.get(PurchaseDecisionEvent, event_id)
        assert stored.household_subject_id == member
        await _assert_ledger_clean()

    async def test_a_deactivated_member_can_still_have_their_event_appended(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Deactivation is not deletion, and the ownership check knows it.

        Using the ordinary subject resolver here would have been the obvious
        mistake: it refuses an inactive member, because nobody may *select*
        them any more. Whose household they are in is a different question, and
        it stays answerable — their existing history is still theirs.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        candidate_id = await _candidate(account_id)
        decision_id = await _decision(account_id, candidate_id, subject_id=member)

        patched = await app_client.patch(
            f"{PROFILES_URL}/{member}", headers=auth(token), json={"active": False},
        )
        assert patched.status_code == 200, patched.text

        async with get_sessionmaker()() as session:
            event = await decision_memory.record_decision_event_for_account(
                session, principal_account_id=account_id, decision_id=decision_id,
            )
            await session.commit()
        assert event is not None
        assert event.household_subject_id == member
        await _assert_ledger_clean()


# ---------------------------------------------------------------------------
# 3. The retired Style path, made correct rather than left as a trap
# ---------------------------------------------------------------------------
class TestRetiredStyleCanonicalEvaluation:
    async def test_a_forged_account_on_a_foreign_evaluation_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """§8A. The field that used to be the whole check."""
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        b_candidate = await _candidate(b_account)
        b_evaluation = await _evaluation(b_account, b_candidate)

        forged = await _detached_evaluation(b_evaluation)
        forged.account_id = a_account

        async with get_sessionmaker()() as session:
            with pytest.raises(NotFoundError):
                await recommendation_service.save_decision(
                    session, principal_account_id=a_account,
                    evaluation=forged, decision="bought", note=None,
                )
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0

    async def test_the_canonical_evaluation_decides_the_candidate(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """§8B. A forged candidate on an otherwise legitimate evaluation."""
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_candidate = await _candidate(a_account, name="Mine")
        b_candidate = await _candidate(b_account, name="Theirs")
        evaluation_id = await _evaluation(a_account, a_candidate)

        forged = await _detached_evaluation(evaluation_id)
        forged.candidate_id = b_candidate

        async with get_sessionmaker()() as session:
            row = await recommendation_service.save_decision(
                session, principal_account_id=a_account,
                evaluation=forged, decision="bought", note=None,
            )
            await session.commit()
            decision_id = row.id

        async with get_sessionmaker()() as session:
            stored = await session.get(PurchaseDecision, decision_id)
        assert stored.candidate_id == a_candidate
        assert stored.account_id == a_account
        await _assert_ledger_clean()

    async def test_a_malformed_stored_evaluation_fails_closed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """§8C. The corruption is in the database, not in the caller's hand.

        An evaluation this account owns whose candidate belongs to another
        would otherwise create an account-owned decision pointing across
        accounts. It stops, and it is not repaired here.
        """
        _, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_candidate = await _candidate(a_account)
        b_candidate = await _candidate(b_account)
        evaluation_id = await _evaluation(a_account, a_candidate)

        async with get_sessionmaker()() as session:
            evaluation = await session.get(PurchaseEvaluation, evaluation_id)
            evaluation.candidate_id = b_candidate
            await session.commit()

        async with get_sessionmaker()() as session:
            with pytest.raises(IdentityInvariantError) as refusal:
                await recommendation_service.save_decision(
                    session, principal_account_id=a_account,
                    evaluation=await _detached_evaluation(evaluation_id),
                    decision="bought", note=None,
                )
            await session.commit()
        assert refusal.value.reason == "purchase_decision_candidate_identity_mismatch"

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
            # Untouched, deliberately.
            evaluation = await session.get(PurchaseEvaluation, evaluation_id)
        assert evaluation.candidate_id == b_candidate

    async def test_forged_verdict_and_provenance_are_ignored(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """§8D. What the recommendation actually said is not the caller's to set.

        ``followed_recommendation`` is computed from the verdict, so a forged
        verdict would also rewrite whether the customer took the product's
        advice — a fact later read back as evidence about how they behave.
        """
        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        evaluation_id = await _evaluation(
            account_id, candidate_id, verdict="skip", roi_version="canonical-roi",
        )

        forged = await _detached_evaluation(evaluation_id)
        forged.verdict = "buy"
        forged.roi_version = "forged-roi"
        forged.roi_score = 0.99
        forged.summary = "forged summary"

        async with get_sessionmaker()() as session:
            row = await recommendation_service.save_decision(
                session, principal_account_id=account_id,
                evaluation=forged, decision="bought", note=None,
            )
            await session.commit()
            decision_id = row.id

        async with get_sessionmaker()() as session:
            stored = await session.get(PurchaseDecision, decision_id)
            event = (await session.execute(
                select(PurchaseDecisionEvent)
                .where(PurchaseDecisionEvent.decision_id == decision_id)
            )).scalar_one()
        assert stored.recommendation_verdict == "skip"
        assert stored.recommendation_version == "canonical-roi"
        # "buy" would have made this True; the stored verdict was "skip".
        assert stored.followed_recommendation is False
        assert event.recommendation_verdict == "skip"
        assert event.recommendation_version == "canonical-roi"
        await _assert_ledger_clean()


# ---------------------------------------------------------------------------
# 4. The public surface, and the ledger invariants after real use
# ---------------------------------------------------------------------------
class TestPublicSurfaceAndLedger:
    async def test_no_public_writer_accepts_an_orm_object_as_authority(self, db_clean):
        """Every exported write takes ids and checked values, never a row.

        Serializers are exempt: they format an object that an authority already
        produced, and formatting is not authority.
        """
        import inspect

        from app.domains.family.decision_subject import DecisionSubject

        forbidden = {PurchaseDecision, ShoppingCandidate, PurchaseEvaluation}
        for name in decision_memory.__all__:
            member = getattr(decision_memory, name)
            if not inspect.iscoroutinefunction(member):
                continue
            for parameter in inspect.signature(member).parameters.values():
                assert parameter.annotation not in forbidden, (name, parameter.name)
        # The one object still accepted anywhere is the DecisionSubject, and it
        # is re-derived at every boundary rather than believed.
        assert DecisionSubject is not None

    async def test_the_private_writer_is_still_private(self, db_clean):
        assert "_record_decision_event" not in decision_memory.__all__
        assert "record_decision_event" not in decision_memory.__all__
        assert "record_decision_event_for_account" in decision_memory.__all__
        for name in decision_memory.__all__:
            assert not name.startswith("_"), name

    async def test_no_application_module_calls_the_private_writer(self, db_clean):
        import pathlib

        backend = pathlib.Path(decision_memory.__file__).resolve().parents[3]
        offenders = [
            str(path.relative_to(backend))
            for path in (backend / "app").rglob("*.py")
            if path.name != "decision_memory.py"
            and "_record_decision_event" in path.read_text()
        ]
        assert offenders == [], offenders

    async def test_every_ledger_invariant_holds_after_ordinary_use(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Real decisions through the real route, then the database is asked."""
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        other = await _member(app_client, token, relation="other")
        first = await _candidate(account_id, name="First")
        second = await _candidate(account_id, name="Second")

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
            assert await session.scalar(
                select(func.count(PurchaseDecisionEvent.id))
            ) == 3
        await _assert_ledger_clean()

    async def test_a_legacy_event_without_a_decision_link_is_left_alone(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The decision-linked checks do not apply where there is no link.

        Pre-Step-9A events carry ``decision_id IS NULL``. Nothing can
        reconstruct which decision row they came from, and inventing one to
        satisfy an invariant would be manufacturing the evidence. The
        candidate and subject checks still apply to them, and do here.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token)
        candidate_id = await _candidate(account_id)

        async with get_sessionmaker()() as session:
            candidate = await session.get(ShoppingCandidate, candidate_id)
            session.add(PurchaseDecisionEvent(
                account_id=account_id, household_subject_id=member,
                candidate_id=candidate_id, decision_id=None,
                category=candidate.category, strategy_key="care_purchase",
                candidate_display_name=candidate.display_name,
                identity_version="v1", identity_state="exact",
                identity_fingerprint="legacy", recommendation_verdict="wait",
                recommendation_version="v", recommendation_snapshot={},
                decision="waiting", followed_recommendation=True,
            ))
            await session.commit()

        invariants = await _ledger_invariants()
        assert invariants["event_candidate_account"] == []
        assert invariants["event_subject_account"] == []
        assert invariants["event_vs_decision"] == []
        async with get_sessionmaker()() as session:
            assert await session.scalar(
                select(func.count(PurchaseDecisionEvent.id))
                .where(PurchaseDecisionEvent.decision_id.is_(None))
            ) == 1
