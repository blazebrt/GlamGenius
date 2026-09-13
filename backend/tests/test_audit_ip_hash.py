"""The audit trail's address hash has to be one-way to somebody with the code.

It used to be ``sha256("glamgenius-audit:" + ip)``, documented as salted. A
constant in the repository is not a salt — it is a domain separator. It stops a
generic rainbow table of bare addresses and nothing else, because anyone who
can read the source can build the table for this exact prefix. There are only
2^32 IPv4 addresses: under two core-hours of unoptimised Python, seconds on a
GPU.

The hash is now keyed with a secret. What the tests below pin is the property
that matters — knowing the algorithm and the source is not enough to reverse
it, only knowing the key is — plus the production gate that stops the
development key ever being the one in use.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest
from app import config
from app.domains.audit import service as audit

from tests.test_production_config import run_config_test


@pytest.fixture
def keyed(monkeypatch):
    def _set(key: str):
        monkeypatch.setattr(config, "AUDIT_IP_HASH_KEY", key)
        monkeypatch.setattr(audit, "AUDIT_IP_HASH_KEY", key)
    return _set


class TestTheHashItself:
    def test_the_same_address_always_gives_the_same_hash(self, keyed):
        """Investigations link one actor's actions by matching hashes."""
        keyed("k" * 40)
        assert audit.hash_ip("203.0.113.42") == audit.hash_ip("203.0.113.42")

    def test_different_addresses_give_different_hashes(self, keyed):
        keyed("k" * 40)
        assert audit.hash_ip("203.0.113.42") != audit.hash_ip("203.0.113.43")

    def test_no_address_is_recorded_as_no_hash(self, keyed):
        keyed("k" * 40)
        assert audit.hash_ip(None) is None
        assert audit.hash_ip("") is None

    def test_the_hash_depends_on_the_key(self, keyed):
        """The point of the change. With the old scheme both of these were the
        same value, because there was nothing to vary."""
        keyed("first-key-" + "x" * 30)
        first = audit.hash_ip("203.0.113.42")
        keyed("second-key-" + "y" * 30)
        assert audit.hash_ip("203.0.113.42") != first

    def test_the_old_constant_prefix_scheme_is_gone(self, keyed):
        """Anyone can still compute the old value; it must no longer match."""
        keyed("k" * 40)
        old = hashlib.sha256(b"glamgenius-audit:203.0.113.42").hexdigest()
        assert audit.hash_ip("203.0.113.42") != old

    def test_the_source_alone_does_not_reverse_it(self, keyed):
        """An attacker with the repository, the algorithm and the whole address
        space — but not the key — gets nothing."""
        keyed("the-real-secret-" + "z" * 30)
        target = audit.hash_ip("203.0.113.42")

        for guess_key in ("", "glamgenius-audit", audit.DEVELOPMENT_ONLY_KEY):
            table = {
                hmac.new(guess_key.encode(), f"203.0.113.{n}".encode(), hashlib.sha256).hexdigest()
                for n in range(256)
            }
            table |= {
                hashlib.sha256(f"{guess_key}:203.0.113.{n}".encode()).hexdigest()
                for n in range(256)
            }
            assert target not in table, f"reversed with {guess_key!r}"


class TestTheProductionGate:
    """The development key must never be the one production runs on.

    Driven through ``run_config_test``, which boots the real validator in a
    subprocess with real environment variables — the same harness the other
    production invariants use. Monkeypatching config attributes in-process does
    not reproduce how the validator actually reads its configuration, and a
    gate is only worth having if it is tested the way it runs.
    """

    def test_a_complete_production_configuration_boots(self):
        """Proves the fixture is valid, so the refusals below are about the key."""
        result = run_config_test({})
        assert result.returncode == 0, result.stderr

    def test_production_refuses_to_start_without_a_key(self):
        result = run_config_test({"AUDIT_IP_HASH_KEY": ""})
        assert result.returncode != 0
        assert "AUDIT_IP_HASH_KEY" in result.stderr

    @pytest.mark.parametrize("short", ["x", "too-short", "a" * 31])
    def test_production_refuses_a_short_key(self, short):
        result = run_config_test({"AUDIT_IP_HASH_KEY": short})
        assert result.returncode != 0
        assert "AUDIT_IP_HASH_KEY" in result.stderr

    def test_staging_is_held_to_the_same_bar(self):
        result = run_config_test({"APP_ENV": "staging", "AUDIT_IP_HASH_KEY": ""})
        assert result.returncode != 0
        assert "AUDIT_IP_HASH_KEY" in result.stderr

    def test_development_still_runs_without_one(self):
        result = run_config_test({"APP_ENV": "development", "AUDIT_IP_HASH_KEY": ""})
        assert result.returncode == 0, result.stderr

    def test_the_refusal_never_echoes_the_key(self):
        """A validator that prints the secret it is checking is worse than none."""
        result = run_config_test({"AUDIT_IP_HASH_KEY": "short-but-secret"})
        assert "short-but-secret" not in result.stderr
        assert "short-but-secret" not in result.stdout

    def test_the_development_key_is_named_for_what_it_is(self):
        assert "development" in audit.DEVELOPMENT_ONLY_KEY
        assert "not-a-secret" in audit.DEVELOPMENT_ONLY_KEY
