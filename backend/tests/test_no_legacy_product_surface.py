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
        "/api/v2/planner",
    ]
    for path in paths:
        for legacy in legacy_patterns:
            assert not path.startswith(legacy), f"Legacy route {path} is still mounted"
