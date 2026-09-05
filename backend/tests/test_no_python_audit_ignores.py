"""Absence regression — the Python dependency audit must carry no suppressions.

`pip-audit` accepts inline `--ignore-vuln <ID>` flags. CI carried eight of
them, added in one commit and never revisited; a fresh unignored audit found
that seven no longer matched anything the dependency graph resolves, and the
eighth (pytest, PYSEC-2026-1845) had a released fix available the whole time.
An advisory silenced on a command line leaves no expiry, no owner and no
review date, so nothing ever forces the question again.

The invariant this pins is deliberately absolute: **zero** Python audit
suppressions. There is no allowlist parameter here, empty or otherwise,
because an empty allowlist is an invitation — the next person appends one ID
and the gate is quietly back where it started. Reintroducing a suppression has
to mean editing this test, which makes it a reviewed decision instead of a
line buried in a workflow diff.

If a future advisory genuinely cannot be remediated, that is a governance
change: propose it on its own, with the evidence that remediation is
impossible, the way the container exceptions in `.trivy-exceptions.yaml` are
governed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Production CI definitions. Everything GitHub Actions will actually execute.
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# Other places a Python audit could realistically be invoked from.
EXTRA_SCAN_DIRS = [REPO_ROOT / ".github" / "scripts", REPO_ROOT / "scripts"]

SUPPRESSION_FLAG = "--ignore-vuln"

# This file names the flag in order to forbid it.
EXCLUDE_FILES = {Path(__file__).resolve()}


def _files_to_scan() -> list[Path]:
    files: list[Path] = []
    if WORKFLOW_DIR.is_dir():
        files += sorted(p for p in WORKFLOW_DIR.rglob("*") if p.suffix in {".yml", ".yaml"})
    for directory in EXTRA_SCAN_DIRS:
        if directory.is_dir():
            files += sorted(p for p in directory.rglob("*") if p.suffix in {".sh", ".py", ".yml", ".yaml"})
    return [p for p in files if p.is_file() and p.resolve() not in EXCLUDE_FILES]


def test_workflow_directory_is_present() -> None:
    """A scan that silently finds nothing to read proves nothing."""
    assert WORKFLOW_DIR.is_dir(), f"expected workflows at {WORKFLOW_DIR}"
    assert _files_to_scan(), "no CI files were scanned; the guard would pass vacuously"


def test_ci_declares_no_python_audit_suppressions() -> None:
    """No CI file may pass an inline pip-audit suppression."""
    offenders: list[str] = []
    for path in _files_to_scan():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:  # pragma: no cover - CI files are text
            continue
        for number, line in enumerate(lines, start=1):
            if SUPPRESSION_FLAG in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")

    if offenders:
        listing = "\n".join(f"  {entry}" for entry in offenders)
        pytest.fail(
            f"Found {len(offenders)} inline Python audit suppression(s).\n"
            f"{listing}\n\n"
            f"Python audit exceptions are zero. Remediate the advisory — upgrade the\n"
            f"package, or remove it — rather than passing {SUPPRESSION_FLAG}. If it is\n"
            f"genuinely unremediable, that needs a reviewed governance change of its\n"
            f"own, not a flag added here.",
            pytrace=False,
        )


def test_audit_invocations_stay_strict() -> None:
    """Every pip-audit call that gates CI keeps --strict and the OSV service."""
    weakened: list[str] = []
    for path in _files_to_scan():
        text = path.read_text(encoding="utf-8")
        if "pip-audit" not in text:
            continue
        # Join line continuations so a multi-line invocation reads as one command.
        joined = text.replace("\\\n", " ")
        for number, line in enumerate(joined.splitlines(), start=1):
            # An invocation, not a mention. `pip install pip-audit`, a job name,
            # an artifact name and a path glob all contain the string; only a
            # real call audits a requirements file.
            if "pip-audit" not in line or "-r requirements.txt" not in line:
                continue
            if "pip install" in line:
                continue
            # The post-failure JSON report is deliberately non-blocking and
            # deliberately not strict; it exists to describe a failure that has
            # already been raised by the gating call above it.
            if "--format=json" in line:
                continue
            if "--strict" not in line:
                weakened.append(f"{path.relative_to(REPO_ROOT)}:{number}: missing --strict: {line.strip()}")
            if "--vulnerability-service osv" not in line:
                weakened.append(f"{path.relative_to(REPO_ROOT)}:{number}: not using OSV: {line.strip()}")
            if "|| true" in line:
                weakened.append(f"{path.relative_to(REPO_ROOT)}:{number}: failure swallowed: {line.strip()}")

    if weakened:
        listing = "\n".join(f"  {entry}" for entry in weakened)
        pytest.fail(f"Weakened pip-audit invocation(s):\n{listing}", pytrace=False)
