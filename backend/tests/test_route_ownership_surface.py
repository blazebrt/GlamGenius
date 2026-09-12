"""Every addressable route, swept for the two mistakes that are easy to make.

Ownership is checked route by route elsewhere — ``test_domain_media.py``,
``test_domain_inventory.py``, ``test_v2_api.py`` and others each prove that one
account cannot reach another's rows. What none of them checks is the *surface*:
that the next route somebody adds with an id in its address is covered too.

This file walks the published OpenAPI schema instead of a hand-written list, so
a new route joins the sweep the day it is mounted, and asserts two things that
hold for every one of them:

1. **An anonymous caller gets nothing.** A route with an id in its address is
   addressing somebody's row. Forgetting the auth dependency is a one-line
   mistake and an invisible one — the route works perfectly in manual testing,
   because the developer is signed in. Only ``PUBLIC_BY_ID_ROUTES`` below may
   answer a stranger, and each entry there is a deliberate decision.

2. **No id produces a server error.** A 500 on an unknown or malformed id means
   an unhandled path: at best a stack trace in the logs, at worst a detail in
   the response body. Not-found is the correct answer, and it is also the
   answer that tells an attacker nothing.

The sweep uses ids that belong to nobody, so it proves the shape of the surface
rather than the ownership rules themselves. Both matter; this is the half that
no single route's test can cover.
"""
from __future__ import annotations

import re
import uuid

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


# By-id routes that may answer a caller with no credentials at all. Empty, and
# worth keeping that way: every route with an id in its address is addressing
# somebody's row, even the barcode ones — ``/community/observations/context``
# reads what *this device* last scanned, so it takes a device token like the
# rest. An entry here is a deliberate decision that a route is public.
PUBLIC_BY_ID_ROUTES: frozenset[tuple[str, str]] = frozenset()


def _placeholder(name: str) -> str:
    """A value of the right shape that belongs to nobody."""
    if "date" in name or name in {"done_on", "old_date", "plan_date"}:
        return "2026-01-01"
    if name == "barcode":
        return "8901234567890"
    if name in {"kind_key", "key", "category", "ingredient_key"}:
        return "a-key-that-is-not-registered"
    return str(uuid.uuid4())


def _addressable_routes(app) -> list[tuple[str, str, str]]:
    """(method, template, concrete url) for every route with a path parameter."""
    routes: list[tuple[str, str, str]] = []
    for template, operations in app.openapi()["paths"].items():
        names = re.findall(r"\{(\w+)\}", template)
        if not names:
            continue
        url = template
        for name in names:
            url = url.replace("{" + name + "}", _placeholder(name))
        for method in sorted(operations):
            if method.upper() in {"HEAD", "OPTIONS"}:
                continue
            routes.append((method.upper(), template, url))
    return routes


@pytest.fixture
def addressable_routes():
    """The routes to sweep, and a clean connection pool afterwards.

    Sweeping every route touches most tables in the schema. Whatever the pool
    is holding when the sweep ends, the next test's ``db_clean`` has to take an
    ACCESS EXCLUSIVE lock on all of them to TRUNCATE — and a pooled connection
    still inside a transaction deadlocks against that. Disposing the pool on
    the way out returns every connection and releases every lock, so this file
    cannot make an unrelated test fail.
    """
    from server import app

    routes = _addressable_routes(app)
    assert len(routes) > 50, "the sweep found almost no routes; the schema walk is broken"
    return routes


async def test_no_addressable_route_answers_an_anonymous_caller(
    app_client, db_clean, addressable_routes
):
    answered = []
    for method, template, url in addressable_routes:
        if (method, template) in PUBLIC_BY_ID_ROUTES:
            continue
        resp = await app_client.request(method, url, json={})
        if resp.status_code < 400:
            answered.append((method, template, resp.status_code))
    assert not answered, (
        "these routes address a row by id and answered a caller with no "
        f"credentials: {answered}"
    )


async def test_no_addressable_route_returns_a_server_error_for_an_unknown_id(
    app_client, db_clean, registered_supabase_user, addressable_routes
):
    token, _ = await registered_supabase_user()
    broke = []
    for method, template, url in addressable_routes:
        resp = await app_client.request(method, url, headers=auth(token), json={})
        if resp.status_code >= 500:
            broke.append((method, template, resp.status_code))
    assert not broke, f"an id belonging to nobody produced a server error: {broke}"


async def test_a_malformed_id_is_refused_rather_than_crashing(
    app_client, db_clean, registered_supabase_user, addressable_routes
):
    """The same sweep with ids that are not even the right shape."""
    token, _ = await registered_supabase_user()
    broke = []
    for method, template, _ in addressable_routes:
        url = re.sub(r"\{(\w+)\}", "not-an-id", template)
        resp = await app_client.request(method, url, headers=auth(token), json={})
        if resp.status_code >= 500:
            broke.append((method, template, resp.status_code))
    assert not broke, f"a malformed id produced a server error: {broke}"
