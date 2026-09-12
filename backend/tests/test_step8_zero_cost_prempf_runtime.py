"""Step 8 — the zero-cost pre-PMF runtime, and what must not slip with it.

The estate shrank from three Render services to one free web service, and the
two batch workers moved behind HTTP. That is a hosting change, and the whole
risk of a hosting change is that something governed rides along with it.

Four things are under test.

**The Blueprint contract.** One service, free plan, no worker, no cron, no
database, no Redis, one auto-deploy field, and a start command that runs the
release gate *first*. The gate moved from `preDeployCommand` — a paid feature
— into `dockerCommand`, joined with `&&`. A `;` there would start uvicorn
against an unmigrated database, so the separator is asserted, not assumed.

**The scheduler door.** Two POST routes behind one shared secret, compared in
constant time, refusing every failure mode identically, and never logging or
echoing the credential. A customer JWT must not open them: they run batch work
on nobody's behalf, and authorising that with a user token would let any
signed-in user trigger it.

**Bounded work.** One HTTP request processes at most one deletion job. A
handler that drained the queue would time out under exactly the backlog it was
meant to clear.

**One schedule authority.** Readiness and the admin endpoint used to hold
their own literals for the same workers, which is how they came to disagree.
Both now read `app/workers/schedule.py`.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from app import config
from app.api.v2 import admin, internal_scheduler
from app.workers import account_deletion, schedule

# The Blueprint reader, borrowed rather than rebuilt.
#
# PyYAML is deliberately not a backend dependency, and this milestone does not
# add one: `import yaml` at module scope passes anywhere it happens to be
# installed and fails collection in CI, which is exactly what it did. The
# repository already owns a small strict reader for the one file, written for
# this reason, and one reader means the two modules cannot come to disagree
# about what render.yaml says.
from tests.test_production_runtime_foundation import _blueprint as _read_blueprint

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
RENDER_YAML = REPOSITORY_ROOT / "render.yaml"
ENV_EXAMPLE = REPOSITORY_ROOT / "env.example"
OPERATIONS_DOC = REPOSITORY_ROOT / "docs" / "OPERATIONS.md"

#: A token that is obviously not a production credential, long enough to pass
#: the length floor so the tests exercise comparison rather than validation.
TEST_TOKEN = "test-only-scheduler-token-not-a-real-secret-000000"

#: The module that owns the credential boundary, read as source where a
#: behavioural test cannot see the property being defended.
SCHEDULER_MODULE = BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py"

SCHEDULER_PREFIX = "/api/v2/internal/scheduler"
DELETION_PATH = f"{SCHEDULER_PREFIX}/account-deletion"
NOTIFICATION_PATH = f"{SCHEDULER_PREFIX}/notifications"


def _executable_source(path: Path) -> str:
    """A Python file's code with every docstring removed.

    These files explain at length what they refuse to do. A raw-text scan
    finds those refusals and calls them violations -- a mistake worth making
    once.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body.pop(0)
            if not node.body:
                node.body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def _yaml_without_comments() -> str:
    """render.yaml's declarations only. Its comments name what it excludes."""
    return "\n".join(
        line for line in RENDER_YAML.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _blueprint() -> dict[str, Any]:
    return _read_blueprint()


def _the_service() -> dict[str, Any]:
    services = _blueprint()["services"]
    assert len(services) == 1, "the pre-PMF runtime is exactly one service"
    return services[0]


# ---------------------------------------------------------------------------
# 1. The Blueprint contract
# ---------------------------------------------------------------------------
class TestTheRenderBlueprint:
    def test_there_is_exactly_one_service(self) -> None:
        # The whole point of the correction. A second service is a second
        # bill, and on free tier it is not even available.
        assert len(_blueprint()["services"]) == 1

    def test_it_is_the_api_web_service(self) -> None:
        service = _the_service()
        assert service["type"] == "web"
        assert service["name"] == "glamgenius-api"
        assert service["runtime"] == "docker"
        assert service["region"] == "singapore"

    def test_the_plan_is_free(self) -> None:
        assert _the_service()["plan"] == "free"

    def test_no_service_is_a_worker_or_a_cron(self) -> None:
        types = {service["type"] for service in _blueprint()["services"]}
        assert "worker" not in types
        assert "cron" not in types
        assert types == {"web"}

    def test_there_is_no_managed_database_block(self) -> None:
        # A third database authority is how the ODbL wall gets breached by
        # accident. Store A and the application database are both Supabase.
        assert "databases" not in _blueprint()

    def test_there_is_no_key_value_or_redis(self) -> None:
        # Declarations only: a comment saying "there is no Redis here" is the
        # file keeping its promise, not breaking it.
        raw = _yaml_without_comments().lower()
        for forbidden in ("redis", "keyvalue", "key-value"):
            assert forbidden not in raw, forbidden

    def test_the_build_source_is_unchanged(self) -> None:
        service = _the_service()
        assert service["dockerfilePath"] == "./deploy/render/Dockerfile"
        assert service["dockerContext"] == "."

    def test_the_health_check_is_readiness(self) -> None:
        assert _the_service()["healthCheckPath"] == "/api/v2/ready"

    def test_auto_deploy_is_off_through_exactly_one_field(self) -> None:
        # autoDeployTrigger supersedes the deprecated autoDeploy. Writing both
        # gave the Blueprint parser two sources of truth for one decision.
        service = _the_service()
        assert service["autoDeployTrigger"] == "off"
        assert "autoDeploy" not in service

    def test_the_off_value_is_a_string_not_a_boolean(self) -> None:
        # YAML 1.1 reads a bare `off` as false, which is not what the schema
        # expects. The quotes in the file are load-bearing.
        assert isinstance(_the_service()["autoDeployTrigger"], str)

    def test_the_deprecated_auto_deploy_field_appears_nowhere(self) -> None:
        raw = RENDER_YAML.read_text(encoding="utf-8")
        declarations = [
            line for line in raw.splitlines()
            if re.match(r"\s*autoDeploy\s*:", line)
        ]
        assert declarations == [], declarations

    def test_pre_deploy_command_is_absent(self) -> None:
        # A paid feature. Its absence is why the gate lives in dockerCommand.
        assert "preDeployCommand" not in _the_service()
        assert "preDeployCommand" not in _yaml_without_comments()

    def test_both_environment_groups_are_still_consumed(self) -> None:
        groups = {entry["fromGroup"] for entry in _the_service()["envVars"]}
        assert groups == {
            "glamgenius-production-invariants",
            "glamgenius-production-secrets",
        }

    def test_the_production_invariants_are_unchanged(self) -> None:
        (group,) = _blueprint()["envVarGroups"]
        assert group["name"] == "glamgenius-production-invariants"
        declared = {entry["key"]: entry["value"] for entry in group["envVars"]}
        assert declared == {
            "APP_ENV": "production",
            "INVITE_REQUIRED": "true",
            "REQUIRE_ANALYSIS_CONSENT": "true",
            "MEDIA_STORAGE_BACKEND": "supabase",
            "MEDIA_ALLOW_LOCAL_IN_PRODUCTION": "false",
            # Render puts exactly one load balancer in front of the container.
            # At the default of 0 the app reads the socket peer, which is that
            # load balancer for every request: one shared rate-limit bucket for
            # the whole product, and one address in every audit row. See
            # app/shared/security/network.py.
            "TRUSTED_PROXY_HOPS": "1",
        }

    def test_no_secret_value_is_declared_in_the_blueprint(self) -> None:
        raw = RENDER_YAML.read_text(encoding="utf-8")
        # The token may be *named* in a comment; it must never have a value.
        assert not re.search(r"INTERNAL_SCHEDULER_TOKEN\s*[:=]\s*\S", raw)
        for secret in ("POSTGRES_URL", "SUPABASE_SERVICE_ROLE_KEY", "GEMINI_API_KEY"):
            assert not re.search(rf"key:\s*{secret}", raw), secret


class TestTheReleaseGateRunsBeforeUvicorn:
    """The gate moved out of preDeployCommand; it did not go away."""

    @staticmethod
    def _command() -> str:
        return _the_service()["dockerCommand"]

    def test_the_release_entrypoint_is_invoked(self) -> None:
        assert "python -m app.release" in self._command()

    def test_uvicorn_is_invoked(self) -> None:
        assert "uvicorn server:app" in self._command()

    def test_release_comes_before_uvicorn(self) -> None:
        command = self._command()
        assert command.index("python -m app.release") < command.index("uvicorn")

    def test_the_two_are_joined_by_a_conditional_and(self) -> None:
        # The whole safety property. `;` would start the API against a database
        # that was never migrated.
        command = self._command()
        between = command[
            command.index("python -m app.release") + len("python -m app.release")
            : command.index("uvicorn")
        ]
        assert "&&" in between, between
        assert ";" not in between, between

    def test_there_is_no_escape_hatch(self) -> None:
        command = self._command()
        for forbidden in ("|| true", "|| exit 0", " & ", "nohup", "|| :"):
            assert forbidden not in command, forbidden

    def test_the_server_is_exec_so_signals_reach_it(self) -> None:
        assert "exec uvicorn" in self._command()

    def test_it_binds_every_interface_on_renders_port(self) -> None:
        command = self._command()
        assert "--host 0.0.0.0" in command
        assert "--port $PORT" in command

    def test_the_release_module_still_exists_and_is_the_only_gate(self) -> None:
        assert (BACKEND_ROOT / "app" / "release.py").is_file()
        # Invoked once, in the one start command. The comments explain it more
        # than once, which is fine.
        assert _yaml_without_comments().count("python -m app.release") == 1


# ---------------------------------------------------------------------------
# 2. The scheduler credential boundary
# ---------------------------------------------------------------------------
@pytest.fixture()
def scheduler_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", TEST_TOKEN)
    return TEST_TOKEN


class TestThisModuleItself:
    """One check on the tests, because a silent one is worse than none.

    Two methods with the same name in one class is legal Python: the second
    definition replaces the first and the first never runs again. Mutation
    testing found exactly that here -- a stronger constant-time check written
    above a weaker one of the same name, with only the weaker one ever
    executing, and a mutation walking straight past both.
    """

    def test_no_test_name_is_defined_twice_in_a_class(self) -> None:
        import ast
        from collections import Counter

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        shadowed = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = [
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            shadowed += [
                f"{node.name}.{name}"
                for name, count in Counter(names).items()
                if count > 1
            ]
        assert shadowed == [], shadowed


class TestSchedulerAuthorisation:
    """Every wrong way in is the same refusal."""

    @staticmethod
    def _denied(header: str | None) -> None:
        with pytest.raises(Exception) as caught:
            internal_scheduler.require_scheduler_token(authorization=header)
        assert getattr(caught.value, "status_code", None) == 401

    def test_the_correct_token_is_accepted(self, scheduler_token: str) -> None:
        assert (
            internal_scheduler.require_scheduler_token(
                authorization=f"Bearer {scheduler_token}"
            )
            is None
        )

    def test_a_missing_authorization_header_is_denied(self, scheduler_token: str) -> None:
        self._denied(None)

    def test_an_empty_authorization_header_is_denied(self, scheduler_token: str) -> None:
        self._denied("")

    def test_a_wrong_token_is_denied(self, scheduler_token: str) -> None:
        self._denied("Bearer not-the-configured-token-aaaaaaaaaaaaaaaaaaaa")

    def test_an_empty_bearer_value_is_denied(self, scheduler_token: str) -> None:
        self._denied("Bearer ")
        self._denied("Bearer")

    @pytest.mark.parametrize("scheme", ["Basic", "Token", "JWT", "Digest", "bearerr"])
    def test_a_wrong_auth_scheme_is_denied(self, scheduler_token: str, scheme: str) -> None:
        self._denied(f"{scheme} {scheduler_token}")

    def test_the_bearer_scheme_is_case_insensitive(self, scheduler_token: str) -> None:
        # RFC 7235 says the scheme is case-insensitive. Being strict here would
        # reject a correct caller, which is a different failure from a secure one.
        for spelling in ("bearer", "BEARER", "BeArEr"):
            assert (
                internal_scheduler.require_scheduler_token(
                    authorization=f"{spelling} {scheduler_token}"
                )
                is None
            )

    def test_the_token_alone_without_a_scheme_is_denied(self, scheduler_token: str) -> None:
        self._denied(scheduler_token)

    def test_a_prefix_of_the_token_is_denied(self, scheduler_token: str) -> None:
        self._denied(f"Bearer {scheduler_token[:-1]}")

    def test_the_token_with_extra_characters_is_denied(self, scheduler_token: str) -> None:
        self._denied(f"Bearer {scheduler_token}x")

    def test_the_comparison_is_constant_time(self) -> None:
        """The one property no behavioural test can observe.

        ``==`` on two strings short-circuits at the first differing byte, so
        the time it takes leaks how much of the secret a guess got right.
        Every assertion above passes just as happily with ``==``, which is
        exactly why this one reads the code: the defect is in the timing, and
        the timing is not in the response.

        Read from docstring-stripped source, so the explanation you are
        reading now cannot be what satisfies it.
        """
        source = _executable_source(SCHEDULER_MODULE)
        assert "hmac.compare_digest(presented, configured)" in source
        # And no equality comparison of the credential anywhere beside it.
        for leaky in (
            "presented == configured",
            "presented != configured",
            "configured == presented",
            "configured != presented",
        ):
            assert leaky not in source, leaky

    def test_an_unconfigured_token_denies_everyone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unconfigured is a closed door, never an open one.
        monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", "")
        self._denied(f"Bearer {TEST_TOKEN}")
        self._denied(None)

    def test_a_whitespace_only_token_configuration_denies_everyone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", "    ")
        self._denied("Bearer     ")

    def test_every_refusal_is_identical(self, scheduler_token: str) -> None:
        # Distinguishable refusals tell an attacker which half to work on.
        seen = set()
        for header in (
            None, "", "Bearer", "Bearer ", "Basic x",
            "Bearer wrong-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", TEST_TOKEN,
        ):
            with pytest.raises(Exception) as caught:
                internal_scheduler.require_scheduler_token(authorization=header)
            seen.add((caught.value.status_code, str(caught.value.detail)))
        assert len(seen) == 1, seen

    def test_each_refusal_is_its_own_exception_object(self) -> None:
        # Not shared module state: raising one instance repeatedly attaches a
        # new traceback to it on every failed request, on the path an attacker
        # can call as often as they like.
        raised = []
        for header in (None, "Basic x", "Bearer wrong-aaaaaaaaaaaaaaaaaaaaaaa"):
            with pytest.raises(Exception) as caught:
                internal_scheduler.require_scheduler_token(authorization=header)
            raised.append(caught.value)
        assert len({id(exc) for exc in raised}) == len(raised)


class TestTheTokenIsNeverDisclosed:
    def test_it_is_not_logged_on_any_refusal(
        self, scheduler_token: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        for header in (None, "Bearer wrong-token-aaaaaaaaaaaaaaaaaaaaaaaa", "Basic x"):
            with pytest.raises(Exception):
                internal_scheduler.require_scheduler_token(authorization=header)
        logged = caplog.text
        assert scheduler_token not in logged
        assert "wrong-token" not in logged

    def test_it_is_not_logged_on_success(
        self, scheduler_token: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        internal_scheduler.require_scheduler_token(
            authorization=f"Bearer {scheduler_token}"
        )
        assert scheduler_token not in caplog.text

    def test_the_refusal_body_describes_nothing(self, scheduler_token: str) -> None:
        with pytest.raises(Exception) as caught:
            internal_scheduler.require_scheduler_token(authorization=None)
        detail = str(caught.value.detail).lower()
        assert scheduler_token not in str(caught.value.detail)
        for leak in ("length", "expected", "configured", "starts with", "missing header"):
            assert leak not in detail, leak

    def test_the_source_never_formats_the_credential_into_a_message(self) -> None:
        source = (
            BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py"
        ).read_text(encoding="utf-8")
        for leak in (
            "logger.info(presented", "logger.debug(presented", "%s\", presented",
            "{presented}", "{configured}", "len(configured)", "len(presented)",
        ):
            assert leak not in source, leak


class TestTheSchedulerRoutesAreNotCustomerRoutes:
    @staticmethod
    def _spec() -> dict[str, Any]:
        from server import app

        return app.openapi()

    def test_both_routes_exist_at_the_reviewed_paths(self) -> None:
        paths = self._spec()["paths"]
        assert DELETION_PATH in paths
        assert NOTIFICATION_PATH in paths

    def test_they_are_post_only(self) -> None:
        # A credential must never be placeable in a URL, where a proxy log or
        # a redirect would keep it.
        paths = self._spec()["paths"]
        for path in (DELETION_PATH, NOTIFICATION_PATH):
            assert set(paths[path]) == {"post"}, paths[path]

    def test_there_are_exactly_two_internal_scheduler_routes(self) -> None:
        paths = [p for p in self._spec()["paths"] if "/internal/" in p]
        assert sorted(paths) == sorted([DELETION_PATH, NOTIFICATION_PATH])

    def test_they_do_not_depend_on_the_customer_account_dependency(self) -> None:
        # A Supabase JWT must not be an authority here. If get_current_account
        # appeared, any signed-in user could run the batch.
        source = (
            BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("get_current_account", "supabase_auth", "require_flag"):
            assert forbidden not in source, forbidden

    def test_the_router_declares_the_reviewed_prefix(self) -> None:
        assert internal_scheduler.router.prefix == "/internal/scheduler"


# ---------------------------------------------------------------------------
# 3. Bounded deletion work
# ---------------------------------------------------------------------------
class _RecordingSession:
    """Stands in for a session so the cycle's shape can be tested directly."""

    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        self.executed.append(args)

    async def commit(self) -> None:
        return None


def _sessionmaker(session: _RecordingSession):
    def factory() -> _RecordingSession:
        return session

    return factory


class TestTheDeletionCycleIsBounded:
    @pytest.mark.asyncio
    async def test_one_invocation_claims_at_most_one_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The property that keeps an HTTP handler from timing out under the
        # backlog it exists to clear.
        #
        # The stub refuses a second claim rather than supplying one. A cycle
        # that drained the queue would otherwise loop against an endless
        # supply of jobs and hang instead of failing -- and a test that hangs
        # reports nothing, holds the runner, and looks exactly like a test
        # that is merely slow.
        claims = 0
        runs = 0

        async def claim_next(session: Any) -> object:
            nonlocal claims
            claims += 1
            if claims > 1:
                raise RuntimeError("run_cycle claimed a second job")
            return object()

        async def run_job(session: Any, job: object) -> tuple[str, None]:
            nonlocal runs
            runs += 1
            return ("complete", None)

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", claim_next)
        monkeypatch.setattr(account_deletion.deletion_service, "run_job", run_job)
        summary = await account_deletion.run_cycle(
            sessionmaker=_sessionmaker(_RecordingSession())
        )
        assert claims == 1
        assert runs == 1
        assert summary.processed is True
        assert summary.ok is True

    @pytest.mark.asyncio
    async def test_no_pending_job_still_writes_a_heartbeat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Silence must not look like absence: readiness watches this row.
        session = _RecordingSession()

        async def claim_next(_: Any) -> None:
            return None

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", claim_next)
        summary = await account_deletion.run_cycle(sessionmaker=_sessionmaker(session))
        assert summary.processed is False
        assert summary.ok is True
        assert session.executed, "an idle cycle must still record that it ran"

    @pytest.mark.asyncio
    async def test_a_failed_job_is_reported_without_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def claim_next(_: Any) -> object:
            return object()

        async def run_job(_: Any, __: object) -> tuple[str, str]:
            return ("export_failed", "storage_unavailable")

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", claim_next)
        monkeypatch.setattr(account_deletion.deletion_service, "run_job", run_job)
        summary = await account_deletion.run_cycle(
            sessionmaker=_sessionmaker(_RecordingSession())
        )
        assert summary.processed is True
        assert summary.ok is False
        assert summary.error_code == "storage_unavailable"

    @pytest.mark.asyncio
    async def test_an_exception_becomes_a_safe_summary_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def claim_next(_: Any) -> object:
            raise RuntimeError("connection to db 'glamgenius' failed for user 'x'")

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", claim_next)
        summary = await account_deletion.run_cycle(
            sessionmaker=_sessionmaker(_RecordingSession())
        )
        assert summary.ok is False
        assert summary.error_code == "unexpected_worker_error"
        # The exception text can quote a row or a connection string.
        assert "glamgenius" not in str(summary.as_dict())

    @pytest.mark.asyncio
    async def test_the_summary_carries_no_customer_data(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def claim_next(_: Any) -> object:
            return object()

        async def run_job(_: Any, __: object) -> tuple[str, None]:
            return ("complete", None)

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", claim_next)
        monkeypatch.setattr(account_deletion.deletion_service, "run_job", run_job)
        summary = await account_deletion.run_cycle(
            sessionmaker=_sessionmaker(_RecordingSession())
        )
        assert set(summary.as_dict()) == {"processed", "ok", "error_code"}

    def test_it_does_not_reimplement_the_state_machine(self) -> None:
        source = (
            BACKEND_ROOT / "app" / "workers" / "account_deletion.py"
        ).read_text(encoding="utf-8")
        # Claiming and running stay in the service. If the worker grew its own
        # SELECT ... FOR UPDATE it would be a second, unreviewed claim path.
        assert "deletion_service.claim_next" in source
        assert "deletion_service.run_job" in source
        assert "FOR UPDATE" not in source.upper().replace("SELECT … FOR UPDATE", "")

    def test_the_long_running_daemon_is_still_available(self) -> None:
        # Kept for the day there is a worker process to pay for.
        assert callable(account_deletion.run_forever)


class TestTheScheduledDeletionWorkerHasAStableName:
    def test_the_name_is_deterministic(self) -> None:
        assert schedule.ACCOUNT_DELETION_WORKER_NAME == "account_deletion_worker"

    def test_it_carries_no_hostname(self) -> None:
        # A per-container name would mint a row per restart, and the previous
        # row would sit there looking permanently stale.
        import socket

        assert socket.gethostname() not in schedule.ACCOUNT_DELETION_WORKER_NAME

    def test_the_cycle_writes_under_that_name(self) -> None:
        source = (
            BACKEND_ROOT / "app" / "workers" / "account_deletion.py"
        ).read_text(encoding="utf-8")
        cycle = source[source.index("async def _write_heartbeat") :]
        assert "ACCOUNT_DELETION_WORKER_NAME" in cycle

    @pytest.mark.asyncio
    async def test_the_row_the_cycle_actually_writes_is_named_exactly_that(
        self, db_clean
    ) -> None:
        """Against the database, because the source check is not enough.

        Mutation testing found this gap: interpolating the hostname *into* the
        constant — ``f"{ACCOUNT_DELETION_WORKER_NAME}_{gethostname()}"`` —
        leaves the constant's name in the source and passes a source scan
        while writing exactly the per-container row this design removed.
        """
        from app.shared.database.sql import get_sessionmaker
        from sqlalchemy import text

        summary = await account_deletion.run_cycle()
        assert summary.processed is False  # nothing queued; the heartbeat is the point

        factory = get_sessionmaker()
        async with factory() as session:
            rows = (
                await session.execute(text("SELECT worker_name FROM system_worker_status"))
            ).scalars().all()
        assert rows == ["account_deletion_worker"], rows

    def test_the_daemon_keeps_its_per_host_identity(self) -> None:
        # Two pods are two daemons and an operator wants to see both. Only the
        # scheduled path is deterministic.
        source = (
            BACKEND_ROOT / "app" / "workers" / "account_deletion.py"
        ).read_text(encoding="utf-8")
        daemon = source[source.index("async def run_forever") :]
        assert "socket.gethostname()" in daemon


# ---------------------------------------------------------------------------
# 4. Notifications reuse the existing cycle
# ---------------------------------------------------------------------------
class TestTheNotificationSchedulerReusesTheWorker:
    def test_the_route_calls_the_existing_run_cycle(self) -> None:
        source = (
            BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py"
        ).read_text(encoding="utf-8")
        assert "notifications.run_cycle()" in source

    def test_the_manual_single_account_path_is_not_reachable(self) -> None:
        # `--account` is a testing affordance. An endpoint that exposed it
        # could notify a chosen person on demand.
        source = _executable_source(
            BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py"
        )
        for forbidden in ("--account", "account_id", "process_account", "notify_account"):
            assert forbidden not in source, forbidden

    def test_it_does_not_reimplement_the_cycle(self) -> None:
        source = _executable_source(
            BACKEND_ROOT / "app" / "api" / "v2" / "internal_scheduler.py"
        )
        for bypassed in (
            "quiet_hours", "process_once", "record_heartbeat", "expo", "push",
            "NotificationPreference",
        ):
            assert bypassed not in source, bypassed

    @pytest.mark.asyncio
    async def test_the_response_carries_only_operational_counts(
        self, monkeypatch: pytest.MonkeyPatch, scheduler_token: str
    ) -> None:
        from app.workers.notifications import RunSummary

        summary = RunSummary(accounts_considered=3, notifications_sent=2)
        summary.failed_account_ids.append("11111111-2222-3333-4444-555555555555")

        async def run_cycle() -> RunSummary:
            return summary

        monkeypatch.setattr(internal_scheduler.notifications, "run_cycle", run_cycle)
        body = await internal_scheduler.run_notification_cycle()
        assert set(body) == {"ok", "accounts_considered", "notifications_sent"}
        assert "11111111-2222-3333-4444-555555555555" not in str(body)

    @pytest.mark.asyncio
    async def test_a_failed_cycle_does_not_leak_the_error_text(
        self, monkeypatch: pytest.MonkeyPatch, scheduler_token: str
    ) -> None:
        async def run_cycle() -> None:
            raise RuntimeError("ValueError: account 9f3c-... has no push token")

        monkeypatch.setattr(internal_scheduler.notifications, "run_cycle", run_cycle)
        with pytest.raises(Exception) as caught:
            await internal_scheduler.run_notification_cycle()
        assert caught.value.status_code == 500
        assert "9f3c" not in str(caught.value.detail)


# ---------------------------------------------------------------------------
# 5. One schedule authority
# ---------------------------------------------------------------------------
class TestTheScheduleAuthority:
    def test_the_reviewed_intervals(self) -> None:
        assert schedule.ACCOUNT_DELETION_INTERVAL_SECONDS == 300
        assert schedule.NOTIFICATION_INTERVAL_SECONDS == 3600

    def test_both_scheduled_workers_are_declared(self) -> None:
        # Bound to a name rather than compared against an inline literal, and
        # named side first: the repository pins ruff 0.3.5, which reads a
        # dotted attribute on the left of `==` as the Yoda half.
        expected = {
            "account_deletion_worker": 300,
            "notification_worker": 3600,
        }
        assert expected == schedule.SCHEDULED_WORKERS

    def test_admin_reads_the_same_authority(self) -> None:
        assert admin.SCHEDULED_WORKERS is schedule.SCHEDULED_WORKERS
        assert admin._MISSED_GRACE == schedule.MISSED_GRACE_MULTIPLIER

    def test_admin_declares_no_interval_of_its_own(self) -> None:
        source = _executable_source(BACKEND_ROOT / "app" / "api" / "v2" / "admin.py")
        assert "3600" not in source
        assert '"notification_worker": ' not in source

    def test_readiness_reads_the_same_authority(self) -> None:
        source = (BACKEND_ROOT / "app" / "api" / "v2" / "config.py").read_text(
            encoding="utf-8"
        )
        assert "schedule.ACCOUNT_DELETION_WORKER_NAME" in source
        assert "schedule.ACCOUNT_DELETION_STALE_AFTER_SECONDS" in source

    def test_readiness_no_longer_scans_hostname_worker_names(self) -> None:
        # The LIKE scan belonged to the always-on topology.
        source = _executable_source(BACKEND_ROOT / "app" / "api" / "v2" / "config.py")
        # The comment explains what the query used to be; the query must not
        # still be it.
        assert "account_deletion_worker_%" not in source
        assert "worker_name LIKE" not in source

    def test_readiness_declares_no_stale_literal_of_its_own(self) -> None:
        source = _executable_source(BACKEND_ROOT / "app" / "api" / "v2" / "config.py")
        assert "age > 300" not in source
        assert "age > 600" not in source

    def test_the_stale_window_is_derived_from_the_interval(self) -> None:
        assert schedule.ACCOUNT_DELETION_STALE_AFTER_SECONDS == 600
        assert schedule.stale_after_seconds(3600) == 7200


class TestTheServiceVersionFallback:
    @pytest.mark.parametrize(
        ("environment", "expected"),
        [
            ({"COMMIT_SHA": "aaa", "RENDER_GIT_COMMIT": "bbb", "APP_VERSION": "ccc"}, "aaa"),
            ({"RENDER_GIT_COMMIT": "bbb", "APP_VERSION": "ccc"}, "bbb"),
            ({"APP_VERSION": "ccc"}, "ccc"),
            ({}, "unknown"),
            ({"COMMIT_SHA": "  ", "RENDER_GIT_COMMIT": "bbb"}, "bbb"),
        ],
    )
    def test_the_precedence(
        self, monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str
    ) -> None:
        for name in ("COMMIT_SHA", "RENDER_GIT_COMMIT", "APP_VERSION"):
            monkeypatch.delenv(name, raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        assert schedule.service_version() == expected

    def test_both_scheduled_paths_use_it(self) -> None:
        deletion = (BACKEND_ROOT / "app" / "workers" / "account_deletion.py").read_text(
            encoding="utf-8"
        )
        assert "service_version()" in deletion


# ---------------------------------------------------------------------------
# 5a. What the scheduler is told over HTTP
# ---------------------------------------------------------------------------
class TestTheDeletionRouteReportsFailureAsFailure:
    """A failed cycle must not look successful to anything that watches.

    pg_cron records the delivery, not the outcome. If a failing cycle answers
    200, then `cron.job_run_details` says succeeded, `net._http_response` says
    200, and a deletion that has failed every five minutes for a week looks
    identical to one that worked — from every angle an operator checks first.
    """

    @staticmethod
    def _summary(**kwargs: Any) -> Any:
        return account_deletion.CycleSummary(**kwargs)

    @pytest.mark.asyncio
    async def test_an_idle_cycle_is_200(
        self, app_client, scheduler_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run_cycle() -> Any:
            return self._summary(processed=False, ok=True)

        monkeypatch.setattr(account_deletion, "run_cycle", run_cycle)
        response = await app_client.post(
            DELETION_PATH, headers={"Authorization": f"Bearer {scheduler_token}"}
        )
        assert response.status_code == 200
        assert response.json() == {"processed": False, "ok": True}

    @pytest.mark.asyncio
    async def test_a_processed_cycle_is_200(
        self, app_client, scheduler_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def run_cycle() -> Any:
            return self._summary(processed=True, ok=True)

        monkeypatch.setattr(account_deletion, "run_cycle", run_cycle)
        response = await app_client.post(
            DELETION_PATH, headers={"Authorization": f"Bearer {scheduler_token}"}
        )
        assert response.status_code == 200
        assert response.json() == {"processed": True, "ok": True}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("processed", "error_code"),
        [
            (True, "storage_unavailable"),   # a controlled run_job failure
            (False, "unexpected_worker_error"),  # the cycle itself failed
        ],
    )
    async def test_a_failed_cycle_is_500(
        self,
        app_client,
        scheduler_token: str,
        monkeypatch: pytest.MonkeyPatch,
        processed: bool,
        error_code: str,
    ) -> None:
        async def run_cycle() -> Any:
            return self._summary(processed=processed, ok=False, error_code=error_code)

        monkeypatch.setattr(account_deletion, "run_cycle", run_cycle)
        response = await app_client.post(
            DELETION_PATH, headers={"Authorization": f"Bearer {scheduler_token}"}
        )
        assert response.status_code == 500
        assert response.json() == {
            "detail": "The account-deletion cycle failed. See worker status."
        }

    @pytest.mark.asyncio
    async def test_the_failure_body_names_nothing(
        self, app_client, scheduler_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The caller holds a shared secret, not an operator's access. The
        # reason lives in system_worker_status, which requires being one.
        async def run_cycle() -> Any:
            return self._summary(
                processed=True, ok=False, error_code="storage_unavailable"
            )

        monkeypatch.setattr(account_deletion, "run_cycle", run_cycle)
        response = await app_client.post(
            DELETION_PATH, headers={"Authorization": f"Bearer {scheduler_token}"}
        )
        body = response.text
        # "account-deletion" is the name of the job and belongs in the
        # sentence. What must not appear is anything identifying: an error
        # code, an id, a provider or database detail, a traceback.
        for leaked in (
            "storage_unavailable", "postgres", "asyncpg", "Traceback",
            "supabase", "lease", "attempt",
        ):
            assert leaked not in body.lower() and leaked not in body, leaked
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", body), "an id reached the body"
        assert response.json() == {
            "detail": "The account-deletion cycle failed. See worker status."
        }

    @pytest.mark.asyncio
    async def test_the_route_still_refuses_an_unauthorised_caller_first(
        self, app_client, scheduler_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The 500 path must not be reachable without the credential: a wrong
        # token must never get far enough to run a cycle at all.
        ran = False

        async def run_cycle() -> Any:
            nonlocal ran
            ran = True
            return self._summary(processed=False, ok=True)

        monkeypatch.setattr(account_deletion, "run_cycle", run_cycle)
        response = await app_client.post(
            DELETION_PATH, headers={"Authorization": "Bearer wrong-token-aaaaaaaaaaaaaaaaaa"}
        )
        assert response.status_code == 401
        assert ran is False


# ---------------------------------------------------------------------------
# 5b. The cycle's transaction, against a real database
# ---------------------------------------------------------------------------
class TestTheScheduledCycleCommits:
    """The regression the mock-session tests could not catch.

    A fake session records that ``commit()`` was called. It cannot record that
    the row changed, because there is no row. The first version of run_cycle
    claimed a job, ran it, and let the session context close without
    committing — every test passed, and every state transition was silently
    rolled back after the stages had already deleted the account's media,
    revoked its integrations and removed its Supabase Auth user.

    These tests reload the row in a *new* transaction. Nothing a rolled-back
    session did is visible from there.
    """

    @staticmethod
    async def _reload_only_job() -> dict[str, object]:
        """Read the job back through a connection that shares no transaction."""
        from app.shared.database.sql import get_sessionmaker
        from sqlalchemy import text

        factory = get_sessionmaker()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT state, attempt_count, lease_owner, started_at, "
                        "next_retry_at, last_error_code "
                        "FROM account_deletion_jobs"
                    )
                )
            ).mappings().all()
        assert len(row) == 1, row
        return dict(row[0])

    @pytest.mark.asyncio
    async def test_a_successful_transition_is_visible_from_a_new_transaction(
        self, db_clean, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _insert_pending_job()

        async def run_job(session: Any, job: Any) -> tuple[str, None]:
            # Deterministic, and it touches no provider: the point is the
            # transaction, not the deletion stages.
            job.state = "complete"
            job.completed_at = job.started_at
            return ("complete", None)

        monkeypatch.setattr(account_deletion.deletion_service, "run_job", run_job)
        summary = await account_deletion.run_cycle()
        assert summary.processed is True
        assert summary.ok is True

        persisted = await self._reload_only_job()
        assert persisted["state"] == "complete", "run_job's transition was rolled back"
        # claim_next's own mutations have to survive too — without them a
        # crashed worker's job looks untouched and is claimed again forever.
        assert persisted["attempt_count"] == 1, "the claim was rolled back"
        assert persisted["lease_owner"] is not None, "the lease was rolled back"
        assert persisted["started_at"] is not None

    @pytest.mark.asyncio
    async def test_a_controlled_failure_persists_its_retry_state(
        self, db_clean, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed attempt is a fact about the job, and must survive.

        Rolling this back loses the attempt count and the retry schedule, so
        the job is re-claimed immediately and forever rather than backing off.
        """
        import datetime

        await _insert_pending_job()

        async def run_job(session: Any, job: Any) -> tuple[str, str]:
            job.state = "failed_retryable"
            job.last_error_code = "storage_unavailable"
            job.last_error_stage = "storage_deleting"
            job.next_retry_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                minutes=5
            )
            return ("failed_retryable", "storage_unavailable")

        monkeypatch.setattr(account_deletion.deletion_service, "run_job", run_job)
        summary = await account_deletion.run_cycle()
        assert summary.processed is True
        assert summary.ok is False

        persisted = await self._reload_only_job()
        assert persisted["state"] == "failed_retryable"
        assert persisted["last_error_code"] == "storage_unavailable"
        assert persisted["next_retry_at"] is not None, "the retry schedule was rolled back"
        assert persisted["attempt_count"] == 1

    @pytest.mark.asyncio
    async def test_the_commit_happens_after_run_job_not_before(self) -> None:
        # A commit between claim and run would persist the claim of a job
        # whose processing then crashed, which is a different bug wearing the
        # same fix. Ordering is read from source because no observable state
        # distinguishes the two once both have succeeded.
        source = _executable_source(
            BACKEND_ROOT / "app" / "workers" / "account_deletion.py"
        )
        cycle = source[source.index("async def run_cycle"):source.index("async def run_forever")]
        assert "await session.commit()" in cycle
        assert cycle.index("run_job") < cycle.index("await session.commit()")

    @pytest.mark.asyncio
    async def test_a_heartbeat_failure_cannot_undo_a_committed_deletion(
        self, db_clean, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Observability must never be able to reverse the work."""

        async def run_job(session: Any, job: Any) -> tuple[str, None]:
            job.state = "complete"
            return ("complete", None)

        async def exploding_heartbeat(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("heartbeat table is on fire")

        await _insert_pending_job()
        monkeypatch.setattr(account_deletion.deletion_service, "run_job", run_job)
        monkeypatch.setattr(account_deletion, "_write_heartbeat", exploding_heartbeat)

        with pytest.raises(RuntimeError):
            await account_deletion.run_cycle()

        persisted = await self._reload_only_job()
        assert persisted["state"] == "complete", "a heartbeat failure rolled back a deletion"


# ---------------------------------------------------------------------------
# 5c. The failure log says one fixed thing
# ---------------------------------------------------------------------------
#: Fake, and shaped exactly like the things a real database error quotes.
LEAKY_MESSAGE = (
    "connection failed: postgresql://fake-user:fake-password@example.invalid/private "
    "while deleting account=11111111-2222-3333-4444-555555555555"
)
LEAKY_FRAGMENTS = (
    "fake-user",
    "fake-password",
    "example.invalid",
    "11111111-2222-3333-4444-555555555555",
    LEAKY_MESSAGE,
)


class TestTheFailureLogDisclosesNothing:
    @pytest.mark.asyncio
    async def test_an_unexpected_failure_logs_a_fixed_event_only(
        self, db_clean, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """logger.exception writes the traceback, and the traceback is the leak.

        A database error's text is its connection string; an asyncpg error
        quotes the row it choked on. This path is reached by an
        unauthenticated caller sending a wrong token often enough to break
        something, so the log line is a fixed event and the shape of the
        failure goes to system_worker_status instead.
        """

        async def exploding_claim(session: Any) -> None:
            raise RuntimeError(LEAKY_MESSAGE)

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", exploding_claim)
        caplog.set_level(logging.DEBUG)
        summary = await account_deletion.run_cycle()

        assert summary.ok is False
        assert summary.error_code == "unexpected_worker_error"

        logged = caplog.text
        for fragment in LEAKY_FRAGMENTS:
            assert fragment not in logged, fragment
        assert "account_deletion_scheduled_cycle_failed" in logged

    @pytest.mark.asyncio
    async def test_no_traceback_is_attached_to_the_failure_record(
        self, db_clean, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # logger.exception sets exc_info on the record. Asserting on the
        # record rather than on the rendered text catches a handler that
        # happens not to format it today.
        async def exploding_claim(session: Any) -> None:
            raise RuntimeError(LEAKY_MESSAGE)

        monkeypatch.setattr(account_deletion.deletion_service, "claim_next", exploding_claim)
        caplog.set_level(logging.DEBUG)
        await account_deletion.run_cycle()

        failures = [
            record for record in caplog.records
            if record.getMessage() == "account_deletion_scheduled_cycle_failed"
        ]
        assert failures, "the fixed event must still be logged"
        for record in failures:
            assert record.exc_info is None, "the traceback reached the log"
            assert record.args in (None, ()), "the event carries no interpolated data"

    def test_the_source_does_not_call_logger_exception(self) -> None:
        source = _executable_source(
            BACKEND_ROOT / "app" / "workers" / "account_deletion.py"
        )
        scheduled = source[source.index("async def _write_heartbeat"):source.index(
            "async def run_forever"
        )]
        assert "logger.exception" not in scheduled
        assert "exc_info" not in scheduled
        # The exception's class name may be recorded in the bounded operator
        # summary; its text may not be recorded anywhere.
        assert "str(exc)" not in scheduled


# ---------------------------------------------------------------------------
# 6. The scheduler secret is required, and never printed
# ---------------------------------------------------------------------------
class TestTheSchedulerSecretConfiguration:
    def test_it_is_declared_with_a_length_floor(self) -> None:
        assert config.INTERNAL_SCHEDULER_TOKEN_MIN_LENGTH >= 32

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_a_missing_token_is_missing(self, value: str) -> None:
        assert config.classify_scheduler_token(value) == "missing"

    @pytest.mark.parametrize("value", ["short", "abc", "x" * 31])
    def test_a_short_token_is_invalid(self, value: str) -> None:
        # Length before shape, deliberately: a short placeholder is reported
        # as too short. Both refuse; what must not happen is the production
        # validator and the readiness report disagreeing about which.
        assert config.classify_scheduler_token(value) == "invalid"

    @pytest.mark.parametrize(
        "value",
        [
            "placeholder" + "x" * 30,
            "changeme" + "y" * 30,
            "your_scheduler_token_here" + "z" * 20,
            "replace-me" + "q" * 30,
            "secret-here" + "r" * 30,
            "xxxx" + "s" * 30,
            "your-token" + "t" * 30,
            "example" + "u" * 30,
            "change_me" + "v" * 30,
            "TODO" + "w" * 30,
        ],
    )
    def test_a_placeholder_token_is_a_placeholder(self, value: str) -> None:
        assert config.classify_scheduler_token(value) == "placeholder"

    def test_a_real_looking_token_is_configured(self) -> None:
        assert config.classify_scheduler_token("A7f" + "k9Qz" * 12) == "configured"

    def test_the_production_validator_uses_the_shared_classifier(self) -> None:
        """The refusal and the report must be one judgement, not two.

        Read from the validator's own source rather than inferred: the two
        used to keep separate marker lists, so a token containing "replace",
        "secret-here", "xxxx" or "example" was refused at boot and reported
        `configured` by the very tool an operator reaches for to find out why
        boot was refused.
        """
        source = _executable_source(BACKEND_ROOT / "app" / "config.py")
        block = source[source.index("def validate_production_configuration"):]
        assert "classify_scheduler_token(INTERNAL_SCHEDULER_TOKEN)" in block
        # And no second opinion left behind beside it: the validator may name
        # the floor in the message it raises, but must not apply one itself.
        assert "len(scheduler_token)" not in block
        assert "SCHEDULER_TOKEN_PLACEHOLDER_MARKERS" not in block

    def test_the_readiness_report_delegates_rather_than_deciding(self) -> None:
        source = _executable_source(BACKEND_ROOT / "app" / "release_readiness.py")
        block = source[source.index("def _scheduler_token_status"):]
        assert "config.classify_scheduler_token" in block
        assert "PLACEHOLDER_MARKERS" not in block
        assert "INTERNAL_SCHEDULER_TOKEN_MIN_LENGTH" not in block

    @pytest.mark.parametrize(
        "value",
        [
            "", "   ", "short", "x" * 31,
            "replace" + "a" * 30, "secret-here" + "b" * 30, "xxxx" + "c" * 30,
            "your-thing" + "d" * 30, "example" + "e" * 30,
            "placeholder" + "f" * 30, "changeme" + "g" * 30,
            "not-a-real-token-not-a-real-token-not-a-real-token",
        ],
    )
    def test_config_and_readiness_never_disagree(self, monkeypatch, value: str) -> None:
        """The parity that did not hold, on the markers where it did not hold.

        Every one of "replace", "secret-here", "xxxx", "your-" and "example"
        refused production boot while the readiness report said `configured`.
        """
        from app import release_readiness

        monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", value)
        report = release_readiness.evaluate()
        assert report.required["INTERNAL_SCHEDULER_TOKEN"] == (
            config.classify_scheduler_token(value)
        )

    def test_the_readiness_report_states_status_never_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import release_readiness

        secret = "S3cr3t" + "v" * 40
        monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", secret)
        report = release_readiness.evaluate()
        rendered = release_readiness.render(report)
        assert "INTERNAL_SCHEDULER_TOKEN" in rendered
        assert secret not in rendered
        assert str(len(secret)) not in rendered.split("INTERNAL_SCHEDULER_TOKEN")[1][:80]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", "missing"),
            ("placeholder" + "x" * 30, "placeholder"),
            ("tooshort", "invalid"),
            ("A7f" + "k9Qz" * 12, "configured"),
        ],
    )
    def test_the_readiness_status_words(
        self, monkeypatch: pytest.MonkeyPatch, value: str, expected: str
    ) -> None:
        from app import release_readiness

        monkeypatch.setattr(config, "INTERNAL_SCHEDULER_TOKEN", value)
        report = release_readiness.evaluate()
        assert report.required["INTERNAL_SCHEDULER_TOKEN"] == expected

    def test_the_env_example_ships_it_empty(self) -> None:
        raw = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "INTERNAL_SCHEDULER_TOKEN=" in raw
        line = next(
            ln for ln in raw.splitlines() if ln.startswith("INTERNAL_SCHEDULER_TOKEN=")
        )
        assert line.strip() == "INTERNAL_SCHEDULER_TOKEN=", line

    def test_no_token_value_is_committed_anywhere(self) -> None:
        for path in (RENDER_YAML, ENV_EXAMPLE, OPERATIONS_DOC):
            raw = path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if "INTERNAL_SCHEDULER_TOKEN" not in line:
                    continue
                after = line.split("INTERNAL_SCHEDULER_TOKEN", 1)[1].lstrip()
                if after.startswith("="):
                    assert after.strip() == "=", (path.name, line)


class TestProductionValidationStillRequiresEverythingElse:
    @pytest.mark.parametrize(
        "key",
        [
            "POSTGRES_URL", "OFF_DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
            "GEMINI_API_KEY", "SENTRY_BACKEND_DSN", "PRIVACY_POLICY_URL",
            "SUPPORT_URL", "ALLOWED_ORIGINS",
        ],
    )
    def test_the_existing_requirement_is_still_written_down(self, key: str) -> None:
        # Free-tier hosting is not a reason to relax runtime validation: every
        # one of these providers has a free-start path.
        source = (BACKEND_ROOT / "app" / "config.py").read_text(encoding="utf-8")
        assert key in source, key


# ---------------------------------------------------------------------------
# 7. The Supabase Cron runbook is documented, not installed
# ---------------------------------------------------------------------------
class TestTheSupabaseCronRunbook:
    @staticmethod
    def _doc() -> str:
        return OPERATIONS_DOC.read_text(encoding="utf-8")

    def test_both_jobs_are_documented_with_their_schedules(self) -> None:
        doc = self._doc()
        assert "glamgenius-account-deletion" in doc
        assert "glamgenius-notifications" in doc
        assert "*/5 * * * *" in doc
        assert "0 * * * *" in doc

    def test_both_targets_are_documented(self) -> None:
        doc = self._doc()
        assert DELETION_PATH in doc
        assert NOTIFICATION_PATH in doc

    def test_the_required_extensions_are_named(self) -> None:
        doc = self._doc()
        for extension in ("pg_cron", "pg_net", "Vault"):
            assert extension in doc, extension

    def test_the_secret_is_referenced_from_vault_not_inlined(self) -> None:
        doc = self._doc()
        assert "vault" in doc.lower()
        # The runbook may name the key; it must never show a value.
        assert not re.search(r"Bearer\s+[A-Za-z0-9_\-]{20,}", doc)

    def test_store_a_is_excluded_from_the_scheduler_jobs(self) -> None:
        doc = self._doc().lower()
        assert "store a" in doc
        assert "application" in doc

    def test_a_finite_timeout_is_documented(self) -> None:
        assert "timeout" in self._doc().lower()

    def test_the_timeouts_are_specific_reviewed_numbers(self) -> None:
        """"Use a finite timeout" is an instruction nobody can get wrong twice.

        Left unspecified it becomes whatever the operator typed, including
        nothing. The runbook names both values so the cron definitions can be
        written from it rather than from judgement.
        """
        doc = self._doc()
        assert "timeout_milliseconds" in doc
        assert "90000" in doc, "the deletion timeout must be a stated number"
        assert "120000" in doc, "the notification timeout must be a stated number"

    def test_the_deletion_timeout_cannot_overlap_its_own_schedule(self) -> None:
        # 90s inside a 300s interval. A timeout longer than the interval would
        # let two cycles be in flight at once.
        from app.workers import schedule

        assert schedule.ACCOUNT_DELETION_INTERVAL_SECONDS > 90

    def test_the_three_observability_layers_are_distinguished(self) -> None:
        doc = self._doc()
        for layer in ("cron.job_run_details", "net._http_response", "system_worker_status"):
            assert layer in doc, layer

    def test_it_does_not_claim_http_status_appears_in_cron_job_run_details(self) -> None:
        """pg_net is asynchronous, and this is the mistake it causes.

        `cron.job_run_details = succeeded` means the request was enqueued. An
        HTTP 401 never appears there. A runbook that sends an operator to that
        table to find a 401 sends them to a table that will always look fine.
        """
        # Emphasis markers stripped: the runbook writes "*not* evidence", and
        # a scan for "is not evidence" would miss its own denial.
        prose = " ".join(self._doc().lower().replace("*", "").replace("`", "").split())
        assert "asynchronous" in prose
        assert "not evidence that the endpoint returned 2xx" in prose
        assert "never appears there" in prose

    #: The dotted identifiers have to survive sentence splitting, so they are
    #: swapped for single tokens before the text is cut on full stops.
    #: Lowercase on purpose. The text is lowered before substitution, so an
    #: uppercase token would never match the lowercase needle the tests look
    #: for — and a scan that matches nothing passes everything.
    _TABLE_TOKENS = {
        "cron.job_run_details": "cronruns",
        "net._http_response": "httpresp",
        "net.http_request_queue": "reqqueue",
    }

    #: Words that make a mention a denial rather than a claim.
    _NEGATIONS = (
        "not ", "never", "only", "nothing more", "enqueue", "does not",
        "cannot", "no evidence", "is not",
    )

    #: Ways of naming the cron layer other than its full identifier. A false
    #: claim does not have to spell the table out to mislead an operator —
    #: "the cron table also records the HTTP status" does it just as well.
    _CRON_ALIASES = ("cronruns", "job_run_details", "cron table", "cron job table")

    @classmethod
    def _sentences(cls, text: str) -> list[str]:
        flat = " ".join(text.lower().replace("*", "").replace("`", "").split())
        for dotted, token in cls._TABLE_TOKENS.items():
            flat = flat.replace(dotted, token)
        return [part.strip() for part in re.split(r"[.:;]", flat) if part.strip()]

    @classmethod
    def _names_the_cron_layer(cls, sentence: str) -> bool:
        return any(alias in sentence for alias in cls._CRON_ALIASES)

    def test_no_sentence_anywhere_puts_an_http_status_in_cron_job_run_details(self) -> None:
        """Every mention, not merely the existence of a denial somewhere.

        The previous version asserted the correct sentence appeared in the
        observability section. It did — and a contradictory sentence sat in
        the qualification procedure several screens later, telling the
        operator a 401 shows up in `cron.job_run_details`. Both can be true of
        a document; only one can be true of pg_net, and the procedure is the
        half that gets followed under time pressure.

        So this walks every sentence naming the table and refuses any that
        also names an HTTP status without denying the association.
        """
        offenders = []
        for sentence in self._sentences(self._doc()):
            if not self._names_the_cron_layer(sentence):
                continue
            if not re.search(r"\b(401|2xx|4xx|5xx|status.?code|http status)\b", sentence):
                continue
            if not any(marker in sentence for marker in self._NEGATIONS):
                offenders.append(sentence)
        assert offenders == [], offenders

    def test_the_http_status_source_is_named_positively(self) -> None:
        """Saying where the status is not found is only half of it."""
        positive = [
            s for s in self._sentences(self._doc())
            if "httpresp" in s
            and re.search(r"\b(401|status.?code|http result)\b", s)
            and not any(m in s for m in ("not ", "never", "cannot"))
        ]
        assert positive, "the runbook must say where an HTTP status does come from"

    def test_the_qualification_procedure_sends_the_operator_to_the_right_table(self) -> None:
        """The section an operator actually follows, checked on its own."""
        doc = self._doc()
        anchor = "Verify the hourly notification job"
        assert anchor in doc
        section = doc[doc.index(anchor):]
        section = section[: section.index("**11.")]

        for sentence in self._sentences(section):
            if self._names_the_cron_layer(sentence) and re.search(
                r"\b(401|status)\b", sentence
            ):
                assert any(m in sentence for m in self._NEGATIONS), sentence

        flat = " ".join(section.lower().replace("*", "").replace("`", "").split())
        assert "net._http_response" in flat
        assert "401" in flat
        index = flat.index("401")
        assert "net._http_response" in flat[max(0, index - 200):index + 200], (
            "the 401 must be attributed to net._http_response"
        )
        # And the procedure must state positively that the response table is
        # where an HTTP status comes from, in its own right. Leaning on the
        # 401 sentence alone lets the statement be deleted while the section
        # still mentions the table in passing.
        positive = [
            sentence for sentence in self._sentences(section)
            if "httpresp" in sentence
            and re.search(r"\b(status.?code|http status|http result)\b", sentence)
            and not any(m in sentence for m in ("not ", "never", "cannot"))
        ]
        assert positive, (
            "the procedure must say that net._http_response carries the HTTP status"
        )

    def test_the_three_layer_model_survives_in_the_procedure(self) -> None:
        doc = self._doc()
        section = doc[doc.index("Verify the hourly notification job"):]
        section = section[: section.index("**11.")]
        for layer in ("cron.job_run_details", "net._http_response", "system_worker_status"):
            assert layer in section, layer

    def test_the_http_result_query_reads_no_headers(self) -> None:
        """The SQL the operator will paste, not the prose around it.

        The narrow query stands on its own merits: five operational columns
        answer every routine scheduler question, and the response body and
        response headers are unnecessary and may carry incidental
        information. (The outbound bearer is not in this table at all — see
        the request/response mapping tests below.)
        """
        doc = self._doc()
        sql_blocks = re.findall(r"```sql\n(.*?)```", doc, flags=re.DOTALL)
        assert sql_blocks, "the runbook must show the query"
        http_queries = [b for b in sql_blocks if "net._http_response" in b]
        assert http_queries, "the HTTP-result query must be shown"
        for block in http_queries:
            assert "select *" not in block.lower(), block
            assert "headers" not in block.lower(), block
            assert "status_code" in block.lower()
        # And the prose must say why the columns are named.
        assert "header" in doc.lower()

    def test_it_does_not_claim_the_token_is_absent_from_tests(self) -> None:
        """The runbook said "not in any test". It was not true.

        Tests deliberately carry scheduler-token fixtures — the validation
        rules cannot be exercised without one. An operator document that
        overstates the rule teaches the reader that the document is
        approximately true, which is the worst thing a security runbook can
        teach.
        """
        prose = " ".join(self._doc().lower().replace("*", "").replace("`", "").split())
        # Every way of saying it, because mutation testing showed that
        # forbidding one phrasing only forbids that phrasing: rewording the
        # denial walked straight past the first version of this test.
        for denial in (
            "not in any test",
            "in no test",
            "no test contains",
            "not in tests",
            "do not contain scheduler-token fixtures",
            "contains no scheduler-token",
        ):
            assert denial not in prose, denial
        # And the affirmative, which no denial can satisfy: the document has
        # to say that the fixtures exist, not merely avoid saying they don't.
        assert "do contain scheduler-token fixtures" in prose

    def test_it_states_the_real_rule_about_committed_credentials(self) -> None:
        prose = " ".join(self._doc().lower().replace("*", "").replace("`", "").split())
        # What must not be committed: a production or generated secret.
        assert "no production credential" in prose
        assert "high-entropy" in prose
        # What may exist: explicitly synthetic fixtures.
        assert "synthetic" in prose
        assert "low-entropy" in prose

    def test_the_real_token_still_lives_in_exactly_two_places(self) -> None:
        prose = " ".join(self._doc().lower().replace("*", "").replace("`", "").split())
        block = prose[prose.index("the real production internal_scheduler_token"):][:400]
        assert "render" in block
        assert "vault" in block

    def test_the_request_and_response_header_tables_are_distinguished(self) -> None:
        """pg_net keeps the outbound Authorization header somewhere else.

        The runbook used to justify the narrow query by saying
        `net._http_response` carries the request headers. It does not: its
        `headers` column is the endpoint's *response* headers. The outbound
        bearer lives in `net.http_request_queue` while the request is queued.
        Getting this backwards sends an operator looking for a secret in the
        wrong table and reassures them about the right one.
        """
        doc = self._doc()
        assert "net.http_request_queue" in doc
        request_rows = [
            line for line in doc.splitlines()
            if "net.http_request_queue" in line and "|" in line
        ]
        assert request_rows, "the request-side table must be named in the mapping"
        for row in request_rows:
            assert "request" in row.lower(), row

        response_rows = [
            line for line in doc.splitlines()
            if "net._http_response" in line and "|" in line and "headers" in line.lower()
        ]
        assert response_rows, "the response-side table must be named in the mapping"
        for row in response_rows:
            assert "response" in row.lower(), row

    def test_it_never_claims_the_bearer_is_in_the_response_headers(self) -> None:
        """Asserted on the claim, not on proximity.

        The corrected runbook necessarily mentions `_http_response.headers`
        and the bearer in the same paragraph — to say they are *not* the same
        thing. A scan for the two words near each other would read the
        correction as the error it corrects.
        """
        doc = self._doc()
        rows = [
            line for line in doc.splitlines()
            if "net._http_response" in line and line.strip().startswith("|")
        ]
        header_rows = [r for r in rows if "headers" in r.lower()]
        assert header_rows, "the mapping row must exist"
        for row in header_rows:
            # The bearer-token column must answer No for this table.
            assert "no" in row.lower().split("|")[-2], row

        # And the prose must deny it explicitly rather than merely omit it.
        prose = " ".join(doc.lower().replace("*", "").replace("`", "").split())
        assert "are the endpoint's response headers, not the outbound bearer" in prose

    def test_the_outbound_authorization_header_is_still_forbidden(self) -> None:
        prose = " ".join(self._doc().lower().replace("*", "").replace("`", "").split())
        block = prose[prose.index("the outbound authorization header"):][:500]
        assert "never be queried" in block
        assert "vault" in block

    def test_each_http_outcome_is_explained(self) -> None:
        doc = self._doc()
        for outcome in ("401", "5xx", "timed_out", "error_msg"):
            assert outcome in doc, outcome


class TestTheZeroCostInvariant:
    """₹0 is a billing configuration, not a plan name."""

    @staticmethod
    def _doc() -> str:
        return OPERATIONS_DOC.read_text(encoding="utf-8")

    def test_the_payment_method_rule_is_stated_as_an_invariant(self) -> None:
        doc = self._doc()
        assert "DO NOT ADD A PAYMENT METHOD" in doc

    def test_it_explains_what_a_payment_method_changes(self) -> None:
        prose = " ".join(self._doc().lower().split())
        # Both halves, because only the pair makes it a guarantee: without a
        # card an overage suspends, with one it bills.
        assert "suspend" in prose
        assert "bandwidth" in prose
        assert "build" in prose

    def test_outbound_bandwidth_is_stated_to_include_external_traffic(self) -> None:
        prose = " ".join(self._doc().lower().split())
        assert "supabase" in prose
        anchor = "outbound bandwidth counts more than people expect"
        assert anchor in prose, "the paragraph that makes this point must exist"
        block = prose[prose.index(anchor):][:600]
        assert "supabase" in block
        assert "gemini" in block or "external api" in block

    def test_the_qualification_checklist_exists_and_stops(self) -> None:
        doc = self._doc()
        for item in (
            "workspace plan", "payment method", "paid service",
        ):
            assert item.lower() in doc.lower(), item
        assert "STOP" in doc

    def test_the_checklist_is_not_performed_here(self) -> None:
        # Documented, never executed: no provider was touched.
        prose = " ".join(self._doc().lower().split())
        assert "not in this change" in prose or "does not create them" in prose

    def test_it_says_plainly_that_the_jobs_are_not_created_here(self) -> None:
        # A reviewer has to be able to tell what this repository did from what
        # an operator still has to do. The claim is load-bearing, so it is
        # asserted rather than trusted.
        prose = " ".join(self._doc().lower().split())
        assert "it does not create them" in prose

    def test_the_token_is_listed_among_the_secret_key_names(self) -> None:
        # The procedure tells the operator to enter every key from that table.
        # A key named only in render.yaml's comments is a key nobody enters,
        # and a service that starts refusing its own scheduler.
        assert "INTERNAL_SCHEDULER_TOKEN" in self._doc()

    def test_the_free_tier_limits_are_stated_honestly(self) -> None:
        doc = self._doc().lower()
        for honesty in ("no sla", "restart", "free", "private beta"):
            assert honesty in doc, honesty

    def test_it_does_not_call_the_infrastructure_production_grade(self) -> None:
        # The phrase may appear only in a denial. Checked per sentence so a
        # denial elsewhere cannot cover a claim here. Soft line wraps are
        # joined first — a sentence the author happened to break across two
        # lines is still one sentence, and splitting on every newline would
        # read "it is not" and "production-grade infrastructure" as two.
        doc = re.sub(r"[ \t]*\n(?!\n)[ \t]*", " ", self._doc().lower())
        for sentence in re.split(r"[.\n]", doc):
            if "production-grade" not in sentence:
                continue
            assert "not production-grade" in sentence or "≠" in sentence, sentence.strip()

    def test_no_fake_keepalive_is_introduced(self) -> None:
        # Named only to forbid them. The paragraph that mentions them must be
        # the one that says not to add them.
        doc = self._doc().lower()
        for forbidden in ("uptimerobot", "cron-job.org", "keepalive", "self-ping"):
            if forbidden not in doc:
                continue
            paragraph = next(
                block for block in doc.split("\n\n") if forbidden in block
            )
            assert "do **not** add" in paragraph or "not add" in paragraph, forbidden

    def test_the_repository_installs_no_cron(self) -> None:
        # Documented, never performed. Nothing here creates the job.
        for path in (RENDER_YAML,):
            raw = path.read_text(encoding="utf-8")
            assert "pg_cron" not in raw
            assert "cron.schedule" not in raw


# ---------------------------------------------------------------------------
# 8. Readiness behaviour, in terms of the scheduled worker
# ---------------------------------------------------------------------------
class TestReadinessGraceArithmetic:
    """The decision readiness makes, isolated from the database."""

    @staticmethod
    def _stale(age_seconds: int) -> bool:
        return age_seconds > schedule.ACCOUNT_DELETION_STALE_AFTER_SECONDS

    def test_a_fresh_heartbeat_is_not_stale(self) -> None:
        assert not self._stale(0)
        assert not self._stale(299)

    def test_one_missed_run_is_tolerated(self) -> None:
        # A scheduler that fires a little late must not take the API out of
        # rotation.
        assert not self._stale(301)
        assert not self._stale(600)

    def test_two_missed_runs_are_stale(self) -> None:
        assert self._stale(601)
        assert self._stale(3600)

    def test_the_boundary_is_exactly_two_intervals(self) -> None:
        interval = schedule.ACCOUNT_DELETION_INTERVAL_SECONDS
        assert interval * 2 == schedule.ACCOUNT_DELETION_STALE_AFTER_SECONDS

    def test_readiness_only_consults_the_worker_when_jobs_are_pending(self) -> None:
        # An idle system with no deletion requests must not be unready merely
        # because nothing has run.
        source = (BACKEND_ROOT / "app" / "api" / "v2" / "config.py").read_text(
            encoding="utf-8"
        )
        block = source[source.index("pending_jobs"):]
        assert "if pending_jobs > 0:" in block
        assert "idle_no_pending_jobs" in block


class TestReadinessAgainstTheDatabase:
    """The real query, against the real table."""

    @pytest.mark.asyncio
    async def test_pending_jobs_with_no_heartbeat_is_not_ready(
        self, db_clean, app_client
    ) -> None:
        await _insert_pending_job()
        response = await app_client.get("/api/v2/ready")
        body = response.json()
        assert body["components"]["worker_heartbeat"] == "missing"
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_pending_jobs_with_a_fresh_scheduled_heartbeat_passes_that_check(
        self, db_clean, app_client
    ) -> None:
        await _insert_pending_job()
        await _insert_heartbeat(age_seconds=60)
        response = await app_client.get("/api/v2/ready")
        assert response.json()["components"]["worker_heartbeat"] == "fresh"

    @pytest.mark.asyncio
    async def test_pending_jobs_with_a_stale_scheduled_heartbeat_is_not_ready(
        self, db_clean, app_client
    ) -> None:
        await _insert_pending_job()
        await _insert_heartbeat(age_seconds=1200)
        response = await app_client.get("/api/v2/ready")
        assert response.json()["components"]["worker_heartbeat"] == "stale"
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_a_heartbeat_just_inside_the_grace_window_is_fresh(
        self, db_clean, app_client
    ) -> None:
        await _insert_pending_job()
        await _insert_heartbeat(age_seconds=500)
        response = await app_client.get("/api/v2/ready")
        assert response.json()["components"]["worker_heartbeat"] == "fresh"

    @pytest.mark.asyncio
    async def test_no_pending_jobs_never_fails_for_a_worker_reason(
        self, db_clean, app_client
    ) -> None:
        response = await app_client.get("/api/v2/ready")
        assert response.json()["components"]["worker_heartbeat"] == "idle_no_pending_jobs"

    @pytest.mark.asyncio
    async def test_a_hostname_named_worker_row_no_longer_satisfies_readiness(
        self, db_clean, app_client
    ) -> None:
        # The old daemon row must not be mistaken for the scheduled worker.
        await _insert_pending_job()
        await _insert_heartbeat(age_seconds=10, name="account_deletion_worker_pod7")
        response = await app_client.get("/api/v2/ready")
        assert response.json()["components"]["worker_heartbeat"] == "missing"


async def _insert_pending_job() -> None:
    """One deletion job in a state readiness counts as pending."""
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text

    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO account_deletion_jobs "
                "(id, account_id, state, attempt_count, requested_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), gen_random_uuid(), 'requested', 0, now(), now(), now())"
            )
        )
        await session.commit()


async def _insert_heartbeat(*, age_seconds: int, name: str | None = None) -> None:
    """One heartbeat row, aged in the database rather than in Python.

    ``last_heartbeat_at`` is a naive column, so the interval is subtracted in
    SQL and the result cast there — a Python-side datetime would have to guess
    at the session's time zone to write the same instant.
    """
    from app.shared.database.sql import get_sessionmaker
    from sqlalchemy import text

    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO system_worker_status "
                "(id, worker_name, last_heartbeat_at, started_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :name, "
                "(now() AT TIME ZONE 'UTC') - make_interval(secs => :age), "
                "now(), now(), now()) "
                "ON CONFLICT (worker_name) DO UPDATE "
                "SET last_heartbeat_at = EXCLUDED.last_heartbeat_at"
            ),
            {"name": name or schedule.ACCOUNT_DELETION_WORKER_NAME, "age": age_seconds},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 9. Admin observability
# ---------------------------------------------------------------------------
class TestAdminWorkerObservability:
    def test_both_scheduled_workers_are_reported(self) -> None:
        assert set(admin.SCHEDULED_WORKERS) == {
            "account_deletion_worker",
            "notification_worker",
        }

    def test_the_reported_intervals(self) -> None:
        assert admin.SCHEDULED_WORKERS["account_deletion_worker"] == 300
        assert admin.SCHEDULED_WORKERS["notification_worker"] == 3600

    def test_the_missed_semantics_are_unchanged(self) -> None:
        now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

        class _Worker:
            last_heartbeat_at = now - dt.timedelta(seconds=10)
            last_error_code = None
            last_error_at = None
            last_successful_job_at = now

        healthy = admin._scheduled_state("account_deletion_worker", 300, _Worker(), now)
        assert healthy["state"] == "healthy"

        class _Late(_Worker):
            last_heartbeat_at = now - dt.timedelta(seconds=1200)

        missed = admin._scheduled_state("account_deletion_worker", 300, _Late(), now)
        assert missed["state"] == "missed"

        never = admin._scheduled_state("account_deletion_worker", 300, None, now)
        assert never["state"] == "never_run"

    def test_no_customer_data_is_added_to_the_report(self) -> None:
        now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
        state = admin._scheduled_state("account_deletion_worker", 300, None, now)
        assert set(state) == {
            "worker_name",
            "expected_interval_seconds",
            "state",
            "last_heartbeat_age_seconds",
            "detail",
        }
