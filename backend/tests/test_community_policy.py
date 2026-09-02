"""The closed vocabulary, the lot comparison, and the display policy.

Pure units. No database, no HTTP — these are the rules the rest of Step 5 is
built on, and they are worth being able to read on their own.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domains.community import observations, policy
from app.domains.community.policy import AggregateEvidence

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def evidence(*, pairing: dict[str, set[str]] | None = None,
             accounts: int = 3, photos: int = 3,
             code: str = observations.OBSERVATION_SEAL_BROKEN,
             scope: str = observations.SCOPE_BATCH, batch: str | None = "b-123"):
    """Evidence for one aggregate key.

    ``pairing`` states who supplied which photograph. The convenience form
    spreads ``photos`` distinct hashes over ``accounts`` reporters, one each.
    """
    if pairing is None:
        pairing = {
            f"account-{index}": {f"sha-{index}"} if index < photos else set()
            for index in range(accounts)
        }
    return AggregateEvidence(
        observation_code=code, scope=scope, batch_number=batch,
        reporter_photo_hashes={name: frozenset(hashes) for name, hashes in pairing.items()},
        first_reported_at=NOW - timedelta(days=2), last_reported_at=NOW,
    )


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def test_the_vocabulary_is_closed_and_holds_no_conclusions():
    """Every code names something a person can see. None names a verdict."""
    assert len(observations.OBSERVATION_CODES) == 10
    assert observations.PRODUCT_DATA_OBSERVATIONS.isdisjoint(observations.PACK_CONDITION_OBSERVATIONS)
    forbidden = {
        "adulterated", "fake", "counterfeit", "unsafe", "dangerous", "fraud", "spoiled",
        "unusual_smell", "unusual_colour", "appeared_spoiled",
    }
    assert not [code for code in observations.OBSERVATION_CODES if code in forbidden]
    # V1 deliberately omits the subjective and storage-dependent ones too.
    assert not [code for code in observations.OBSERVATION_CODES if "smell" in code or "colour" in code]


def test_pack_condition_observations_are_always_about_one_lot():
    """A pack made on one line on one day says nothing about the next lot."""
    for code in observations.PACK_CONDITION_OBSERVATIONS:
        assert observations.observation_scope(code) == observations.SCOPE_BATCH
        assert observations.is_batch_scoped(code) is True
    for code in observations.PRODUCT_DATA_OBSERVATIONS:
        assert observations.observation_scope(code) == observations.SCOPE_PRODUCT
        assert observations.is_batch_scoped(code) is False


# ---------------------------------------------------------------------------
# Lot comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("placeholder", [
    "NA", "N/A", "nil", "none", "Not Applicable", "not available", "other", "others",
    "-", ".", "NO", "LOOSE", "loose sample", "sold as loose", "0", "00", "000", "   NA  ",
])
def test_values_that_identify_no_lot_are_not_lot_numbers(placeholder):
    assert observations.normalise_batch(placeholder) is None


@pytest.mark.parametrize("batch", ["C", "1", "L0", "B-123", "0789/55"])
def test_short_real_lot_codes_survive(batch):
    assert observations.normalise_batch(batch) == batch.casefold()


def test_case_is_forgiving_and_separators_are_not():
    assert observations.normalise_batch("B-123") == observations.normalise_batch("b-123")
    assert observations.normalise_batch("B-123") != observations.normalise_batch("B 123")
    assert observations.normalise_batch("  B-123  ") == "b-123"
    # Never a typed-in value: only a string read off a confirmed capture.
    assert observations.normalise_batch(12345) is None
    assert observations.normalise_batch(None) is None


def test_the_community_lot_rule_is_this_domains_own():
    """Aggregation must not depend on the FSSAI ingestion layer."""
    source = (observations.__file__)
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "official_records" not in text


# ---------------------------------------------------------------------------
# Display policy
# ---------------------------------------------------------------------------

def test_three_people_with_three_photographs_is_the_bar():
    decision = policy.evaluate(evidence())
    assert decision.public is True
    assert decision.independent_reporters == 3
    assert decision.unique_supporting_photos == 3
    assert decision.reason_keys == (policy.REASON_THRESHOLD_MET,)
    # Two invariants restated in every decision, so no caller can confuse them.
    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


def test_two_reporters_are_never_public_however_the_code_sounds():
    for code in sorted(observations.OBSERVATION_CODES):
        decision = policy.evaluate(evidence(accounts=2, photos=2, code=code))
        assert decision.public is False
        assert policy.REASON_BELOW_REPORTER_THRESHOLD in decision.reason_keys


def test_three_people_holding_one_photograph_between_them_is_not_three_photographs():
    decision = policy.evaluate(evidence(pairing={"a": {"h1"}, "b": {"h1"}, "c": {"h1"}}))
    assert decision.public is False
    assert decision.reason_keys == (policy.REASON_BELOW_PHOTO_THRESHOLD,)
    assert decision.unique_supporting_photos == 1


# ---------------------------------------------------------------------------
# Who actually photographed anything
# ---------------------------------------------------------------------------

def test_one_busy_reporter_cannot_lend_photographs_to_two_silent_ones():
    """The exploit the two-set count missed.

    A uploads three distinct photographs; B and C each re-upload A's first.
    Counted as sets that is three accounts and three hashes — public. Matched,
    B and C compete for the one hash they share and only one can hold it, so
    two people evidenced this, not three.
    """
    decision = policy.evaluate(evidence(
        pairing={"a": {"h1", "h2", "h3"}, "b": {"h1"}, "c": {"h1"}},
    ))
    assert decision.independent_reporters == 3
    assert decision.unique_supporting_photos == 2
    assert decision.public is False
    assert decision.reason_keys == (policy.REASON_BELOW_PHOTO_THRESHOLD,)


def test_a_pairing_that_genuinely_exists_is_found():
    """A shares a hash with B but has one of their own, so all three can pair.

    a->h2, b->h1, c->h3. A greedy first-come assignment would give A h1, strand
    B, and wrongly refuse a signal three people did evidence.
    """
    decision = policy.evaluate(evidence(
        pairing={"a": {"h1", "h2"}, "b": {"h1"}, "c": {"h3"}},
    ))
    assert decision.unique_supporting_photos == 3
    assert decision.public is True


def test_ten_photographs_from_one_person_is_one_person():
    decision = policy.evaluate(evidence(
        pairing={"a": {f"h{index}" for index in range(10)}},
    ))
    assert decision.independent_reporters == 1
    assert decision.unique_supporting_photos == 1
    assert decision.public is False


def test_the_pairing_is_deterministic_whatever_order_the_rows_arrive_in():
    """Two shoppers must never see different answers for the same evidence."""
    pairing = {"a": {"h1", "h2"}, "b": {"h1"}, "c": {"h2", "h3"}, "d": {"h3"}}
    answers = {
        policy.max_independent_pairs(
            {name: frozenset(hashes) for name, hashes in sorted(pairing.items(), reverse=reverse)}
        )
        for reverse in (False, True)
    }
    assert answers == {3}


def test_the_policy_names_itself_and_its_window():
    assert policy.COMMUNITY_POLICY_VERSION == "community-observations-v1"
    assert policy.ACTIVE_WINDOW_DAYS == 90
    assert policy.active_window_start(NOW) == NOW - timedelta(days=90)
    assert (policy.MIN_PUBLIC_REPORTERS, policy.MIN_UNIQUE_PHOTOS) == (3, 3)


def test_the_policy_module_contains_no_customer_copy():
    """Keys and counts leave this module; sentences are the string file's job."""
    with open(policy.__file__, encoding="utf-8") as handle:
        body = [line for line in handle if not line.lstrip().startswith("#")]
    text = "".join(body)
    for sentence in ("shoppers reported", "Reported by", "Warning", "Danger", "Unsafe"):
        assert sentence not in text


# ---------------------------------------------------------------------------
# Fail-closed public display
# ---------------------------------------------------------------------------

def test_public_display_fails_closed_without_a_way_for_a_brand_to_answer():
    """The Constitution requires a visible right of reply before any UGC shows."""
    assert policy.public_display_state(enabled=False, brand_reply_url="https://example.org/x")[0] is False
    assert policy.public_display_state(enabled=True, brand_reply_url=None)[0] is False
    assert policy.public_display_state(enabled=True, brand_reply_url="")[0] is False
    assert policy.public_display_state(enabled=True, brand_reply_url="not-a-url")[0] is False
    assert policy.public_display_state(enabled=True, brand_reply_url="http://example.org/x")[0] is False
    assert policy.public_display_state(enabled=True, brand_reply_url="https://example.org/reply")[0] is True

    _, reasons = policy.public_display_state(enabled=False, brand_reply_url=None)
    assert set(reasons) == {policy.REASON_PUBLIC_DISPLAY_DISABLED, policy.REASON_BRAND_REPLY_URL_MISSING}


# ---------------------------------------------------------------------------
# The layers stay apart
# ---------------------------------------------------------------------------

def test_no_scientific_or_official_module_imports_community():
    """A shopper observation must not be able to reach the grade or the register.

    The cheapest way for that boundary to rot is an import, so this walks the
    packages that own the other epistemic layers and refuses to find one.
    """
    import pathlib

    backend = pathlib.Path(__file__).resolve().parent.parent
    protected = [
        backend / "app" / "domains" / "nutrition",
        backend / "app" / "domains" / "evidence",
        backend / "app" / "domains" / "official_records",
        backend / "app" / "domains" / "off",
    ]
    offenders = []
    for root in protected:
        for module in root.rglob("*.py"):
            text = module.read_text(encoding="utf-8")
            if "domains.community" in text or "domains import community" in text:
                offenders.append(str(module.relative_to(backend)))
    assert offenders == []


def test_the_community_domain_reaches_for_no_scoring_or_official_module():
    """And the dependency does not run the other way either."""
    import pathlib

    community = pathlib.Path(__file__).resolve().parent.parent / "app" / "domains" / "community"
    forbidden = ("domains.nutrition", "domains.evidence", "domains.official_records", "domains.off")
    offenders = [
        f"{module.name}:{needle}"
        for module in community.rglob("*.py")
        for needle in forbidden
        if needle in module.read_text(encoding="utf-8")
    ]
    assert offenders == []
