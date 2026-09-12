"""Production Runtime Foundation V1 — the deployment artefacts, held to their contract.

These tests read the real files. There is no fixture copy of `render.yaml` and
no sample Dockerfile, because a test that agrees with its own fixture proves
nothing about what would actually deploy.

What is being defended, in one sentence each:

* **Nothing deploys itself.** A deployment is an operator choosing one exact
  reviewed commit. Automatic deploy from Git being switched on is the failure.
* **Nothing activates itself.** Deploying the software and governing what the
  product may say are separate events. No start command, pre-deploy command or
  cron may reach the Phase B operator.
* **Nothing leaks.** The public readiness endpoint and the release log both sit
  where a database driver's message — which contains the connection string —
  could reach an operator's terminal or an unauthenticated caller.
* **Two stores stay two.** No Render database is introduced, and the app and
  Open Food Facts authorities stay physically distinct.

The sentinel tests are the load-bearing ones. They inject a fake credential
into a fake failure and assert it cannot reach a caller or a log.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import subprocess
from pathlib import Path

import pytest


@contextlib.contextmanager
def _preserving_the_event_loop():
    """Call something that runs ``asyncio.run()`` without breaking the session.

    ``app.release.main()`` is a synchronous entrypoint whose whole job is to
    call ``asyncio.run(release())``. ``asyncio.run`` sets the current event
    loop to ``None`` when it finishes, and this suite runs on a session-scoped
    pytest-asyncio loop — so calling ``main()`` bare leaves every async test
    that runs afterwards to fail with "There is no current event loop".

    Testing the real entrypoint is worth this: the ``except Exception`` branch
    it guards is exactly where a driver's connection string would escape.
    """
    try:
        saved = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:  # pragma: no cover - no loop installed yet
        saved = None
    try:
        yield
    finally:
        if saved is not None and not saved.is_closed():
            asyncio.set_event_loop(saved)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

RENDER_YAML = REPOSITORY_ROOT / "render.yaml"
PRODUCTION_DOCKERFILE = REPOSITORY_ROOT / "deploy" / "render" / "Dockerfile"
PRODUCTION_ENTRYPOINT = REPOSITORY_ROOT / "deploy" / "render" / "entrypoint.sh"
ROOT_DOCKERIGNORE = REPOSITORY_ROOT / ".dockerignore"
BACKEND_DOCKERFILE = BACKEND_ROOT / "Dockerfile"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
PHASE_B_OPERATOR = REPOSITORY_ROOT / "scripts" / "operate_step8i_petrolatum_release.py"

#: The one string that must never come back out of a redacted failure.
SENTINEL = "PRODUCTION_RUNTIME_SECRET_SENTINEL"
#: A connection string shaped exactly like the one asyncpg puts in its errors.
FAKE_CONNECTION_URL = (
    f"postgresql://gg_prod_user:{SENTINEL}@db.prod-sentinel.example.internal:5432/glamgenius"
)
FAKE_DRIVER_MESSAGE = (
    f'connection to server at "db.prod-sentinel.example.internal" failed: '
    f'password authentication failed for user "gg_prod_user" ({FAKE_CONNECTION_URL})'
)


# ---------------------------------------------------------------------------
# Reading the Blueprint without adding a dependency
# ---------------------------------------------------------------------------
# PyYAML is not a backend dependency and this milestone does not add one, so
# the Blueprint is read by a deliberately small parser that understands only
# the shapes render.yaml actually uses: block mappings, block sequences of
# mappings, comments, and plain or quoted scalars.
#
# It is strict on purpose. Anything it does not recognise raises rather than
# being skipped, so render.yaml cannot quietly drift into a construct this
# reader would misread while the assertions below carried on passing.
#
# Scalar typing follows YAML 1.1, the same as PyYAML: a bare `off` is the
# boolean false, not the string. That is what makes the quoting of
# `autoDeployTrigger: "off"` a real requirement rather than a style choice,
# and `test_the_minimal_reader_agrees_with_pyyaml` pins the equivalence
# wherever PyYAML happens to be installed.

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}


def _scalar(raw: str) -> object:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    if lowered in {"null", "~", ""}:
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    for bad in ("{", "[", "|", ">", "&", "*"):
        if raw.startswith(bad):
            raise AssertionError(f"render.yaml uses a YAML construct this reader rejects: {raw!r}")
    return raw


def _lines() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in RENDER_YAML.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or not line.strip():
            continue
        if line.startswith("---") or line.startswith("..."):
            raise AssertionError("render.yaml must stay a single YAML document")
        out.append((len(line) - len(line.lstrip(" ")), line.strip()))
    return out


def _parse_block(rows: list[tuple[int, str]], start: int, indent: int) -> tuple[object, int]:
    index = start
    if rows[index][1].startswith("- "):
        items: list[object] = []
        while index < len(rows) and rows[index][0] == indent and rows[index][1].startswith("- "):
            depth, text = rows[index]
            inner = [(depth + 2, text[2:])]
            index += 1
            while index < len(rows) and rows[index][0] > depth:
                inner.append(rows[index])
                index += 1
            value, consumed = _parse_block(inner, 0, inner[0][0])
            assert consumed == len(inner), f"unparsed remainder in render.yaml near {text!r}"
            items.append(value)
        return items, index - start

    mapping: dict[str, object] = {}
    while index < len(rows) and rows[index][0] == indent:
        depth, text = rows[index]
        assert ":" in text, f"render.yaml line is neither a mapping nor a sequence item: {text!r}"
        key, _, rest = text.partition(":")
        key = key.strip()
        index += 1
        if rest.strip():
            mapping[key] = _scalar(rest)
            continue
        child = [row for row in rows[index:]]
        nested_end = 0
        while nested_end < len(child) and child[nested_end][0] > depth:
            nested_end += 1
        assert nested_end, f"render.yaml key {key!r} has no value"
        value, consumed = _parse_block(child[:nested_end], 0, child[0][0])
        assert consumed == nested_end
        mapping[key] = value
        index += nested_end
    return mapping, index - start


def _blueprint() -> dict:
    rows = _lines()
    parsed, consumed = _parse_block(rows, 0, 0)
    assert consumed == len(rows), "render.yaml was not fully parsed"
    assert isinstance(parsed, dict)
    return parsed


def _service(name: str) -> dict:
    for service in _blueprint()["services"]:
        if service["name"] == name:
            return service
    raise AssertionError(f"{name} is not declared in render.yaml")


API = "glamgenius-api"
#: There is one service now. This used to be three — the API, an always-on
#: deletion worker and a Render cron — and the two batch processes moved
#: behind HTTP when the runtime moved to Render's free tier, which has
#: neither background workers nor cron. The jobs did not go away; the
#: invariants that used to be asserted against those services are asserted
#: below against the scheduled cycles and the runbook that invokes them.
ALL_SERVICES = (API,)

#: The Supabase Cron job names, which are now where the two batch schedules
#: live. Documented in docs/OPERATIONS.md; this repository does not install
#: them.
DELETION_JOB = "glamgenius-account-deletion"
NOTIFICATION_JOB = "glamgenius-notifications"

#: The heading the runbook section carries, in one place: every runbook test
#: below slices the document at it.
RUNBOOK_HEADING = "## Render Pre-PMF Runtime (zero cost)"


def _runbook_section() -> str:
    """The runtime section of docs/OPERATIONS.md, and only that section.

    Sliced rather than read whole: an assertion satisfied by a sentence in the
    systemd guidance thirty pages earlier would prove nothing about the Render
    runbook.
    """
    operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert RUNBOOK_HEADING in operations
    return operations.split(RUNBOOK_HEADING, 1)[1]


# ---------------------------------------------------------------------------
# Render topology
# ---------------------------------------------------------------------------
class TestRenderTopology:
    def test_the_blueprint_exists_and_parses(self) -> None:
        assert RENDER_YAML.is_file()
        assert isinstance(_blueprint().get("services"), list)

    def test_the_minimal_reader_agrees_with_pyyaml(self) -> None:
        """Equivalence check, wherever PyYAML is installed.

        Skipped in the backend suite, which deliberately has no PyYAML. It
        runs anywhere the library is present and is the reason the reader
        above can be trusted not to quietly disagree with what Render parses.
        """
        yaml = pytest.importorskip("yaml")
        assert _blueprint() == yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))

    def test_exactly_one_free_web_service_is_declared(self) -> None:
        declared = {service["name"]: service["type"] for service in _blueprint()["services"]}
        assert declared == {API: "web"}

    def test_the_one_service_is_on_the_free_plan(self) -> None:
        # The whole point of this shape. A paid plan here would be a bill
        # nobody agreed to, arriving before there is any evidence of demand.
        assert _service(API)["plan"] == "free"

    def test_no_worker_or_cron_service_is_declared(self) -> None:
        # Free tier has neither. A declared one would either fail the sync or
        # silently start charging.
        for service in _blueprint()["services"]:
            assert service["type"] not in {"worker", "cron"}, service["name"]

    @pytest.mark.parametrize("name", ALL_SERVICES)
    def test_every_service_runs_in_singapore(self, name: str) -> None:
        assert _service(name)["region"] == "singapore"

    @pytest.mark.parametrize("name", ALL_SERVICES)
    def test_no_service_deploys_itself_from_git(self, name: str) -> None:
        service = _service(name)
        # A bare `off` is the YAML 1.1 boolean false, which is not the string
        # the schema expects, so the quoting is part of the contract.
        assert service["autoDeployTrigger"] == "off"

    @pytest.mark.parametrize("name", ALL_SERVICES)
    def test_exactly_one_field_decides_automatic_deployment(self, name: str) -> None:
        # This file used to require both autoDeploy and autoDeployTrigger, on
        # the reasoning that a deployment nobody chose is expensive enough to
        # deserve two locks. That was wrong in a way only the Blueprint parser
        # could tell us: two fields for one decision is two sources of truth,
        # and the deprecated one is the one that must go.
        assert "autoDeploy" not in _service(name)

    def test_the_notification_job_runs_exactly_once_an_hour(self) -> None:
        # The schedule moved out of render.yaml and into Supabase Cron, so it
        # is asserted where it now lives: the interval the application expects
        # and the cron expression the runbook tells the operator to install.
        from app.workers import schedule

        assert schedule.NOTIFICATION_INTERVAL_SECONDS == 3600
        runbook = _runbook_section()
        assert NOTIFICATION_JOB in runbook
        assert "`0 * * * *`" in runbook

    def test_the_deletion_job_runs_every_five_minutes(self) -> None:
        from app.workers import schedule

        assert schedule.ACCOUNT_DELETION_INTERVAL_SECONDS == 300
        runbook = _runbook_section()
        assert DELETION_JOB in runbook
        assert "`*/5 * * * *`" in runbook

    def test_the_notification_job_is_a_schedule_and_not_a_daemon(self) -> None:
        # It was a Render cron; it is now a Supabase Cron POST. Either way the
        # worker must stay a batch that runs once and exits — a daemon loop in
        # this module would mean an hourly job that never ends.
        notifications = (BACKEND_ROOT / "app" / "workers" / "notifications.py").read_text(
            encoding="utf-8"
        )
        assert "def run_cycle(" in notifications
        assert "def run_forever(" not in notifications

    def test_readiness_is_the_exact_v2_ready_path(self) -> None:
        assert _service(API)["healthCheckPath"] == "/api/v2/ready"

    def test_readiness_is_not_the_liveness_path(self) -> None:
        # /api/v2/health makes no network calls, so using it as the readiness
        # probe would let Render send customer traffic to a process that
        # cannot reach PostgreSQL, has no seed data and has not migrated.
        assert _service(API)["healthCheckPath"] != "/api/v2/health"

    def test_no_render_database_is_introduced(self) -> None:
        blueprint = _blueprint()
        assert "databases" not in blueprint
        for service in blueprint["services"]:
            assert service["type"] not in {"pserv", "redis", "keyvalue"}

    @pytest.mark.parametrize("name", ALL_SERVICES)
    def test_no_service_claims_a_persistent_disk(self, name: str) -> None:
        assert "disk" not in _service(name)

    @pytest.mark.parametrize("name", ALL_SERVICES)
    def test_every_service_builds_the_same_production_image(self, name: str) -> None:
        service = _service(name)
        assert service["runtime"] == "docker"
        assert service["dockerfilePath"] == "./deploy/render/Dockerfile"
        assert service["dockerContext"] == "."

    @pytest.mark.parametrize("name", ALL_SERVICES)
    def test_every_workload_reads_the_same_configuration(self, name: str) -> None:
        groups = {entry["fromGroup"] for entry in _service(name)["envVars"] if "fromGroup" in entry}
        assert groups == {
            "glamgenius-production-invariants",
            "glamgenius-production-secrets",
        }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
class TestCommands:
    def test_the_api_binds_all_interfaces_on_renders_runtime_port(self) -> None:
        command = _service(API)["dockerCommand"]
        assert "--host 0.0.0.0" in command
        assert "$PORT" in command
        assert "uvicorn server:app" in command

    def test_the_api_does_not_hard_code_a_port(self) -> None:
        # A literal port would be ignored by Render's router and the service
        # would never receive traffic.
        assert not re.search(r"--port\s+\d+", _service(API)["dockerCommand"])

    def test_the_deletion_job_reaches_the_canonical_worker(self) -> None:
        # The command moved to an HTTP call, so what must be canonical is what
        # the route calls. Not a second deletion implementation behind a door.
        scheduler = (BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py").read_text(
            encoding="utf-8"
        )
        assert "account_deletion.run_cycle()" in scheduler
        assert _runbook_section().count(
            "/api/v2/internal/scheduler/account-deletion"
        ) >= 1

    def test_the_notification_job_reaches_the_canonical_worker(self) -> None:
        scheduler = (BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py").read_text(
            encoding="utf-8"
        )
        assert "notifications.run_cycle()" in scheduler
        assert _runbook_section().count("/api/v2/internal/scheduler/notifications") >= 1

    def test_the_batch_work_is_not_reachable_from_a_customer_route(self) -> None:
        # What "the worker is not the API" means once they share a process:
        # the only module that may invoke a worker cycle is the scheduler
        # router, and it is behind a shared secret rather than a user token.
        v2 = BACKEND_ROOT / "app" / "api" / "v2"
        callers = [
            path.name
            for path in sorted(v2.glob("*.py"))
            if "run_cycle()" in path.read_text(encoding="utf-8")
        ]
        assert callers == ["internal_scheduler.py"], callers

    def test_the_two_scheduled_cycles_are_not_the_same_process(self) -> None:
        from app.workers import account_deletion, notifications

        assert account_deletion.run_cycle is not notifications.run_cycle

    def test_the_release_gate_is_the_canonical_release_entrypoint(self) -> None:
        # Not a bash re-implementation of migrate-then-seed. app.release already
        # owns config validation, the advisory lock, alembic upgrade, alembic
        # check, Store A provisioning, seeding and the consistency checks.
        #
        # It used to be preDeployCommand, which is a paid feature. It is now
        # the first half of the start command, joined with `&&` so a failed
        # release cannot be followed by a served request.
        service = _service(API)
        assert "preDeployCommand" not in service
        command = service["dockerCommand"]
        assert "python -m app.release && exec uvicorn" in command
        assert "python -m app.release ;" not in command
        assert "|| true" not in command

    def test_no_second_migration_lock_is_introduced(self) -> None:
        # The PostgreSQL advisory lock in app.release stays the only
        # concurrency authority for the two pre-deploy commands.
        blueprint_text = RENDER_YAML.read_text(encoding="utf-8")
        for competing in ("flock", "lockfile", "advisory_lock(", "pg_advisory"):
            assert competing not in blueprint_text
        assert "pg_advisory_lock" in (BACKEND_ROOT / "app" / "release.py").read_text(encoding="utf-8")

    def test_no_command_anywhere_selects_something_by_recency(self) -> None:
        text = RENDER_YAML.read_text(encoding="utf-8").lower()
        for selector in (":latest", "latest-tag", "--latest", "newest", "head~"):
            assert selector not in text


# ---------------------------------------------------------------------------
# The Phase B operator: present, never automatic
# ---------------------------------------------------------------------------
class TestPhaseBOperatorIsManualOnly:
    def test_the_exact_operator_is_in_the_production_build_context(self) -> None:
        assert PHASE_B_OPERATOR.is_file()
        # Comment-stripped, and specifically a COPY. Mutation testing caught
        # this: deleting the COPY line left the operator's path in the header
        # comment that explains why it is copied, and a raw-text scan happily
        # passed on the prose while the image no longer contained the file.
        copies = [
            line for line in PRODUCTION_DOCKERFILE.read_text(encoding="utf-8").splitlines()
            if line.strip().upper().startswith("COPY ")
        ]
        assert any("scripts/operate_step8i_petrolatum_release.py" in line for line in copies), (
            "the production image must COPY the Phase B operator"
        )

    def test_the_dockerignore_does_not_exclude_the_operator(self) -> None:
        ignore = ROOT_DOCKERIGNORE.read_text(encoding="utf-8")
        assert "!scripts/operate_step8i_petrolatum_release.py" in ignore

    def test_the_image_preserves_the_sibling_layout_the_operator_expects(self) -> None:
        # The operator resolves parents[1] / "backend" onto sys.path, so it
        # needs backend/ and scripts/ to remain siblings. Flattening them
        # would be the moment somebody "fixes" it by moving the operator into
        # the application, which would delete the boundary it exists to make.
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        assert "/workspace/backend/" in dockerfile
        assert "/workspace/scripts/" in dockerfile
        assert 'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))' in (
            PHASE_B_OPERATOR.read_text(encoding="utf-8")
        )

    def test_no_render_command_invokes_the_operator(self) -> None:
        blueprint = _blueprint()
        command_fields = ("dockerCommand", "preDeployCommand", "startCommand", "buildCommand")
        for service in blueprint["services"]:
            for field in command_fields:
                command = service.get(field, "")
                assert "operate_step8i" not in command, f"{service['name']}.{field}"
                for operation in ("prepare", "compile", "activate", "deactivate", "status"):
                    assert f"petrolatum_release.py {operation}" not in command

    def test_the_blueprint_never_names_the_operator_at_all(self) -> None:
        # Not even in a comment that a future edit could uncomment.
        for line in RENDER_YAML.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            assert "operate_step8i" not in code

    def test_the_image_never_invokes_the_operator(self) -> None:
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        for line in dockerfile.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            directive = stripped.split(None, 1)[0].upper()
            if directive in {"RUN", "CMD", "ENTRYPOINT", "HEALTHCHECK"}:
                assert "operate_step8i" not in stripped

    def test_the_entrypoint_starts_only_what_it_was_given(self) -> None:
        entrypoint = PRODUCTION_ENTRYPOINT.read_text(encoding="utf-8")
        assert 'exec "$@"' in entrypoint
        assert "operate_step8i" not in entrypoint
        for privileged in ("alembic", "app.release", "app.bootstrap"):
            assert privileged not in entrypoint

    def test_normal_runtime_still_cannot_import_the_knowledge_pack(self) -> None:
        # Restated here because this milestone puts the pack in the same image
        # as the API for the first time. Being in the image must not be the
        # same thing as being reachable from the application.
        allowed = {
            BACKEND_ROOT / "app" / "knowledge_packs" / "__init__.py",
            BACKEND_ROOT / "app" / "knowledge_packs" / "petrolatum_dry_skin_v1.py",
        }
        offenders = [
            path.relative_to(BACKEND_ROOT).as_posix()
            for path in (BACKEND_ROOT / "app").rglob("*.py")
            if path not in allowed and "app.knowledge_packs" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_no_http_route_reaches_the_operator(self) -> None:
        api_root = BACKEND_ROOT / "app" / "api"
        for path in api_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "operate_step8i" not in text
            assert "knowledge_packs" not in text


# ---------------------------------------------------------------------------
# The production image
# ---------------------------------------------------------------------------
class TestProductionImage:
    def test_the_dockerfile_and_entrypoint_exist(self) -> None:
        assert PRODUCTION_DOCKERFILE.is_file()
        assert PRODUCTION_ENTRYPOINT.is_file()

    def test_the_runtime_user_is_not_root(self) -> None:
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", dockerfile, flags=re.MULTILINE)
        assert users, "the production image must declare a USER"
        assert users[-1] == "10001"
        assert "root" not in users

    def test_the_base_image_is_pinned_by_digest(self) -> None:
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        froms = re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)
        assert froms
        for image in froms:
            assert "@sha256:" in image, image
            assert ":latest" not in image

    def test_the_base_digest_matches_the_backend_image(self) -> None:
        # One digest, one update script. Two production images on different
        # base layers would mean two different patch levels in one deployment.
        def digests(path: Path) -> set[str]:
            return set(re.findall(r"FROM\s+\S+@(sha256:[0-9a-f]{64})", path.read_text(encoding="utf-8")))

        assert digests(PRODUCTION_DOCKERFILE) == digests(BACKEND_DOCKERFILE)

    def test_the_build_is_multi_stage_and_ships_no_compiler(self) -> None:
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        assert len(re.findall(r"^FROM ", dockerfile, flags=re.MULTILINE)) >= 2
        runtime_stage = dockerfile.split("AS runtime", 1)[1]
        assert "build-essential" not in runtime_stage

    def test_pid_one_handles_signals(self) -> None:
        assert "tini" in PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")

    def test_liveness_targets_the_route_that_exists(self) -> None:
        for dockerfile in (PRODUCTION_DOCKERFILE, BACKEND_DOCKERFILE):
            healthcheck = [
                line for line in dockerfile.read_text(encoding="utf-8").splitlines()
                if "urllib.request.urlopen" in line
            ]
            assert healthcheck, dockerfile
            probe = healthcheck[0]
            assert "/api/v2/health" in probe, dockerfile
            # Liveness must not depend on PostgreSQL.
            assert "/api/v2/ready" not in probe, dockerfile

    def test_no_secret_is_taken_as_a_build_argument(self) -> None:
        args = re.findall(r"^ARG\s+([A-Z_]+)", PRODUCTION_DOCKERFILE.read_text(encoding="utf-8"), flags=re.MULTILINE)
        forbidden = ("KEY", "TOKEN", "SECRET", "PASSWORD", "URL", "DSN")
        for arg in args:
            assert not any(word in arg for word in forbidden), arg

    def test_the_build_context_excludes_what_must_not_ship(self) -> None:
        ignore = {
            line.strip()
            for line in ROOT_DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        for required in (".env", ".git/", "frontend/", "backend/tests/", "**/__pycache__/"):
            assert required in ignore, required

    def test_the_frontend_is_not_copied_into_the_production_image(self) -> None:
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        copies = re.findall(r"^COPY\s+(.*)$", dockerfile, flags=re.MULTILINE)
        for copy in copies:
            assert "frontend" not in copy, copy


# ---------------------------------------------------------------------------
# Exact-commit provenance
# ---------------------------------------------------------------------------
class TestCommitProvenance:
    def test_the_runtime_derives_commit_sha_from_renders_git_commit(self) -> None:
        entrypoint = PRODUCTION_ENTRYPOINT.read_text(encoding="utf-8")
        assert "RENDER_GIT_COMMIT" in entrypoint
        assert "COMMIT_SHA" in entrypoint
        assert "export COMMIT_SHA" in entrypoint

    def test_the_entrypoint_is_wired_into_the_image(self) -> None:
        dockerfile = PRODUCTION_DOCKERFILE.read_text(encoding="utf-8")
        assert "glamgenius-entrypoint" in dockerfile
        entrypoint_line = [
            line for line in dockerfile.splitlines() if line.startswith("ENTRYPOINT")
        ]
        assert entrypoint_line
        assert "glamgenius-entrypoint" in entrypoint_line[0]
        assert "tini" in entrypoint_line[0]

    def test_both_workers_record_the_commit_on_their_heartbeat(self) -> None:
        # Both resolve it through one helper now, rather than each reading the
        # environment its own way — which is how they came to disagree about
        # whether RENDER_GIT_COMMIT counted. The helper is asserted separately
        # to read COMMIT_SHA first.
        from app.workers import schedule

        for worker in ("account_deletion.py", "notifications.py"):
            text = (BACKEND_ROOT / "app" / "workers" / worker).read_text(encoding="utf-8")
            assert "service_version" in text, worker
        assert "COMMIT_SHA" in (
            BACKEND_ROOT / "app" / "workers" / "schedule.py"
        ).read_text(encoding="utf-8")
        monkey = os.environ.get("COMMIT_SHA")
        os.environ["COMMIT_SHA"] = "deadbeefdeadbeef"
        try:
            assert schedule.service_version() == "deadbeefdeadbeef"
        finally:
            if monkey is None:
                del os.environ["COMMIT_SHA"]
            else:
                os.environ["COMMIT_SHA"] = monkey

    def test_no_source_file_hard_codes_a_moving_production_commit(self) -> None:
        # A 40-hex literal in deployment source or application source would be
        # a provenance claim that goes stale the next time main moves.
        candidates = [RENDER_YAML, PRODUCTION_DOCKERFILE, PRODUCTION_ENTRYPOINT]
        candidates += sorted((BACKEND_ROOT / "app").rglob("*.py"))
        pattern = re.compile(r"\b[0-9a-f]{40}\b")
        offenders = []
        for path in candidates:
            for line in path.read_text(encoding="utf-8").splitlines():
                code = line.split("#", 1)[0]
                if pattern.search(code):
                    offenders.append(f"{path.relative_to(REPOSITORY_ROOT)}: {line.strip()[:80]}")
        assert offenders == []


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
class TestNoSecretsInDeploymentConfiguration:
    def test_the_blueprint_declares_no_secret_group(self) -> None:
        blueprint = _blueprint()
        declared = {group["name"] for group in blueprint.get("envVarGroups", [])}
        # The secrets group is created in the dashboard on purpose. Declaring
        # it here would either require values in Git or let a Blueprint sync
        # overwrite what an operator typed.
        assert "glamgenius-production-secrets" not in declared
        assert declared == {"glamgenius-production-invariants"}

    def test_every_declared_value_is_a_non_secret_invariant(self) -> None:
        blueprint = _blueprint()
        allowed = {
            "APP_ENV": "production",
            "INVITE_REQUIRED": "true",
            "REQUIRE_ANALYSIS_CONSENT": "true",
            "MEDIA_STORAGE_BACKEND": "supabase",
            "MEDIA_ALLOW_LOCAL_IN_PRODUCTION": "false",
            # How many proxies sit in front of the container. A deployment
            # fact, not a secret — and the blueprint is where a deployment
            # fact belongs. See app/shared/security/network.py.
            "TRUSTED_PROXY_HOPS": "1",
        }
        for group in blueprint["envVarGroups"]:
            for entry in group["envVars"]:
                assert entry["key"] in allowed, entry["key"]
                assert entry["value"] == allowed[entry["key"]], entry["key"]

    def test_no_service_carries_an_inline_environment_value(self) -> None:
        for service in _blueprint()["services"]:
            for entry in service.get("envVars", []):
                assert "value" not in entry, entry
                assert "fromGroup" in entry

    @pytest.mark.parametrize(
        "path",
        [RENDER_YAML, PRODUCTION_DOCKERFILE, PRODUCTION_ENTRYPOINT, ROOT_DOCKERIGNORE],
        ids=lambda p: p.name,
    )
    def test_no_deployment_file_contains_anything_credential_shaped(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        patterns = {
            "postgres connection string with credentials": r"postgres(?:ql)?(?:\+\w+)?://[^\s\"']*:[^\s\"'@]+@",
            "supabase project url": r"https://[a-z0-9]{20}\.supabase\.co",
            "supabase key": r"\bsb[ph]_[A-Za-z0-9_-]{16,}",
            "json web token": r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.",
            "google api key": r"\bAIza[0-9A-Za-z_-]{30,}",
            "sentry dsn": r"https://[0-9a-f]{16,}@[^\s/]+/\d+",
        }
        for label, pattern in patterns.items():
            assert not re.search(pattern, text), f"{path.name} looks like it contains a {label}"

    def test_the_sentinel_itself_would_be_caught(self) -> None:
        # A mutation check on the check: if the pattern set above stopped
        # matching, this is what would notice.
        pattern = r"postgres(?:ql)?(?:\+\w+)?://[^\s\"']*:[^\s\"'@]+@"
        assert re.search(pattern, FAKE_CONNECTION_URL)

    def test_no_environment_file_is_committed(self) -> None:
        committed = subprocess.run(
            ["git", "ls-files"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        for path in committed:
            name = Path(path).name
            assert name != ".env", path
            assert not name.startswith(".env."), path


# ---------------------------------------------------------------------------
# The ODbL wall, at the deployment layer
# ---------------------------------------------------------------------------
class TestTwoStoresStayTwo:
    def test_the_blueprint_introduces_no_database_of_its_own(self) -> None:
        # Comment-stripped: the file explains at length why there is no
        # `databases:` block, and a raw-text scan cannot tell that prose from
        # the declaration it forbids.
        code = "\n".join(
            line.split("#", 1)[0]
            for line in RENDER_YAML.read_text(encoding="utf-8").splitlines()
        )
        assert "databases:" not in code
        assert "databases" not in _blueprint()

    def test_the_two_database_urls_are_still_required_to_differ(self) -> None:
        config = (BACKEND_ROOT / "app" / "config.py").read_text(encoding="utf-8")
        assert "OFF_DATABASE_URL == POSTGRES_URL" in config
        assert "physically distinct" in config

    def test_the_validator_actually_refuses_when_they_match(self, monkeypatch) -> None:
        import app.config as config_mod

        shared = "postgresql+asyncpg://user:pw@db.example.internal:5432/glamgenius"
        monkeypatch.setattr(config_mod, "APP_ENV", "production")
        monkeypatch.setattr(config_mod, "POSTGRES_URL", shared)
        monkeypatch.setattr(config_mod, "OFF_DATABASE_URL", shared)
        with pytest.raises(RuntimeError) as caught:
            config_mod.validate_production_configuration()
        assert "OFF_DATABASE_URL" in str(caught.value)


# ---------------------------------------------------------------------------
# Redaction: the public readiness endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestReadinessNeverLeaks:
    async def test_a_driver_failure_cannot_reach_an_unauthenticated_caller(
        self, app_client, db_clean, monkeypatch, caplog
    ) -> None:
        """Inject a real-shaped driver error and read the public response.

        This is the endpoint an unauthenticated caller can hit and Render polls
        continuously, so a connection string in its body is a credential the
        whole internet can read.
        """
        import app.api.v2.config as config_mod
        import app.domains.ai_gateway.providers.gemini as gemini_mod

        monkeypatch.setattr(gemini_mod, "is_configured", lambda: True)

        def explode(*_args, **_kwargs):
            raise RuntimeError(FAKE_DRIVER_MESSAGE)

        # Every component that catches a broad exception, poisoned at once.
        monkeypatch.setattr(config_mod, "validate_production_configuration", explode)
        monkeypatch.setattr(config_mod.flags, "warn_if_essentials_disabled", explode)

        original_execute = config_mod.AsyncSession.execute

        async def poisoned_execute(self, statement, *args, **kwargs):
            raise RuntimeError(FAKE_DRIVER_MESSAGE)

        monkeypatch.setattr(config_mod.AsyncSession, "execute", poisoned_execute)
        try:
            with caplog.at_level(logging.DEBUG):
                response = await app_client.get("/api/v2/ready")
        finally:
            monkeypatch.setattr(config_mod.AsyncSession, "execute", original_execute)

        body = response.text
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

        for forbidden in (
            SENTINEL,
            FAKE_CONNECTION_URL,
            "db.prod-sentinel.example.internal",
            "gg_prod_user",
            "password authentication failed",
        ):
            assert forbidden not in body, f"{forbidden!r} escaped through /api/v2/ready"
            assert forbidden not in caplog.text, f"{forbidden!r} escaped into the request log"

    async def test_the_endpoint_still_says_which_component_is_unhappy(
        self, app_client, db_clean, monkeypatch
    ) -> None:
        # Redaction must not become silence: an operator still needs to know
        # what is wrong, just not in the driver's words.
        import app.api.v2.config as config_mod
        import app.domains.ai_gateway.providers.gemini as gemini_mod

        monkeypatch.setattr(gemini_mod, "is_configured", lambda: True)

        def explode(*_args, **_kwargs):
            raise RuntimeError(FAKE_DRIVER_MESSAGE)

        monkeypatch.setattr(config_mod, "validate_production_configuration", explode)
        response = await app_client.get("/api/v2/ready")
        assert response.json()["components"]["production_config"] == "invalid"

    async def test_liveness_stays_independent_of_the_database(self, app_client) -> None:
        response = await app_client.get("/api/v2/health")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"
        assert "components" not in response.json()


class TestReadinessSourceBoundary:
    def test_no_readiness_component_interpolates_an_exception(self) -> None:
        source = (BACKEND_ROOT / "app" / "api" / "v2" / "config.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        # Strip the module docstring, which quotes the banned pattern in order
        # to explain why it is banned.
        code = re.sub(r'"""[\s\S]*?"""', "", code, count=1)
        assert "except Exception as e" not in code
        assert "except RuntimeError as e" not in code
        assert not re.search(r'f"[^"]*\{e\}', code)
        assert not re.search(r"f'[^']*\{e\}", code)


# ---------------------------------------------------------------------------
# Redaction: the release entrypoint
# ---------------------------------------------------------------------------
class TestReleaseNeverLeaks:
    def test_a_failing_migration_does_not_publish_its_stderr(self, monkeypatch, caplog) -> None:
        import app.release as release_mod

        class FakeCompleted:
            returncode = 1
            stdout = f"alembic stdout mentioning {FAKE_CONNECTION_URL}"
            stderr = f"alembic stderr: {FAKE_DRIVER_MESSAGE}"

        monkeypatch.setattr(release_mod.subprocess, "run", lambda *a, **k: FakeCompleted())

        with caplog.at_level(logging.DEBUG), pytest.raises(SystemExit) as exited:
            release_mod._fail("alembic_upgrade", "migration_failed", returncode=FakeCompleted.returncode)

        assert exited.value.code == 1
        assert SENTINEL not in caplog.text
        assert FAKE_CONNECTION_URL not in caplog.text
        # The failure is still reported, and classified.
        assert "alembic_upgrade" in caplog.text
        assert "migration_failed" in caplog.text
        assert "returncode=1" in caplog.text

    def test_an_unexpected_release_failure_is_classified_not_quoted(
        self, monkeypatch, caplog
    ) -> None:
        import app.release as release_mod

        async def explode() -> None:
            raise RuntimeError(FAKE_DRIVER_MESSAGE)

        monkeypatch.setattr(release_mod, "release", explode)

        with (
            _preserving_the_event_loop(),
            caplog.at_level(logging.DEBUG),
            pytest.raises(SystemExit) as exited,
        ):
            release_mod.main()

        assert exited.value.code == 1
        for forbidden in (SENTINEL, FAKE_CONNECTION_URL, "gg_prod_user", "db.prod-sentinel.example.internal"):
            assert forbidden not in caplog.text, forbidden
        assert "unexpected_error" in caplog.text

    def test_a_classified_failure_keeps_its_own_exit_code(self, monkeypatch, caplog) -> None:
        import app.release as release_mod

        async def fail_cleanly() -> None:
            release_mod._fail("alembic_check", "schema_drift_detected", returncode=2)

        monkeypatch.setattr(release_mod, "release", fail_cleanly)
        with (
            _preserving_the_event_loop(),
            caplog.at_level(logging.DEBUG),
            pytest.raises(SystemExit) as exited,
        ):
            release_mod.main()
        assert exited.value.code == 1
        # main() must not relabel an already-classified failure as unexpected.
        assert "schema_drift_detected" in caplog.text
        assert "unexpected_error" not in caplog.text

    def test_the_release_module_never_interpolates_subprocess_output(self) -> None:
        source = (BACKEND_ROOT / "app" / "release.py").read_text(encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", source, count=1)
        code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
        for banned in ("result.stderr", "result.stdout", "{e}"):
            assert banned not in code, banned

    def test_a_failed_release_always_exits_non_zero(self) -> None:
        # Fail closed: a release that reports failure and exits 0 would let a
        # deployment continue onto an unmigrated database.
        source = (BACKEND_ROOT / "app" / "release.py").read_text(encoding="utf-8")
        assert "sys.exit(1)" in source
        assert "sys.exit(0)" not in source


# ---------------------------------------------------------------------------
# CI must build the image Render actually deploys
# ---------------------------------------------------------------------------
class TestCIBuildsTheRealProductionImage:
    """The workflow is part of the contract, not just the Dockerfile.

    The first version of this milestone passed CI green while CI built only
    ``backend/Dockerfile``. Every assertion elsewhere in this file reads the
    production Dockerfile's *text*; none of them noticed that nothing ever
    built it. These read the workflow instead, so that regression cannot
    return quietly.
    """

    @staticmethod
    def workflow() -> str:
        return CI_WORKFLOW.read_text(encoding="utf-8")

    @staticmethod
    def code() -> str:
        """The workflow with comment-only lines removed.

        The workflow explains at length why the Render image is built; that
        prose must not be able to satisfy an assertion about the build.
        """
        return "\n".join(
            line for line in CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_a_job_builds_the_render_dockerfile_from_the_repository_root(self) -> None:
        code = self.code()
        assert "-f deploy/render/Dockerfile" in code, (
            "CI must build the production Dockerfile Render deploys"
        )
        # The Render build invocation specifically. There are two `docker build`
        # calls in this workflow and the backend one comes first, so the block
        # is selected by content rather than by position.
        candidates = [
            chunk.split("- name:", 1)[0]
            for chunk in code.split("docker build")[1:]
        ]
        matching = [c for c in candidates if "-f deploy/render/Dockerfile" in c]
        assert len(matching) == 1, "expected exactly one Render image build in CI"
        build = matching[0]
        assert "-t glamgenius-render:ci" in build
        assert "--platform linux/amd64" in build
        # The build context has to be the repository root, or the operator and
        # the sibling layout cannot be in the image at all.
        assert re.search(r"^\s+\.\s*$", build, flags=re.MULTILINE), (
            "the Render image must be built with the repository root as context"
        )

    def test_the_backend_image_qualification_is_preserved(self) -> None:
        # The Render image is added alongside the existing contract, never
        # instead of it: backend/Dockerfile still serves compose and local dev.
        code = self.code()
        assert "-t glamgenius-backend:ci" in code
        assert "glamgenius-backend:ci" in code

    def test_the_built_render_image_is_qualified_not_merely_built(self) -> None:
        code = self.code()
        for probe in (
            "id -u",                       # non-root
            "command -v $tool",            # no build chain
            "/workspace/scripts",          # layout and shipped-script shape
            "Config.WorkingDir",           # workdir
            "glamgenius-entrypoint",       # entrypoint
            "Config.Healthcheck.Test",     # liveness target
            "RENDER_GIT_COMMIT",           # provenance
            "importlib.import_module",     # import smoke test
        ):
            assert probe in code, f"the Render image job must check {probe}"

    def test_ci_never_names_the_controlled_activation_operator(self) -> None:
        """The Phase B boundary, restated from the CI side.

        ``test_production_activation_is_not_wired_into_anything_automatic``
        forbids the operator's filename anywhere under ``.github/``. That guard
        is deliberately blunt: an automation surface that knows the operator's
        name is how an automatic execution eventually creeps in, and "it is
        only --help" is the sentence that precedes "it is only status".

        So CI proves the *shape* of what shipped and never the name, while
        ``test_the_exact_operator_is_in_the_production_build_context`` above --
        which lives outside ``.github/`` and may name it -- proves the right
        file is copied. Neither test alone is enough; together they cover
        identity and packaging without breaching the boundary.
        """
        workflow = self.workflow()
        assert PHASE_B_OPERATOR.name not in workflow
        assert "operate_step8i" not in workflow
        # And no operation may be dispatched from CI, under any spelling.
        code = self.code()
        for operation in ("prepare", "compile", "activate", "deactivate", "status"):
            assert f"petrolatum_release.py {operation}" not in code

    def test_ci_still_proves_the_shipped_operator_works_in_the_image(self) -> None:
        # Shape, not name: exactly one script, it imports, its parser builds.
        code = self.code()
        assert "/workspace/scripts" in code
        assert "spec.loader.exec_module" in code
        assert "build_parser()" in code
        assert "format_help()" in code
        # main() is never called, so nothing is dispatched.
        assert "module.main(" not in code

    def test_trivy_scans_the_render_image_under_the_existing_policy(self) -> None:
        code = self.code()
        assert "image-ref: glamgenius-render:ci" in code
        # One policy for both images. A second, weaker gate for the image that
        # actually reaches production would defeat the purpose of the gate.
        assert code.count("trivyignores: .trivyignore") >= 2
        assert code.count("severity: HIGH,CRITICAL") >= 2
        assert "validate_trivy_exceptions.py" in code

    def test_the_render_image_receives_its_own_sbom(self) -> None:
        code = self.code()
        assert "image: glamgenius-render:ci" in code
        assert "render-production-sbom" in code
        # Separate artifacts, so one image's SBOM cannot overwrite the other's.
        assert "name: backend-sbom" in code
        assert "name: render-production-sbom" in code

    def test_the_two_images_are_saved_as_distinct_artifacts(self) -> None:
        code = self.code()
        assert "name: docker-image" in code
        assert "name: render-image" in code
        assert "glamgenius-render-ci.tar.gz" in code
        assert "glamgenius-backend-ci.tar.gz" in code

    def test_the_pr_gate_requires_the_render_image(self) -> None:
        code = self.code()
        assert "RENDER_IMAGE_RESULT" in code
        assert 'require_success "Render production image" "$RENDER_IMAGE_RESULT"' in code
        # And the scan and SBOM cannot run without it.
        assert "needs: [scope, docker-build, render-image]" in code

    def test_action_pinning_is_not_weakened(self) -> None:
        for line in self.workflow().splitlines():
            match = re.match(r"^\s*(?:-\s+)?uses:\s+(\S+)\s*(?:#.*)?$", line)
            if match:
                assert re.search(r"@[0-9a-f]{40}$", match.group(1)), line.strip()

    def test_workflow_edits_are_qualified_by_the_gates_they_govern(self) -> None:
        # A change to ci.yml must run the backend, container, security and
        # release qualification, or the workflow could weaken its own gates.
        detector = (REPOSITORY_ROOT / ".github" / "scripts" / "detect-ci-scope.sh").read_text(
            encoding="utf-8"
        )
        result = subprocess.run(
            ["bash", str(REPOSITORY_ROOT / ".github" / "scripts" / "detect-ci-scope.sh"),
             "base", "head", "pull_request"],
            cwd=REPOSITORY_ROOT,
            env={"PATH": os.environ.get("PATH", ""), "CI_SCOPE_CHANGED_FILES": ".github/workflows/ci.yml"},
            capture_output=True,
            text=True,
            check=True,
        )
        scopes = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        for required in ("backend", "container", "security", "release"):
            assert scopes.get(required) == "true", f"ci.yml must set {required}"
        assert detector  # the detector is the thing under test


# ---------------------------------------------------------------------------
# Operations documentation is part of the contract
# ---------------------------------------------------------------------------
class TestRunbook:
    def test_the_render_runtime_section_exists(self) -> None:
        operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        assert RUNBOOK_HEADING in operations

    def test_the_existing_systemd_guidance_is_not_deleted(self) -> None:
        # Render is one deployment model, not the only valid one.
        operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        assert "systemd" in operations

    def test_the_runbook_separates_deployment_from_activation(self) -> None:
        operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        section = operations.split(RUNBOOK_HEADING, 1)[1]
        assert "RENDER_GIT_COMMIT" in section
        assert "operate_step8i_petrolatum_release.py status" in section
        # And it must say where to stop.
        assert "STOP" in section

    def test_the_runbook_requires_blueprint_auto_sync_to_be_disabled(self) -> None:
        """Two controls, and the service one alone is not enough.

        ``autoDeployTrigger: "off"`` stops a push from deploying a service.
        It cannot stop a push from re-applying ``render.yaml`` itself, because
        the Blueprint is what creates the services. Blueprint Auto Sync is a
        dashboard setting, it defaults to enabled, and Render exposes no YAML
        field for it — so the runbook is the only place this can be enforced.
        """
        operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        section = operations.split(RUNBOOK_HEADING, 1)[1]
        assert "Auto Sync" in section
        assert "Auto Sync = No" in section or "→ No" in section
        assert "Manual Sync" in section
        # And it must say plainly that the service setting does not cover it.
        assert "autoDeployTrigger" in section
        # Whitespace-normalised: this is prose, and a sentence that happens to
        # wrap across a line is the same sentence.
        prose = " ".join(section.lower().split())
        assert "auto sync defaults to enabled" in prose

    def test_the_runbook_no_longer_claims_service_settings_stop_blueprint_sync(self) -> None:
        operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        section = operations.split(RUNBOOK_HEADING, 1)[1]
        assert (
            "No deploy happens automatically, because automatic deploy is off on all three."
            not in section
        ), "the runbook must not imply service auto-deploy governs Blueprint sync"

    def test_the_runbook_lists_key_names_and_no_values(self) -> None:
        operations = (REPOSITORY_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        section = operations.split(RUNBOOK_HEADING, 1)[1]
        for key in (
            "POSTGRES_URL",
            "OFF_DATABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "GEMINI_API_KEY",
            "EXPO_PUBLIC_BACKEND_URL",
        ):
            assert key in section, key
        assert not re.search(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"'`]*:[^\s\"'`@]+@", section)
