#!/usr/bin/env python3
"""Live monitoring proof (Fix 8, WP5).

Fires a controlled test event to the configured Sentry backend, waits for
it to appear via the Sentry HTTP API, and emits a machine-readable report.

Opt-in in three layers:

  1. Runs only from `.github/workflows/live-monitoring.yml`, which is
     `workflow_dispatch` only. No PR event triggers it.
  2. Refuses to run unless `GLAMGENIUS_LIVE_MONITORING_ENABLED=true`.
  3. Refuses to run unless BOTH `SENTRY_BACKEND_DSN` and
     `SENTRY_LOOKUP_TOKEN` (an API auth token with the `event:read`
     scope on the Sentry project) are present.

The probe:

* Initialises the same Sentry bootstrap the app uses.
* Sends one message-level and one exception-level event, each tagged
  with a fresh nonce so the lookup step can find them precisely.
* Polls the Sentry events REST endpoint for at most 60 seconds,
  looking for the two event ids that Sentry returned to the client.
* Reports which events landed within the deadline, at what latency,
  and whether the PII scrubber removed the fields it should.

Never touches user data. The nonce, the tags, and the exception
message are all synthetic strings owned by this probe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


PROBE_ORIGIN = "glamgenius-live-monitoring-probe"


def _guard() -> None:
    if os.environ.get("GLAMGENIUS_LIVE_MONITORING_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        print(
            "live_monitoring_probe: refusing to run. Set "
            "GLAMGENIUS_LIVE_MONITORING_ENABLED=true.",
            file=sys.stderr,
        )
        sys.exit(2)
    for var in ("SENTRY_BACKEND_DSN", "SENTRY_LOOKUP_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT"):
        if not os.environ.get(var, "").strip():
            print(f"live_monitoring_probe: {var} is not set.", file=sys.stderr)
            sys.exit(2)


@dataclass
class EventOutcome:
    kind: str                # 'message' or 'exception'
    nonce: str
    sdk_event_id: Optional[str] = None
    found_via_api: bool = False
    latency_ms: Optional[int] = None
    error: str = ""


@dataclass
class Report:
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    outcomes: List[EventOutcome] = field(default_factory=list)
    scrubber_check_pass: Optional[bool] = None
    scrubber_check_detail: str = ""


def _send_events() -> List[EventOutcome]:
    """Send the two synthetic events. Returns the outcomes with `sdk_event_id` set."""
    import sentry_sdk  # noqa: PLC0415

    # Bootstrap using the app's own initialiser so the scrubber runs.
    # We deliberately set TESTING to nothing here so init_sentry does not skip;
    # the pytest guard is what we bypass to make the live probe possible.
    os.environ.pop("TESTING", None)
    from app.shared.observability.sentry_bootstrap import init_sentry  # noqa: PLC0415
    init_sentry()

    outcomes = []
    for kind in ("message", "exception"):
        nonce = f"probe-{uuid.uuid4()}"
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("probe_origin", PROBE_ORIGIN)
            scope.set_tag("probe_nonce", nonce)
            scope.set_tag("probe_kind", kind)
            # Extra field that MUST NOT be forwarded — the scrubber test asserts
            # this key does not appear in the payload we later retrieve.
            scope.set_extra("synthetic_secret_field", "SHOULD_BE_SCRUBBED")
            if kind == "message":
                event_id = sentry_sdk.capture_message(f"[live-probe] hello {nonce}")
            else:
                try:
                    raise RuntimeError(f"[live-probe] deliberate exception {nonce}")
                except RuntimeError as exc:
                    event_id = sentry_sdk.capture_exception(exc)
        outcomes.append(EventOutcome(kind=kind, nonce=nonce, sdk_event_id=event_id))
    sentry_sdk.flush(timeout=10.0)
    return outcomes


def _poll_sentry_for_events(outcomes: List[EventOutcome], deadline_seconds: float = 60.0) -> None:
    """Poll Sentry's REST API for each event id and mark outcomes accordingly."""
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        for o in outcomes:
            o.error = "httpx not installed"
        return

    token = os.environ["SENTRY_LOOKUP_TOKEN"]
    org = os.environ["SENTRY_ORG"]
    project = os.environ["SENTRY_PROJECT"]
    base = os.environ.get("SENTRY_API_BASE", "https://sentry.io").rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    started = time.monotonic()
    for o in outcomes:
        if not o.sdk_event_id:
            o.error = "no event id returned from SDK"
            continue
        # Poll every 3 seconds until deadline or found.
        while time.monotonic() - started < deadline_seconds:
            url = f"{base}/api/0/projects/{org}/{project}/events/{o.sdk_event_id}/"
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    o.found_via_api = True
                    o.latency_ms = int((time.monotonic() - started) * 1000)
                    break
            except Exception as exc:  # noqa: BLE001
                o.error = f"{type(exc).__name__}: {exc}"[:200]
            time.sleep(3.0)
        else:
            if not o.error:
                o.error = f"not found within {deadline_seconds}s"


def _scrubber_check(outcomes: List[EventOutcome]) -> tuple[Optional[bool], str]:
    """Re-fetch the found events and assert the scrubber removed the
    synthetic secret field. Returns (pass, detail); pass is None if we
    cannot check (event not found)."""
    found = [o for o in outcomes if o.found_via_api and o.sdk_event_id]
    if not found:
        return None, "no events retrievable"
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None, "httpx not installed"

    token = os.environ["SENTRY_LOOKUP_TOKEN"]
    org = os.environ["SENTRY_ORG"]
    project = os.environ["SENTRY_PROJECT"]
    base = os.environ.get("SENTRY_API_BASE", "https://sentry.io").rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    for o in found:
        url = f"{base}/api/0/projects/{org}/{project}/events/{o.sdk_event_id}/"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.text
        except Exception as exc:  # noqa: BLE001
            return None, f"lookup failed: {type(exc).__name__}"
        # The synthetic field should be scrubbed. Sentry stores nothing
        # about it if scrub_event stripped it before send.
        if "SHOULD_BE_SCRUBBED" in body:
            return False, f"synthetic_secret_field survived on event {o.sdk_event_id}"
    return True, "scrubber removed the synthetic secret field"


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Sentry monitoring probe (Fix 8, WP5).")
    parser.add_argument("--report", default="/tmp/live_monitoring_report.json")
    parser.add_argument("--deadline", type=float, default=60.0)
    args = parser.parse_args()

    _guard()
    report = Report()

    print("[live-monitoring] sending synthetic events...")
    outcomes = _send_events()
    for o in outcomes:
        print(f"[live-monitoring]  sent kind={o.kind} sdk_event_id={o.sdk_event_id}")

    print(f"[live-monitoring] polling API for up to {args.deadline}s...")
    _poll_sentry_for_events(outcomes, deadline_seconds=args.deadline)
    for o in outcomes:
        state = "found" if o.found_via_api else f"not_found ({o.error or 'no error'})"
        print(f"[live-monitoring]  {o.kind}: {state} latency_ms={o.latency_ms}")

    print("[live-monitoring] checking scrubber...")
    passed, detail = _scrubber_check(outcomes)
    report.scrubber_check_pass = passed
    report.scrubber_check_detail = detail
    print(f"[live-monitoring]  scrubber: {passed} — {detail}")

    report.outcomes = outcomes
    Path(args.report).write_text(
        json.dumps({**asdict(report),
                    "summary": {
                        "sent": len(outcomes),
                        "found": sum(1 for o in outcomes if o.found_via_api),
                        "scrubber_pass": passed,
                    }}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"[live-monitoring] report -> {args.report}")

    if any(not o.found_via_api for o in outcomes):
        return 1
    if passed is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
