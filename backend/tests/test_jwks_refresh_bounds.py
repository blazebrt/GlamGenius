"""A made-up ``kid`` must not send us to the network.

``kid`` is a token *header* field. It is read before any signature has been
checked, so its value is chosen by whoever sent the request — including
somebody with no credentials at all. The JWKS cache used it to decide when to
re-fetch the key set, which made every unauthenticated request worth three
outbound requests to Supabase:

    25 tokens with invented kids -> 75 JWKS fetches  (3.00 per request)

One of the three came from PyJWKClient itself, whose ``get_signing_key``
re-fetches on a miss unconditionally; the other two from rebuilding the client
around it, which starts with a cold cache and then does the same thing again.

Enough of that and Supabase throttles or blocks the endpoint, at which point
nobody can log in at all — an availability failure reachable by anyone who can
send an HTTP request. The same requests also each occupy a worker thread for up
to the client's 30-second timeout.

These tests pin the bound. They exercise :class:`_JWKSCache` directly rather
than through the route, because what is being tested is how often it decides to
go to the network — which is invisible from the outside, and is exactly why the
behaviour survived this long.
"""
from __future__ import annotations

import base64
import time
import uuid

import jwt
import pytest
from app.shared.security import supabase_auth
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

pytestmark = pytest.mark.asyncio

_JWKS_URL = "https://test.supabase.co/auth/v1/.well-known/jwks.json"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_numbers = _key.public_key().public_numbers()


def _b64u(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(kid: str) -> dict:
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64u(_numbers.n),
        "e": _b64u(_numbers.e),
    }


def _token(kid: str) -> str:
    return jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": int(time.time()) + 600,
            "aud": "authenticated",
            "role": "authenticated",
        },
        _key,
        algorithm="RS256",
        headers={"kid": kid},
    )


class _CountingEndpoint:
    """Stands in for the JWKS endpoint and counts how often it is asked.

    Writes through to the client's own cache exactly as the real
    ``fetch_data`` does. Skipping that would disable PyJWKClient's caching and
    make every measurement here meaningless — a fetch count is only evidence if
    the caching under test is still running.
    """

    def __init__(self, kids: list[str]) -> None:
        self.kids = list(kids)
        self.fetches = 0

    def install(self, monkeypatch) -> None:
        endpoint = self

        def fetch_data(client_self):
            endpoint.fetches += 1
            payload = {"keys": [_jwk(kid) for kid in endpoint.kids]}
            if client_self.jwk_set_cache is not None:
                client_self.jwk_set_cache.put(payload)
            return payload

        monkeypatch.setattr(PyJWKClient, "fetch_data", fetch_data)


@pytest.fixture
def endpoint(monkeypatch) -> _CountingEndpoint:
    served = _CountingEndpoint(["genuine-key-1"])
    served.install(monkeypatch)
    return served


@pytest.fixture
def cache() -> supabase_auth._JWKSCache:
    return supabase_auth._JWKSCache(_JWKS_URL)


async def test_a_known_kid_is_served_from_cache(cache, endpoint):
    """The ordinary case: one fetch to warm up, none afterwards."""
    await cache.signing_key(_token("genuine-key-1"))
    assert endpoint.fetches == 1

    for _ in range(10):
        await cache.signing_key(_token("genuine-key-1"))
    assert endpoint.fetches == 1


async def test_invented_kids_cannot_drive_repeated_fetches(cache, endpoint):
    """The finding. Twenty-five invented kids used to cost seventy-five fetches."""
    await cache.signing_key(_token("genuine-key-1"))
    baseline = endpoint.fetches

    for index in range(25):
        with pytest.raises(PyJWKClientError):
            await cache.signing_key(_token(f"made-up-{index}"))

    caused = endpoint.fetches - baseline
    assert caused <= 1, (
        f"{caused} outbound JWKS fetches for 25 unauthenticated requests; "
        "an invented kid must not be able to drive traffic to Supabase"
    )


async def test_an_invented_kid_does_not_cost_real_callers_a_fetch(cache, endpoint):
    """The second half of the damage: the warm cache used to be thrown away."""
    await cache.signing_key(_token("genuine-key-1"))

    with pytest.raises(PyJWKClientError):
        await cache.signing_key(_token("made-up"))

    before = endpoint.fetches
    await cache.signing_key(_token("genuine-key-1"))
    assert endpoint.fetches == before


async def test_a_genuine_key_rotation_is_still_picked_up(cache, endpoint):
    """The bound must not break the thing the refresh exists for."""
    await cache.signing_key(_token("genuine-key-1"))

    # The project rotates: the endpoint now serves a new key id.
    endpoint.kids = ["genuine-key-1", "genuine-key-2"]

    key = await cache.signing_key(_token("genuine-key-2"))
    assert key is not None


async def test_rotation_is_picked_up_however_long_the_process_has_been_up(
    cache, endpoint,
):
    """The first forced refresh must not depend on the machine's uptime.

    ``time.monotonic()`` counts from an arbitrary origin, so a zero-initialised
    "last refreshed at" would put a fresh process either inside or outside the
    cooldown depending only on how long the host had been running — a rotation
    picked up on one box and not another.
    """
    assert cache._last_forced_at == float("-inf")

    await cache.signing_key(_token("genuine-key-1"))
    endpoint.kids = ["genuine-key-2"]
    assert await cache.signing_key(_token("genuine-key-2")) is not None


async def test_a_second_rotation_inside_the_cooldown_waits(cache, endpoint, monkeypatch):
    """States the cost of the bound honestly, rather than implying there is none.

    A rotation that lands within the cooldown of a previous forced refresh is
    not seen until the cooldown passes. That is the trade: a minute of delay on
    an event that happens rarely, against an unauthenticated request being able
    to reach the network at will.
    """
    await cache.signing_key(_token("genuine-key-1"))

    # Burn the one forced refresh on an invented kid.
    with pytest.raises(PyJWKClientError):
        await cache.signing_key(_token("made-up"))

    endpoint.kids = ["genuine-key-2"]
    with pytest.raises(PyJWKClientError):
        await cache.signing_key(_token("genuine-key-2"))

    # Once the cooldown has passed, the rotation is picked up.
    monkeypatch.setattr(
        supabase_auth, "_JWKS_FORCED_REFRESH_COOLDOWN_SECONDS", 0,
    )
    assert await cache.signing_key(_token("genuine-key-2")) is not None


async def test_a_token_with_no_kid_is_refused_without_a_fetch(cache, endpoint):
    """Nothing to look up, so nothing to go and ask about."""
    await cache.signing_key(_token("genuine-key-1"))
    before = endpoint.fetches

    unsigned_header = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": int(time.time()) + 600,
            "aud": "authenticated",
            "role": "authenticated",
        },
        _key,
        algorithm="RS256",
    )
    with pytest.raises(PyJWKClientError):
        await cache.signing_key(unsigned_header)

    assert endpoint.fetches - before <= 1


async def test_concurrent_invented_kids_do_not_multiply_fetches(cache, endpoint):
    """A burst is the realistic shape of this, not a serial loop."""
    import asyncio

    await cache.signing_key(_token("genuine-key-1"))
    baseline = endpoint.fetches

    async def attempt(index: int) -> None:
        try:
            await cache.signing_key(_token(f"burst-{index}"))
        except PyJWKClientError:
            pass

    await asyncio.gather(*(attempt(i) for i in range(30)))

    caused = endpoint.fetches - baseline
    assert caused <= 2, f"{caused} fetches from a 30-request burst"
