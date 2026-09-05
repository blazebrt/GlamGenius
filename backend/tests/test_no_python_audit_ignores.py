"""Absence regression — the Python dependency audit must carry no suppressions.

`pip-audit` accepts inline `--ignore-vuln <ID>` flags. CI carried eight of
them, added in one commit and never revisited; a fresh unignored audit found
that seven no longer matched anything the dependency graph resolves, and the
eighth (pytest, PYSEC-2026-1845) had a released fix available the whole time.
An advisory silenced on a command line leaves no expiry, no owner and no
review date, so nothing ever forces the question again.

The invariant is absolute: **zero** Python audit suppressions anywhere in the
repository. There is no allowlist, empty or otherwise, because an empty
allowlist is an invitation — the next person appends one ID and the gate is
back where it started. Reintroducing a suppression has to mean editing this
test, which makes it a reviewed decision instead of a line buried in a diff.

Two stages, because they answer different questions:

1. **Raw bytes.** Every readable tracked file is searched for the literal
   ASCII sequence `--ignore-vuln`, whatever else the file contains. Encoding
   is irrelevant here: a shell helper can hold a perfectly executable ASCII
   command and an invalid UTF-8 byte elsewhere in the same file, so decoding
   first and skipping what fails would hand an attacker a one-byte bypass.
2. **Decoded text.** Files that decode as UTF-8 and carry no NUL are then
   parsed for the semantic checks — that every blocking `pip-audit` call keeps
   `--strict`, keeps OSV, and does not swallow its exit code. That stage needs
   line structure, so it needs text.

The inventory is `git ls-files -z`, not a directory list. An earlier version
walked `.github/workflows`, `.github/scripts` and `scripts`; moving the audit
into `backend/tools/python_audit.sh` and calling it from the workflow would
have left no flag in any scanned file. Git already knows what this repository
contains, and `.git` is never walked.

If a future advisory genuinely cannot be remediated, that is a governance
change: propose it on its own, with the evidence that remediation is
impossible, the way `.trivy-exceptions.yaml` governs container findings.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SUPPRESSION_BYTES = b"--ignore-vuln"

# The one and only filename special case: this file must name the forbidden
# flag in order to forbid it. Nothing else is exempt.
GUARD_FILE = Path(__file__).resolve()

# `pip-audit` is the only spelling this repository invokes. Deliberately not
# generalised to hypothetical `python -m pip_audit` or `pipx run` forms that
# appear nowhere here.
EXECUTABLE = "pip-audit"

# The requirement file, however it is spelled on the command line:
#   -r requirements.txt        --requirement requirements.txt
#   -r ./requirements.txt      --requirement=./requirements.txt
REQUIREMENT_ARG = re.compile(
    r"(?:^|\s)(?:-r|--requirement)(?:=|\s+)(?:\./)?[^\s;|&]*requirements[^\s;|&]*\.txt"
)

ByteInventory = list[tuple[str, bytes]]
TextInventory = list[tuple[str, str]]


def tracked_files(repo_root: Path = REPO_ROOT) -> ByteInventory:
    """Every readable tracked file, as (repository-relative path, raw bytes).

    Git is the authority on what is tracked, so nothing has to be remembered
    in a directory list and `.git` is never walked. `-z` keeps paths intact
    when they contain spaces or non-ASCII bytes.
    """
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    inventory: ByteInventory = []
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
            inventory.append((relative, absolute.read_bytes()))
        except OSError:  # pragma: no cover - unreadable file
            continue
    return inventory


def find_suppressions(inventory: ByteInventory) -> list[str]:
    """Every occurrence of the literal suppression flag, byte-exact.

    No decoding and no binary classification: the flag is ASCII, and a file
    holding one invalid byte is still perfectly capable of executing the
    command that holds it. Offending lines are decoded only to be printed,
    with undecodable bytes replaced so the report stays readable.
    """
    offenders: list[str] = []
    for relative, data in inventory:
        if SUPPRESSION_BYTES not in data:
            continue
        for number, line in enumerate(data.split(b"\n"), start=1):
            if SUPPRESSION_BYTES in line:
                shown = line.decode("utf-8", errors="replace").strip()
                offenders.append(f"{relative}:{number}: {shown}")
    return offenders


def decoded_text_files(inventory: ByteInventory) -> TextInventory:
    """The subset of the inventory that is usable as text.

    Used only for the semantic invocation checks below, which need line
    structure. It is never used to decide whether a suppression exists.
    """
    text: TextInventory = []
    for relative, data in inventory:
        if b"\0" in data:
            continue
        try:
            text.append((relative, data.decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return text


def find_weakened_invocations(inventory: TextInventory) -> list[str]:
    """Every blocking pip-audit call that lost --strict, OSV, or its exit code.

    A blocking call names the executable and points it at a requirements file.
    `pip install pip-audit`, a job name, an artifact name, a path glob and
    prose all mention the executable without auditing anything.
    """
    weakened: list[str] = []
    for relative, text in inventory:
        if EXECUTABLE not in text:
            continue
        # Join line continuations so a multi-line invocation reads as one command.
        joined = text.replace("\\\n", " ")
        for number, line in enumerate(joined.splitlines(), start=1):
            if EXECUTABLE not in line or not REQUIREMENT_ARG.search(line):
                continue
            if "pip install" in line:
                continue
            stripped = line.strip()
            # Comments and documentation describe invocations; they are not one.
            if stripped.startswith("#"):
                continue
            # The post-failure JSON report is deliberately non-blocking and
            # deliberately not strict: it describes a failure the blocking call
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
    inventory = tracked_files()
    assert inventory, "git ls-files returned no tracked files"
    paths = {relative for relative, _ in inventory}
    assert ".github/workflows/ci.yml" in paths, "the CI workflow was not scanned"
    assert "backend/requirements.txt" in paths, "the requirements file was not scanned"


def test_repository_declares_no_python_audit_suppressions() -> None:
    """No tracked file may carry the suppression flag, in any encoding."""
    offenders = find_suppressions(tracked_files())
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


def test_blocking_audit_invocations_stay_strict() -> None:
    """Every blocking pip-audit call keeps --strict, OSV, and a real exit code."""
    weakened = find_weakened_invocations(decoded_text_files(tracked_files()))
    if weakened:
        listing = "\n".join(f"  {entry}" for entry in weakened)
        pytest.fail(f"Weakened pip-audit invocation(s):\n{listing}", pytrace=False)


# --------------------------------------------------------------------------
# Adversarial coverage, driven over synthetic inventories so no fake
# vulnerable helper is ever written into the repository.
# --------------------------------------------------------------------------

HELPER_PATH = "backend/tools/python_audit.sh"

CLEAN_HELPER = """#!/usr/bin/env bash
set -euo pipefail
pip-audit -r requirements.txt \\
  --vulnerability-service osv \\
  --strict
"""


def _git_repo_with(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """A throwaway Git repository, so the REAL discovery path can be exercised."""
    for name, payload in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_suppression_survives_invalid_utf8_end_to_end(tmp_path: Path) -> None:
    """The bypass the previous version had: valid ASCII command, one bad byte.

    The helper is genuinely executable — the suppression line is plain ASCII —
    while a stray 0xFF elsewhere makes the file undecodable. Skipping it as
    "not text" would have hidden a live suppression.
    """
    helper = (
        b"#!/usr/bin/env bash\n"
        b"# caf\xe9 \xff invalid utf-8 lives here\n"
        b"pip-audit -r requirements.txt \\\n"
        b"  --vulnerability-service osv \\\n"
        b"  --strict \\\n"
        b"  --ignore-vuln PYSEC-EXAMPLE\n"
    )
    repo = _git_repo_with(tmp_path, {HELPER_PATH: helper, "README.md": b"ok\n"})

    inventory = tracked_files(repo)
    assert HELPER_PATH in {p for p, _ in inventory}, "undecodable file was dropped"

    # It genuinely does not decode, so the text stage cannot see it...
    assert HELPER_PATH not in {p for p, _ in decoded_text_files(inventory)}
    # ...but the raw-byte stage must still catch the suppression.
    offenders = find_suppressions(inventory)
    assert offenders, "a suppression hidden behind an invalid UTF-8 byte was missed"
    assert HELPER_PATH in offenders[0] and "PYSEC-EXAMPLE" in offenders[0]


def test_ordinary_binary_files_do_not_false_positive(tmp_path: Path) -> None:
    """Binaries without the sequence must stay silent."""
    repo = _git_repo_with(
        tmp_path,
        {
            "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00binary\x00payload\xff\xfe",
            "data.bin": bytes(range(256)),
            "README.md": b"nothing to see\n",
        },
    )
    assert find_suppressions(tracked_files(repo)) == []


def test_suppression_in_a_helper_outside_the_old_directories_is_detected() -> None:
    """The directory-scoped blind spot, in plain UTF-8."""
    suppressed = CLEAN_HELPER.replace(
        "  --strict\n", "  --strict \\\n  --ignore-vuln PYSEC-EXAMPLE\n"
    )
    offenders = find_suppressions([(HELPER_PATH, suppressed.encode())])
    assert offenders and HELPER_PATH in offenders[0] and "PYSEC-EXAMPLE" in offenders[0]


def test_a_clean_helper_outside_the_old_directories_is_accepted() -> None:
    """Being repository-wide must not mean flagging correct helpers."""
    assert find_suppressions([(HELPER_PATH, CLEAN_HELPER.encode())]) == []
    assert find_weakened_invocations([(HELPER_PATH, CLEAN_HELPER)]) == []


# -- alternate requirement spellings must not bypass the strictness check ----


@pytest.mark.parametrize(
    "invocation",
    [
        "pip-audit -r requirements.txt --vulnerability-service osv",
        "pip-audit -r ./requirements.txt --vulnerability-service osv",
        "pip-audit --requirement requirements.txt --vulnerability-service osv",
        "pip-audit --requirement=./requirements.txt --vulnerability-service osv",
        "pip-audit --requirement=backend/requirements.txt --vulnerability-service osv",
    ],
)
def test_dropping_strict_is_detected_in_every_requirement_spelling(invocation: str) -> None:
    weakened = find_weakened_invocations([(HELPER_PATH, invocation + "\n")])
    assert any("missing --strict" in entry for entry in weakened), (invocation, weakened)


@pytest.mark.parametrize(
    "invocation",
    [
        "pip-audit -r ./requirements.txt --strict",
        "pip-audit --requirement requirements.txt --strict",
        "pip-audit --requirement=./requirements.txt --strict",
    ],
)
def test_switching_away_from_osv_is_detected_in_every_spelling(invocation: str) -> None:
    weakened = find_weakened_invocations([(HELPER_PATH, invocation + "\n")])
    assert any("not using OSV" in entry for entry in weakened), (invocation, weakened)


def test_swallowing_failure_with_or_true_is_detected() -> None:
    swallowed = CLEAN_HELPER.replace("  --strict\n", "  --strict || true\n")
    weakened = find_weakened_invocations([(HELPER_PATH, swallowed)])
    assert any("failure swallowed" in entry for entry in weakened), weakened


def test_a_mention_is_not_mistaken_for_an_invocation() -> None:
    """Installs, job names, artifact names, comments and prose must not trip it."""
    noise = "\n".join(
        [
            "      - name: Install pip-audit",
            "        run: pip install pip-audit==2.7.3",
            "      - name: Upload full pip-audit report",
            "          name: pip-audit-report",
            "        # pip-audit -r requirements.txt is described here, not run",
            "  pip-audit:",
            "The pip-audit gate runs with --strict and OSV; see the report.",
        ]
    )
    assert find_weakened_invocations([(".github/workflows/example.yml", noise)]) == []


def test_the_json_diagnostic_command_is_exempt() -> None:
    """The post-failure report is not the gate and is deliberately non-blocking."""
    diagnostic = (
        "run: pip-audit -r requirements.txt --vulnerability-service osv "
        "--format=json > /tmp/x.json || true"
    )
    assert find_weakened_invocations([(".github/workflows/example.yml", diagnostic)]) == []
