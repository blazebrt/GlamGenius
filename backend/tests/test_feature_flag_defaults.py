"""Feature-flag defaults regression (§11 of the Supabase hardening spec).

Guarantees:

* If ``V2_FEATURES`` is unset, the stable private-beta default set is
  used, so the app boots with the essential product on.
* If ``V2_FEATURES=""`` is explicitly set, every flag is off (deliberate
  operator override).
* The env override list wins over the stable defaults.
* Essential-flag health check surfaces disabled essentials.
* Adding a new key to ``KNOWN_FLAGS`` requires a stable default entry so
  a silent regression cannot slip in.
"""
from __future__ import annotations

from app.shared.flags import service as flags


def test_stable_defaults_include_every_known_flag():
    """Every KNOWN_FLAGS key must have an explicit stable default.

    A missing entry means a new flag was added without deciding what it
    should default to in the private beta.
    """
    missing = [k for k in flags.KNOWN_FLAGS if k not in flags.STABLE_BETA_DEFAULTS]
    assert not missing, f"KNOWN_FLAGS missing stable defaults: {missing}"


def test_stable_defaults_enable_essentials():
    for key in flags.ESSENTIAL_BETA_FLAGS:
        assert flags.STABLE_BETA_DEFAULTS.get(key) is True, (
            f"essential flag {key!r} must default to True in the private beta"
        )


def test_unset_v2_features_uses_stable_default(monkeypatch):
    monkeypatch.delenv("V2_FEATURES", raising=False)
    assert flags.resolved_default("v2_profile") is True
    assert flags.resolved_default("v2_scan") is True
    # Unfinished features remain off.
    assert flags.resolved_default("v2_virtual_tryon") is False


def test_empty_v2_features_string_is_an_explicit_disable(monkeypatch):
    monkeypatch.setenv("V2_FEATURES", "")
    # Explicit empty override — the operator asked for nothing.
    assert flags.resolved_default("v2_profile") is False


def test_v2_features_env_override_wins(monkeypatch):
    monkeypatch.setenv("V2_FEATURES", "v2_virtual_tryon,v2_profile")
    assert flags.resolved_default("v2_virtual_tryon") is True
    assert flags.resolved_default("v2_profile") is True
    # Not listed — off, because override is explicit.
    assert flags.resolved_default("v2_scan") is False


def test_warn_if_essentials_disabled_reports_missing(monkeypatch, caplog):
    monkeypatch.setenv("V2_FEATURES", "v2_privacy")  # only privacy on
    missing = flags.warn_if_essentials_disabled()
    assert "v2_profile" in missing
    assert "v2_scan" in missing
    # Privacy is on so it must not appear.
    assert "v2_privacy" not in missing


def test_warn_if_essentials_disabled_returns_empty_with_defaults(monkeypatch):
    monkeypatch.delenv("V2_FEATURES", raising=False)
    assert flags.warn_if_essentials_disabled() == []
