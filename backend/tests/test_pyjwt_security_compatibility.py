"""Exercise PyJWT's real parser, HTTP loader and parsed cache at our auth boundary."""
from __future__ import annotations

import base64
import io
import json
import logging
import sys
import time
import uuid

import jwt
import pytest
from app.shared.security import supabase_auth as auth
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

ISSUER = "https://test.supabase.co/auth/v1"


@pytest.fixture
def real_jwks(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    state = {"keys": [{**jwk, "kid": "known", "use": "sig", "alg": "RS256"}], "fetches": 0}

    class Endpoint:
        def open(self, request, timeout):
            assert request.full_url == ISSUER + "/jwks"
            state["fetches"] += 1
            return io.BytesIO(json.dumps({"keys": state["keys"]}).encode())

    # Only transport is replaced: real fetch_data/get_jwk_set/cache/parser run.
    monkeypatch.setattr(jwt.jwks_client.urllib.request, "build_opener", lambda *args: Endpoint())
    cache = auth._JWKSCache(ISSUER + "/jwks")
    monkeypatch.setattr(auth, "_get_jwks_cache", lambda: cache)
    monkeypatch.setattr(auth, "SUPABASE_JWT_ISSUER", ISSUER)
    return key, cache, state


def token(key, kid="known", algorithm="RS256", sub=None):
    return jwt.encode(
        {"sub": sub or str(uuid.uuid4()), "role": "authenticated", "aud": "authenticated",
         "iss": ISSUER, "exp": int(time.time()) + 300},
        key, algorithm=algorithm, headers={"kid": kid},
    )


@pytest.mark.asyncio
async def test_nested_payload_fails_closed_through_real_jwk_client(real_jwks, caplog):
    _, cache, state = real_jwks
    # Python 3.12's C JSON recursion bound differs from the Python frame limit.
    depth = max(3000, sys.getrecursionlimit() + 10)
    assert depth < 10000, "Keep the regression payload bounded"
    payload = b'{"nested":' + b"[" * depth + b"0" + b"]" * depth + b"}"
    with pytest.raises(RecursionError):
        json.loads(payload)
    header = json.dumps({"alg": "RS256", "kid": "known"}).encode()
    malformed = ".".join(base64.urlsafe_b64encode(part).rstrip(b"=").decode()
                         for part in (header, payload, b"invalid-signature"))
    app = FastAPI()
    identities = []

    @app.get("/identity")
    async def identity(user=Depends(auth.get_current_supabase_user)):
        identities.append(user.id)
        return {"id": str(user.id)}

    caplog.set_level(logging.INFO, logger=auth.__name__)
    # ASGITransport re-raises application exceptions, including raw recursion errors.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/identity", headers={"Authorization": f"Bearer {malformed}"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthenticated"
    assert identities == []
    assert state["fetches"] == 0  # Payload rejected before remote key lookup.
    assert cache._client is not None
    assert "reason=DecodeError" in caplog.text
    assert malformed not in caplog.text
    assert payload.decode() not in caplog.text


@pytest.mark.asyncio
async def test_real_parsed_cache_preserves_refresh_bound(real_jwks, monkeypatch):
    key, cache, state = real_jwks
    client = await cache._ensure_client()
    assert client.jwk_set_cache.get() is None
    assert cache._kid_is_absent(client, "known") is False
    assert cache._kid_is_absent(client, "unknown") is False
    await auth.verify_supabase_token(token(key))
    assert isinstance(client.jwk_set_cache.get(), jwt.PyJWKSet)
    assert cache._kid_is_absent(client, "known") is False
    assert cache._kid_is_absent(client, "unknown") is True
    assert state["fetches"] == 1
    await auth.verify_supabase_token(token(key))
    assert state["fetches"] == 1
    for index in range(25):
        with pytest.raises(auth.AuthError):
            await auth.verify_supabase_token(token(key, kid=f"unknown-{index}"))
    assert state["fetches"] == 2  # Exactly one permitted forced refresh.
    state["keys"].append({**state["keys"][0], "kid": "rotated"})
    with pytest.raises(auth.AuthError):
        await auth.verify_supabase_token(token(key, kid="rotated"))
    assert state["fetches"] == 2
    # Advance cache policy time, without sleeps or changing the production bound.
    monkeypatch.setattr(cache, "_last_forced_at", time.monotonic() - 61)
    assert (await auth.verify_supabase_token(token(key, kid="rotated")))["role"] == "authenticated"
    assert state["fetches"] == 3
    await auth.verify_supabase_token(token(key, kid="rotated"))
    assert state["fetches"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"])
async def test_valid_supported_asymmetric_token_preserves_identity(real_jwks, algorithm):
    key, _, state = real_jwks
    if algorithm.startswith("ES"):
        curve = {"ES256": ec.SECP256R1, "ES384": ec.SECP384R1, "ES512": ec.SECP521R1}[algorithm]
        key = ec.generate_private_key(curve())
        jwk = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    else:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    state["keys"] = [{**jwk, "kid": "known", "use": "sig", "alg": algorithm}]
    from fastapi.security import HTTPAuthorizationCredentials

    uid = uuid.uuid4()
    user = await auth.get_current_supabase_user(HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=token(key, algorithm=algorithm, sub=str(uid)),
    ))
    assert user.id == uid
    assert user.raw_claims["role"] == "authenticated"
    assert user.raw_claims["aud"] == "authenticated"
    assert user.raw_claims["iss"] == ISSUER
    assert user.raw_claims["exp"] > time.time()
