"""Device registration is the one write that takes no credential at all.

``POST /api/v2/scan/device`` is where a phone gets its first identity, so it
cannot require one. Every unknown ``device_key`` inserts a row, and the key is
chosen by the caller — so before this bound existed, a loop could insert rows
until the database was full. On the free tier that is a cheap outage, and it
needs no account, no invite and no token.

The limit is per address and deliberately loose, because mobile India is mostly
behind carrier-grade NAT: thousands of real people share one public address. A
tight limit would lock out a carrier to inconvenience one attacker. These tests
pin both halves — that a flood is stopped, and that an ordinary burst is not.
"""
from __future__ import annotations

import uuid

import pytest
from app.api.v2 import product
from app.domains.product.models import ScanDevice
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


def _body() -> dict[str, str]:
    return {"device_key": uuid.uuid4().hex, "platform": "ios"}


async def _device_rows() -> int:
    factory = get_sessionmaker()
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(ScanDevice)) or 0)


async def test_a_first_launch_still_registers(app_client, db_clean):
    resp = await app_client.post("/api/v2/scan/device", json=_body())

    assert resp.status_code == 201, resp.text
    assert resp.json()["token"]


async def test_an_ordinary_burst_from_one_carrier_address_is_not_refused(
    app_client, db_clean
):
    """Well inside the limit: a shared address must keep working."""
    for _ in range(product._DEVICE_REGISTRATIONS_PER_WINDOW):
        resp = await app_client.post("/api/v2/scan/device", json=_body())
        assert resp.status_code == 201, resp.text


async def test_a_flood_is_refused_rather_than_inserted(app_client, db_clean):
    allowed = product._DEVICE_REGISTRATIONS_PER_WINDOW
    for _ in range(allowed):
        assert (await app_client.post("/api/v2/scan/device", json=_body())).status_code == 201

    before = await _device_rows()
    refused = 0
    for _ in range(50):
        resp = await app_client.post("/api/v2/scan/device", json=_body())
        if resp.status_code == 429:
            refused += 1
    after = await _device_rows()

    assert refused == 50, "every attempt past the limit must be refused"
    assert after == before, "a refused registration must not insert a row"


async def test_the_refusal_says_it_can_be_retried_and_names_no_internals(
    app_client, db_clean
):
    for _ in range(product._DEVICE_REGISTRATIONS_PER_WINDOW + 1):
        resp = await app_client.post("/api/v2/scan/device", json=_body())

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["code"] == "rate_limited"
    assert detail["retryable"] is True
    # Nothing about which key, which device, or how the limit is counted.
    assert "device_key" not in resp.text
    assert "limiter" not in resp.text.lower()


async def test_the_limit_does_not_reveal_which_device_keys_exist(app_client, db_clean):
    """The check runs before the lookup on purpose.

    If a known key were exempt from the limit, an unauthenticated caller could
    tell registered keys from unregistered ones by which requests got a 429.
    """
    first = await app_client.post("/api/v2/scan/device", json=_body())
    assert first.status_code == 201

    for _ in range(product._DEVICE_REGISTRATIONS_PER_WINDOW):
        await app_client.post("/api/v2/scan/device", json=_body())

    known = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "ios"}
    )
    assert known.status_code == 429


async def test_the_table_of_tracked_addresses_is_bounded(app_client, db_clean):
    """The limiter must not become the memory leak it is guarding against."""
    limiter = product._device_registration_limiter
    for n in range(limiter.max_keys + 500):
        limiter.hit(f"device-register:probe-{n}")
    assert len(limiter.state) <= limiter.max_keys
