"""Shared test fixtures for the Supabase-cutover backend.

The suite runs against a real PostgreSQL database — faking it would not
verify the migration works, which is one of the acceptance criteria. Point
``POSTGRES_URL`` at a disposable Postgres before running pytest.

Supabase Auth is stubbed: the ``fake_supabase_user`` fixture patches
``verify_supabase_token`` so tests can mint credentials for a caller without
ever making a network call to Supabase.

The AI provider is stubbed for the same reason.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any, Optional

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure test-safe env values are set BEFORE the app imports config.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service")
os.environ.setdefault("SUPABASE_JWT_ISSUER", "https://test.supabase.co/auth/v1")
os.environ.setdefault(
    "V2_FEATURES",
    "v2_scan,v2_quiz,v2_profile,v2_inventory,v2_recommendations,v2_media,"
    "v2_privacy,v2_consent,v2_ai_gateway,v2_progress,v2_routines,v2_today,"
    "v2_planner,v2_shopping_decisions",
)
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("MEDIA_STORAGE_BACKEND", "local")
# These two describe the product configuration the suite asserts: the beta is
# invite-only and photo analysis requires consent. They are set, not defaulted,
# because an ambient value from the environment would silently change what the
# tests mean — CI exports INVITE_REQUIRED=false and REQUIRE_ANALYSIS_CONSENT=
# false, which unmounts the invite-reservation route and switches off the
# consent gate, so "scan without consent is refused" quietly became "scan
# without consent succeeds" and the test failed for a configuration reason
# rather than a code one.
os.environ["INVITE_REQUIRED"] = "true"
os.environ["REQUIRE_ANALYSIS_CONSENT"] = "true"
os.environ.setdefault(
    "POSTGRES_URL",
    "postgresql://glamgenius:glamgenius@localhost:5432/glamgenius_v2_test",
)

from app.domains.ai_gateway.providers import gemini  # noqa: E402
from app.domains.media.storage import factory as storage_factory  # noqa: E402
from app.domains.media.storage.local import LocalFilesystemStorage  # noqa: E402
from app.shared.database import sql  # noqa: E402
from app.shared.security import supabase_auth  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def _reset_access_rate_state():
    """The invite-reserve route has an in-process rate limiter; other tests
    hitting the same fake IP would otherwise accumulate over a session."""
    from app.api.v2 import access
    access._rate_state.clear()
    yield
    access._rate_state.clear()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
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
    storage_factory.set_storage(LocalFilesystemStorage(root=str(tmp_path)))
    yield tmp_path
    storage_factory.set_storage(None)


# ---------------------------------------------------------------------------
# Supabase Auth stub
# ---------------------------------------------------------------------------

def make_supabase_claims(
    *,
    user_id: Optional[uuid.UUID] = None,
    email: Optional[str] = None,
    role: str = "authenticated",
    extras: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    uid = user_id or uuid.uuid4()
    claims: dict[str, Any] = {
        "sub": str(uid),
        "email": email or f"{uid}@example.com",
        "role": role,
        "aud": "authenticated",
        "iss": "https://test.supabase.co/auth/v1",
        "iat": 1_700_000_000,
        "exp": 9_999_999_999,
    }
    if extras:
        claims.update(extras)
    return claims


@pytest.fixture
def fake_supabase_user(monkeypatch):
    """Patch Supabase JWT verification to accept an in-memory fake token.

    Returns a helper: ``token = fake_supabase_user(user_id=..., email=..., admin=False)``.
    The ``Authorization: Bearer <token>`` header uses the token as-is; the
    stubbed ``verify_supabase_token`` looks it up in ``registry`` and returns
    the corresponding claims.
    """
    registry: dict[str, dict[str, Any]] = {}

    async def _verify(token: str) -> dict[str, Any]:
        if token in registry:
            return registry[token]
        raise supabase_auth.AuthError()

    monkeypatch.setattr(supabase_auth, "verify_supabase_token", _verify)

    def _register(
        *,
        user_id: Optional[uuid.UUID] = None,
        email: Optional[str] = None,
        role: str = "authenticated",
        admin: bool = False,
    ) -> tuple[str, uuid.UUID]:
        uid = user_id or uuid.uuid4()
        claims = make_supabase_claims(user_id=uid, email=email, role=role)
        token = f"faketoken-{uuid.uuid4().hex}"
        registry[token] = claims
        if admin:
            current_admins = set(supabase_auth.SUPABASE_ADMIN_USER_IDS)
            current_admins.add(str(uid).lower())
            monkeypatch.setattr(
                supabase_auth, "SUPABASE_ADMIN_USER_IDS", current_admins
            )
        return token, uid

    return _register


@pytest_asyncio.fixture
async def registered_supabase_user(fake_supabase_user):
    """Mint a Supabase token AND create the ``accounts`` row.

    Use this in tests that hit protected routes (`/api/v2/me`, quiz, scan,
    inventory, etc.). Tests that specifically need to assert the
    invite-bypass gate ("valid token, no account row → 403") must use the
    raw ``fake_supabase_user`` fixture instead.
    """
    from app.domains.identity import service as identity
    from app.shared.database.sql import get_sessionmaker

    factory = get_sessionmaker()

    async def _register(**kwargs):
        token, uid = fake_supabase_user(**kwargs)
        async with factory() as session:
            await identity.register_account(session, uid)
            await session.commit()
        return token, uid

    return _register



def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fake AI provider
# ---------------------------------------------------------------------------

class FakeProvider:
    def __init__(self) -> None:
        self.text: str = (
            '{"observations": ["Even tone"], "colour_palette": ["#0F766E"], '
            '"recommended_next_steps": ["Drink water"], "confidence": 0.8}'
        )
        self.raises: Optional[BaseException] = None
        self.calls: int = 0
        self.model: str = "fake-model"
        self.last_system: Optional[str] = None

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        image_base64: Optional[str] = None,
        **kwargs: Any,
    ) -> gemini.ProviderResponse:
        self.calls += 1
        self.last_system = system
        if self.raises is not None:
            raise self.raises
        return gemini.ProviderResponse(
            text=self.text, model=self.model, input_tokens=1200, output_tokens=800
        )


@pytest.fixture
def fake_provider(monkeypatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setattr(gemini, "generate", provider.generate)
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    return provider


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "0005570cf5a20000000049454e44ae426082"
)


def png_bytes() -> bytes:
    return PNG_1PX


# ---------------------------------------------------------------------------
# DB setup: reset tables before each test module
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_clean() -> AsyncIterator[None]:
    """Truncate every table before yielding. Requires migrations applied."""
    from app.shared.database.registry import Base
    from sqlalchemy import text

    engine = sql.get_engine()
    async with engine.begin() as conn:
        table_names = ", ".join(
            f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables)
        )
        await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    yield
