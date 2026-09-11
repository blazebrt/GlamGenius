import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
PYTHON = sys.executable

def run_config_test(env_vars: dict) -> subprocess.CompletedProcess:
    env = {
        "APP_ENV": "production",
        "SUPABASE_URL": "https://valid.supabase.co",
        "SUPABASE_ANON_KEY": "valid_anon",
        "SUPABASE_SERVICE_ROLE_KEY": "valid_service",
        "SUPABASE_JWKS_URL": "https://valid.supabase.co/auth/v1/.well-known/jwks.json",
        "SUPABASE_JWT_ISSUER": "https://valid.supabase.co/auth/v1",
        "POSTGRES_URL": "postgresql://user:pass@db.example.com:5432/db",
        # A different host on purpose: OFF_DATABASE_URL is what makes the ODbL
        # separation physical rather than notional, so a valid production
        # config is one where Store A is genuinely somewhere else.
        "OFF_DATABASE_URL": "postgresql://user:pass@off.example.com:5432/off",
        "SUPABASE_STORAGE_BUCKET": "bucket",
        "GEMINI_API_KEY": "key",
        "SENTRY_BACKEND_DSN": "https://user@sentry.io/123",
        "INVITE_REQUIRED": "1",
        "REQUIRE_ANALYSIS_CONSENT": "1",
        "CONSENT_VERSION": "v1",
        "MEDIA_STORAGE_BACKEND": "supabase",
        "ALLOWED_ORIGINS": "https://example.com",
        "PRIVACY_POLICY_URL": "https://example.org/privacy",
        "SUPPORT_URL": "https://example.org/support",
        # The scheduler credential. Long enough to clear the minimum
        # length and not shaped like a placeholder, because production
        # rejects both and this fixture is the valid case.
        #
        # Deliberately repetitive words rather than random hex: the secret
        # scanner reads a high-entropy string beside a token-shaped key name
        # as a leaked credential, and it is right to. A fixture should not
        # look like a secret to anything, a scanner included.
        "INTERNAL_SCHEDULER_TOKEN": "not-a-real-token-not-a-real-token-not-a-real-token",
    }
    env.update(env_vars)
    
    script = """
import os
from app.config import validate_production_configuration
validate_production_configuration()
print("OK")
"""
    return subprocess.run(
        [PYTHON, "-c", script],
        env=env,
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )

def test_valid_production_config():
    res = run_config_test({})
    assert res.returncode == 0
    assert "OK" in res.stdout


def test_production_refuses_to_start_without_a_separate_off_store():
    """Store A sharing the application database is a development convenience.

    In production it is the difference between the two stores being physically
    apart and merely intending to be, so a missing OFF_DATABASE_URL stops the
    boot rather than silently falling back.
    """
    res = run_config_test({"OFF_DATABASE_URL": ""})
    assert res.returncode != 0
    assert "OFF_DATABASE_URL" in res.stderr

@pytest.mark.parametrize("bad_postgres", [
    "postgresql://postgres:postgres@db.example.com/db",
    "postgresql://user:pass@localhost/db",
    "postgresql://user:pass@127.0.0.1/db",
    "mysql://user:pass@db.example.com/db",
    "postgresql://user:pass@placeholder.example.com/db",
    "postgresql://user:pass@db.example.com:999999/db",
])
def test_bad_postgres(bad_postgres):
    res = run_config_test({"POSTGRES_URL": bad_postgres})
    assert res.returncode != 0
    assert "CRITICAL" in res.stderr

@pytest.mark.parametrize("bad_origin", [
    "http://localhost",
    "*",
    "https://example.com/path",
    "https://user:pass@example.com",
])
def test_bad_cors(bad_origin):
    res = run_config_test({"ALLOWED_ORIGINS": bad_origin})
    assert res.returncode != 0
    assert "CRITICAL" in res.stderr

def test_missing_jwks():
    res = run_config_test({"SUPABASE_JWKS_URL": " "})
    assert res.returncode != 0
    assert "JWKS" in res.stderr

def test_bad_supabase_keys():
    res = run_config_test({"SUPABASE_ANON_KEY": "placeholder_key"})
    assert res.returncode != 0
    assert "CRITICAL" in res.stderr


# ---------------------------------------------------------------------------
# The scheduler credential, through the real validator
# ---------------------------------------------------------------------------
# These run `validate_production_configuration()` itself, in a subprocess with
# a real production environment, rather than a test-local reimplementation of
# the rule. A helper that restates the logic passes even when the logic it
# restates has been deleted, which is the one failure that matters here: the
# scheduler routes refuse every caller when the token is unset, so a
# production instance that boots without one has two batch jobs that can
# never run.
#
# Fixture values are deliberately low-entropy and obviously synthetic, so the
# secret scanner never sees something shaped like a credential and no scanner
# allowlist is needed.

def test_missing_scheduler_token_refuses_production_boot():
    res = run_config_test({"INTERNAL_SCHEDULER_TOKEN": ""})
    assert res.returncode != 0
    assert "INTERNAL_SCHEDULER_TOKEN" in res.stderr
    assert "must be set in production" in res.stderr


def test_whitespace_only_scheduler_token_refuses_production_boot():
    res = run_config_test({"INTERNAL_SCHEDULER_TOKEN": "   "})
    assert res.returncode != 0
    assert "must be set in production" in res.stderr


@pytest.mark.parametrize("short", ["x", "short-token", "a" * 31])
def test_short_scheduler_token_refuses_production_boot(short):
    res = run_config_test({"INTERNAL_SCHEDULER_TOKEN": short})
    assert res.returncode != 0
    assert "too short" in res.stderr


@pytest.mark.parametrize("marker", [
    "placeholder", "changeme", "change_me", "todo",
    "your_", "your-", "replace", "secret-here", "xxxx", "example",
])
def test_placeholder_scheduler_token_refuses_production_boot(marker):
    # Padded past the length floor so the refusal is about the shape, not the
    # length. Every marker the production validator knows is covered, which is
    # what keeps its list and the readiness report's list the same list.
    res = run_config_test({"INTERNAL_SCHEDULER_TOKEN": marker + "-token" * 6})
    assert res.returncode != 0
    assert "placeholder" in res.stderr


def test_a_synthetic_but_acceptable_scheduler_token_boots():
    res = run_config_test(
        {"INTERNAL_SCHEDULER_TOKEN": "not-a-real-token-not-a-real-token-not-a-real-token"}
    )
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout


def test_the_scheduler_token_value_never_reaches_the_refusal_message():
    # The operator reads stderr. It must name the key and the problem, never
    # the value, so that a refusal is safe to paste into a ticket.
    value = "replace-this-scheduler-token-please-0000000"
    res = run_config_test({"INTERNAL_SCHEDULER_TOKEN": value})
    assert res.returncode != 0
    assert value not in res.stderr
    assert value not in res.stdout
