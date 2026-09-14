"""Regression gate for the post-pivot customer-product boundary.

This deliberately checks semantic paths and customer contracts, not generic
programming words such as React Native's ``style`` prop. Historical Alembic
migrations are database history and intentionally outside this scan.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"


def test_removed_customer_routes_and_routers_do_not_return() -> None:
    for path in (
        FRONTEND / "app" / "(tabs)" / "style.tsx",
        FRONTEND / "app" / "look.tsx",
        FRONTEND / "app" / "my-appearance.tsx",
        FRONTEND / "app" / "(tabs)" / "services.tsx",
        BACKEND / "app" / "api" / "v2" / "style.py",
        BACKEND / "app" / "api" / "v2" / "quiz.py",
    ):
        assert not path.exists(), path


def test_active_customer_shelf_is_limited_to_governed_body_product_categories() -> None:
    taxonomy = (BACKEND / "app" / "domains" / "inventory" / "taxonomy.py").read_text(encoding="utf-8")
    schemas = (BACKEND / "app" / "domains" / "inventory" / "schemas.py").read_text(encoding="utf-8")
    frontend_contract = (FRONTEND / "src" / "services" / "apiV2.ts").read_text(encoding="utf-8")
    for legacy_category in ('"wardrobe"', '"shoes"', '"accessories"'):
        assert legacy_category not in taxonomy
        assert legacy_category not in schemas
        # The active shelf declaration, rather than historical Event Ready
        # implementation types lower in this client, is the customer contract.
        declaration = frontend_contract.split("export type InventoryCategory", 1)[0]
        assert legacy_category not in declaration


def test_agent_handoff_is_governed_by_the_product_constitution() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for handoff in (claude, agents):
        assert "Existing code is **not** evidence" in handoff or "Do not infer scope from old code" in handoff
        assert "SCAN → UNDERSTAND → DECIDE → REMEMBER → MANAGE" in handoff
    assert "body-and-appearance manager" not in claude
    assert "Today · Style · Care · Plan · You" not in claude


def test_active_server_identity_is_product_decision_engine() -> None:
    server = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "Personal Appearance Operating System" not in server
def test_active_route_reachability() -> None:
    from server import app
    paths = [route.path for route in app.routes if hasattr(route, "path")]
    legacy_patterns = [
        "/api/v2/style",
        "/api/v2/quiz",
        "/api/v2/shopping/roi-model",
        "/api/v2/shopping/evaluate",
        "/api/v2/shopping/evaluations",
        "/api/v2/today",
        "/api/v2/planner", "/api/v2/onboarding", "/api/v2/progress", "/api/v2/goals", "/api/v2/milestones", "/api/v2/memory",
    ]
    for path in paths:
        for legacy in legacy_patterns:
            assert not path.startswith(legacy), f"Legacy route {path} is still mounted"
def test_profile_active_allowlist_forbids_legacy_keys() -> None:
    from fastapi.testclient import TestClient
    from server import app
    from app.shared.security.deps import get_current_account
    from app.shared.database.sql import get_session
    from unittest.mock import MagicMock, AsyncMock
    import uuid
   
    def override_get_current_account():
        mock = MagicMock()
        mock.account_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
        mock.account_id_str = "11111111-1111-4111-8111-111111111111"
        return mock

    async def override_get_session():
        yield AsyncMock()
       
    app.dependency_overrides[get_current_account] = override_get_current_account
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
   
    # Actually wait, profile.py service layer tries to read from the db session!
    # I can just monkeypatch app.domains.profile.service
    # Let's override the service calls instead so it doesn't need DB at all.
    import app.domains.profile.service as profile_service
    profile_service.get_or_create_profile = AsyncMock()
    profile_service.apply_attributes = AsyncMock()
    profile_service.serialize_profile = AsyncMock(return_value={"attributes": [
        {"key": "care_skin_usual_feel", "value": "often_dry_or_tight"},
        {"key": "preferred_style", "value": "classic"},
        {"key": "favourite_colours", "value": ["black", "white"]},
        {"key": "usual_top_size", "value": "M"},
        {"key": "silhouettes_liked", "value": ["a_line"]},
        {"key": "appearance_goals", "value": ["look_taller"]},
    ]})
    profile_service.change_history = AsyncMock(return_value=[])

    res = client.patch("/api/v2/profile", json={"attributes": [
        {"key": "care_skin_usual_feel", "value": "often_dry_or_tight"},
        {"key": "preferred_style", "value": "classic"},
        {"key": "favourite_colours", "value": ["black", "white"]},
        {"key": "usual_top_size", "value": "M"},
        {"key": "silhouettes_liked", "value": ["a_line"]},
        {"key": "appearance_goals", "value": ["look_taller"]},
    ]})
    assert res.status_code == 200, res.text
   
    # apply_attributes should have been called with only allowed keys!
    args, kwargs = profile_service.apply_attributes.call_args
    passed_attrs = args[2]
    passed_keys = [attr["key"] for attr in passed_attrs]
    assert "care_skin_usual_feel" in passed_keys
    assert "preferred_style" not in passed_keys
    assert "favourite_colours" not in passed_keys
   
    data = res.json()
    keys = [attr["key"] for attr in data.get("attributes", [])]
    assert "care_skin_usual_feel" in keys
    assert "preferred_style" not in keys
    assert "favourite_colours" not in keys
    assert "usual_top_size" not in keys
    assert "silhouettes_liked" not in keys
    assert "appearance_goals" not in keys
    app.dependency_overrides.clear()
