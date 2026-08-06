"""Supabase JWT authentication for FastAPI.

This module is the **only** authentication gate for V2 routes. Identity comes
from a verified Supabase Auth access token supplied in the ``Authorization``
header. Nothing else — never a URL parameter, never a request body field,
never a cached V1 token.

## What we verify

1. Signature — against the Supabase JWKS (asymmetric keys, the current
   Supabase default).
2. Issuer — must equal ``SUPABASE_JWT_ISSUER``.
3. Expiry — the ``exp`` claim must be in the future.
4. Required claims — ``sub`` must be a valid UUID.
5. ``role`` when present — anonymous keys and service-role keys are rejected;
   only ``authenticated`` and ``aud=authenticated`` are allowed.

## Fail closed

Any failure to complete validation — network error fetching JWKS, expired
signing keys, malformed token — returns 401 with a consistent structured
body. The token is never logged.

## JWKS caching

JWKS is fetched on the first verification that needs it and cached in
memory for up to five minutes. A cache miss on a fresh ``kid`` triggers a
single re-fetch; if the re-fetch also fails to produce a matching key the
request is rejected. The cache is per-process and thread-safe.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from app.config import (
    SUPABASE_ADMIN_USER_IDS,
    SUPABASE_JWKS_URL,
    SUPABASE_JWT_ISSUER,
)

logger = logging.getLogger(__name__)


class AuthError(HTTPException):
    """Consistent 401 body for every authentication failure.

    Attackers see the same shape whether the token is missing, malformed,
    unsigned or expired — no oracle for what part of the check failed.
    """

    def __init__(self, code: str = "unauthenticated", detail: str = "Not authenticated") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": code, "message": detail},
            headers={"WWW-Authenticate": "Bearer"},
        )


@dataclass(frozen=True)
class SupabaseUser:
    """The authenticated caller.

    ``id`` is the canonical account identifier throughout V2 — it **is** the
    Supabase Auth user UUID. There is no separate ``account_id``.
    """

    id: uuid.UUID
    email: str | None
    is_admin: bool
    raw_claims: dict[str, Any]

    @property
    def id_str(self) -> str:
        return str(self.id)


_JWKS_TTL_SECONDS = 300


class _JWKSCache:
    """PyJWKClient with a bounded refresh policy.

    PyJWKClient itself caches, but has no TTL and no way to force a refresh
    when a new kid appears. We wrap it: keep one client per JWKS URL, rebuild
    it after ``_JWKS_TTL_SECONDS`` or on demand.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: PyJWKClient | None = None
        self._built_at: float = 0.0
        self._lock = asyncio.Lock()

    async def signing_key(self, token: str) -> Any:
        """Return the signing key for ``token``. Refreshes JWKS once if needed."""
        client = await self._ensure_client()
        try:
            return await asyncio.to_thread(client.get_signing_key_from_jwt, token)
        except PyJWKClientError:
            # Unknown kid — the project may have rotated keys. Force one refresh.
            client = await self._ensure_client(force=True)
            return await asyncio.to_thread(client.get_signing_key_from_jwt, token)

    async def _ensure_client(self, *, force: bool = False) -> PyJWKClient:
        async with self._lock:
            fresh = (
                self._client is not None
                and not force
                and (time.monotonic() - self._built_at) < _JWKS_TTL_SECONDS
            )
            if not fresh:
                # PyJWKClient uses urllib under the hood which is synchronous —
                # confine that to a worker thread rather than the event loop.
                def _build() -> PyJWKClient:
                    return PyJWKClient(self._url, cache_keys=True)

                self._client = await asyncio.to_thread(_build)
                self._built_at = time.monotonic()
            assert self._client is not None
            return self._client


_jwks_cache: _JWKSCache | None = None


def _get_jwks_cache() -> _JWKSCache | None:
    global _jwks_cache
    if not SUPABASE_JWKS_URL:
        return None
    if _jwks_cache is None:
        _jwks_cache = _JWKSCache(SUPABASE_JWKS_URL)
    return _jwks_cache


async def _decode_with_jwks(token: str, unverified_header: dict[str, Any]) -> dict[str, Any]:
    cache = _get_jwks_cache()
    if cache is None:
        raise InvalidTokenError("JWKS not configured")

    signing_key = await cache.signing_key(token)
    alg = unverified_header.get("alg") or "RS256"
    if alg not in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512"):
        raise InvalidTokenError(f"Unexpected asymmetric algorithm: {alg}")

    return jwt.decode(
        token,
        signing_key.key,
        algorithms=[alg],
        issuer=SUPABASE_JWT_ISSUER or None,
        audience="authenticated",
        options={
            "require": ["exp", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iss": bool(SUPABASE_JWT_ISSUER),
            "verify_aud": True,
        },
    )


async def verify_supabase_token(token: str) -> dict[str, Any]:
    """Decode and validate a Supabase access token. Raises AuthError on failure.

    Order: try JWKS (asymmetric — current Supabase default). Any other
    failure returns 401.
    """
    if not token:
        raise AuthError()

    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        logger.info("supabase_jwt_malformed reason=%s", type(exc).__name__)
        raise AuthError()

    alg = (header.get("alg") or "").upper()

    if alg.startswith("RS") or alg.startswith("ES") or alg.startswith("PS"):
        try:
            return await _decode_with_jwks(token, header)
        except InvalidTokenError as exc:
            logger.info("supabase_jwt_reject_jwks reason=%s", type(exc).__name__)
            raise AuthError()
        except PyJWKClientError as exc:
            logger.warning("supabase_jwks_unavailable reason=%s", type(exc).__name__)
            raise AuthError(code="jwks_unavailable", detail="Authentication is temporarily unavailable")
        except httpx.HTTPError as exc:  # pragma: no cover - network-level
            logger.warning("supabase_jwks_network_error reason=%s", type(exc).__name__)
            raise AuthError(code="jwks_unavailable", detail="Authentication is temporarily unavailable")

    # Unknown algorithm or no verification path available. Refuse.
    logger.info("supabase_jwt_reject_alg alg=%s", alg or "<missing>")
    raise AuthError()


def _extract_sub_uuid(claims: dict[str, Any]) -> uuid.UUID:
    raw_sub = claims.get("sub")
    if not raw_sub or not isinstance(raw_sub, str):
        raise AuthError()
    try:
        return uuid.UUID(raw_sub)
    except (TypeError, ValueError):
        raise AuthError()


def _require_authenticated_role(claims: dict[str, Any]) -> None:
    """Positively require ``role == "authenticated"``.

    Supabase issues three role families on JWTs: ``anon`` (project-level API
    key, never a user), ``service_role`` (server-side privileged key, never a
    user), and ``authenticated`` (a real user session). Only the last one is
    accepted here. A token that carries any other role — including a missing
    role, which never happens for a genuine Supabase user session — is
    rejected.
    """
    role = claims.get("role")
    if role != "authenticated":
        raise AuthError()


_bearer = HTTPBearer(auto_error=False)


async def get_current_supabase_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> SupabaseUser:
    """FastAPI dependency: resolve the caller from a Supabase access token."""
    if credentials is None or not credentials.credentials:
        raise AuthError()

    claims = await verify_supabase_token(credentials.credentials)
    _require_authenticated_role(claims)

    user_id = _extract_sub_uuid(claims)
    email_raw = claims.get("email")
    email = email_raw if isinstance(email_raw, str) and email_raw else None
    is_admin = str(user_id).lower() in SUPABASE_ADMIN_USER_IDS

    return SupabaseUser(
        id=user_id,
        email=email,
        is_admin=is_admin,
        raw_claims=claims,
    )


async def get_current_admin(
    user: SupabaseUser = Depends(get_current_supabase_user),
) -> SupabaseUser:
    """Dependency for admin-only endpoints. 404 not 403 — an admin surface
    exists only for admins."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return user


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


__all__ = [
    "AuthError",
    "SupabaseUser",
    "client_ip",
    "get_current_admin",
    "get_current_supabase_user",
    "verify_supabase_token",
]
