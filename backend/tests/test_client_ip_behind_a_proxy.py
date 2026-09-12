"""The caller's address, and the two ways of getting it wrong.

``request.client.host`` is the socket peer. Behind a platform router that is
the router — identical for every user — so the invite rate limiter degrades
into one shared bucket (ten attempts a minute for the entire product, and one
attacker can spend them) and every audit row records the same address.

Reading ``X-Forwarded-For`` instead is the other mistake, and it is worse: the
caller writes that header, so trusting its left-most entry lets every request
claim a fresh address and removes the limiter altogether.

``TRUSTED_PROXY_HOPS`` is how the deployment says which of those is true, and
the tests below pin both ends: nothing is trusted at 0, and at 1 a forged
header cannot move the answer.
"""
from __future__ import annotations

import pytest
from app.shared.security import network


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Request:
    """Only the two things ``client_ip`` reads."""

    def __init__(self, peer: str | None, headers: dict[str, str] | None = None) -> None:
        self.client = _Client(peer) if peer else None
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


@pytest.fixture
def hops(monkeypatch):
    def _set(value: int):
        monkeypatch.setattr(network, "TRUSTED_PROXY_HOPS", value)
    return _set


class TestNothingTrusted:
    """The default. Behaviour is exactly what it was before the header was read."""

    def test_the_socket_peer_is_the_answer(self, hops):
        hops(0)
        request = _Request("198.51.100.7")
        assert network.client_ip(request) == "198.51.100.7"

    def test_a_forwarded_header_is_ignored_entirely(self, hops):
        hops(0)
        request = _Request("198.51.100.7", {"X-Forwarded-For": "203.0.113.9"})
        assert network.client_ip(request) == "198.51.100.7"

    def test_no_peer_is_none_rather_than_a_guess(self, hops):
        hops(0)
        assert network.client_ip(_Request(None)) is None


class TestOneTrustedProxy:
    def test_the_entry_the_proxy_appended_is_the_answer(self, hops):
        hops(1)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "203.0.113.9"})
        assert network.client_ip(request) == "203.0.113.9"

    def test_a_forged_header_cannot_change_the_answer(self, hops):
        """The heart of it.

        The caller sends ``X-Forwarded-For: 1.2.3.4``; the trusted proxy
        appends the address it actually saw. Reading from the right gets the
        real caller, and the forged value sits harmlessly to its left.
        """
        hops(1)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
        assert network.client_ip(request) == "203.0.113.9"

    def test_a_long_forged_chain_cannot_change_the_answer_either(self, hops):
        hops(1)
        forged = ", ".join(f"1.2.3.{n}" for n in range(1, 30))
        request = _Request("10.0.0.1", {"X-Forwarded-For": f"{forged}, 203.0.113.9"})
        assert network.client_ip(request) == "203.0.113.9"

    def test_two_callers_behind_one_proxy_are_told_apart(self, hops):
        """What the rate limiter needs and did not have."""
        hops(1)
        first = network.client_ip(_Request("10.0.0.1", {"X-Forwarded-For": "203.0.113.9"}))
        second = network.client_ip(_Request("10.0.0.1", {"X-Forwarded-For": "203.0.113.10"}))
        assert first != second

    def test_a_missing_header_falls_back_to_the_peer(self, hops):
        hops(1)
        assert network.client_ip(_Request("10.0.0.1")) == "10.0.0.1"

    def test_a_junk_entry_never_reaches_a_rate_limit_key(self, hops):
        hops(1)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "not-an-address"})
        assert network.client_ip(request) == "10.0.0.1"

    def test_a_port_is_stripped_so_one_caller_is_one_key(self, hops):
        hops(1)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "203.0.113.9:51234"})
        assert network.client_ip(request) == "203.0.113.9"

    def test_ipv6_is_understood(self, hops):
        hops(1)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "2001:db8::42"})
        assert network.client_ip(request) == "2001:db8::42"

    def test_bracketed_ipv6_with_a_port_is_understood(self, hops):
        hops(1)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "[2001:db8::42]:51234"})
        assert network.client_ip(request) == "2001:db8::42"


class TestSeveralTrustedProxies:
    def test_the_caller_is_counted_from_the_right(self, hops):
        hops(2)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "203.0.113.9, 10.0.0.9"})
        assert network.client_ip(request) == "203.0.113.9"

    def test_fewer_hops_than_configured_falls_back_rather_than_trusting(self, hops):
        """Something unexpected is in front. The left-most entry is the
        caller's to write, so it is not an answer — the peer is."""
        hops(3)
        request = _Request("10.0.0.1", {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        assert network.client_ip(request) == "10.0.0.1"


def test_there_is_only_one_implementation():
    """There were two identical copies, and a fix to either would have missed
    half the call sites."""
    from app.shared.security import deps, supabase_auth

    assert deps.client_ip is network.client_ip
    assert supabase_auth.client_ip is network.client_ip
