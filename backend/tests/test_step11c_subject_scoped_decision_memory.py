"""Step 11C — one account, several people, and whose decision was whose.

Decision Memory arrived before households did, so for most of this product's
life ``account_id`` was a complete answer to "who decided this". A household
does not make those old rows wrong; it makes some of them *unanswerable*. The
decision was real and the record is truthful — what is no longer knowable is
which human it was for.

Two things are being proven here, and the second is the harder one.

That a household member's history is their own: another member's BUY must never
show up in this member's Purchase Guard, their count, or their history page.

And that the old rows are read honestly. A subject-less decision from before the
household existed is safely the account holder's. One from after it could have
been about anybody in it, so it is shown to nobody and the customer is told
their history is incomplete rather than handed a confident half of it.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from app.domains.family.decision_subject import canonical_decision_subject
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import (
    AGE_BAND_ADULT,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
    SubjectNotFound,
    account_holder_subject,
)
from app.domains.privacy import export as privacy_export
from app.domains.product.models import LabelSnapshot, ScanDecisionEvent, ScanEvent
from app.domains.recommendation.models import PurchaseDecision, PurchaseDecisionEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate

pytestmark = pytest.mark.asyncio

PROFILES_URL = "/api/v2/family-circle/profiles"
CIRCLE_URL = "/api/v2/family-circle"
HISTORY_URL = "/api/v2/shopping/decision-history"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _member(client, token, *, relation="adult") -> str:
    response = await client.post(
        PROFILES_URL, headers=auth(token), json={"relation": relation},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _self_id(client, token) -> str:
    circle = (await client.get(CIRCLE_URL, headers=auth(token))).json()
    return next(p["id"] for p in circle["profiles"] if p["relation"] == "self")


async def _candidate(account_id: uuid.UUID) -> uuid.UUID:
    """One exact-identity candidate. Account-owned, never cloned per subject."""
    candidate_id = await _seed_db_candidate(account_id)
    from app.domains.recommendation.models import ShoppingCandidate

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


def _subject_query(subject_id: str | None) -> str:
    return f"&subject_id={subject_id}" if subject_id else ""


async def _decide(client, token, candidate_id, decision, *, subject_id=None):
    return await client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision"
        f"?on=2026-08-20{_subject_query(subject_id)}",
        headers=auth(token), json={"decision": decision},
    )


async def _guard(client, token, candidate_id, *, subject_id=None):
    suffix = f"?subject_id={subject_id}" if subject_id else ""
    return await client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard{suffix}",
        headers=auth(token),
    )


async def _history(client, token, *, subject_id=None, limit=20, before=None):
    suffix = _subject_query(subject_id) + (f"&before={before}" if before else "")
    return await client.get(
        f"{HISTORY_URL}?limit={limit}{suffix}", headers=auth(token),
    )


async def _circle_created_at(account_id):
    async with get_sessionmaker()() as session:
        return await session.scalar(
            select(FamilyCircle.created_at).where(FamilyCircle.account_id == account_id)
        )


async def _self_subject(session, account_id):
    return await canonical_decision_subject(
        session,
        principal_account_id=account_id,
        subject=account_holder_subject(account_id),
    )


async def _snapshot(barcode: str = "8901030000011") -> LabelSnapshot:
    """A label snapshot with real scan provenance, for scan decision memory."""
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


async def _scan_decide(client, token, snapshot, decision, key, *, subject_id=None, note=None):
    suffix = f"?subject_id={subject_id}" if subject_id else ""
    return await client.post(
        f"/api/v2/scan/verdict/{snapshot.barcode}/memory{suffix}",
        headers=auth(token),
        json={
            "decision": decision,
            "label_snapshot_id": str(snapshot.id),
            "label_version": snapshot.version_number,
            "content_fingerprint": snapshot.content_fingerprint,
            "idempotency_key": key,
            **({"note": note} if note is not None else {}),
        },
    )


async def _scan_memory(client, token, snapshot, *, subject_id=None):
    suffix = f"?subject_id={subject_id}" if subject_id else ""
    return await client.get(
        f"/api/v2/scan/verdict/{snapshot.barcode}/memory{suffix}", headers=auth(token),
    )


# ---------------------------------------------------------------------------
# 1. One candidate, several people, independent answers
# ---------------------------------------------------------------------------
class TestOneCandidateManySubjects:
    async def test_three_people_decide_the_same_candidate_independently(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The shape of the whole slice.

        A shopping candidate is one thing the account is considering, and it is
        not cloned per person. Three humans can hold three different answers
        about it at once, and none of them overwrites another.
        """
        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)

        assert (await _decide(app_client, token, candidate_id, "waiting")).status_code == 200
        assert (await _decide(app_client, token, candidate_id, "bought", subject_id=member_a)).status_code == 200
        assert (await _decide(app_client, token, candidate_id, "skipped", subject_id=member_b)).status_code == 200

        async with get_sessionmaker()() as session:
            # One candidate, three current rows, three subjects.
            from app.domains.recommendation.models import ShoppingCandidate

            assert await session.scalar(select(func.count(ShoppingCandidate.id))) == 1
            rows = (await session.execute(
                select(PurchaseDecision.household_subject_id, PurchaseDecision.decision)
                .where(PurchaseDecision.candidate_id == candidate_id)
            )).all()
        by_subject = {str(subject): decision for subject, decision in rows}
        assert by_subject == {
            self_id: "waiting", member_a: "bought", member_b: "skipped",
        }

    async def test_reading_one_subject_never_returns_another(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await _decide(app_client, token, candidate_id, "waiting")
        await _decide(app_client, token, candidate_id, "bought", subject_id=member)

        mine = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
        )
        theirs = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/decision?subject_id={member}",
            headers=auth(token),
        )
        assert mine.json()["decision"]["decision"] == "waiting"
        assert theirs.json()["decision"]["decision"] == "bought"
        assert mine.json()["subject"]["is_account_holder"] is True
        assert theirs.json()["subject"]["household_subject_id"] == member
        assert theirs.json()["subject"]["is_account_holder"] is False


# ---------------------------------------------------------------------------
# 2. Purchase Guard sees one human
# ---------------------------------------------------------------------------
class TestPurchaseGuardIsolation:
    async def test_each_subject_sees_only_their_own_prior_consideration(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The sentence the guard exists to say is about a person.

        "You looked at this before and skipped it" told to the wrong member of a
        household is both wrong and a disclosure of what somebody else decided.
        """
        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")

        # The same exact product identity, considered three times over — once by
        # each human, on their own candidate rows.
        first = await _candidate(account_id)
        second = await _candidate(account_id)
        await _decide(app_client, token, first, "waiting")
        await _decide(app_client, token, second, "bought", subject_id=member_a)

        third = await _candidate(account_id)
        mine = (await _guard(app_client, token, third)).json()
        theirs = (await _guard(app_client, token, third, subject_id=member_a)).json()
        other = (await _guard(app_client, token, third, subject_id=member_b)).json()

        assert mine["prior_consideration_count"] == 1
        assert mine["most_recent"]["decision"] == "waiting"
        assert mine["guard_state"] == "exact_prior_waiting"

        assert theirs["prior_consideration_count"] == 1
        assert theirs["most_recent"]["decision"] == "bought"
        assert theirs["guard_state"] == "exact_prior_bought"

        # Member B has decided nothing, and neither of the others leaks in.
        assert other["prior_consideration_count"] == 0
        assert other["most_recent"] is None
        assert other["guard_state"] == "no_step9a_prior_event"

        for payload, subject in ((mine, None), (theirs, member_a), (other, member_b)):
            assert payload["subject"]["household_subject_id"] == (
                subject if subject else payload["subject"]["household_subject_id"]
            )

    async def test_another_members_untracked_row_does_not_spoil_this_guard(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The account-wide incompleteness check, corrected.

        It used to ask whether *the account* had a current row without an
        event. One member's untracked row would then have told every other
        member their own history was incomplete, permanently.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)

        # A current row for the member with no event behind it, which is what a
        # pre-Step-9 row looks like.
        async with get_sessionmaker()() as session:
            session.add(PurchaseDecision(
                account_id=account_id, household_subject_id=uuid.UUID(member),
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            ))
            await session.commit()

        mine = (await _guard(app_client, token, candidate_id)).json()
        assert mine["guard_state"] == "no_step9a_prior_event"
        assert mine["history_coverage"]["complete_for_subject"] is True
        assert mine["subject"]["household_subject_id"] == self_id

        theirs = (await _guard(app_client, token, candidate_id, subject_id=member)).json()
        assert theirs["guard_state"] == "historical_context_incomplete"


# ---------------------------------------------------------------------------
# 3. The legacy boundary
# ---------------------------------------------------------------------------
class TestLegacyAttribution:
    """``FamilyCircle.created_at``, per account, and strictly ``<``.

    Before that moment the account was one person. From it on, a subject-less
    row could have been about anybody, and "probably the account holder" is not
    a standard this should meet with somebody's purchase history.
    """

    async def _legacy_event(self, account_id, candidate_id, *, offset: timedelta):
        """A subject-less event placed relative to the household's creation."""
        circle_created = await _circle_created_at(account_id)
        async with get_sessionmaker()() as session:
            event = PurchaseDecisionEvent(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, decision_id=None,
                category="beauty", strategy_key="care_purchase",
                candidate_display_name="Gentle Cleanser",
                identity_version="v1", identity_state="exact",
                identity_fingerprint="legacy-fingerprint",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created + offset
            await session.commit()
            return event.id

    async def test_an_event_from_before_the_household_is_the_account_holders(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        event_id = await self._legacy_event(
            account_id, candidate_id, offset=timedelta(seconds=-60),
        )

        mine = (await _history(app_client, token)).json()
        assert [item["id"] for item in mine["items"]] == [str(event_id)]
        assert mine["history_coverage"]["legacy_self_events_included"] is True
        assert mine["history_coverage"]["unattributed_legacy_events_present"] is False
        assert mine["history_coverage"]["complete_for_subject"] is True

        # A member's history begins when they were named. Nothing older is
        # theirs, because there was nothing to distinguish them from.
        theirs = (await _history(app_client, token, subject_id=member)).json()
        assert theirs["items"] == []

    async def test_an_event_from_after_the_household_belongs_to_nobody(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Nobody's to see, and nobody's to be told their history is complete.

        This used to assert that a member's coverage was unaffected, on the
        reasoning that their history begins when they were named. That is right
        about rows from *before* the household and wrong about rows from after
        it: a subject-less row written once several people shared the account
        could have been about any of them. "We do not know whose this is" is not
        evidence that it was not this member's.

        So the row stays out of everybody's history — it is never shown, never
        counted, never named — and everybody who asks is told their answer is
        incomplete rather than handed a confident empty page.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        other = await _member(app_client, token, relation="other")
        candidate_id = await _candidate(account_id)
        await self._legacy_event(account_id, candidate_id, offset=timedelta(seconds=60))

        for subject in (None, member, other):
            page = (await _history(app_client, token, subject_id=subject)).json()
            # Said, not implied. "You have no earlier decisions" and "we cannot
            # tell whose some of the earlier decisions were" are different
            # sentences, and only one of them is true here.
            assert page["items"] == [], subject
            coverage = page["history_coverage"]
            assert coverage["unattributed_legacy_events_present"] is True, subject
            assert coverage["complete_for_subject"] is False, subject

        # Only the account holder is ever handed pre-household legacy events,
        # so that flag stays theirs alone.
        assert (await _history(app_client, token)).json()[
            "history_coverage"]["legacy_self_events_included"] is True
        assert (await _history(app_client, token, subject_id=member)).json()[
            "history_coverage"]["legacy_self_events_included"] is False

    async def test_a_pre_household_event_leaves_a_members_coverage_complete(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The other side of the boundary, and the reason it is a boundary.

        A subject-less row from before the household is not ambiguous at all —
        there was one person then, and it was them. It is the account holder's
        outright, and its existence says nothing about whether a member's own
        history is complete. Treating every legacy row as doubt would tell every
        member, forever, that their history might be missing something, which is
        as unhelpful as the confident lie it replaced.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await self._legacy_event(
            account_id, candidate_id, offset=timedelta(seconds=-60),
        )

        theirs = (await _history(app_client, token, subject_id=member)).json()
        assert theirs["items"] == []
        assert theirs["history_coverage"]["unattributed_legacy_events_present"] is False
        assert theirs["history_coverage"]["complete_for_subject"] is True

        mine = (await _history(app_client, token)).json()
        assert len(mine["items"]) == 1
        assert mine["history_coverage"]["complete_for_subject"] is True

    async def test_with_no_household_a_subject_less_event_is_simply_mine(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """No household, no ambiguity. Nothing becomes unattributed by itself."""
        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        async with get_sessionmaker()() as session:
            event = PurchaseDecisionEvent(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, decision_id=None,
                category="beauty", strategy_key="care_purchase",
                candidate_display_name="Gentle Cleanser",
                identity_version="v1", identity_state="exact",
                identity_fingerprint="legacy-fingerprint",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add(event)
            await session.commit()
            event_id = event.id

        mine = (await _history(app_client, token)).json()
        assert [item["id"] for item in mine["items"]] == [str(event_id)]
        assert mine["history_coverage"]["unattributed_legacy_events_present"] is False
        assert mine["history_coverage"]["complete_for_subject"] is True

    async def test_the_equality_boundary_is_ambiguous(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Exactly equal is not "before".

        An event written in the same instant the circle was created cannot be
        shown to predate it, and a comparison that let it through would be
        deciding a coin flip in the account holder's favour.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await self._legacy_event(account_id, candidate_id, offset=timedelta(0))

        mine = (await _history(app_client, token)).json()
        assert mine["items"] == []
        assert mine["history_coverage"]["complete_for_subject"] is False

    async def test_a_member_never_adopts_a_decision_from_before_they_existed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """"Before the household" means before *any* member, not before me.

        The safe-legacy rule is easy to state one guard short: a subject-less
        row older than the circle is unambiguous, so claim it. For the account
        holder that is right. For a named member it is exactly backwards — the
        row predates the household, so it predates them being a distinguishable
        person, and it is the account holder's by definition.

        Reading is already safe because a member's filter matches only rows
        carrying their id. Writing is where it bites: adoption looks at the same
        legacy row and, without the account-holder check, hands it over. The
        member would take the account holder's decision, keep its id and its
        events, and the account holder's own history would lose it.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        circle_created = await _circle_created_at(account_id)

        async with get_sessionmaker()() as session:
            legacy = PurchaseDecision(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add(legacy)
            await session.flush()
            legacy.updated_at = circle_created - timedelta(seconds=60)
            await session.commit()
            legacy_id = legacy.id

        assert (await _decide(
            app_client, token, candidate_id, "bought", subject_id=member,
        )).status_code == 200

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(PurchaseDecision).where(PurchaseDecision.candidate_id == candidate_id)
            )).scalars().all()
        by_id = {row.id: row for row in rows}
        assert len(rows) == 2, "the member adopted a row that was never theirs"
        assert by_id[legacy_id].household_subject_id is None
        assert by_id[legacy_id].decision == "waiting"
        theirs = next(row for row in rows if row.id != legacy_id)
        assert str(theirs.household_subject_id) == member

        # And the account holder can still adopt it themselves afterwards,
        # which is the proof it was left genuinely untouched rather than merely
        # not overwritten.
        assert (await _decide(app_client, token, candidate_id, "skipped")).status_code == 200
        async with get_sessionmaker()() as session:
            adopted = await session.get(PurchaseDecision, legacy_id)
            await session.refresh(adopted)
        assert str(adopted.household_subject_id) == await _self_id(app_client, token)

    async def test_a_member_cannot_retry_into_a_pre_household_scan_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The same rule on the scan side, where it decides an idempotent retry.

        A retry that matched a pre-household event for a member would hand that
        member the account holder's stored decision and note, and record nothing
        of their own.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        snapshot = await _snapshot()
        circle_created = await _circle_created_at(account_id)

        async with get_sessionmaker()() as session:
            event = ScanDecisionEvent(
                account_id=account_id, household_subject_id=None,
                barcode=snapshot.barcode, label_snapshot_id=snapshot.id,
                label_version=snapshot.version_number,
                content_fingerprint=snapshot.content_fingerprint,
                decision="WAIT", idempotency_key="shared-key",
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created - timedelta(seconds=60)
            await session.commit()
            legacy_id = event.id

        retry = await _scan_decide(
            app_client, token, snapshot, "WAIT", "shared-key", subject_id=member,
        )
        assert retry.status_code == 409, retry.text

        async with get_sessionmaker()() as session:
            assert await session.scalar(
                select(func.count(ScanDecisionEvent.id))
            ) == 1
            assert await session.scalar(
                select(ScanDecisionEvent.household_subject_id)
                .where(ScanDecisionEvent.id == legacy_id)
            ) is None

    async def test_with_no_household_nothing_is_ambiguous_by_construction(
        self, db_clean, registered_supabase_user,
    ):
        """The guard behind the guard, asserted directly because nothing reaches it.

        Callers check ``has_household`` before asking which rows are ambiguous,
        so :func:`ambiguous_legacy_filter` is never reached with no boundary
        today. It still has to be right: it is a pure function that builds an
        SQL predicate, and one that matched every subject-less row when no
        household existed would mark an ordinary single-person account's entire
        history unattributable the moment a caller forgot the outer check.

        Compiled rather than described, so the assertion is about the SQL that
        would actually run.
        """
        from app.domains.family.decision_subject import ambiguous_legacy_filter

        _, account_id = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            lone = await _self_subject(session, account_id)
        assert lone.circle_created_at is None

        predicate = ambiguous_legacy_filter(PurchaseDecisionEvent, lone)
        compiled = str(predicate.compile(compile_kwargs={"literal_binds": True}))
        assert compiled.lower() == "false"
        assert "household_subject_id" not in compiled

    async def test_a_row_with_no_timestamp_is_never_claimable(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The one branch the schema cannot produce, asserted directly.

        ``created_at`` and ``updated_at`` are both NOT NULL, so no row reaching
        the boundary rule through a route can carry ``None``. The branch is
        still reachable by any future caller that passes an unflushed object or
        a column this step does not know about, and "no timestamp" has to mean
        "cannot be shown to predate the household" rather than defaulting to
        yes. Asserted here because a route cannot reach it.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")

        async with get_sessionmaker()() as session:
            holder = await _self_subject(session, account_id)
        assert holder.is_account_holder
        assert holder.circle_created_at is not None
        assert holder.legacy_row_is_mine(None) is False

        # And with no household at all there is nothing to be ambiguous against,
        # so the same account holder owns their subject-less history outright.
        token2, account2 = await registered_supabase_user()
        async with get_sessionmaker()() as session:
            lone = await _self_subject(session, account2)
        assert lone.circle_created_at is None
        assert lone.legacy_row_is_mine(None) is True

    async def _matching_legacy_event(self, account_id, candidate_id, *, offset):
        """An ambiguous event the guard cannot dismiss on identity grounds.

        The guard only looks at events for the same category, strategy and exact
        identity fingerprint, so an ambiguous row with an unrelated fingerprint
        proves nothing about coverage — it was never a candidate for this
        answer. This one carries the real identity, which is the case §3B is
        about.
        """
        from app.domains.purchase.contract import resolve_purchase_strategy
        from app.domains.purchase.identity import identity_for_candidate
        from app.domains.recommendation.models import ShoppingCandidate

        circle_created = await _circle_created_at(account_id)
        async with get_sessionmaker()() as session:
            candidate = await session.get(ShoppingCandidate, candidate_id)
            identity = identity_for_candidate(candidate)
            assert identity["state"] == "exact", identity
            event = PurchaseDecisionEvent(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, decision_id=None,
                category=candidate.category,
                strategy_key=resolve_purchase_strategy(candidate.category).key,
                candidate_display_name=candidate.display_name,
                identity_version=identity["version"],
                identity_state=identity["state"],
                identity_fingerprint=identity["fingerprint"],
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created + offset
            await session.commit()
            return event.id

    async def test_the_guard_never_counts_an_ambiguous_event(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Uncounted and unnamed for everybody, and admitted to everybody.

        A guard that answered ``no_step9a_prior_event`` with complete coverage
        would be telling this person, confidently, that nobody has considered
        this product — while holding a decision about this exact product that
        might have been theirs.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await self._matching_legacy_event(
            account_id, candidate_id, offset=timedelta(seconds=30),
        )

        for subject in (None, member):
            guard = (await _guard(app_client, token, candidate_id, subject_id=subject)).json()
            assert guard["prior_consideration_count"] == 0, subject
            assert guard["most_recent"] is None, subject
            assert guard["guard_state"] == "historical_context_incomplete", subject
            coverage = guard["history_coverage"]
            assert coverage["unattributed_legacy_events_present"] is True, subject
            assert coverage["complete_for_subject"] is False, subject

    async def test_a_member_with_real_history_keeps_it_and_is_told_of_the_doubt(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Known history is still returned exactly. Only the coverage changes.

        The correction is about what the product admits it cannot answer, not
        about hiding what it can. A member who has decided about this product
        still gets their own exact state and their own count; the ambiguous row
        contributes nothing to either, and is acknowledged only as doubt.
        """
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        assert (await _decide(
            app_client, token, candidate_id, "bought", subject_id=member,
        )).status_code == 200
        await self._matching_legacy_event(
            account_id, candidate_id, offset=timedelta(seconds=30),
        )

        guard = (await _guard(app_client, token, candidate_id, subject_id=member)).json()
        assert guard["guard_state"] == "exact_prior_bought"
        assert guard["prior_consideration_count"] == 1
        assert guard["most_recent"]["decision"] == "bought"
        assert guard["most_recent"]["household_subject_id"] == member
        assert guard["history_coverage"]["unattributed_legacy_events_present"] is True
        assert guard["history_coverage"]["complete_for_subject"] is False

    async def test_a_members_scan_memory_admits_an_ambiguous_label_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The same rule on the scan side, for the exact scanned version."""
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        other = await _member(app_client, token, relation="other")
        snapshot = await _snapshot()
        circle_created = await _circle_created_at(account_id)

        async with get_sessionmaker()() as session:
            event = ScanDecisionEvent(
                account_id=account_id, household_subject_id=None,
                barcode=snapshot.barcode, label_snapshot_id=snapshot.id,
                label_version=snapshot.version_number,
                content_fingerprint=snapshot.content_fingerprint,
                decision="WAIT", idempotency_key="ambiguous-scan",
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created + timedelta(seconds=30)
            await session.commit()

        for subject in (None, member, other):
            memory = (await _scan_memory(
                app_client, token, snapshot, subject_id=subject,
            )).json()
            assert memory["decision"] is None, subject
            assert memory["history"] == [], subject
            coverage = memory["history_coverage"]
            assert coverage["unattributed_legacy_events_present"] is True, subject
            assert coverage["complete_for_subject"] is False, subject

    async def test_a_pre_household_scan_decision_leaves_members_complete(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        snapshot = await _snapshot()
        circle_created = await _circle_created_at(account_id)

        async with get_sessionmaker()() as session:
            event = ScanDecisionEvent(
                account_id=account_id, household_subject_id=None,
                barcode=snapshot.barcode, label_snapshot_id=snapshot.id,
                label_version=snapshot.version_number,
                content_fingerprint=snapshot.content_fingerprint,
                decision="WAIT", idempotency_key="safe-scan",
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created - timedelta(seconds=60)
            await session.commit()

        theirs = (await _scan_memory(
            app_client, token, snapshot, subject_id=member,
        )).json()
        assert theirs["decision"] is None
        assert theirs["history_coverage"]["unattributed_legacy_events_present"] is False
        assert theirs["history_coverage"]["complete_for_subject"] is True

        # And it is positively the account holder's, not merely nobody's.
        mine = (await _scan_memory(app_client, token, snapshot)).json()
        assert mine["decision"]["decision"] == "WAIT"
        assert mine["history_coverage"]["complete_for_subject"] is True


# ---------------------------------------------------------------------------
# 4. Lazy adoption of a current row, only where it is safe
# ---------------------------------------------------------------------------
class TestSafeAdoption:
    async def _legacy_current(self, account_id, candidate_id, *, offset: timedelta):
        circle_created = await _circle_created_at(account_id)
        async with get_sessionmaker()() as session:
            row = PurchaseDecision(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add(row)
            await session.flush()
            row.updated_at = circle_created + offset
            await session.commit()
            return row.id

    async def test_a_safe_legacy_row_is_adopted_in_place(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """One column changes. The row keeps its identity and its history."""
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)
        row_id = await self._legacy_current(
            account_id, candidate_id, offset=timedelta(seconds=-60),
        )

        assert (await _decide(app_client, token, candidate_id, "bought")).status_code == 200

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(PurchaseDecision).where(PurchaseDecision.candidate_id == candidate_id)
            )).scalars().all()
        assert len(rows) == 1, "adoption must not clone the current row"
        assert rows[0].id == row_id
        assert str(rows[0].household_subject_id) == self_id
        assert rows[0].decision == "bought"

    async def test_an_ambiguous_legacy_row_is_left_alone(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Coexistence is the honest outcome, not a conflict.

        The old row cannot be claimed, so it stays unattributed, and the account
        holder gets a new row of their own beside it.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)
        legacy_id = await self._legacy_current(
            account_id, candidate_id, offset=timedelta(seconds=60),
        )

        assert (await _decide(app_client, token, candidate_id, "bought")).status_code == 200

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(PurchaseDecision).where(PurchaseDecision.candidate_id == candidate_id)
            )).scalars().all()
        by_id = {row.id: row for row in rows}
        assert len(rows) == 2
        assert by_id[legacy_id].household_subject_id is None
        assert by_id[legacy_id].decision == "waiting"
        mine = next(r for r in rows if r.id != legacy_id)
        assert str(mine.household_subject_id) == self_id
        assert mine.decision == "bought"

    async def test_a_current_row_at_the_exact_boundary_is_not_adopted(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Equality again, on the path that decides adoption rather than reading.

        The boundary rule exists in more than one place — an SQL predicate for
        reads, a Python comparison for adoption and retries — and each has to be
        strict on its own. A read that correctly hid an equal-instant row while
        adoption claimed it would be the worst of both: the customer would not
        see it, and it would silently become theirs anyway.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        legacy_id = await self._legacy_current(
            account_id, candidate_id, offset=timedelta(0),
        )

        assert (await _decide(app_client, token, candidate_id, "bought")).status_code == 200

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(PurchaseDecision).where(PurchaseDecision.candidate_id == candidate_id)
            )).scalars().all()
        by_id = {row.id: row for row in rows}
        assert len(rows) == 2, "an equal-instant row was adopted"
        assert by_id[legacy_id].household_subject_id is None
        assert by_id[legacy_id].decision == "waiting"

    async def test_a_scan_retry_at_the_exact_boundary_conflicts(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """And once more where the comparison decides an idempotent retry."""
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        snapshot = await _snapshot()
        circle_created = await _circle_created_at(account_id)

        async with get_sessionmaker()() as session:
            event = ScanDecisionEvent(
                account_id=account_id, household_subject_id=None,
                barcode=snapshot.barcode, label_snapshot_id=snapshot.id,
                label_version=snapshot.version_number,
                content_fingerprint=snapshot.content_fingerprint,
                decision="WAIT", idempotency_key="boundary-key",
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created
            await session.commit()

        retry = await _scan_decide(app_client, token, snapshot, "WAIT", "boundary-key")
        assert retry.status_code == 409, retry.text

    async def test_a_reading_route_never_adopts(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        row_id = await self._legacy_current(
            account_id, candidate_id, offset=timedelta(seconds=-60),
        )

        for _ in range(3):
            assert (await app_client.get(
                f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
            )).status_code == 200
            assert (await _guard(app_client, token, candidate_id)).status_code == 200
            assert (await _history(app_client, token)).status_code == 200

        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, row_id)
        assert row.household_subject_id is None, "a read adopted a legacy row"

    async def test_safe_legacy_beside_an_explicit_self_row_fails_closed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """One human cannot have two current answers to the same question."""
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)
        await self._legacy_current(account_id, candidate_id, offset=timedelta(seconds=-60))
        async with get_sessionmaker()() as session:
            session.add(PurchaseDecision(
                account_id=account_id, household_subject_id=uuid.UUID(self_id),
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="skipped",
                followed_recommendation=False,
            ))
            await session.commit()

        read = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
        )
        assert read.status_code == 503, read.text
        assert read.json()["detail"]["code"] == "FEATURE_UNAVAILABLE"
        for leak in ("dual", str(account_id), str(candidate_id)):
            assert leak not in read.text, leak

        write = await _decide(app_client, token, candidate_id, "bought")
        assert write.status_code == 503, write.text

    async def test_ambiguous_legacy_beside_an_explicit_self_row_is_allowed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)
        await self._legacy_current(account_id, candidate_id, offset=timedelta(seconds=60))
        async with get_sessionmaker()() as session:
            session.add(PurchaseDecision(
                account_id=account_id, household_subject_id=uuid.UUID(self_id),
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="skipped",
                followed_recommendation=False,
            ))
            await session.commit()

        read = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
        )
        assert read.status_code == 200, read.text
        # The explicit row is current memory; the ambiguous one is only context.
        assert read.json()["decision"]["decision"] == "skipped"


# ---------------------------------------------------------------------------
# 5. Scan decision memory
# ---------------------------------------------------------------------------
class TestScanDecisionMemory:
    async def test_one_label_snapshot_holds_three_independent_answers(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        snapshot = await _snapshot()

        assert (await _scan_decide(app_client, token, snapshot, "WAIT", "k-self")).status_code == 200
        assert (await _scan_decide(app_client, token, snapshot, "BUY", "k-a", subject_id=member_a)).status_code == 200
        assert (await _scan_decide(app_client, token, snapshot, "SKIP", "k-b", subject_id=member_b)).status_code == 200

        mine = (await _scan_memory(app_client, token, snapshot)).json()
        theirs = (await _scan_memory(app_client, token, snapshot, subject_id=member_a)).json()
        other = (await _scan_memory(app_client, token, snapshot, subject_id=member_b)).json()
        assert mine["decision"]["decision"] == "WAIT"
        assert theirs["decision"]["decision"] == "BUY"
        assert other["decision"]["decision"] == "SKIP"
        assert len(mine["history"]) == len(theirs["history"]) == len(other["history"]) == 1

    async def test_a_scan_buy_still_owns_nothing_for_any_subject(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Deciding to buy is not owning. That holds for every human here."""
        from app.domains.inventory.models import InventoryItem, InventoryProductLink

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        snapshot = await _snapshot()
        await _scan_decide(app_client, token, snapshot, "BUY", "k-self")
        await _scan_decide(app_client, token, snapshot, "BUY", "k-member", subject_id=member)

        candidate_id = await _candidate(account_id)
        await _decide(app_client, token, candidate_id, "bought", subject_id=member)

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(InventoryItem.id))) == 0
            assert await session.scalar(select(func.count(InventoryProductLink.id))) == 0


class TestScanIdempotency:
    async def test_a_plain_retry_returns_the_same_event(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        snapshot = await _snapshot()
        first = await _scan_decide(app_client, token, snapshot, "BUY", "same-key")
        second = await _scan_decide(app_client, token, snapshot, "BUY", "same-key")
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

    async def test_a_pre_household_self_retry_survives_the_household(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The event predates the household, so it was necessarily this person's."""
        token, account_id = await registered_supabase_user()
        snapshot = await _snapshot()
        first = await _scan_decide(app_client, token, snapshot, "BUY", "cross-key")
        assert first.status_code == 200

        await _member(app_client, token, relation="adult")
        retry = await _scan_decide(app_client, token, snapshot, "BUY", "cross-key")
        assert retry.status_code == 200, retry.text
        assert retry.json()["id"] == first.json()["id"]
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 1

    async def test_an_ambiguous_legacy_event_cannot_be_claimed_by_a_retry(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """It was written after the household, so nobody can prove it was theirs."""
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        snapshot = await _snapshot()
        circle_created = await _circle_created_at(account_id)
        async with get_sessionmaker()() as session:
            event = ScanDecisionEvent(
                account_id=account_id, household_subject_id=None,
                barcode=snapshot.barcode, label_snapshot_id=snapshot.id,
                label_version=snapshot.version_number,
                content_fingerprint=snapshot.content_fingerprint,
                decision="BUY", idempotency_key="ambiguous-key",
            )
            session.add(event)
            await session.flush()
            event.created_at = circle_created + timedelta(seconds=60)
            await session.commit()

        retry = await _scan_decide(app_client, token, snapshot, "BUY", "ambiguous-key")
        assert retry.status_code == 409, retry.text
        assert retry.json()["detail"] == "idempotency_conflict"

    async def test_the_same_key_for_a_different_member_conflicts(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """One key names one operation, whoever it was for.

        And the refusal says nothing about which subject used it first.
        """
        token, _ = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        snapshot = await _snapshot()

        assert (await _scan_decide(
            app_client, token, snapshot, "BUY", "shared-key", subject_id=member_a,
        )).status_code == 200
        clash = await _scan_decide(
            app_client, token, snapshot, "BUY", "shared-key", subject_id=member_b,
        )
        assert clash.status_code == 409
        assert member_a not in clash.text
        assert clash.json()["detail"] == "idempotency_conflict"

    async def test_a_forged_subject_is_refused_before_the_event_is_looked_up(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        foreign = await _member(app_client, theirs_token, relation="adult")
        snapshot = await _snapshot()
        await _scan_decide(app_client, token, snapshot, "BUY", "probe-key")

        refused = await _scan_decide(
            app_client, token, snapshot, "BUY", "probe-key", subject_id=foreign,
        )
        assert refused.status_code == 404, refused.text
        assert foreign not in refused.text

    async def test_a_changed_decision_or_identity_conflicts(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        snapshot = await _snapshot()
        await _scan_decide(app_client, token, snapshot, "BUY", "k")
        changed = await _scan_decide(app_client, token, snapshot, "SKIP", "k")
        assert changed.status_code == 409
        noted = await _scan_decide(app_client, token, snapshot, "BUY", "k", note="new")
        assert noted.status_code == 409

    async def test_an_exact_concurrent_retry_returns_the_same_event_to_both(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Both callers get 200 and the same event. Neither is told to try again.

        Accepting "one of them got a 409" would be a weaker contract than the
        implementation actually offers, and weaker than the customer needs. Two
        identical requests are one tap that the network sent twice — the phone
        retrying, the user double-pressing — and the second one is not a
        conflict with anything. It is the same decision.

        What delivers that is worth naming exactly, because the obvious answer
        is no longer the right one. These two never race on the unique index at
        all: ``record_scan_decision`` takes write authority first, and for the
        account holder that means ``Account FOR UPDATE`` — so the second request
        waits on the account row, and by the time it looks for an existing event
        the first has committed one. It finds it, matches on every field
        including the subject, and returns it. The same holds for a named
        member, serialised on their ``family_profiles`` row instead.

        So this is a proof about serialisation plus the pre-insert lookup. The
        savepoint and unique-violation recovery are still necessary and still
        exercised, by two *different* members sharing one account-global
        idempotency key — see
        ``test_step11c_decision_memory_races.TestConcurrentScanRetries``, where
        that collision is forced deterministically.

        A 409 here would send a phone into a retry loop over a decision that was
        already saved.
        """
        token, _ = await registered_supabase_user()
        snapshot = await _snapshot()
        first, second = await asyncio.gather(
            _scan_decide(app_client, token, snapshot, "BUY", "race-key", note="same"),
            _scan_decide(app_client, token, snapshot, "BUY", "race-key", note="same"),
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 1

    async def test_an_exact_concurrent_retry_for_one_member_behaves_the_same(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The same guarantee once a household exists and a subject is named."""
        token, _ = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        snapshot = await _snapshot()
        first, second = await asyncio.gather(
            _scan_decide(app_client, token, snapshot, "WAIT", "member-race",
                         subject_id=member),
            _scan_decide(app_client, token, snapshot, "WAIT", "member-race",
                         subject_id=member),
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]
        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(ScanDecisionEvent.household_subject_id)
            )).scalars().all()
        assert rows == [uuid.UUID(member)]

    async def test_a_concurrent_conflicting_retry_is_still_refused(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Exactness is the whole condition, so the near-miss must still conflict.

        Same key, same product, different answer. One of these is not a retry of
        the other, and returning the winner's row to both would tell somebody
        their SKIP was recorded when a BUY was.
        """
        token, _ = await registered_supabase_user()
        snapshot = await _snapshot()
        first, second = await asyncio.gather(
            _scan_decide(app_client, token, snapshot, "BUY", "clash-key"),
            _scan_decide(app_client, token, snapshot, "SKIP", "clash-key"),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409]
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 1

    async def test_a_concurrent_same_key_retry_for_two_members_conflicts(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Account-global idempotency, kept. One key names one operation.

        Two people deciding at once under the same client key is a client bug,
        and the safe answer is to refuse the second rather than to quietly
        record one person's decision against the other.
        """
        token, _ = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        snapshot = await _snapshot()
        first, second = await asyncio.gather(
            _scan_decide(app_client, token, snapshot, "BUY", "shared-key",
                         subject_id=member_a),
            _scan_decide(app_client, token, snapshot, "BUY", "shared-key",
                         subject_id=member_b),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409]
        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 1


# ---------------------------------------------------------------------------
# 6. Principal, subject, and forgery
# ---------------------------------------------------------------------------
class TestSubjectAuthority:
    async def test_a_forged_identity_of_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Complete, internally consistent, and entirely somebody else's.

        Re-resolving it under its own ``account_id`` would confirm the forgery
        rather than catch it. Only the separately supplied principal can.
        """
        a_token, a_account = await registered_supabase_user()
        b_token, b_account = await registered_supabase_user()
        b_member = uuid.UUID(await _member(app_client, b_token, relation="adult"))
        b_candidate = await _candidate(b_account)
        await _decide(app_client, b_token, b_candidate, "bought", subject_id=str(b_member))

        forged = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER, account_id=b_account,
            subject_id=b_member, relation="adult", age_band=AGE_BAND_ADULT,
        )
        async with get_sessionmaker()() as session:
            with pytest.raises(SubjectNotFound):
                await canonical_decision_subject(
                    session, principal_account_id=a_account, subject=forged,
                )
            with pytest.raises(SubjectNotFound):
                await canonical_decision_subject(
                    session,
                    principal_account_id=a_account,
                    subject=account_holder_subject(b_account),
                )

    async def test_another_accounts_member_is_the_same_404_everywhere(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        mine_token, mine = await registered_supabase_user()
        theirs_token, _ = await registered_supabase_user()
        foreign = await _member(app_client, theirs_token, relation="adult")
        invented = str(uuid.uuid4())
        candidate_id = await _candidate(mine)

        for subject in (foreign, invented):
            assert (await _guard(app_client, mine_token, candidate_id, subject_id=subject)).status_code == 404
            assert (await _history(app_client, mine_token, subject_id=subject)).status_code == 404
            assert (await _decide(app_client, mine_token, candidate_id, "bought", subject_id=subject)).status_code == 404
            assert (await app_client.get(
                f"/api/v2/shopping/candidates/{candidate_id}/decision?subject_id={subject}",
                headers=auth(mine_token),
            )).status_code == 404

    async def test_an_inactive_member_cannot_be_selected_but_keeps_their_history(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await _decide(app_client, token, candidate_id, "bought", subject_id=member)

        await app_client.patch(
            f"{PROFILES_URL}/{member}", headers=auth(token), json={"active": False},
        )
        assert (await _guard(app_client, token, candidate_id, subject_id=member)).status_code == 404
        assert (await _history(app_client, token, subject_id=member)).status_code == 404
        assert (await _decide(app_client, token, candidate_id, "waiting", subject_id=member)).status_code == 404

        # Deactivation is not deletion: the history stays, and the export keeps it.
        async with get_sessionmaker()() as session:
            payload = await privacy_export.build_export(session, account_id)
        entry = next(
            s for s in payload["domains"]["shopping"]["subjects"]
            if s["household_subject_id"] == member
        )
        assert entry["active"] is False
        assert len(entry["decisions"]) == 1
        assert len(entry["decision_events"]) == 1

    async def test_a_cursor_from_another_subject_is_not_found(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Cursor validation must not confirm somebody else has an event."""
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await _decide(app_client, token, candidate_id, "bought", subject_id=member)

        theirs = (await _history(app_client, token, subject_id=member)).json()
        cursor = theirs["items"][0]["id"]
        refused = await _history(app_client, token, before=cursor)
        assert refused.status_code == 404, refused.text


# ---------------------------------------------------------------------------
# 7. Malformed households and cross-account corruption
# ---------------------------------------------------------------------------
class TestMalformedIdentity:
    async def test_a_broken_household_is_governed_on_every_memory_surface(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = uuid.UUID(await _self_id(app_client, token))
        candidate_id = await _candidate(account_id)
        async with get_sessionmaker()() as session:
            (await session.get(FamilyProfile, self_id)).active = False
            await session.commit()

        for response in (
            await _guard(app_client, token, candidate_id),
            await _history(app_client, token),
            await _decide(app_client, token, candidate_id, "bought"),
            await app_client.get(
                f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
            ),
        ):
            assert response.status_code == 503, response.text
            assert response.json()["detail"]["code"] == "FEATURE_UNAVAILABLE"

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0

    async def test_a_cross_account_decision_is_exported_unattributed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Two duties pulling opposite ways, and both of them met.

        The row belongs to this account by ``account_id`` and is their data. The
        subject it names belongs to another household and is not theirs to see.
        """
        a_token, a_account = await registered_supabase_user()
        _, b_account = await registered_supabase_user()
        a_member = uuid.UUID(await _member(app_client, a_token, relation="adult"))
        b_candidate = await _candidate(b_account)

        async with get_sessionmaker()() as session:
            corrupt = PurchaseDecision(
                account_id=b_account, household_subject_id=a_member,
                candidate_id=b_candidate, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="bought",
                followed_recommendation=False,
            )
            session.add(corrupt)
            await session.flush()
            corrupt_id = corrupt.id
            await session.commit()

        async with get_sessionmaker()() as session:
            b_export = await privacy_export.build_export(session, b_account)
            a_export = await privacy_export.build_export(session, a_account)

        shopping = b_export["domains"]["shopping"]
        assert shopping["invariant_errors"] == [
            {"purchase_decision_id": str(corrupt_id),
             "error": "decision_subject_ownership_invalid"}
        ]
        entry = next(
            d for d in shopping["unattributed_decisions"] if d["id"] == str(corrupt_id)
        )
        assert entry["household_subject_id"] is None, "a foreign member id leaked"
        assert entry["decision"] == "bought"
        import json as _json
        assert str(a_member) not in _json.dumps(b_export, default=str)
        # And A's own export never contains B's decision.
        assert str(corrupt_id) not in _json.dumps(a_export, default=str)

        # Nothing was repaired on the way past.
        async with get_sessionmaker()() as session:
            row = await session.get(PurchaseDecision, corrupt_id)
            assert row.account_id == b_account
            assert row.household_subject_id == a_member


# ---------------------------------------------------------------------------
# 8. Privacy export shape and erasure
# ---------------------------------------------------------------------------
class TestExportAndDeletion:
    async def test_decision_memory_is_grouped_by_human(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)
        snapshot = await _snapshot()
        await _decide(app_client, token, candidate_id, "waiting")
        await _decide(app_client, token, candidate_id, "bought", subject_id=member)
        await _scan_decide(app_client, token, snapshot, "WAIT", "s-self")
        await _scan_decide(app_client, token, snapshot, "BUY", "s-member", subject_id=member)

        async with get_sessionmaker()() as session:
            payload = await privacy_export.build_export(session, account_id)
        assert payload["schema_version"] == "1.2"

        shopping = {s["household_subject_id"]: s for s in payload["domains"]["shopping"]["subjects"]}
        assert [d["decision"] for d in shopping[self_id]["decisions"]] == ["waiting"]
        assert [d["decision"] for d in shopping[member]["decisions"]] == ["bought"]
        # Candidates stay account-wide: one thing the account is considering.
        assert len(payload["domains"]["shopping"]["candidates"]) == 1

        scans = {s["household_subject_id"]: s for s in payload["domains"]["product_scans"]["subjects"]}
        assert [e["decision"] for e in scans[self_id]["scan_decision_events"]] == ["WAIT"]
        assert [e["decision"] for e in scans[member]["scan_decision_events"]] == ["BUY"]
        # The scan itself stays account-level. It records that this account
        # looked at a barcode, which is true whoever the answer was for.
        assert "scans" in payload["domains"]["product_scans"]

    async def test_a_row_at_the_exact_boundary_is_exported_unattributed(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """The export implements the boundary rule itself, so it is pinned itself.

        Reads, adoption and the export each compare the same two timestamps in
        their own code — an SQL predicate, a Python method and this splitter.
        An export that resolved the equal instant in the account holder's favour
        would put another person's possible decision into a file the customer
        can download, which is the one place a wrong attribution leaves the
        product entirely.
        """
        token, account_id = await registered_supabase_user()
        await _member(app_client, token, relation="adult")
        self_id = await _self_id(app_client, token)
        candidate_id = await _candidate(account_id)
        circle_created = await _circle_created_at(account_id)

        async with get_sessionmaker()() as session:
            row = PurchaseDecision(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, strategy_key="care_purchase",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            event = PurchaseDecisionEvent(
                account_id=account_id, household_subject_id=None,
                candidate_id=candidate_id, decision_id=None,
                category="beauty", strategy_key="care_purchase",
                candidate_display_name="Gentle Cleanser",
                identity_version="v1", identity_state="exact",
                identity_fingerprint="boundary-fingerprint",
                recommendation_verdict="wait", recommendation_version="v",
                recommendation_snapshot={}, decision="waiting",
                followed_recommendation=True,
            )
            session.add_all([row, event])
            await session.flush()
            # A current decision is judged on when it last said something, an
            # immutable event on when it was written. Both sit exactly on the
            # boundary here, so both must fall the same way.
            row.updated_at = circle_created
            event.created_at = circle_created
            await session.commit()
            row_id, event_id = row.id, event.id

        async with get_sessionmaker()() as session:
            payload = await privacy_export.build_export(session, account_id)
        shopping = payload["domains"]["shopping"]

        holder = next(
            s for s in shopping["subjects"] if s["household_subject_id"] == self_id
        )
        assert holder["decisions"] == []
        assert holder["decision_events"] == []
        assert [d["id"] for d in shopping["unattributed_decisions"]] == [str(row_id)]
        assert [
            e["id"] for e in shopping["unattributed_decision_events"]
        ] == [str(event_id)]

        # Still in the customer's file — it is their data. Just without a name
        # on it, which is the whole distinction this step is about.
        assert shopping["unattributed_decisions"][0]["decision"] == "waiting"

    async def test_account_deletion_erases_every_subject_and_keeps_the_tombstone(
        self, db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        from app.domains.privacy import deletion_service
        from app.domains.privacy.models import AccountDeletionJob

        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        candidate_id = await _candidate(account_id)
        snapshot = await _snapshot()
        await _decide(app_client, token, candidate_id, "waiting")
        await _decide(app_client, token, candidate_id, "bought", subject_id=member_a)
        await _decide(app_client, token, candidate_id, "skipped", subject_id=member_b)
        await _scan_decide(app_client, token, snapshot, "WAIT", "d-self")
        await _scan_decide(app_client, token, snapshot, "BUY", "d-a", subject_id=member_a)
        await app_client.patch(
            f"{PROFILES_URL}/{member_b}", headers=auth(token), json={"active": False},
        )

        async def no_external(*_args, **_kwargs):
            return None
        monkeypatch.setattr(deletion_service, "_delete_supabase_identity", no_external)
        monkeypatch.setattr(deletion_service, "_remove_external_integrations", no_external)

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()
        async with get_sessionmaker()() as session:
            assert await deletion_service.drain_all(session) >= 1
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.scalar(select(func.count(PurchaseDecision.id))) == 0
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 0
            assert await session.scalar(select(func.count(ScanDecisionEvent.id))) == 0
            assert await session.scalar(select(func.count(FamilyCircle.id))) == 0
            assert await session.scalar(select(func.count(FamilyProfile.id))) == 0
            job = (await session.execute(
                select(AccountDeletionJob)
                .where(AccountDeletionJob.account_id == account_id)
            )).scalar_one()
            assert job.state == "complete"
            # Global Product Truth is untouched: it is shared with every other
            # shopper and was never this account's to erase.
            assert await session.scalar(select(func.count(LabelSnapshot.id))) == 1

    async def test_deleting_a_member_with_decision_memory_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The deferred foreign key, proven by behaviour.

        No route deletes a member today. When one is built it must make an
        explicit decision about that person's history rather than silently
        taking it with them.
        """
        from sqlalchemy.exc import IntegrityError

        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)
        await _decide(app_client, token, candidate_id, "bought", subject_id=member)

        async with get_sessionmaker()() as session:
            await session.execute(
                FamilyProfile.__table__.delete().where(FamilyProfile.id == uuid.UUID(member))
            )
            with pytest.raises(IntegrityError):
                await session.commit()


# ---------------------------------------------------------------------------
# 9. Concurrency, against real PostgreSQL
# ---------------------------------------------------------------------------
class TestConcurrency:
    async def test_two_identical_decisions_for_one_subject_make_one_event(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        member = await _member(app_client, token, relation="adult")
        candidate_id = await _candidate(account_id)

        first, second = await asyncio.gather(
            _decide(app_client, token, candidate_id, "waiting", subject_id=member),
            _decide(app_client, token, candidate_id, "waiting", subject_id=member),
        )
        assert first.status_code == second.status_code == 200
        async with get_sessionmaker()() as session:
            assert await session.scalar(
                select(func.count(PurchaseDecision.id))
                .where(PurchaseDecision.candidate_id == candidate_id)
            ) == 1
            assert await session.scalar(select(func.count(PurchaseDecisionEvent.id))) == 1

    async def test_two_subjects_deciding_at_once_never_collapse(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """They may serialise on the candidate. They must not merge.

        The candidate lock freezes one product's facts, so two people deciding
        about it at the same moment take turns — which is fine. What would not
        be fine is one of them ending up with the other's answer, or one event
        suppressing the other.
        """
        token, account_id = await registered_supabase_user()
        member_a = await _member(app_client, token, relation="adult")
        member_b = await _member(app_client, token, relation="other")
        candidate_id = await _candidate(account_id)

        results = await asyncio.gather(
            _decide(app_client, token, candidate_id, "bought", subject_id=member_a),
            _decide(app_client, token, candidate_id, "skipped", subject_id=member_b),
            _decide(app_client, token, candidate_id, "waiting"),
        )
        assert [r.status_code for r in results] == [200, 200, 200]

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(PurchaseDecision.household_subject_id, PurchaseDecision.decision)
                .where(PurchaseDecision.candidate_id == candidate_id)
            )).all()
            events = (await session.execute(
                select(PurchaseDecisionEvent.household_subject_id,
                       PurchaseDecisionEvent.decision)
            )).all()
        self_id = await _self_id(app_client, token)
        assert {str(s): d for s, d in rows} == {
            member_a: "bought", member_b: "skipped", self_id: "waiting",
        }
        assert {str(s): d for s, d in events} == {
            member_a: "bought", member_b: "skipped", self_id: "waiting",
        }

    async def test_a_self_decision_racing_household_creation_leaves_one_identity(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Both serialise on the account row, so neither can half-see the other."""
        from app.domains.family import service as family_service

        token, account_id = await registered_supabase_user()
        candidate_id = await _candidate(account_id)
        barrier = asyncio.Barrier(2)

        async def open_household():
            async with get_sessionmaker()() as session:
                await barrier.wait()
                await family_service.add_profile(session, account_id, relation="adult")
                await session.commit()

        async def decide():
            await barrier.wait()
            return await _decide(app_client, token, candidate_id, "waiting")

        _, response = await asyncio.gather(open_household(), decide())
        assert response.status_code == 200, response.text

        async with get_sessionmaker()() as session:
            rows = (await session.execute(
                select(PurchaseDecision).where(PurchaseDecision.candidate_id == candidate_id)
            )).scalars().all()
        assert len(rows) == 1, rows

        # Whichever way it went, the account holder can still read their own
        # memory: never the forbidden state of a safe legacy row beside an
        # explicit one.
        read = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
        )
        assert read.status_code == 200, read.text
        assert read.json()["decision"]["decision"] == "waiting"
