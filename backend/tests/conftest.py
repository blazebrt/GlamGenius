"""Shared test fixtures.

The app is driven in-process through ``httpx.ASGITransport`` — no uvicorn, no
bound port, no network. PostgreSQL and MongoDB are real containers (see
``docker-compose.test.yml``); faking a database would not prove the migration
works, and proving the migration works is one of the acceptance criteria.

The AI provider is always faked. Tests must never spend money, and the failure
paths are exactly what needs covering.
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Dict, Optional

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.domains.ai_gateway.providers import gemini
from app.domains.media.storage import factory as storage_factory
from app.domains.media.storage.local import LocalFilesystemStorage
from app.shared.database import sql


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    """Close the connection pool once, at the end of the session.

    The suite shares a single event loop (see pytest.ini), so the pool stays
    valid throughout and only needs disposing at the end.
    """
    yield
    await sql.dispose_engine()


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[AsyncClient]:
    """An HTTP client wired straight to the ASGI app."""
    import server

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def media_root(tmp_path) -> Any:
    """Point media storage at a temporary directory for the duration of a test."""
    storage_factory.set_storage(LocalFilesystemStorage(root=str(tmp_path)))
    yield tmp_path
    storage_factory.set_storage(None)


# --- Users -----------------------------------------------------------------


async def create_invite(code: Optional[str] = None, max_uses: int = 50) -> str:
    """An invite code, inserted directly. Signup requires one."""
    from database import db

    value = code or f"TEST-{uuid.uuid4().hex[:8].upper()}"
    await db.invite_codes.insert_one(
        {
            "id": str(uuid.uuid4()),
            "code": value,
            "label": "pytest",
            "max_uses": max_uses,
            "uses": 0,
            "active": True,
        }
    )
    return value


@pytest_asyncio.fixture
async def make_user(app_client: AsyncClient):
    """Factory returning ``(user, token)`` for a freshly registered account."""

    async def _make(email: Optional[str] = None) -> tuple[Dict[str, Any], str]:
        invite = await create_invite()
        address = email or f"test.{uuid.uuid4().hex[:10]}@example.com"
        response = await app_client.post(
            "/api/auth/register",
            json={
                "name": "Test Person",
                "email": address,
                "password": "correct-horse-battery",
                "invite_code": invite,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return body["user"], body["token"]

    return _make


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Fake AI provider -------------------------------------------------------


class FakeProvider:
    """Stand-in for the Gemini transport.

    Set ``raises`` to an exception instance to simulate a provider failure, or
    ``text`` to control exactly what comes back.
    """

    def __init__(self) -> None:
        self.text: str = ""
        self.raises: Optional[BaseException] = None
        self.calls: int = 0
        self.model: str = "fake-model"

    async def generate(
        self, prompt: str, system: str, image_base64: Optional[str] = None
    ) -> gemini.ProviderResponse:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return gemini.ProviderResponse(
            text=self.text, model=self.model, input_tokens=1200, output_tokens=800
        )


@pytest.fixture
def fake_provider(monkeypatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(gemini, "generate", provider.generate)
    # is_configured() drives /api/v2/config and the health route.
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    return provider


def valid_analysis_payload() -> Dict[str, Any]:
    """The smallest response that should pass schema validation."""
    return {
        "meta": {
            "scan_focus": "face",
            "confidence": 0.8,
            "image_quality_notes": "Clear, evenly lit",
        },
        "profile": {
            "face_shape": "oval",
            "skin_type_visible": "combination",
            "skin_tone": "wheatish",
            "undertone": "warm",
            "hair_type_visible": "wavy",
        },
        "observations": [
            {
                "area": "face",
                "what_i_see": "Even tone with a little shine across the T-zone",
                "level": "mild",
                "why_it_matters": "Affects how colours photograph",
            }
        ],
        "fashion": {
            "best_clothing_colors": [
                {"color": "deep teal", "hex_hint": "#0F766E", "why": "Suits warm tones"}
            ]
        },
        "coach_summary": {
            "headline": "Colours for your tone and simple daily habits",
            "top_3_actions_this_week": ["Try deep teal", "SPF daily", "Drink water"],
            "recheck_in_days": 14,
        },
    }


def valid_style_plan_payload() -> Dict[str, Any]:
    return {
        "headline": "Office-ready looks in your colours",
        "top_3_actions_this_week": ["Iron the teal kurta"],
        "salon_suggestions": [],
    }


# --- Small helpers ----------------------------------------------------------

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005570cf5a20000000049454e44ae426082"
)


def png_bytes() -> bytes:
    """A real, valid 1x1 PNG. Sniffing is by magic bytes, so it must be genuine."""
    return PNG_1PX
