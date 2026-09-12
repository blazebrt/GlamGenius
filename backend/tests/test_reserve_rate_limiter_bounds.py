"""The unauthenticated reserve limiter must bound what a stranger can grow.

``POST /api/v2/access/reserve`` takes no credential, and part of its rate-limit
key is caller-chosen (the email). The limiter's table therefore has to be
bounded in memory *and* in the work done per request, because both are
reachable by anyone who can reach the endpoint.

The original implementation was a ``defaultdict`` that was never swept. Buckets
were trimmed inside, but the keys themselves lived for the life of the process:
100,000 distinct addresses retained roughly 86 MiB that nothing ever freed, on
an instance sized at 512 MiB. These tests fail against that implementation.

The first repair traded the memory problem for a worse one — it swept the whole
table on every insertion once the ceiling was reached, so a flood of distinct
keys cost O(table) per request. ``test_a_flood_of_distinct_keys_stays_cheap``
is the regression for that, and it is the reason the sweep is throttled.
"""
from __future__ import annotations

import time

import pytest
from app.api.v2 import access


@pytest.fixture(autouse=True)
def _clean_limiter():
    access._limiter.reset()
    yield
    access._limiter.reset()


def _age_everything(seconds: float) -> None:
    """Move every recorded hit back in time, past the window."""
    for bucket in access._rate_state.values():
        for index in range(len(bucket)):
            bucket[index] -= seconds


class TestTheLimitItselfIsUnchanged:
    def test_the_window_allows_exactly_the_configured_number(self) -> None:
        allowed = [
            not access._hit_rate_limit("email:a@example.com")
            for _ in range(access._RATE_LIMIT_MAX_PER_WINDOW)
        ]
        assert all(allowed)
        assert access._hit_rate_limit("email:a@example.com") is True

    def test_a_fresh_window_allows_again(self) -> None:
        for _ in range(access._RATE_LIMIT_MAX_PER_WINDOW):
            access._hit_rate_limit("email:b@example.com")
        assert access._hit_rate_limit("email:b@example.com") is True
        _age_everything(access._RATE_LIMIT_WINDOW_SECONDS + 1)
        assert access._hit_rate_limit("email:b@example.com") is False

    def test_keys_do_not_share_a_budget(self) -> None:
        for _ in range(access._RATE_LIMIT_MAX_PER_WINDOW):
            access._hit_rate_limit("email:one@example.com")
        assert access._hit_rate_limit("email:one@example.com") is True
        assert access._hit_rate_limit("email:two@example.com") is False


class TestTheTableIsBounded:
    def test_distinct_keys_cannot_grow_the_table_without_limit(self) -> None:
        for index in range(access._RATE_LIMIT_MAX_KEYS * 2):
            access._hit_rate_limit(f"email:user{index}@example.com")
        assert len(access._rate_state) <= access._RATE_LIMIT_MAX_KEYS

    def test_expired_keys_are_reclaimed_not_merely_capped(self) -> None:
        """A ceiling alone would leave the table permanently full."""
        for index in range(500):
            access._hit_rate_limit(f"email:user{index}@example.com")
        assert len(access._rate_state) == 500
        _age_everything(access._RATE_LIMIT_WINDOW_SECONDS + 1)
        access._limiter._sweep_expired(time.monotonic())
        assert access._rate_state == {}

    def test_an_emptied_bucket_does_not_keep_its_key(self) -> None:
        access._hit_rate_limit("email:solo@example.com")
        _age_everything(access._RATE_LIMIT_WINDOW_SECONDS + 1)
        # Touching the key again must not leave two generations behind.
        access._hit_rate_limit("email:solo@example.com")
        assert list(access._rate_state) == ["email:solo@example.com"]
        assert len(access._rate_state["email:solo@example.com"]) == 1


class TestTheCeilingIsNotAWayIn:
    def test_a_full_table_refuses_rather_than_stops_tracking(self) -> None:
        """Failing open here would be the bypass.

        If a full table simply stopped tracking new keys, an attacker could
        fill it with throwaway addresses and then hammer the one address they
        actually wanted, untracked.
        """
        for index in range(access._RATE_LIMIT_MAX_KEYS):
            access._hit_rate_limit(f"email:filler{index}@example.com")
        assert len(access._rate_state) == access._RATE_LIMIT_MAX_KEYS
        assert access._hit_rate_limit("email:victim@example.com") is True


class TestTheRepairDidNotCostCpuInstead:
    def test_a_flood_of_distinct_keys_stays_cheap(self) -> None:
        """Sweeping per request once full is O(table) per request.

        That is a denial of service in its own right, and a cheaper one to
        trigger than the unbounded table it replaced. The sweep is throttled,
        so this has to finish quickly rather than quadratically.
        """
        started = time.perf_counter()
        for index in range(access._RATE_LIMIT_MAX_KEYS * 3):
            access._hit_rate_limit(f"email:flood{index}@example.com")
        elapsed = time.perf_counter() - started
        # Generous: the unthrottled version did not finish this in 120s.
        assert elapsed < 15.0, f"flood path is too expensive: {elapsed:.1f}s"

    def test_the_sweep_is_throttled(self) -> None:
        access._last_sweep_at = time.monotonic()
        for index in range(access._RATE_LIMIT_MAX_KEYS):
            access._hit_rate_limit(f"email:x{index}@example.com")
        before = access._last_sweep_at
        # Table is full and the interval has not elapsed: no sweep may run.
        access._hit_rate_limit("email:another@example.com")
        assert access._last_sweep_at == before
