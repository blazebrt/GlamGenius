"""Step 14A — offline knowledge-pack inventory and contract validation.

Two things are being proved here, and they pull in opposite directions.

The first is that the inspector *works*: it finds every pack committed to the
repository, reports its operational identity, and rejects every structural
violation the release workflow would otherwise discover far too late — a
duplicated id, two packs competing for one reason key, a compiler that was
renamed away.

The second is that the inspector *changes nothing*. A knowledge pack is an
inert source artifact. Adding a tool that reads packs must not turn the
package into something the running application can reach, must not make a
pack load at import time, and must not give the inspector any appetite for a
database, a socket, a credential or a customer decision. Those properties
were true before this milestone and the tests below hold them afterwards.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from app.domains.personal_decision_release.manifest import (
    manifest_content_hash,
    parse_release_manifest,
)
from app.knowledge_packs import inspection
from app.knowledge_packs import petrolatum_dry_skin_v1 as pack
from app.knowledge_packs.inspection import (
    COMPILER_ATTRIBUTE,
    InspectionResult,
    KnowledgePackDescriptor,
    PackValidationCode,
    PackValidationError,
    as_json_payload,
    claims_to_be_a_pack,
    discover_pack_modules,
    inspect_packs,
    validate_descriptors,
)

from tests.test_step8i_first_production_knowledge_pack import _valid_entry

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
INSPECTION_SOURCE = BACKEND_ROOT / "app" / "knowledge_packs" / "inspection.py"
CLI_PATH = REPOSITORY_ROOT / "scripts" / "inspect_knowledge_packs.py"
PACKAGE_INIT = BACKEND_ROOT / "app" / "knowledge_packs" / "__init__.py"

#: The exact content hash of the manifest the reviewed pack compiles from the
#: reviewed published entry, measured on the commit before Step 14A began.
#:
#: Step 14A adds an inventory tool and nothing else. If this number moves, the
#: milestone changed compiled customer knowledge, which it is not allowed to
#: do, and the change is a defect regardless of how sensible it looks.
BASELINE_MANIFEST_CONTENT_HASH = "be1fbbf8ae6435e18fdcfba86d85d7697a6257959bc2c6c75c452bcd434e75be"

VALID_ATTRIBUTES: dict[str, object] = {
    "PACK_ID": "for_you.skin_care.synthetic.v1",
    "DOMAIN": "skin_care",
    "CATEGORY": "skin_care",
    "REASON_KEY": "for_you.skin_care.synthetic.reason",
    COMPILER_ATTRIBUTE: lambda entry: {},
}


def _run_python(code: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a fresh interpreter with the backend importable.

    A subprocess is the only honest way to ask "what does importing this load".
    In-process the answer is always contaminated: the test session has already
    imported the pack, the inspector and most of the application.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


class _Absent:
    """Sentinel meaning "do not set this attribute at all"."""


_ABSENT = _Absent()


def _executable_source(path: Path) -> str:
    """The file's code with every docstring and comment removed.

    A raw-text scan of a file whose docstrings explain what it refuses to do
    finds those refusals and calls them violations. Parsing and re-emitting
    leaves only what actually runs.
    """
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


def _top_level_imports(path: Path) -> set[str]:
    """The root package of everything the file imports, however it imports it."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _called_names(path: Path) -> set[str]:
    """Every name this file calls, whether plainly or through an attribute."""
    called: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


@pytest.fixture
def synthetic_pack():
    """Build throwaway modules that look like packs, and remove them after.

    Every negative case needs a module with a deliberately wrong contract.
    Writing those into the repository would mean shipping broken packs to
    satisfy a test, so they are built in memory and registered under names no
    real module uses.
    """
    registered: list[str] = []

    def build(suffix: str, **overrides: object) -> str:
        name = f"synthetic_knowledge_pack_{suffix}"
        module = ModuleType(name)
        attributes = dict(VALID_ATTRIBUTES)
        attributes.update(overrides)
        for key, value in attributes.items():
            if value is _ABSENT:
                continue
            setattr(module, key, value)
        sys.modules[name] = module
        registered.append(name)
        return name

    yield build

    for name in registered:
        sys.modules.pop(name, None)


def _codes(result: InspectionResult) -> list[tuple[str, str, str]]:
    return [(error.module, error.field, str(error.code)) for error in result.errors]


# ---------------------------------------------------------------------------
# What is actually committed
# ---------------------------------------------------------------------------
class TestTheRepositoryInventory:
    def test_the_committed_inventory_is_valid(self) -> None:
        result = inspect_packs()
        assert result.errors == ()
        assert result.ok is True
        assert len(result.packs) == 1

    def test_the_reviewed_pack_is_reported_with_its_real_identity(self) -> None:
        (descriptor,) = inspect_packs().packs
        assert descriptor.module == "app.knowledge_packs.petrolatum_dry_skin_v1"
        assert descriptor.pack_id == pack.PACK_ID
        assert descriptor.domain == pack.DOMAIN
        assert descriptor.category == pack.CATEGORY
        assert descriptor.reason_key == pack.REASON_KEY
        assert descriptor.compiler_name == COMPILER_ATTRIBUTE
        assert callable(getattr(pack, descriptor.compiler_name))

    def test_discovery_lists_the_pack_and_skips_the_inspector(self) -> None:
        modules = discover_pack_modules()
        assert "app.knowledge_packs.petrolatum_dry_skin_v1" in modules
        assert "app.knowledge_packs.inspection" not in modules
        assert all(not name.rsplit(".", 1)[-1].startswith("_") for name in modules)

    def test_discovery_is_sorted_and_repeatable(self) -> None:
        first = discover_pack_modules()
        assert first == tuple(sorted(first))
        assert first == discover_pack_modules()

    def test_the_inspector_is_not_itself_a_pack(self) -> None:
        assert claims_to_be_a_pack(inspection) is False
        assert claims_to_be_a_pack(importlib.import_module("app.knowledge_packs")) is False
        assert claims_to_be_a_pack(pack) is True


# ---------------------------------------------------------------------------
# The contract, violated one way at a time
# ---------------------------------------------------------------------------
class TestContractViolations:
    def test_a_well_formed_synthetic_pack_passes(self, synthetic_pack) -> None:
        name = synthetic_pack("good")
        result = inspect_packs([name])
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [name]

    def test_a_module_without_the_marker_is_not_a_pack_and_not_an_error(
        self, synthetic_pack
    ) -> None:
        name = synthetic_pack("plain", PACK_ID=_ABSENT)
        result = inspect_packs([name])
        assert result.packs == ()
        assert result.errors == ()

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            " for_you.skin_care.synthetic.v1",
            "for_you.skin_care.synthetic.v1 ",
            "petrolatum",
            "for_you.v1",
            "For_You.Skin_Care.Synthetic.v1",
            "for_you.skin care.synthetic.v1",
            "for_you.skin_care.synthetic.version1",
            "for_you.skin_care.synthetic.v",
            "for_you.skin_care..v1",
            "for_you.skin_care.synthetic.1",
            42,
        ],
    )
    def test_an_unusable_pack_id_is_reported(self, synthetic_pack, value: object) -> None:
        name = synthetic_pack("badid", PACK_ID=value)
        result = inspect_packs([name])
        assert result.packs == ()
        assert _codes(result) == [(name, "PACK_ID", "INVALID_PACK_ID")]

    @pytest.mark.parametrize("value", [None, "", "  ", " skin_care", "skin_care ", 7])
    @pytest.mark.parametrize(
        ("field", "code"),
        [
            ("DOMAIN", "MISSING_DOMAIN"),
            ("CATEGORY", "MISSING_CATEGORY"),
            ("REASON_KEY", "MISSING_REASON_KEY"),
        ],
    )
    def test_an_unusable_identity_field_is_reported(
        self, synthetic_pack, field: str, code: str, value: object
    ) -> None:
        name = synthetic_pack("badfield", **{field: value})
        result = inspect_packs([name])
        assert result.packs == ()
        assert _codes(result) == [(name, field, code)]

    def test_a_field_that_is_absent_entirely_is_reported_the_same_way(
        self, synthetic_pack
    ) -> None:
        name = synthetic_pack("nodomain", DOMAIN=_ABSENT)
        assert _codes(inspect_packs([name])) == [(name, "DOMAIN", "MISSING_DOMAIN")]

    def test_a_missing_compiler_is_reported(self, synthetic_pack) -> None:
        name = synthetic_pack("nocompiler", **{COMPILER_ATTRIBUTE: _ABSENT})
        result = inspect_packs([name])
        assert result.packs == ()
        assert _codes(result) == [(name, COMPILER_ATTRIBUTE, "MISSING_COMPILER")]

    def test_a_compiler_that_is_not_callable_is_reported(self, synthetic_pack) -> None:
        name = synthetic_pack("stringcompiler", **{COMPILER_ATTRIBUTE: "build it yourself"})
        result = inspect_packs([name])
        assert result.packs == ()
        assert _codes(result) == [(name, COMPILER_ATTRIBUTE, "COMPILER_NOT_CALLABLE")]

    def test_a_compiler_set_to_none_is_reported_as_missing(self, synthetic_pack) -> None:
        name = synthetic_pack("nonecompiler", **{COMPILER_ATTRIBUTE: None})
        assert _codes(inspect_packs([name])) == [
            (name, COMPILER_ATTRIBUTE, "MISSING_COMPILER")
        ]

    def test_every_violation_in_one_module_is_reported_not_just_the_first(
        self, synthetic_pack
    ) -> None:
        name = synthetic_pack(
            "allwrong",
            PACK_ID="",
            DOMAIN="",
            CATEGORY="",
            REASON_KEY="",
            **{COMPILER_ATTRIBUTE: _ABSENT},
        )
        assert _codes(inspect_packs([name])) == sorted(
            [
                (name, "PACK_ID", "INVALID_PACK_ID"),
                (name, "DOMAIN", "MISSING_DOMAIN"),
                (name, "CATEGORY", "MISSING_CATEGORY"),
                (name, "REASON_KEY", "MISSING_REASON_KEY"),
                (name, COMPILER_ATTRIBUTE, "MISSING_COMPILER"),
            ]
        )

    def test_a_module_that_cannot_be_imported_is_reported_not_raised(self) -> None:
        missing = "synthetic_knowledge_pack_that_was_never_written"
        result = inspect_packs([missing])
        assert result.packs == ()
        assert _codes(result) == [(missing, "module", "IMPORT_FAILED")]

    def test_an_import_failure_does_not_stop_the_rest_of_the_run(self, synthetic_pack) -> None:
        good = synthetic_pack("survivor")
        missing = "synthetic_knowledge_pack_that_was_never_written"
        result = inspect_packs([missing, good])
        assert [descriptor.module for descriptor in result.packs] == [good]
        assert _codes(result) == [(missing, "module", "IMPORT_FAILED")]

    def test_an_import_failure_never_quotes_the_exception(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A module that dies on import can say anything in its exception text,
        # including something that should never reach a CI log. The report is
        # a coordinate — module, field, code — and carries none of it.
        name = "synthetic_knowledge_pack_that_explodes"
        (tmp_path / f"{name}.py").write_text(
            'raise RuntimeError("postgresql://someone:hunter2@db.internal:5432/prod")\n',
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, name, raising=False)
        result = inspect_packs([name])
        assert _codes(result) == [(name, "module", "IMPORT_FAILED")]
        encoded = json.dumps(as_json_payload(result))
        for secret in ("hunter2", "postgresql://", "db.internal", "RuntimeError"):
            assert secret not in encoded
        sys.modules.pop(name, None)

    def test_one_broken_pack_does_not_hide_the_others(self, synthetic_pack) -> None:
        good = synthetic_pack("fine")
        broken = synthetic_pack("broken", PACK_ID="nope", REASON_KEY="for_you.other.reason")
        result = inspect_packs([broken, good])
        assert [descriptor.module for descriptor in result.packs] == [good]
        assert _codes(result) == [(broken, "PACK_ID", "INVALID_PACK_ID")]


# ---------------------------------------------------------------------------
# Uniqueness across packs
# ---------------------------------------------------------------------------
class TestCrossPackUniqueness:
    def test_two_packs_sharing_a_pack_id_are_both_named(self, synthetic_pack) -> None:
        first = synthetic_pack("dup_a", REASON_KEY="for_you.skin_care.a.reason")
        second = synthetic_pack("dup_b", REASON_KEY="for_you.skin_care.b.reason")
        result = inspect_packs([first, second])
        assert result.ok is False
        assert _codes(result) == sorted(
            [
                (first, "PACK_ID", "DUPLICATE_PACK_ID"),
                (second, "PACK_ID", "DUPLICATE_PACK_ID"),
            ]
        )

    def test_two_packs_sharing_a_reason_key_are_both_named(self, synthetic_pack) -> None:
        first = synthetic_pack("rk_a", PACK_ID="for_you.skin_care.a.v1")
        second = synthetic_pack("rk_b", PACK_ID="for_you.skin_care.b.v1")
        result = inspect_packs([first, second])
        assert result.ok is False
        assert _codes(result) == sorted(
            [
                (first, "REASON_KEY", "DUPLICATE_REASON_KEY"),
                (second, "REASON_KEY", "DUPLICATE_REASON_KEY"),
            ]
        )

    def test_three_way_duplication_names_all_three(self, synthetic_pack) -> None:
        names = [
            synthetic_pack(f"triple_{index}", REASON_KEY=f"for_you.skin_care.{index}.reason")
            for index in range(3)
        ]
        result = inspect_packs(names)
        assert _codes(result) == sorted(
            (name, "PACK_ID", "DUPLICATE_PACK_ID") for name in names
        )

    def test_distinct_packs_do_not_collide(self, synthetic_pack) -> None:
        first = synthetic_pack(
            "sep_a", PACK_ID="for_you.skin_care.a.v1", REASON_KEY="for_you.skin_care.a.reason"
        )
        second = synthetic_pack(
            "sep_b", PACK_ID="for_you.hair_care.b.v2", REASON_KEY="for_you.hair_care.b.reason"
        )
        result = inspect_packs([first, second])
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [second, first]

    def test_a_broken_pack_is_excluded_from_uniqueness_checking(self, synthetic_pack) -> None:
        # Both declare the same reason key, but one has no usable domain, so it
        # never becomes a descriptor. Reporting a duplicate against a pack that
        # failed its own contract would be noise on top of the real finding.
        good = synthetic_pack("uniq_good", PACK_ID="for_you.skin_care.a.v1")
        broken = synthetic_pack("uniq_broken", PACK_ID="for_you.skin_care.b.v1", DOMAIN="")
        result = inspect_packs([good, broken])
        assert _codes(result) == [(broken, "DOMAIN", "MISSING_DOMAIN")]

    def test_a_version_bump_is_not_a_duplicate(self, synthetic_pack) -> None:
        first = synthetic_pack(
            "v1", PACK_ID="for_you.skin_care.thing.v1", REASON_KEY="for_you.skin_care.thing.one"
        )
        second = synthetic_pack(
            "v2", PACK_ID="for_you.skin_care.thing.v2", REASON_KEY="for_you.skin_care.thing.two"
        )
        assert inspect_packs([first, second]).ok is True


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_input_order_does_not_change_output_order(self, synthetic_pack) -> None:
        names = [
            synthetic_pack(
                f"order_{index}",
                PACK_ID=f"for_you.skin_care.p{index}.v1",
                REASON_KEY=f"for_you.skin_care.p{index}.reason",
            )
            for index in range(5)
        ]
        forward = inspect_packs(names)
        backward = inspect_packs(list(reversed(names)))
        assert forward == backward
        assert [descriptor.pack_id for descriptor in forward.packs] == sorted(
            descriptor.pack_id for descriptor in forward.packs
        )

    def test_error_order_is_stable(self, synthetic_pack) -> None:
        names = [
            synthetic_pack(f"err_{index}", PACK_ID="bad", DOMAIN="")
            for index in range(4)
        ]
        assert inspect_packs(names).errors == inspect_packs(list(reversed(names))).errors

    def test_repeated_runs_of_the_real_inventory_agree(self) -> None:
        assert inspect_packs() == inspect_packs()

    def test_the_json_payload_serialises_identically_every_time(self) -> None:
        first = json.dumps(as_json_payload(inspect_packs()), sort_keys=True)
        second = json.dumps(as_json_payload(inspect_packs()), sort_keys=True)
        assert first == second


# ---------------------------------------------------------------------------
# The machine-readable report
# ---------------------------------------------------------------------------
class TestJsonPayload:
    def test_the_shape_is_exactly_this(self) -> None:
        payload = as_json_payload(inspect_packs())
        assert set(payload) == {"status", "pack_count", "packs", "errors"}
        assert payload["status"] == "ok"
        assert payload["pack_count"] == 1
        assert payload["errors"] == []
        (entry,) = payload["packs"]
        assert set(entry) == {
            "module",
            "pack_id",
            "domain",
            "category",
            "reason_key",
            "compiler",
        }

    def test_a_failing_inventory_says_invalid(self, synthetic_pack) -> None:
        name = synthetic_pack("badjson", PACK_ID="bad")
        payload = as_json_payload(inspect_packs([name]))
        assert payload["status"] == "invalid"
        assert payload["pack_count"] == 0
        assert payload["errors"] == [
            {"module": name, "field": "PACK_ID", "code": "INVALID_PACK_ID"}
        ]

    def test_the_payload_carries_no_scientific_content(self) -> None:
        # Inventory is operational metadata. A pack's evidence prose, source
        # locators, fact conditions and strength belong to the reviewed
        # evidence record; a cross-pack listing must not become a second,
        # unreviewed copy of them.
        #
        # The substance key is deliberately not on this list. It is not
        # content: the pack chose to spell it inside its own id and reason
        # key, and a report that hid it would be describing a different pack
        # than the one in the repository.
        encoded = json.dumps(as_json_payload(inspect_packs()))
        for leaked in (
            pack.EVIDENCE_SUMMARY,
            pack.EVIDENCE_SCOPE,
            pack.EVIDENCE_STRENGTH_RATIONALE,
            pack.EVIDENCE_STRENGTH,
            pack.AAD_SOURCE_LOCATOR,
            pack.AAD_SOURCE_URL,
            pack.IDENTITY_SOURCE_URL,
            pack.FACT_KEY,
            pack.FACT_OPERATOR,
            *pack.FACT_VALUES,
        ):
            assert leaked not in encoded

    def test_the_payload_is_plain_json_types(self) -> None:
        payload = as_json_payload(inspect_packs())
        assert json.loads(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# The offline CLI
# ---------------------------------------------------------------------------
def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("step14a_cli_under_test", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheCli:
    def test_it_exists_and_is_not_inside_the_application(self) -> None:
        assert CLI_PATH.is_file()
        assert not CLI_PATH.is_relative_to(BACKEND_ROOT / "app")

    def test_it_exits_zero_on_the_real_repository(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI_PATH)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert pack.PACK_ID in completed.stdout
        assert "Contract check: OK" in completed.stdout

    def test_the_json_flag_emits_exactly_the_payload(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI_PATH), "--json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == as_json_payload(inspect_packs())

    def test_it_runs_from_any_working_directory(self, tmp_path: Path) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI_PATH), "--json"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["status"] == "ok"

    def test_a_failing_contract_produces_a_non_zero_exit(self, monkeypatch, capsys) -> None:
        cli = _load_cli()
        failing = InspectionResult(
            packs=(),
            errors=(
                PackValidationError(
                    module="synthetic",
                    field="PACK_ID",
                    code=PackValidationCode.DUPLICATE_PACK_ID,
                ),
            ),
        )
        monkeypatch.setattr(cli, "inspect_packs", lambda: failing)
        assert cli.main([]) == 1
        human = capsys.readouterr().out
        assert "FAILED" in human
        assert "DUPLICATE_PACK_ID" in human
        assert cli.main(["--json"]) == 1
        assert json.loads(capsys.readouterr().out)["status"] == "invalid"

    def test_an_empty_inventory_renders_without_pretending_to_be_broken(
        self, monkeypatch, capsys
    ) -> None:
        cli = _load_cli()
        monkeypatch.setattr(cli, "inspect_packs", lambda: InspectionResult(packs=(), errors=()))
        assert cli.main([]) == 0
        out = capsys.readouterr().out
        assert "No knowledge packs found." in out
        assert "Contract check: OK" in out

    def test_it_has_no_switch_that_could_change_knowledge(self) -> None:
        source = CLI_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "--activate",
            "--deactivate",
            "--publish",
            "--compile",
            "--prepare",
            "--approve",
            "--write",
            "--fix",
            "--database-url",
            "--url",
        ):
            assert forbidden not in source
        assert source.count("add_argument") == 1
        assert '"--json"' in source


# ---------------------------------------------------------------------------
# Inertness: reading packs must not make packs reachable
# ---------------------------------------------------------------------------
class TestInertness:
    def test_importing_the_package_still_loads_nothing(self) -> None:
        completed = _run_python(
            "import sys, app.knowledge_packs as p;"
            "print(sorted(m for m in sys.modules if m.startswith('app.knowledge_packs')))"
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "['app.knowledge_packs']"

    def test_the_package_root_is_still_only_a_docstring(self) -> None:
        # The moment __init__ imports a pack, "inert source artifact" stops
        # being true: importing the package would execute pack code, and every
        # inertness proof above would be measuring the wrong thing.
        assert _executable_source(PACKAGE_INIT).strip() == "pass"

    def test_importing_the_inspector_does_not_import_a_pack(self) -> None:
        completed = _run_python(
            "import sys, app.knowledge_packs.inspection;"
            "print('app.knowledge_packs.petrolatum_dry_skin_v1' in sys.modules)"
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "False"

    def test_importing_the_application_does_not_import_the_package(self) -> None:
        completed = _run_python(
            "import os;"
            "os.environ.setdefault('POSTGRES_URL',"
            " 'postgresql+asyncpg://glamgenius:glamgenius@localhost:5432/glamgenius_test');"
            "import sys, server;"
            "print(sorted(m for m in sys.modules if 'knowledge_pack' in m))"
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "[]"

    def test_no_application_module_names_the_pack_package(self) -> None:
        # Restated from Step 8I because this milestone adds a file to that
        # package. The inspector resolves the package through __package__ and
        # so is a member rather than a reacher-in; if a later change hard-codes
        # the dotted path inside app/, this fails and it should.
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

    def test_the_inspector_is_not_reachable_from_any_route_or_worker(self) -> None:
        for root in (BACKEND_ROOT / "app" / "api", BACKEND_ROOT / "app" / "workers"):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                assert "knowledge_packs" not in text
                assert "inspect_packs" not in text

    def test_the_inspector_is_not_wired_into_startup_or_seeding(self) -> None:
        for relative in (
            "server.py",
            "app/release.py",
            "app/release_readiness.py",
            "app/bootstrap/reference_data.py",
            "app/config.py",
        ):
            text = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
            assert "knowledge_pack" not in text
            assert "inspect_packs" not in text


# ---------------------------------------------------------------------------
# No database, no network, no credentials
# ---------------------------------------------------------------------------
class TestTheInspectorTouchesNothing:
    def test_it_opens_no_socket(self, monkeypatch) -> None:
        def refuse(*args: object, **kwargs: object):
            raise AssertionError("the inspector must not open a socket")

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        result = inspect_packs()
        assert result.ok is True

    def test_it_runs_with_no_environment_at_all(self) -> None:
        bare = subprocess.run(
            [sys.executable, str(CLI_PATH), "--json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            timeout=180,
        )
        assert bare.returncode == 0, bare.stderr
        assert json.loads(bare.stdout)["status"] == "ok"

    def test_a_wrong_database_configuration_changes_nothing(self) -> None:
        nonsense = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "POSTGRES_URL": "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none",
            "OFF_DATABASE_URL": "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none",
            "SUPABASE_URL": "not-a-url",
            "SUPABASE_JWKS_URL": "not-a-url",
            "GEMINI_API_KEY": "",
            "SENTRY_DSN": "not-a-dsn",
        }
        with_nonsense = subprocess.run(
            [sys.executable, str(CLI_PATH), "--json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            env=nonsense,
            timeout=180,
        )
        bare = subprocess.run(
            [sys.executable, str(CLI_PATH), "--json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            timeout=180,
        )
        assert with_nonsense.returncode == 0, with_nonsense.stderr
        assert with_nonsense.stdout == bare.stdout

    def test_the_inspector_imports_only_the_standard_library_it_needs(self) -> None:
        assert _top_level_imports(INSPECTION_SOURCE) == {
            "__future__",
            "importlib",
            "pkgutil",
            "collections",
            "dataclasses",
            "enum",
            "types",
        }

    def test_the_cli_imports_only_the_standard_library_and_the_inspector(self) -> None:
        assert _top_level_imports(CLI_PATH) == {
            "__future__",
            "argparse",
            "json",
            "sys",
            "pathlib",
            "app",
        }

    def test_neither_file_reads_the_environment_or_writes_a_file(self) -> None:
        for path in (INSPECTION_SOURCE, CLI_PATH):
            called = _called_names(path)
            for forbidden in (
                "getenv",
                "environ",
                "open",
                "write_text",
                "write_bytes",
                "mkdir",
                "unlink",
                "rmtree",
                "connect",
                "execute",
                "run",
                "system",
            ):
                assert forbidden not in called, (path.name, forbidden)

    def test_the_inspector_never_calls_a_compiler_or_a_release_operation(self) -> None:
        # Naming the compiler is the contract. Running it would need a
        # reviewed published evidence entry, which an inventory tool has no
        # business inventing, and would produce a manifest nobody asked for.
        called = _called_names(INSPECTION_SOURCE) | _called_names(CLI_PATH)
        for forbidden in (
            COMPILER_ATTRIBUTE,
            "compiler",
            "prepare",
            "compile",
            "activate",
            "deactivate",
            "publish",
            "approve",
            "rollback",
        ):
            assert forbidden not in called, forbidden


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------
class TestScale:
    def _descriptors(self, count: int) -> list[KnowledgePackDescriptor]:
        return [
            KnowledgePackDescriptor(
                module=f"app.knowledge_packs.pack_{index:04d}",
                pack_id=f"for_you.skin_care.pack_{index:04d}.v1",
                domain="skin_care",
                category="skin_care",
                reason_key=f"for_you.skin_care.pack_{index:04d}.reason",
                compiler_name=COMPILER_ATTRIBUTE,
            )
            for index in range(count)
        ]

    def test_hundreds_of_distinct_packs_validate_clean(self) -> None:
        assert validate_descriptors(self._descriptors(500)) == ()

    def test_a_single_collision_among_hundreds_is_found(self) -> None:
        descriptors = self._descriptors(500)
        descriptors[321] = KnowledgePackDescriptor(
            module="app.knowledge_packs.pack_0321",
            pack_id=descriptors[7].pack_id,
            domain="skin_care",
            category="skin_care",
            reason_key="for_you.skin_care.pack_0321.reason",
            compiler_name=COMPILER_ATTRIBUTE,
        )
        errors = validate_descriptors(descriptors)
        assert {error.module for error in errors} == {
            "app.knowledge_packs.pack_0007",
            "app.knowledge_packs.pack_0321",
        }
        assert {str(error.code) for error in errors} == {"DUPLICATE_PACK_ID"}

    def test_a_reason_key_collision_among_hundreds_is_found(self) -> None:
        descriptors = self._descriptors(400)
        descriptors[11] = KnowledgePackDescriptor(
            module="app.knowledge_packs.pack_0011",
            pack_id="for_you.skin_care.pack_0011.v1",
            domain="skin_care",
            category="skin_care",
            reason_key=descriptors[398].reason_key,
            compiler_name=COMPILER_ATTRIBUTE,
        )
        errors = validate_descriptors(descriptors)
        assert {str(error.code) for error in errors} == {"DUPLICATE_REASON_KEY"}
        assert len(errors) == 2

    def test_validation_stays_linear_enough_to_be_unnoticeable(self) -> None:
        import time

        descriptors = self._descriptors(2000)
        started = time.perf_counter()
        validate_descriptors(descriptors)
        assert time.perf_counter() - started < 2.0


# ---------------------------------------------------------------------------
# Compiled knowledge is byte-for-byte what it was
# ---------------------------------------------------------------------------
class TestCompiledKnowledgeIsUnchanged:
    def test_the_reviewed_pack_still_compiles_the_same_manifest(self) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry())
        assert (
            manifest_content_hash(parse_release_manifest(manifest))
            == BASELINE_MANIFEST_CONTENT_HASH
        )

    def test_the_pack_file_was_not_edited_by_this_milestone(self) -> None:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/main"],
            cwd=REPOSITORY_ROOT,
            text=True,
        ).split()
        assert "backend/app/knowledge_packs/petrolatum_dry_skin_v1.py" not in changed


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
class TestScope:
    def test_forbidden_scope_is_untouched(self) -> None:
        changed = {
            line.strip()
            for line in subprocess.check_output(
                ["git", "diff", "--name-only", "HEAD"], cwd=REPOSITORY_ROOT, text=True
            ).splitlines()
            if line.strip()
        }
        for prefix in ("backend/migrations/", "frontend/", ".github/"):
            assert not any(path.startswith(prefix) for path in changed), prefix
        assert not any("requirements" in path for path in changed)
        assert not any(path.endswith("yarn.lock") for path in changed)

    def test_this_milestone_added_no_migration_and_no_stray_file(self) -> None:
        # git diff cannot see a brand-new file. A migration added but not yet
        # committed is exactly the change this milestone must not contain, so
        # the untracked list is checked as well.
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPOSITORY_ROOT,
            text=True,
        ).split()
        for path in untracked:
            assert not path.startswith("backend/migrations/"), path
            assert not path.startswith("frontend/"), path
            assert not path.startswith(".github/"), path

        against_main = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/main"], cwd=REPOSITORY_ROOT, text=True
        ).split()
        assert not any(path.startswith("backend/migrations/") for path in against_main)
