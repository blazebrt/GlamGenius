"""Absence regression — the Python dependency audit must carry no suppressions.

`pip-audit` accepts inline `--ignore-vuln <ID>` flags. CI carried eight of
them, added in one commit and never revisited; a fresh unignored audit found
that seven no longer matched anything the dependency graph resolves, and the
eighth (pytest, PYSEC-2026-1845) had a released fix available the whole time.
An advisory silenced on a command line leaves no expiry, no owner and no
review date, so nothing ever forces the question again.

The invariant is deliberately absolute: **zero** Python audit suppressions
anywhere in the repository. There is no allowlist here, empty or otherwise,
because an empty allowlist is an invitation — the next person appends one ID
and the gate is quietly back where it started. Reintroducing a suppression has
to mean editing this test, which makes it a reviewed decision instead of a
line buried in a workflow diff.

**Why the scan is repository-wide.** An earlier version of this file walked
three fixed directories: `.github/workflows`, `.github/scripts` and `scripts`.
That left an obvious hole. Move the audit into a helper the directory list does
not name — `backend/tools/python_audit.sh`, say — call it from the workflow,
and the workflow itself contains no flag; the guard passes while the
suppression is live. So the inventory is now every tracked text file, taken
from Git rather than from a hand-maintained directory list, because Git already
knows what this repository contains and a directory list has to be remembered.

If a future advisory genuinely cannot be remediated, that is a governance
change: propose it on its own, with the evidence that remediation is
impossible, the way the container exceptions in `.trivy-exceptions.yaml` are
governed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SUPPRESSION_FLAG = "--ignore-vuln"

# The one and only filename special case: this file must name the forbidden
# flag in order to forbid it. Nothing else is exempt.
GUARD_FILE = Path(__file__).resolve()

# An inventory entry is (repository-relative path, decoded text).
Inventory = list[tuple[str, str]]


def tracked_text_files(repo_root: Path = REPO_ROOT) -> Inventory:
    """Every tracked text file in the repository, as (relative path, text).

    Git is the authority on what is tracked, so nothing has to be remembered
    in a directory list and `.git` is never walked. `-z` keeps paths intact
    when they contain spaces or non-ASCII bytes, which `git ls-files` would
    otherwise quote.
    """
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    inventory: Inventory = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        absolute = repo_root / relative
        # Submodules and deleted-but-tracked entries are not readable files.
        if not absolute.is_file():
            continue
        if absolute.resolve() == GUARD_FILE:
            continue
        try:
            data = absolute.read_bytes()
        except OSError:  # pragma: no cover - unreadable file
            continue
        # A NUL byte is the conventional binary marker, and anything that will
        # not decode as UTF-8 cannot carry the ASCII flag we are looking for.
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        inventory.append((relative, text))
    return inventory


def find_suppressions(inventory: Inventory) -> list[str]:
    """Every line in the inventory carrying an inline audit suppression."""
    offenders: list[str] = []
    for relative, text in inventory:
        for number, line in enumerate(text.splitlines(), start=1):
            if SUPPRESSION_FLAG in line:
                offenders.append(f"{relative}:{number}: {line.strip()}")
    return offenders


def find_weakened_invocations(inventory: Inventory) -> list[str]:
    """Every gating pip-audit call that has lost --strict, OSV, or its exit code.

    A gating call is one that actually audits the requirements file. `pip
    install pip-audit`, a job name, an artifact name, a path glob and a prose
    mention all contain the string "pip-audit" and none of them audit anything.
    """
    weakened: list[str] = []
    for relative, text in inventory:
        if "pip-audit" not in text:
            continue
        # Join line continuations so a multi-line invocation reads as one command.
        joined = text.replace("\\\n", " ")
        for number, line in enumerate(joined.splitlines(), start=1):
            if "pip-audit" not in line or "-r requirements.txt" not in line:
                continue
            if "pip install" in line:
                continue
            stripped = line.strip()
            # Comments and documentation describe invocations; they are not one.
            if stripped.startswith("#"):
                continue
            # The post-failure JSON report is deliberately non-blocking and
            # deliberately not strict: it describes a failure the gating call
            # above it has already raised.
            if "--format=json" in line:
                continue
            if "--strict" not in line:
                weakened.append(f"{relative}:{number}: missing --strict: {stripped}")
            if "--vulnerability-service osv" not in line:
                weakened.append(f"{relative}:{number}: not using OSV: {stripped}")
            if "|| true" in line:
                weakened.append(f"{relative}:{number}: failure swallowed: {stripped}")
    return weakened


# --------------------------------------------------------------------------
# The repository as it actually is.
# --------------------------------------------------------------------------


def test_inventory_is_not_vacuous() -> None:
    """A scan that silently reads nothing would pass while proving nothing."""
    inventory = tracked_text_files()
    assert inventory, "git ls-files returned no tracked text files"
    paths = {relative for relative, _ in inventory}
    assert ".github/workflows/ci.yml" in paths, "the CI workflow was not scanned"
    assert "backend/requirements.txt" in paths, "the requirements file was not scanned"


def test_repository_declares_no_python_audit_suppressions() -> None:
    """No tracked text file may carry an inline pip-audit suppression."""
    offenders = find_suppressions(tracked_text_files())
    if offenders:
        listing = "\n".join(f"  {entry}" for entry in offenders)
        pytest.fail(
            f"Found {len(offenders)} inline Python audit suppression(s).\n"
            f"{listing}\n\n"
            f"Python audit exceptions are zero. Remediate the advisory — upgrade the\n"
            f"package, or remove it — rather than passing the suppression flag. If it\n"
            f"is genuinely unremediable, that needs a reviewed governance change of\n"
            f"its own, not a flag added here.",
            pytrace=False,
        )


def test_gating_audit_invocations_stay_strict() -> None:
    """Every gating pip-audit call keeps --strict, OSV, and a real exit code."""
    weakened = find_weakened_invocations(tracked_text_files())
    if weakened:
        listing = "\n".join(f"  {entry}" for entry in weakened)
        pytest.fail(f"Weakened pip-audit invocation(s):\n{listing}", pytrace=False)


# --------------------------------------------------------------------------
# Adversarial coverage. These drive the same functions over a synthetic
# inventory, so the blind spot is proven closed without writing a fake
# vulnerable helper into the repository.
# --------------------------------------------------------------------------

HELPER_PATH = "backend/tools/python_audit.sh"

CLEAN_HELPER = """#!/usr/bin/env bash
set -euo pipefail
pip-audit -r requirements.txt \\
  --vulnerability-service osv \\
  --strict
"""


def test_suppression_in_a_helper_outside_the_old_directories_is_detected() -> None:
    """The exact bypass the previous directory-scoped guard would have missed."""
    suppressed = CLEAN_HELPER.replace(
        "  --strict\n", "  --strict \\\n  --ignore-vuln PYSEC-EXAMPLE\n"
    )
    offenders = find_suppressions([(HELPER_PATH, suppressed)])
    assert offenders, "a suppression inside backend/tools/ went undetected"
    assert HELPER_PATH in offenders[0]
    assert "PYSEC-EXAMPLE" in offenders[0]


def test_a_clean_helper_outside_the_old_directories_is_accepted() -> None:
    """Being repository-wide must not mean flagging correct helpers."""
    inventory = [(HELPER_PATH, CLEAN_HELPER)]
    assert find_suppressions(inventory) == []
    assert find_weakened_invocations(inventory) == []


def test_dropping_strict_in_a_helper_is_detected() -> None:
    without_strict = CLEAN_HELPER.replace(" \\\n  --strict\n", "\n")
    weakened = find_weakened_invocations([(HELPER_PATH, without_strict)])
    assert any("missing --strict" in entry for entry in weakened), weakened


def test_switching_away_from_osv_is_detected() -> None:
    not_osv = CLEAN_HELPER.replace("--vulnerability-service osv", "--vulnerability-service pypi")
    weakened = find_weakened_invocations([(HELPER_PATH, not_osv)])
    assert any("not using OSV" in entry for entry in weakened), weakened


def test_swallowing_failure_with_or_true_is_detected() -> None:
    swallowed = CLEAN_HELPER.replace("  --strict\n", "  --strict || true\n")
    weakened = find_weakened_invocations([(HELPER_PATH, swallowed)])
    assert any("failure swallowed" in entry for entry in weakened), weakened


def test_a_mention_is_not_mistaken_for_an_invocation() -> None:
    """Installs, job names, artifact names and comments must not trip the check."""
    noise = "\n".join(
        [
            "      - name: Install pip-audit",
            "        run: pip install pip-audit==2.7.3",
            "      - name: Upload full pip-audit report",
            "          name: pip-audit-report",
            "        # pip-audit -r requirements.txt is described here, not run",
            "  pip-audit:",
        ]
    )
    assert find_weakened_invocations([(".github/workflows/example.yml", noise)]) == []


def test_the_json_diagnostic_command_is_exempt() -> None:
    """The post-failure report is not the gate and is deliberately non-blocking."""
    diagnostic = "run: pip-audit -r requirements.txt --vulnerability-service osv --format=json > /tmp/x.json || true"
    assert find_weakened_invocations([(".github/workflows/example.yml", diagnostic)]) == []
