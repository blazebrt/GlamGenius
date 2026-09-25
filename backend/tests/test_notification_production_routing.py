"""Lane D, defect A — notification settings exist in the production router.

The four notification routes used to live inside the retired Today router,
which production does not mount. The suite never noticed, because a session
fixture mounts that router into the test app. So every test passed while the
real production composition answered 404.

Nothing here uses that test app. The app under test is built from exactly what
production mounts — ``app.api.v2.router`` — and a fresh interpreter imports the
real ``server`` module with no fixture in sight. The retired Today product
stays retired: ``GET /today`` is still absent from production.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from app.api.v2 import notification_settings
from app.api.v2 import router as production_v2_router
from app.domains.planning.models import NotificationDevice, NotificationPreference
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.handlers import register_error_handlers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import auth, refuse_retained_routes_in_legacy_routers

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_API = BACKEND_ROOT.parent / "frontend" / "src" / "services" / "apiV2.ts"

PRODUCTION_NOTIFICATION_ROUTES = {
    (method, f"/api/v2{path}") for method, path in notification_settings.NOTIFICATION_ROUTES
}
EXPECTED = {
    ("GET", "/api/v2/today/notifications"),
    ("PATCH", "/api/v2/today/notifications"),
    ("POST", "/api/v2/today/notifications/devices"),
    ("DELETE", "/api/v2/today/notifications/devices/{device_key}"),
}


def _routes(router, prefix: str = "") -> set[tuple[str, str]]:
    """Every (method, full path) under ``router``, through FastAPI's lazily included routers."""
    found: set[tuple[str, str]] = set()
    for route in router.routes:
        if hasattr(route, "original_router"):
            found |= _routes(route.original_router, prefix + route.include_context.prefix)
        else:
            found |= {(method, prefix + route.path) for method in (getattr(route, "methods", None) or ())}
    return found


def _production_app() -> FastAPI:
    """Exactly the production composition: the V2 router and the error handlers."""
    app = FastAPI()
    app.include_router(production_v2_router)
    register_error_handlers(app)
    return app


@pytest.fixture
async def production_client():
    transport = ASGITransport(app=_production_app())
    async with AsyncClient(transport=transport, base_url="http://production") as client:
        yield client


def _device_body(device_key: str, token: str | None = None) -> dict[str, str]:
    return {
        "device_key": device_key,
        "platform": "android",
        "expo_push_token": token or f"ExponentPushToken[{uuid.uuid4().hex}]",
    }


# ---------------------------------------------------------------------------
# The production router owns the four routes, and only production-ready ones
# ---------------------------------------------------------------------------
def test_the_production_router_owns_exactly_the_four_retained_notification_routes():
    assert PRODUCTION_NOTIFICATION_ROUTES == EXPECTED
    production = _routes(production_v2_router)
    assert production >= EXPECTED, EXPECTED - production
    assert {route for route in production if "/today/notifications" in route[1]} == EXPECTED


def test_the_retired_today_product_is_not_restored_to_production():
    production = _routes(production_v2_router)
    for retired in (
        ("GET", "/api/v2/today"),
        ("POST", "/api/v2/today/regenerate"),
        ("GET", "/api/v2/today/agenda"),
        ("POST", "/api/v2/today/events"),
    ):
        assert retired not in production, retired
    from app.api.v2 import today

    included = [getattr(route, "original_router", None) for route in production_v2_router.routes]
    assert today.router not in included
    assert notification_settings.router in included


def test_the_notification_routes_do_not_depend_on_the_retired_today_flag():
    """``v2_today`` gates only the retired Today engine, never notification settings."""
    source = (BACKEND_ROOT / "app" / "api" / "v2" / "notification_settings.py").read_text()
    assert "require_flag" not in source
    assert notification_settings.router.dependencies == []
    for route in notification_settings.router.routes:
        assert all("require_flag" not in repr(dep.call) for dep in route.dependant.dependencies)


def test_no_retired_router_carries_a_notification_route():
    """They cannot quietly move back into a router that production does not mount."""
    from app.api.v2 import onboarding, planner, progress, today

    for module in (today, onboarding, planner, progress):
        assert not {path for _m, path in _routes(module.router) if path.startswith("/today/notifications")}
        source = Path(module.__file__).read_text()
        assert '"/today/notifications' not in source, module.__name__
    init = (BACKEND_ROOT / "app" / "api" / "v2" / "__init__.py").read_text()
    assert "router.include_router(notification_settings.router" in init
    assert "today.router" not in init


def test_the_test_only_legacy_mount_refuses_a_router_that_hides_a_production_route():
    """The conftest fixture cannot be the thing that masks a missing route again."""
    from fastapi import APIRouter

    hiding = APIRouter()

    @hiding.get("/today/notifications")
    async def hidden():  # pragma: no cover - never served
        return {}

    with pytest.raises(RuntimeError, match="production notification route"):
        refuse_retained_routes_in_legacy_routers((hiding,))
    refuse_retained_routes_in_legacy_routers((APIRouter(),))


def test_the_real_server_module_serves_them_with_no_test_mounting():
    """A fresh interpreter, the real ``server.app``, no conftest, real HTTP.

    Unauthenticated on purpose: a route that exists answers at its
    authentication boundary (401); a route that does not exist answers 404.
    """
    probe = (
        "import asyncio, json, server\n"
        "from httpx import ASGITransport, AsyncClient\n"
        "async def main():\n"
        "    async with AsyncClient(transport=ASGITransport(app=server.app), base_url='http://p') as c:\n"
        "        out = {}\n"
        "        for method, path, body in [\n"
        "            ('GET', '/api/v2/today/notifications', None),\n"
        "            ('PATCH', '/api/v2/today/notifications', {'daily_cap': 1}),\n"
        "            ('POST', '/api/v2/today/notifications/devices',\n"
        "             {'device_key': 'k', 'platform': 'android', 'expo_push_token': 't'}),\n"
        "            ('DELETE', '/api/v2/today/notifications/devices/k', None),\n"
        "            ('GET', '/api/v2/today', None),\n"
        "            ('GET', '/api/v2/today/agenda', None),\n"
        "        ]:\n"
        "            out[f'{method} {path}'] = (await c.request(method, path, json=body)).status_code\n"
        "        print(json.dumps(out))\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=BACKEND_ROOT, env=dict(os.environ),
        capture_output=True, text=True, check=False, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    served = json.loads(result.stdout.strip().splitlines()[-1])
    assert served == {
        "GET /api/v2/today/notifications": 401,
        "PATCH /api/v2/today/notifications": 401,
        "POST /api/v2/today/notifications/devices": 401,
        "DELETE /api/v2/today/notifications/devices/k": 401,
        "GET /api/v2/today": 404,
        "GET /api/v2/today/agenda": 404,
    }, served


def test_the_app_calls_exactly_the_production_notification_routes():
    """The installed app's URLs, and logout's, are the ones production serves."""
    source = FRONTEND_API.read_text()
    called = {
        (method.upper(), path)
        for method, path in re.findall(r"api\.(get|patch|post)\(`\$\{V2\}(/today/notifications[^`]*)`", source)
    }
    assert re.search(r"^export const V2 = '/api/v2';$", source, re.MULTILINE)
    # Removal goes through the one shared path, which logout uses too.
    assert "api.delete(notificationDevicePath(deviceKey))" in source
    routes = (FRONTEND_API.parent / "notificationRoutes.ts").read_text()
    [devices] = re.findall(r"NOTIFICATION_DEVICES_PATH = '([^']+)'", routes)
    assert "`${NOTIFICATION_DEVICES_PATH}/${encodeURIComponent(deviceKey)}`" in routes
    called.add(("DELETE", devices.removeprefix("/api/v2") + "/{device_key}"))
    cleanup = (FRONTEND_API.parent / "logoutDeviceCleanup.ts").read_text()
    assert "notificationDevicePath(deviceKey)" in cleanup
    assert called == {(method, path.removeprefix("/api/v2")) for method, path in EXPECTED}


# ---------------------------------------------------------------------------
# Through the production composition, over HTTP
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/api/v2/today/notifications", None),
    ("PATCH", "/api/v2/today/notifications", {"daily_cap": 1}),
    ("POST", "/api/v2/today/notifications/devices", _device_body("install-anon")),
    ("DELETE", "/api/v2/today/notifications/devices/install-anon", None),
])
async def test_an_unauthenticated_request_reaches_the_authentication_boundary_not_a_404(
    production_client, method, path, body,
):
    response = await production_client.request(method, path, json=body)
    assert response.status_code == 401, (response.status_code, response.text)


@pytest.mark.asyncio
async def test_get_patch_register_and_unregister_work_through_production_composition(
    db_clean, production_client, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    headers = auth(token)

    read = await production_client.get("/api/v2/today/notifications", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["current_device_registered"] is False

    registered = await production_client.post(
        "/api/v2/today/notifications/devices", headers=headers, json=_device_body("install-prod"),
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["native_push_enabled"] is True

    patched = await production_client.patch(
        "/api/v2/today/notifications", headers=headers, json={"preferred_hour": 8, "topics": {"care": False}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["preferences"]["preferred_hour"] == 8
    assert patched.json()["preferences"]["topics"]["care"] is False

    current = await production_client.get(
        "/api/v2/today/notifications", headers=headers, params={"device_key": "install-prod"},
    )
    assert current.json()["current_device_registered"] is True

    removed = await production_client.delete("/api/v2/today/notifications/devices/install-prod", headers=headers)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {
        "device_key": "install-prod", "removed": True, "active_devices_remaining": False,
        "native_push_enabled": False, "current_device_registered": False,
    }
    missing = await production_client.delete("/api/v2/today/notifications/devices/install-prod", headers=headers)
    assert missing.status_code == 404
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(NotificationDevice).where(
            NotificationDevice.account_id == account_id,
        ))).scalars().all() == []


@pytest.mark.asyncio
async def test_notification_settings_stay_available_with_the_today_flag_off(
    db_clean, production_client, registered_supabase_user, monkeypatch,
):
    features = [part for part in os.environ["V2_FEATURES"].split(",") if part != "v2_today"]
    monkeypatch.setenv("V2_FEATURES", ",".join(features))
    token, _ = await registered_supabase_user()
    response = await production_client.get("/api/v2/today/notifications", headers=auth(token))
    assert response.status_code == 200, response.text
    registered = await production_client.post(
        "/api/v2/today/notifications/devices", headers=auth(token), json=_device_body("install-flag"),
    )
    assert registered.status_code == 200, registered.text


# ---------------------------------------------------------------------------
# Logout cleanup's server authority: account plus exact device key
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_late_cleanup_by_a_signed_out_account_cannot_touch_the_next_account_on_the_phone(
    db_clean, production_client, registered_supabase_user,
):
    """A→B on one installation: A's delayed DELETE removes A's row only.

    The phone keeps one installation key and one Expo token. A registers, logs
    out, and before A's cleanup reaches the server B signs in and registers the
    same installation. A's late DELETE carries A's own bearer token, and the
    route acts on (A's account, this device key) and nothing else.
    """
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    push_token = f"ExponentPushToken[{uuid.uuid4().hex}]"
    key = "install-shared-phone"

    assert (await production_client.post(
        "/api/v2/today/notifications/devices", headers=auth(token_a), json=_device_body(key, push_token),
    )).status_code == 200
    assert (await production_client.post(
        "/api/v2/today/notifications/devices", headers=auth(token_b), json=_device_body(key, push_token),
    )).status_code == 200

    late = await production_client.delete(f"/api/v2/today/notifications/devices/{key}", headers=auth(token_a))
    assert late.status_code == 200, late.text
    again = await production_client.delete(f"/api/v2/today/notifications/devices/{key}", headers=auth(token_a))
    assert again.status_code == 404

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(NotificationDevice).where(
            NotificationDevice.device_key == key,
        ))).scalars().all()
        assert [(row.account_id, row.status) for row in rows] == [(account_b, "active")]
        preference_b = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_b,
        ))).scalar_one()
        preference_a = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_a,
        ))).scalar_one()
    assert preference_b.native_push_enabled is True
    assert preference_a.native_push_enabled is False, "A's last device is gone, as the route has always done"
    current_b = await production_client.get(
        "/api/v2/today/notifications", headers=auth(token_b), params={"device_key": key},
    )
    assert current_b.json()["current_device_registered"] is True
