"""Supabase JWT verification tests (Asymmetric only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient

from app.shared.security import supabase_auth

# Generate a temporary RSA keypair for testing
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_public_pem = _private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _issue_rs256(
    *,
    sub: str = "not-a-uuid",
    iss: str = "https://test.supabase.co/auth/v1",
    exp_delta_seconds: int = 3600,
    role: str = "authenticated",
    aud: str = "authenticated",
    kid: str = "test-kid",
    extra: dict | None = None,
) -> str:
    payload = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "role": role,
        "iat": int((datetime.now(timezone.utc)).timestamp()),
        "exp": int(
            (datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)).timestamp()
        ),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(
        payload,
        _private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


class MockSigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture
def mock_jwks(monkeypatch):
    def mock_get_signing_key(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != "test-kid":
            raise jwt.exceptions.PyJWKClientError("kid not found")
        return MockSigningKey(_public_pem)

    monkeypatch.setattr("jwt.PyJWKClient.get_signing_key_from_jwt", mock_get_signing_key)


@pytest.fixture
def probe_app(monkeypatch, mock_jwks) -> FastAPI:
    """Minimal FastAPI app exposing a single dependency-guarded route."""
    monkeypatch.setattr(
        supabase_auth, "SUPABASE_JWT_ISSUER", "https://test.supabase.co/auth/v1"
    )
    monkeypatch.setattr(supabase_auth, "SUPABASE_JWKS_URL", "https://test.supabase.co/auth/v1/.well-known/jwks.json")

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(
        user: supabase_auth.SupabaseUser = Depends(supabase_auth.get_current_supabase_user),
    ):
        return {"id": str(user.id), "email": user.email, "is_admin": user.is_admin}

    return app


async def _get(app: FastAPI, headers: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/whoami", headers=headers or {})


@pytest.mark.asyncio
async def test_missing_token_returns_401(probe_app):
    resp = await _get(probe_app)
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_valid_rs256_token_returns_user(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid))
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(uid)


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(probe_app):
    # Tamper with the payload of a valid token
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid))
    parts = token.split(".")
    parts[1] = "eyJzdWIiOiAibmV3LXN1YiJ9"  # fake payload
    tampered = ".".join(parts)
    resp = await _get(probe_app, {"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_issuer_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid), iss="https://evil.example.com/auth/v1")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid), aud="wrong")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid), exp_delta_seconds=-60)
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_uuid_sub_returns_401(probe_app):
    token = _issue_rs256(sub="not-a-uuid")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_service_role_token_rejected(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid), role="service_role")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_anon_role_token_rejected(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid), role="anon")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_marker_from_env(probe_app, monkeypatch):
    uid = uuid.uuid4()
    monkeypatch.setattr(supabase_auth, "SUPABASE_ADMIN_USER_IDS", {str(uid).lower()})
    token = _issue_rs256(sub=str(uid))
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_malformed_token_returns_401(probe_app):
    resp = await _get(probe_app, {"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_hs256_rejection(probe_app):
    # Ensure HS256 tokens are hard rejected
    payload = {
        "sub": str(uuid.uuid4()),
        "iss": "https://test.supabase.co/auth/v1",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int((datetime.now(timezone.utc) + timedelta(seconds=3600)).timestamp()),
    }
    hs256_token = jwt.encode(payload, "secret", algorithm="HS256")
    resp = await _get(probe_app, {"Authorization": f"Bearer {hs256_token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_kid_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid), kid="unknown-kid")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jwks_failure_returns_401(probe_app, monkeypatch):
    def mock_get_signing_key_err(self, token):
        raise jwt.exceptions.PyJWKClientError("Network error")
    monkeypatch.setattr("jwt.PyJWKClient.get_signing_key_from_jwt", mock_get_signing_key_err)
    
    uid = uuid.uuid4()
    token = _issue_rs256(sub=str(uid))
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "jwks_unavailable"
