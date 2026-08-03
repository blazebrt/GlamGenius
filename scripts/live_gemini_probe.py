#!/usr/bin/env python3
"""Live Gemini validation probe (Fix 5, WP3).

Runs a small, safe suite of prompts against a real Gemini endpoint using
project-owned test fixtures — never a customer image, never a customer prompt.
Emits a machine-readable JSON report so the CI live workflow can attach it as
an artefact and the reviewer can read failure modes at a glance.

The probe is opt-in in three layers:

    1. It is only invoked from `.github/workflows/live-gemini.yml`, which is
       a `workflow_dispatch`-only workflow. No PR event triggers it.
    2. The GitHub workflow reads the `GEMINI_API_KEY` secret from the
       repository's own secrets, not from a PR context — an untrusted PR
       cannot exfiltrate it.
    3. The script honours GLAMGENIUS_LIVE_PROBE_ENABLED=true; if the env
       is absent it refuses to run. Belt-and-braces against an accidental
       local execution.

Cost cap:

    * The script exits after AT MOST `GLAMGENIUS_LIVE_PROBE_MAX_CALLS`
      calls (default 10). It does NOT retry on failure — every network
      error, quota error, timeout, or schema failure is counted as one
      attempted call and reported. There is no retry loop.
    * The script does NOT set up a background scheduler. One invocation
      is one bounded run.

Output:

    stdout — human-readable per-case pass/fail lines.
    /tmp/live_gemini_report.json — machine-readable JSON summary.

Failure classification (per case):

    "ok"           — provider returned, schema matched.
    "timeout"      — deadline elapsed.
    "quota_429"    — provider returned a 429.
    "invalid_json" — response body could not be parsed as JSON.
    "schema_fail"  — response parsed but did not match the expected keys.
    "provider_error" — any other exception.

A failed case NEVER consumes a user allowance (this probe is not a user
call), and it NEVER persists a fabricated observation (the probe writes
only to /tmp/live_gemini_report.json — never to the app database).

Do not point this script at anything other than the safe fixtures.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Project-owned safe fixture: a 3x3 solid-colour PNG. Not a customer image.
# The bytes are inlined so the probe has no dependency on repo layout.
_TINY_PNG_HEX = (
    "89504e470d0a1a0a0000000d49484452000000030000000308060000005ba0d1"
    "5f000000164944415478da62fcffff3f0300060404060404060404060404060404"
    "0600006bfd06f7c9b5e26f0000000049454e44ae426082"
)

# Prompt/schema versions the probe validates against. When either changes,
# the WP3 branch bumps them here; the JSON report records the versions it ran.
PROMPT_VERSION = "2026.08.03"
SCHEMA_VERSION = "2026.08.03"

# Cost cap — a wall on how many calls this invocation can make.
DEFAULT_MAX_CALLS = int(os.environ.get("GLAMGENIUS_LIVE_PROBE_MAX_CALLS", "10"))


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: str
    status: str
    latency_ms: Optional[int] = None
    detail: str = ""


@dataclass
class Report:
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    calls_attempted: int = 0
    calls_budget: int = DEFAULT_MAX_CALLS
    cases: List[CaseResult] = field(default_factory=list)
    fixture: str = "project-owned tiny PNG (3x3 solid)"

    def add(self, result: CaseResult) -> None:
        self.calls_attempted += 1
        self.cases.append(result)

    def to_json(self) -> str:
        payload: Dict[str, Any] = asdict(self)
        payload["summary"] = {
            "cases_total": len(self.cases),
            "cases_ok": sum(1 for c in self.cases if c.status == "ok"),
            "cases_failed": sum(1 for c in self.cases if c.status != "ok"),
            "status_counts": {
                s: sum(1 for c in self.cases if c.status == s)
                for s in {c.status for c in self.cases}
            },
        }
        return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Guard: refuse to run unless explicitly enabled
# ---------------------------------------------------------------------------


def _guard() -> None:
    enabled = os.environ.get("GLAMGENIUS_LIVE_PROBE_ENABLED", "").strip().lower()
    if enabled not in ("1", "true", "yes"):
        print(
            "live_gemini_probe: refusing to run. Set GLAMGENIUS_LIVE_PROBE_ENABLED=true "
            "in the ambient environment. This script is intended for the "
            "manually-dispatched live-gemini workflow only.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print(
            "live_gemini_probe: refusing to run. GEMINI_API_KEY is not set. "
            "The probe never runs against a fake or mock provider — provider "
            "faking belongs in the offline pytest suite.",
            file=sys.stderr,
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# One case = one call
# ---------------------------------------------------------------------------


def _tiny_png() -> bytes:
    return bytes.fromhex(_TINY_PNG_HEX)


def _run_case(name: str, prompt: str, image_bytes: Optional[bytes], expect_keys: List[str], timeout_seconds: float) -> CaseResult:
    """Run one live case. All exceptions are caught and turned into a status."""
    started = time.monotonic()
    try:
        # Late import so the offline pytest suite never touches the SDK.
        from app.domains.ai_gateway.providers import gemini as gemini_provider  # noqa: PLC0415
    except ImportError as exc:
        return CaseResult(case=name, status="provider_error", detail=f"import failed: {exc}")

    try:
        b64: Optional[str] = base64.b64encode(image_bytes).decode() if image_bytes else None
        import asyncio  # noqa: PLC0415
        result = asyncio.run(
            asyncio.wait_for(
                gemini_provider.generate(prompt, system="Test system prompt.", image_base64=b64),
                timeout=timeout_seconds,
            )
        )
    except asyncio.TimeoutError:
        return CaseResult(
            case=name, status="timeout",
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"deadline {timeout_seconds}s exceeded",
        )
    except Exception as exc:  # noqa: BLE001 - dynamic provider errors
        # Classify by exception type name. The gateway raises RateLimitError
        # / TimeoutError / SchemaError; anything else is provider_error.
        name_of = type(exc).__name__.lower()
        if "rate" in name_of or "429" in str(exc):
            return CaseResult(case=name, status="quota_429", detail=str(exc)[:200])
        return CaseResult(case=name, status="provider_error", detail=f"{type(exc).__name__}: {str(exc)[:200]}")

    latency_ms = int((time.monotonic() - started) * 1000)

    # Body must parse to JSON with expected keys.
    body = getattr(result, "text", result) if not isinstance(result, str) else result
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        return CaseResult(case=name, status="invalid_json", latency_ms=latency_ms, detail="response was not JSON")

    if not isinstance(payload, dict):
        return CaseResult(case=name, status="schema_fail", latency_ms=latency_ms, detail="response was not a JSON object")

    missing = [k for k in expect_keys if k not in payload]
    if missing:
        return CaseResult(case=name, status="schema_fail", latency_ms=latency_ms, detail=f"missing keys {missing}")

    return CaseResult(case=name, status="ok", latency_ms=latency_ms)


# ---------------------------------------------------------------------------
# The suite
# ---------------------------------------------------------------------------


def _cases(image: bytes) -> List[Dict[str, Any]]:
    """Every named case from the stabilisation brief, using safe fixtures."""
    return [
        # Structured appearance analysis.
        {"name": "appearance_analysis",   "prompt": 'Return JSON with keys "profile" and "observations".', "image": image, "keys": ["profile", "observations"]},
        # Hair analysis.
        {"name": "hair_analysis",         "prompt": 'Return JSON with keys "profile" and "observations" describing hair only.', "image": image, "keys": ["profile", "observations"]},
        # Inventory screenshot extraction.
        {"name": "inventory_extraction",  "prompt": 'Return JSON with key "items" listing product names visible.', "image": image, "keys": ["items"]},
        # Ingredient-label extraction.
        {"name": "ingredient_extraction", "prompt": 'Return JSON with key "ingredients" as a list of strings.', "image": image, "keys": ["ingredients"]},
        # Recommendation explanation (no image needed).
        {"name": "recommendation_explanation", "prompt": 'Return JSON with key "explanation" and key "warnings".', "image": None, "keys": ["explanation", "warnings"]},
        # Malformed-output probe: ask for JSON but bias toward prose.
        {"name": "malformed_probe",       "prompt": "Reply with an ordinary sentence, no JSON.", "image": None, "keys": ["should_fail_schema"]},
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Gemini validation probe (Fix 5, WP3).")
    parser.add_argument("--report", default="/tmp/live_gemini_report.json")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    args = parser.parse_args()

    _guard()

    image = _tiny_png()
    report = Report(calls_budget=args.max_calls)

    for case in _cases(image):
        if report.calls_attempted >= args.max_calls:
            print(f"[live-probe] cost cap reached ({args.max_calls} calls); stopping.")
            break
        result = _run_case(
            case["name"], case["prompt"], case["image"], case["keys"],
            timeout_seconds=args.timeout,
        )
        # `malformed_probe` is expected to schema_fail — invert its status
        # for reporting so a schema_fail on that specific case counts as ok.
        if case["name"] == "malformed_probe":
            if result.status == "schema_fail":
                result = CaseResult(case=case["name"], status="ok", latency_ms=result.latency_ms,
                                    detail="expected schema failure observed")
            elif result.status == "ok":
                result = CaseResult(case=case["name"], status="schema_fail", latency_ms=result.latency_ms,
                                    detail="malformed_probe unexpectedly matched schema — provider changed behaviour?")
        report.add(result)
        print(f"[live-probe] {result.case} -> {result.status} "
              f"({result.latency_ms if result.latency_ms is not None else '-'}ms) {result.detail}")

    Path(args.report).write_text(report.to_json(), encoding="utf-8")
    print(f"[live-probe] report -> {args.report}")

    failed = sum(1 for c in report.cases if c.status != "ok")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
