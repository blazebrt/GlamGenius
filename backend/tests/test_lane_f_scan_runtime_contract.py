"""Lane F runtime contract: ``/scan/analyse`` calls the provider adapter it really has.

On the reviewed head the route called ``gemini.generate(prompt=…, image_base64=…)``
while the adapter is ``generate(prompt, system, image_base64=None)``: every real
request raised ``TypeError: … missing 1 required positional argument: 'system'``
before anything was sent. The test fakes declared ``system=None``, so the suite
never saw it. Now the route passes the role sentence that used to open its
prompt as ``system`` — verbatim, nothing added — the fakes carry the adapter's
exact contract, and a static check binds every adapter call site in the app
against the real signature.

No test here contacts Gemini. R1 drives the real ``gemini.generate`` with only
the SDK client replaced, so the request that would be sent is inspected and
nothing leaves the process.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.api.v2 import scan as scan_route
from app.domains.ai_gateway.providers import gemini
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import BetaUsageEvent
from app.domains.scan.models import Scan
from sqlalchemy import select

from tests.conftest import FakeProvider, auth
from tests.test_lane_f_usage_reservations import (
    _consented,
    _factory,
    _GatedProvider,
    _reservations,
    _scan_body,
    _usage,
    gated,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.asyncio

ROUTE = "/api/v2/scan/analyse"
OK = '{"observations": ["Even tone"], "colour_palette": [], "recommended_next_steps": [], "confidence": 0.8}'
#: Written out, not imported: a changed constant must fail these tests.
ROLE_SENTENCE = "You are a considerate, evidence-first appearance coach."
APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


async def _scans(account_id) -> list[Scan]:
    async with _factory()() as session:
        return list((await session.execute(
            select(Scan).where(Scan.account_id == account_id).order_by(Scan.created_at)
        )).scalars().all())


class _StubModels:
    """Stands in for ``client.models``: records the request, sends nothing."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=OK, usage_metadata=None, candidates=None)


# ---------------------------------------------------------------------------
# R1 / R2 / R3 — the real adapter, end to end, with the SDK client stubbed
# ---------------------------------------------------------------------------
async def test_r1_r2_r3_the_route_satisfies_the_real_adapter_and_sends_the_role_as_system(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    models = _StubModels()
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(gemini, "get_client", lambda: SimpleNamespace(models=models))
    token, account_id = await _consented(registered_supabase_user)

    response = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "scan_type": "hair"})

    assert response.status_code == 201, response.text
    assert len(models.requests) == 1
    request = models.requests[0]
    # R2: exactly the role sentence, through the SDK's system channel.
    assert request["config"].system_instruction == ROLE_SENTENCE
    # R3: the task prompt is the last content part, after the image.
    prompt = request["contents"][-1]
    assert isinstance(prompt, str)
    assert prompt.startswith("Analyse the attached hair photo and return a compact JSON object")
    assert ROLE_SENTENCE not in prompt and "appearance coach" not in prompt
    [scan] = await _scans(account_id)
    assert (scan.status, scan.prompt_version, scan.schema_version) == ("ok", "scan.v2", "scan.v1")


async def test_r1_the_strict_fake_receives_system_as_a_keyword(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, _ = await _consented(registered_supabase_user)
    response = await app_client.post(ROUTE, headers=auth(token), json=_scan_body())
    assert response.status_code == 201, response.text
    assert gated.calls == 1 and gated.systems == [ROLE_SENTENCE]


@pytest.mark.parametrize("scan_type", ["face", "hair", "hands", "full"])
async def test_r3_the_task_prompt_keeps_every_existing_instruction_and_nothing_else(
    app_client, db_clean, registered_supabase_user, gated, scan_type,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, _ = await _consented(registered_supabase_user)
    response = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "scan_type": scan_type})
    assert response.status_code == 201, response.text
    [prompt] = gated.prompts
    assert prompt == (
        f"Analyse the attached {scan_type} photo and return a compact JSON object with "
        'these keys: {"observations": [string], "colour_palette": [string], '
        '"recommended_next_steps": [string], "confidence": number between 0 and 1}.\n'
        "Use premium, constructive language. Never use judgmental terms."
    )
    for required in (
        f"attached {scan_type} photo", "compact JSON object", '"observations"', '"colour_palette"',
        '"recommended_next_steps"', '"confidence"', "premium, constructive language", "Never use judgmental terms",
    ):
        assert required in prompt, required
    assert ROLE_SENTENCE not in prompt
    # The system text and the task together are exactly the reviewed head's
    # single prompt: nothing was added, removed or reworded.
    assert f"{gated.systems[0]} {prompt}" == (
        "You are a considerate, evidence-first appearance coach. Analyse the "
        f"attached {scan_type} photo and return a compact JSON object with "
        'these keys: {"observations": [string], "colour_palette": [string], '
        '"recommended_next_steps": [string], "confidence": number between 0 and 1}.\n'
        "Use premium, constructive language. Never use judgmental terms."
    )


# ---------------------------------------------------------------------------
# R4 — provenance of a new successful scan
# ---------------------------------------------------------------------------
async def test_r4_a_new_successful_scan_records_prompt_v2_and_the_unchanged_schema(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    response = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "idempotency_key": "R4"})
    assert response.status_code == 201, response.text
    [scan] = await _scans(account_id)
    assert (scan.prompt_version, scan.schema_version) == ("scan.v2", "scan.v1")
    assert (scan_route.SCAN_PROMPT_VERSION, scan_route.SCAN_SCHEMA_VERSION) == ("scan.v2", "scan.v1")


# ---------------------------------------------------------------------------
# R5 — a replay never calls the provider and never rewrites provenance
# ---------------------------------------------------------------------------
async def test_r5_a_replay_returns_the_stored_scan_and_leaves_its_provenance_alone(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    gated.text = OK
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    first = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "idempotency_key": "R5"})
    [before] = await _scans(account_id)
    replay = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "idempotency_key": "R5"})
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json() and gated.calls == 1
    [after] = await _scans(account_id)
    assert (after.id, after.prompt_version, after.schema_version, after.updated_at) == (
        before.id, before.prompt_version, before.schema_version, before.updated_at,
    )


async def test_r5_a_result_stored_under_the_old_prompt_replays_as_it_was(
    app_client, db_clean, registered_supabase_user, gated,  # noqa: F811 - shared fixture
):
    """A ``scan.v1`` result counted before this change is replayed unchanged:
    no provider call, no new row, and its provenance is not rewritten."""
    gated.gate.set()
    token, account_id = await _consented(registered_supabase_user)
    stored_id = uuid.uuid4()
    async with _factory()() as session:
        session.add(Scan(
            id=stored_id, account_id=account_id, scan_type="face", status="ok", provider="gemini",
            model="older-model", prompt_version="scan.v1", schema_version="scan.v1",
            analysis={"observations": ["stored before"]}, idempotency_key="OLD",
        ))
        session.add(BetaUsageEvent(
            account_id=account_id, feature=beta.FEATURE_SCAN, idempotency_key="OLD",
            period_key="2026-09", quantity=1, created_at=datetime(2026, 9, 20, tzinfo=UTC),
        ))
        await session.commit()
    replay = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "idempotency_key": "OLD"})
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == str(stored_id)
    assert replay.json()["model"] == "older-model"
    assert replay.json()["analysis"] == {"observations": ["stored before"]}
    assert gated.calls == 0
    [row] = await _scans(account_id)
    assert (row.prompt_version, row.schema_version) == ("scan.v1", "scan.v1")


# ---------------------------------------------------------------------------
# R6 — a governed provider failure under the real contract
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("failure", [
    gemini.ProviderCallFailed("upstream exploded"),
    gemini.ProviderTimeout("Gemini did not respond within 45s"),
])
async def test_r6_a_governed_provider_failure_releases_the_key_for_a_retry(
    app_client, db_clean, registered_supabase_user, gated, failure,  # noqa: F811 - shared fixture
):
    gated.gate.set()
    gated.raises = failure
    token, account_id = await _consented(registered_supabase_user)
    failed = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "idempotency_key": "R6"})
    assert failed.status_code == 502, failed.text
    assert gated.systems == [ROLE_SENTENCE]  # the failure came from the provider, not the call
    assert await _reservations(account_id) == [] and await _usage(account_id, beta.FEATURE_SCAN) == []
    assert [(row.status, row.idempotency_key) for row in await _scans(account_id)] == [("provider_failure", None)]

    gated.raises, gated.text = None, OK
    retried = await app_client.post(ROUTE, headers=auth(token), json={**_scan_body(), "idempotency_key": "R6"})
    assert retried.status_code == 201 and "Idempotent-Replayed" not in retried.headers
    assert gated.calls == 2
    assert [row.idempotency_key for row in await _usage(account_id, beta.FEATURE_SCAN)] == ["R6"]
    ok = [row for row in await _scans(account_id) if row.status == "ok"]
    assert [(row.idempotency_key, row.prompt_version) for row in ok] == [("R6", "scan.v2")]


# ---------------------------------------------------------------------------
# R7 — no call site can drift from the adapter again, and no fake can hide it
# ---------------------------------------------------------------------------
def _adapter_call_sites() -> list[tuple[pathlib.Path, ast.Call]]:
    sites = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_as = {"gemini"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.domains.ai_gateway.providers":
                imported_as |= {alias.asname or alias.name for alias in node.names if alias.name == "gemini"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "generate"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in imported_as
            ):
                sites.append((path, node))
    return sites


async def test_r7_every_adapter_call_in_the_app_binds_to_the_real_signature():
    signature = inspect.signature(gemini.generate)
    sites = _adapter_call_sites()
    names = sorted({path.relative_to(APP_ROOT).as_posix() for path, _ in sites})
    assert names == ["api/v2/scan.py", "domains/ai_gateway/gateway.py"], names
    for path, call in sites:
        assert not any(isinstance(arg, ast.Starred) for arg in call.args), path
        assert all(keyword.arg is not None for keyword in call.keywords), path
        # Raises TypeError — the production failure — if ``system`` is missing
        # or an argument the adapter does not take is passed.
        signature.bind(*[None] * len(call.args), **{keyword.arg: None for keyword in call.keywords})


@pytest.mark.parametrize("fake", [FakeProvider.generate, _GatedProvider.generate])
async def test_r7_the_test_fakes_carry_the_adapter_contract_exactly(fake):
    real = inspect.signature(gemini.generate).parameters
    faked = {name: parameter for name, parameter in inspect.signature(fake).parameters.items() if name != "self"}
    assert list(faked) == list(real)
    for name, parameter in real.items():
        assert (faked[name].default is inspect.Parameter.empty) == (parameter.default is inspect.Parameter.empty), name
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in faked.values())


async def test_r7_a_route_that_forgets_system_now_fails_the_suite():
    """The strict fake refuses exactly what the real adapter refuses."""
    provider = _GatedProvider(OK)
    provider.gate.set()
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'system'"):
        await provider.generate(prompt="p", image_base64=None)
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'system'"):
        await gemini.generate(prompt="p", image_base64=None)
