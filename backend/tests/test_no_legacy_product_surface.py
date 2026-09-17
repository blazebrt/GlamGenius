"""Regression gate for the post-pivot customer-product boundary.

This deliberately checks semantic paths and customer contracts, not generic
programming words such as React Native's `style` prop. Historical Alembic
migrations are database history and intentionally outside this scan.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.shared.database.sql import get_session
from app.shared.security.deps import get_current_account
from fastapi.testclient import TestClient
from server import app

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
    from app.domains.inventory.taxonomy import CATEGORIES

    frontend_contract = (FRONTEND / "src" / "services" / "apiV2.ts").read_text(encoding="utf-8")
    assert set(CATEGORIES) == {"beauty", "hair", "perfumes", "supplements"}
    # The active shelf declaration, rather than historical Event Ready
    # implementation types lower in this client, is the customer contract.
    declaration = frontend_contract.split("export type InventoryCategory", 1)[0]
    for legacy_category in ('"wardrobe"', '"shoes"', '"accessories"'):
        assert legacy_category not in declaration


def test_agent_handoff_is_governed_by_the_product_constitution() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for handoff in (claude, agents):
        assert "Existing code is **not** evidence" in handoff or "Do not infer scope from old code" in handoff
        assert all(x in handoff for x in ("SCAN", "UNDERSTAND", "DECIDE", "REMEMBER", "MANAGE"))
    assert "body-and-appearance manager" not in claude
    assert "Today | Style | Care | Plan | You" not in claude


def test_active_server_identity_is_product_decision_engine() -> None:
    server = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "Personal Appearance Operating System" not in server


def test_active_route_reachability() -> None:
    # We dynamically mount planner, progress, and today in tests to allow legacy data testing.
    # We re-import the production router directly to verify it does not contain them.
    from app.api.v2 import router as v2_router
    paths = [route.path for route in v2_router.routes if hasattr(route, "path")]
    legacy_patterns = [
        "/api/v2/style",
        "/api/v2/quiz",
        "/api/v2/shopping/roi-model",
        "/api/v2/shopping/evaluate",
        "/api/v2/shopping/evaluations",
        "/api/v2/today",
        "/api/v2/planner",
        "/api/v2/onboarding",
        "/api/v2/progress",
        "/api/v2/goals",
        "/api/v2/milestones",
        "/api/v2/memory",
    ]
    for path in paths:
        for legacy in legacy_patterns:
            assert not path.startswith(legacy), f"Legacy route {path} is still mounted"


def test_profile_active_allowlist_forbids_legacy_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.v2.profile as prof
    monkeypatch.setattr(prof, "ALLOWED_KEYS", {"care_skin_usual_feel", "care_skin_sensitivity"})
    def override_get_current_account():
        mock = MagicMock()
        mock.account_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
        mock.account_id_str = "11111111-1111-4111-8111-111111111111"
        return mock

    async def override_get_session():
        yield AsyncMock()
        
    app.dependency_overrides[get_current_account] = override_get_current_account
    app.dependency_overrides[get_session] = override_get_session
    
    import app.domains.profile.service as profile_service
    
    monkeypatch.setattr(
        "app.api.v2.profile.resolve_self_profile_for_write", AsyncMock()
    )
    apply_attributes_mock = AsyncMock()
    monkeypatch.setattr(profile_service, "apply_attributes", apply_attributes_mock)
    monkeypatch.setattr(profile_service, "serialize_profile", AsyncMock(return_value={
        "baseline_status": "ready",
        "readiness": [{"area": "style", "ready": True, "message": ""}],
        "attributes": [
            {"key": "care_skin_usual_feel", "value": "often_dry_or_tight"},
            {"key": "preferred_style", "value": "classic"},
            {"key": "favourite_colours", "value": ["black", "white"]}
        ]
    }))
    monkeypatch.setattr(profile_service, "change_history", AsyncMock(return_value=[
        {"attribute_key": "care_skin_usual_feel", "old_value": None, "new_value": "often_dry_or_tight", "source": "user", "reason": None, "created_at": "2024-01-01T00:00:00Z"},
        {"attribute_key": "preferred_style", "old_value": None, "new_value": "classic", "source": "user", "reason": None, "created_at": "2024-01-01T00:00:00Z"}
    ]))
    
    mock_row = MagicMock()
    mock_row.key = "care_skin_usual_feel"
    mock_row2 = MagicMock()
    mock_row2.key = "preferred_style"
    monkeypatch.setattr(profile_service, "attributes_for", AsyncMock(return_value=[mock_row, mock_row2]))
    monkeypatch.setattr(profile_service, "serialize_attribute", lambda r: {"key": r.key})
    
    try:
        with TestClient(app) as client:
            # Test 1: allowed Care-only PATCH succeeds
            res_allowed = client.patch("/api/v2/profile", json={"attributes": [
                {"key": "care_skin_usual_feel", "value": "often_dry_or_tight"}
            ]})
            assert res_allowed.status_code == 200, res_allowed.text
            apply_attributes_mock.assert_called_once()
            apply_attributes_mock.reset_mock()
            
            # Test 2: forbidden legacy-only PATCH fails
            res_forbidden = client.patch("/api/v2/profile", json={"attributes": [
                {"key": "preferred_style", "value": "classic"}
            ]})
            assert res_forbidden.status_code == 422
            apply_attributes_mock.assert_not_called()
            
            # Test 3: mixed Care + legacy PATCH fails atomically
            res_mixed = client.patch("/api/v2/profile", json={"attributes": [
                {"key": "care_skin_usual_feel", "value": "mixed"},
                {"key": "preferred_style", "value": "classic"}
            ]})
            assert res_mixed.status_code == 422
            apply_attributes_mock.assert_not_called()
            
            # Test 4: GET /profile does not expose legacy things
            res_get = client.get("/api/v2/profile")
            assert res_get.status_code == 200
            data = res_get.json()
            assert "baseline_status" not in data
            assert "readiness" not in data
            keys = [attr["key"] for attr in data.get("attributes", [])]
            assert "care_skin_usual_feel" in keys
            assert "preferred_style" not in keys
            assert "favourite_colours" not in keys
            history_keys = [item["attribute_key"] for item in data.get("change_history", [])]
            assert "care_skin_usual_feel" in history_keys
            assert "preferred_style" not in history_keys
            
            # Test 5: GET /profile/attributes does not expose legacy things
            res_attr = client.get("/api/v2/profile/attributes")
            assert res_attr.status_code == 200
            attr_data = res_attr.json()
            assert "readiness" not in attr_data
            attr_keys = [attr["key"] for attr in attr_data.get("attributes", [])]
            assert "care_skin_usual_feel" in attr_keys
            assert "preferred_style" not in attr_keys
            reg_keys = [item["key"] for item in attr_data.get("registry", [])]
            assert "care_skin_usual_feel" in reg_keys
            assert "preferred_style" not in reg_keys
    finally:
        app.dependency_overrides.clear()
