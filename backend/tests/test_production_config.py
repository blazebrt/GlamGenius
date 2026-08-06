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
        "SUPABASE_STORAGE_BUCKET": "bucket",
        "GEMINI_API_KEY": "key",
        "SENTRY_BACKEND_DSN": "https://user@sentry.io/123",
        "INVITE_REQUIRED": "1",
        "REQUIRE_ANALYSIS_CONSENT": "1",
        "CONSENT_VERSION": "v1",
        "MEDIA_STORAGE_BACKEND": "supabase",
        "ALLOWED_ORIGINS": "https://example.com",
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
    res = run_config_test({"SUPABASE_JWKS_URL": ""})
    assert res.returncode != 0
    assert "CRITICAL" in res.stderr

def test_bad_supabase_keys():
    res = run_config_test({"SUPABASE_ANON_KEY": "placeholder_key"})
    assert res.returncode != 0
    assert "CRITICAL" in res.stderr
