"""Supabase JWT verification tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient

from app.shared.security import supabase_auth


def _issue_hs256(
    *,
    secret: str = "test-secret",
    sub: str = "not-a-uuid",
    iss: str = "https://test.supabase.co/auth/v1",
    exp_delta_seconds: int = 3600,
    role: str = "authenticated",
    aud: str = "authenticated",
    extra: dict | None = None,
) -> str:
    payload = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "role": role,
        "iat": int((datetime.now(timezone.utc)).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def probe_app(monkeypatch) -> FastAPI:
    """Minimal FastAPI app exposing a single dependency-guarded route."""
    monkeypatch.setattr(supabase_auth, "SUPABASE_JWT_SECRET", "test-secret")
    monkeypatch.setattr(
        supabase_auth, "SUPABASE_JWT_ISSUER", "https://test.supabase.co/auth/v1"
    )
    monkeypatch.setattr(supabase_auth, "SUPABASE_JWKS_URL", "")

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user: supabase_auth.SupabaseUser = Depends(supabase_auth.get_current_supabase_user)):
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
async def test_valid_hs256_token_returns_user(probe_app):
    uid = uuid.uuid4()
    token = _issue_hs256(sub=str(uid))
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(uid)


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_hs256(secret="wrong-secret", sub=str(uid))
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_issuer_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_hs256(sub=str(uid), iss="https://evil.example.com/auth/v1")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_returns_401(probe_app):
    uid = uuid.uuid4()
    token = _issue_hs256(sub=str(uid), exp_delta_seconds=-60)
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_uuid_sub_returns_401(probe_app):
    token = _issue_hs256(sub="not-a-uuid")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_service_role_token_rejected(probe_app):
    uid = uuid.uuid4()
    token = _issue_hs256(sub=str(uid), role="service_role")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_anon_role_token_rejected(probe_app):
    uid = uuid.uuid4()
    token = _issue_hs256(sub=str(uid), role="anon")
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_marker_from_env(probe_app, monkeypatch):
    uid = uuid.uuid4()
    monkeypatch.setattr(
        supabase_auth, "SUPABASE_ADMIN_USER_IDS", {str(uid).lower()}
    )
    token = _issue_hs256(sub=str(uid))
    resp = await _get(probe_app, {"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_malformed_token_returns_401(probe_app):
    resp = await _get(probe_app, {"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
