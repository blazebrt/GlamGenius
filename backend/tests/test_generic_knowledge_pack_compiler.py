"""Step 14C — the generic offline knowledge-pack compiler.

Step 14A can find and validate every committed pack without running any of
them. Step 14B makes CI check that on every run. Compilation was still wired
to one pack by name, in a script that imports
``app.knowledge_packs.petrolatum_dry_skin_v1`` directly — fine for one pack,
useless for twenty.

``scripts/build_knowledge_pack_release.py`` generalises it, and the thing to
hold is where execution begins. The inspector's whole safety property is that
it never runs candidate source. The compiler's whole purpose is to run one.
The two coexist only because of the order: inspect everything statically,
refuse on any structural failure, resolve exactly one id, and only then import
that one module. These tests hold that order, the exactness of the selection,
and the one regression that would matter most — that the generic tool produces
byte-for-byte what the reviewed pack-specific builder produces.

The legacy builder is deliberately left in place and untouched. It is the
independent oracle the compatibility proof compares against; folding it into
the generic tool now would leave nothing to compare with.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
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
from app.knowledge_packs.inspection import COMPILER_ATTRIBUTE

from tests.test_step8i_first_production_knowledge_pack import _valid_entry

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
GENERIC_BUILDER = REPOSITORY_ROOT / "scripts" / "build_knowledge_pack_release.py"
LEGACY_BUILDER = REPOSITORY_ROOT / "scripts" / "build_step8i_petrolatum_release.py"
OPERATOR = REPOSITORY_ROOT / "scripts" / "operate_step8i_petrolatum_release.py"
PACK_MODULE = "app.knowledge_packs.petrolatum_dry_skin_v1"

REVIEWED_PACK_ID = "for_you.skin_care.petrolatum_dry_skin.v1"

#: The hash the reviewed pack has compiled to since Step 8I. The generic tool
#: adapts to this; this never adapts to the generic tool.
REVIEWED_CONTENT_HASH = "be1fbbf8ae6435e18fdcfba86d85d7697a6257959bc2c6c75c452bcd434e75be"


@pytest.fixture(scope="module")
def entry_file(tmp_path_factory) -> Path:
    """The reviewed published Step 8G entry, written once.

    Reused from the Step 8I suite rather than copied. A second hand-written
    copy of this payload would be a second scientific authority that drifts
    the first time either is edited.
    """
    path = tmp_path_factory.mktemp("step14c") / "published_entry.json"
    path.write_text(json.dumps(_valid_entry(), indent=2), encoding="utf-8")
    return path


def _executable_source(path: Path) -> str:
    """The file's code with every docstring and comment removed.

    A raw-text scan of a file whose docstring explains what it refuses to do
    finds those refusals and calls them violations — a mistake worth making
    only once. Parsing and re-emitting leaves only what actually runs.
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


def _called_names(path: Path) -> set[str]:
    """Every name this file calls, plainly or through an attribute."""
    called: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def _top_level_imports(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERIC_BUILDER), *args],
        cwd=cwd or REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("step14c_builder_under_test", GENERIC_BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Runs the builder in a fresh interpreter behind a meta-path recorder, and
#: reports which pack modules the import system was asked for. A recorder
#: rather than a blocker: the question is not "can it be stopped" but "what
#: did it actually reach for".
_IMPORT_PROBE = '''
import json, sys, importlib.util

class Recorder:
    def __init__(self):
        self.seen = []

    def find_spec(self, name, path=None, target=None):
        if name.startswith("app.knowledge_packs.") and name != "app.knowledge_packs.inspection":
            self.seen.append(name)
        return None

recorder = Recorder()
sys.meta_path.insert(0, recorder)

spec = importlib.util.spec_from_file_location("builder_under_probe", {builder!r})
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
{patch}
import contextlib, io
captured = io.StringIO()
with contextlib.redirect_stderr(captured), contextlib.redirect_stdout(io.StringIO()):
    status = builder.main({argv!r})
print(json.dumps({{
    "status": status,
    "imported": recorder.seen,
    "stderr": captured.getvalue(),
}}))
'''


def _probe_imports(argv: list[str], *, patch: str = "") -> dict:
    code = _IMPORT_PROBE.format(builder=str(GENERIC_BUILDER), argv=argv, patch=patch)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
class TestTheCommandLine:
    def test_the_builder_exists_and_is_offline_tooling(self) -> None:
        assert GENERIC_BUILDER.is_file()
        assert not GENERIC_BUILDER.is_relative_to(BACKEND_ROOT / "app")

    def test_the_pack_id_is_mandatory(self, entry_file: Path) -> None:
        # One pack exists today and it would be trivial to default to it. That
        # default is the whole hazard: the day a second pack lands, muscle
        # memory compiles the wrong knowledge and it looks like success.
        completed = _run(str(entry_file))
        assert completed.returncode != 0
        assert "--pack-id" in completed.stderr

    def test_the_input_file_is_mandatory(self) -> None:
        completed = _run("--pack-id", REVIEWED_PACK_ID)
        assert completed.returncode != 0

    def test_it_offers_no_way_to_avoid_naming_the_pack(self) -> None:
        source = _executable_source(GENERIC_BUILDER)
        for forbidden in ("--latest", "--newest", "--default", "--domain", "--category"):
            assert forbidden not in source, forbidden

    def test_it_offers_no_switch_that_could_reach_a_database_or_a_release(self) -> None:
        source = _executable_source(GENERIC_BUILDER)
        for forbidden in (
            "--activate",
            "--deactivate",
            "--approve",
            "--publish",
            "--release-id",
            "--database",
            "--action",
            "--signal",
            "--policy",
            "--reason",
        ):
            assert forbidden not in source, forbidden

    def test_it_declares_exactly_three_arguments(self) -> None:
        # ast.unparse normalises quoting, so the flags are read from the
        # parsed call rather than matched as text.
        tree = ast.parse(GENERIC_BUILDER.read_text(encoding="utf-8"))
        declared = [
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]
        assert declared == ["--pack-id", "input", "--output"]


# ---------------------------------------------------------------------------
# Selection is exact, or it is a refusal
# ---------------------------------------------------------------------------
class TestPackSelectionIsExact:
    @pytest.mark.parametrize(
        ("label", "pack_id"),
        [
            ("unknown", "for_you.skin_care.does_not_exist.v1"),
            ("case mismatch", "FOR_YOU.SKIN_CARE.PETROLATUM_DRY_SKIN.V1"),
            ("one letter wrong", "for_you.skin_care.petrolatum_dry_skin.v2"),
            ("prefix", "for_you.skin_care.petrolatum_dry_skin"),
            ("suffix", "petrolatum_dry_skin.v1"),
            ("substring", "petrolatum"),
            ("empty", ""),
            ("whitespace padded", " for_you.skin_care.petrolatum_dry_skin.v1 "),
        ],
    )
    def test_only_the_exact_id_resolves(
        self, entry_file: Path, label: str, pack_id: str
    ) -> None:
        completed = _run("--pack-id", pack_id, str(entry_file))
        assert completed.returncode != 0, label
        assert "PACK_NOT_FOUND" in completed.stderr, label

    def test_the_refusal_names_the_ids_that_do_exist(self, entry_file: Path) -> None:
        # An operator who mistyped needs the real spelling, and pack ids are
        # already public repository metadata.
        completed = _run("--pack-id", "for_you.skin_care.typo.v1", str(entry_file))
        assert REVIEWED_PACK_ID in completed.stderr


# ---------------------------------------------------------------------------
# The golden proof: generic == the reviewed pack-specific builder
# ---------------------------------------------------------------------------
class TestCompatibilityWithTheReviewedBuilder:
    def test_the_generic_builder_reproduces_the_reviewed_hash(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "generic.json"
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(output))
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == f"content_hash={REVIEWED_CONTENT_HASH}"

    def test_both_builders_emit_byte_identical_manifests(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        # The regression that matters most. If these ever diverge, the generic
        # builder is wrong — the reviewed pack-specific path is the authority
        # and does not move to accommodate it.
        legacy_out = tmp_path / "legacy.json"
        generic_out = tmp_path / "generic.json"
        legacy = subprocess.run(
            [sys.executable, str(LEGACY_BUILDER), str(entry_file), "--output", str(legacy_out)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        generic = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(generic_out))
        assert legacy.returncode == 0, legacy.stderr
        assert generic.returncode == 0, generic.stderr
        assert legacy_out.read_bytes() == generic_out.read_bytes()
        assert legacy.stdout.strip() == generic.stdout.strip()
        assert legacy.stdout.strip() == f"content_hash={REVIEWED_CONTENT_HASH}"

    def test_the_manifests_agree_as_parsed_objects_too(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        legacy_out = tmp_path / "legacy.json"
        generic_out = tmp_path / "generic.json"
        subprocess.run(
            [sys.executable, str(LEGACY_BUILDER), str(entry_file), "--output", str(legacy_out)],
            cwd=REPOSITORY_ROOT, capture_output=True, text=True, timeout=180, check=True,
        )
        assert _run(
            "--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(generic_out)
        ).returncode == 0
        legacy = json.loads(legacy_out.read_text(encoding="utf-8"))
        generic = json.loads(generic_out.read_text(encoding="utf-8"))
        assert legacy == generic
        assert manifest_content_hash(parse_release_manifest(generic)) == REVIEWED_CONTENT_HASH

    def test_stdout_carries_the_manifest_when_no_output_file_is_given(
        self, entry_file: Path
    ) -> None:
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file))
        assert completed.returncode == 0, completed.stderr
        manifest_text, _, hash_line = completed.stdout.rpartition("content_hash=")
        assert hash_line.strip() == REVIEWED_CONTENT_HASH
        emitted = json.loads(manifest_text)
        assert manifest_content_hash(parse_release_manifest(emitted)) == REVIEWED_CONTENT_HASH


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_repeated_invocations_produce_identical_bytes(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        first = tmp_path / "first.json"
        second = tmp_path / "second.json"
        one = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(first))
        two = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(second))
        assert one.returncode == 0 and two.returncode == 0
        assert first.read_bytes() == second.read_bytes()
        assert one.stdout == two.stdout

    def test_stdout_is_identical_across_runs(self, entry_file: Path) -> None:
        first = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file))
        second = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file))
        assert first.stdout == second.stdout

    def test_the_builder_adds_nothing_of_its_own_to_the_manifest(
        self, entry_file: Path
    ) -> None:
        # No timestamp, no run id, no tool version. The manifest is the pack's
        # output and nothing else, which is what makes the hash meaningful.
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file))
        manifest_text = completed.stdout.rpartition("content_hash=")[0]
        emitted = json.loads(manifest_text)
        from app.knowledge_packs import petrolatum_dry_skin_v1 as pack

        assert emitted == pack.build_release_manifest_from_published_entry(_valid_entry())


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
class TestRefusals:
    def test_unreadable_input(self, tmp_path: Path) -> None:
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(tmp_path / "absent.json"))
        assert completed.returncode != 0
        assert "INPUT_UNREADABLE" in completed.stderr

    def test_invalid_json(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{ not json at all", encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(broken))
        assert completed.returncode != 0
        assert "INVALID_INPUT_JSON" in completed.stderr

    @pytest.mark.parametrize(("label", "body"), [
        ("list", "[1, 2, 3]"),
        ("null", "null"),
        ("string", '"a published entry"'),
        ("number", "42"),
        ("bool", "true"),
    ])
    def test_a_json_document_that_is_not_an_object(
        self, tmp_path: Path, label: str, body: str
    ) -> None:
        path = tmp_path / f"{label}.json"
        path.write_text(body, encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(path))
        assert completed.returncode != 0, label
        assert "INPUT_NOT_OBJECT" in completed.stderr, label

    def test_a_published_entry_the_pack_rejects(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps({"claim_key": "something else"}), encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(path))
        assert completed.returncode != 0
        assert "COMPILER_REJECTED_INPUT" in completed.stderr

    def test_a_refusal_never_prints_a_traceback(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps({"claim_key": "leaked-value-should-not-appear"}), encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(path))
        assert "Traceback" not in completed.stderr
        assert "leaked-value-should-not-appear" not in completed.stderr
        assert "leaked-value-should-not-appear" not in completed.stdout

    def test_every_refusal_exits_non_zero_rather_than_reporting_an_error_object(
        self, tmp_path: Path
    ) -> None:
        # A machine driving this has to be able to trust the process status.
        path = tmp_path / "wrong.json"
        path.write_text("null", encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(path))
        assert completed.returncode != 0
        assert completed.stdout == ""

    def test_the_refusal_vocabulary_is_closed(self) -> None:
        builder = _load_builder()
        assert {code.value for code in builder.RefusalCode} == {
            "INVALID_INVENTORY",
            "PACK_NOT_FOUND",
            "INPUT_UNREADABLE",
            "INVALID_INPUT_JSON",
            "INPUT_NOT_OBJECT",
            "SELECTED_PACK_IMPORT_FAILED",
            "COMPILER_MISSING",
            "COMPILER_REJECTED_INPUT",
            "INVALID_COMPILED_MANIFEST",
            "OUTPUT_WRITE_FAILED",
        }


# ---------------------------------------------------------------------------
# The whole inventory has to be valid, not just the pack you asked for
# ---------------------------------------------------------------------------
def _broken_inventory_patch(directory: Path) -> str:
    return (
        "from app.knowledge_packs.inspection import inspect_packs as _real\n"
        f"builder.inspect_packs = lambda: _real({str(directory)!r},"
        " package_name='synthetic_knowledge_packs')\n"
    )


@pytest.fixture
def colliding_pack_directory(tmp_path: Path) -> Path:
    """Two synthetic packs claiming the same id — a real inventory failure.

    The real inspector is used; only the directory it looks at is substituted.
    Breaking the committed pack directory to test this would be a worse trade
    than pointing a real inspector at a broken one.
    """
    directory = tmp_path / "packs"
    directory.mkdir()
    body = (
        'PACK_ID = "for_you.skin_care.collision.v1"\n'
        'DOMAIN = "skin_care"\n'
        'CATEGORY = "skin_care"\n'
        'REASON_KEY = "for_you.skin_care.{stem}.reason"\n'
        "\n\n"
        f"def {COMPILER_ATTRIBUTE}(entry):\n"
        "    return {{}}\n"
    )
    for stem in ("first", "second"):
        (directory / f"{stem}.py").write_text(body.format(stem=stem), encoding="utf-8")
    return directory


class TestTheWholeInventoryMustBeValid:
    def test_a_collision_anywhere_refuses_compilation(
        self, entry_file: Path, colliding_pack_directory: Path
    ) -> None:
        # Not "the pack I asked for looks fine". When two packs claim one id,
        # which one you would have got is precisely the open question.
        #
        # The refusal *code* is asserted, not merely that it refused. A
        # mutation probe that deleted the inventory guard left this test
        # passing: the tool still refused, with PACK_NOT_FOUND, for an
        # entirely different reason. "It failed" is not the property.
        result = _probe_imports(
            ["--pack-id", REVIEWED_PACK_ID, str(entry_file)],
            patch=_broken_inventory_patch(colliding_pack_directory),
        )
        assert result["status"] != 0
        assert "INVALID_INVENTORY" in result["stderr"]
        assert "PACK_NOT_FOUND" not in result["stderr"]

    def test_the_refusal_names_the_colliding_packs(
        self, entry_file: Path, colliding_pack_directory: Path
    ) -> None:
        result = _probe_imports(
            ["--pack-id", REVIEWED_PACK_ID, str(entry_file)],
            patch=_broken_inventory_patch(colliding_pack_directory),
        )
        assert "DUPLICATE_PACK_ID" in result["stderr"]

    def test_the_inventory_guard_is_a_refusal_and_not_a_log_line(
        self, colliding_pack_directory: Path, monkeypatch
    ) -> None:
        # In-process and direct, so the guard cannot be deleted and covered
        # for by some later check that happens to fail too. The real inspector
        # runs; only the directory it reads is substituted.
        builder = _load_builder()
        real = builder.inspect_packs
        monkeypatch.setattr(
            builder,
            "inspect_packs",
            lambda: real(colliding_pack_directory, package_name="synthetic_knowledge_packs"),
        )
        with pytest.raises(builder.Refusal) as raised:
            builder._valid_inventory()
        assert raised.value.code == builder.RefusalCode.INVALID_INVENTORY
        assert "DUPLICATE_PACK_ID" in raised.value.detail

    def test_a_healthy_inventory_passes_the_guard(self) -> None:
        builder = _load_builder()
        inventory = builder._valid_inventory()
        assert inventory.errors == ()
        assert REVIEWED_PACK_ID in {d.pack_id for d in inventory.packs}

    def test_a_broken_inventory_imports_no_pack_at_all(
        self, entry_file: Path, colliding_pack_directory: Path
    ) -> None:
        result = _probe_imports(
            ["--pack-id", REVIEWED_PACK_ID, str(entry_file)],
            patch=_broken_inventory_patch(colliding_pack_directory),
        )
        assert result["imported"] == []


# ---------------------------------------------------------------------------
# The import boundary
# ---------------------------------------------------------------------------
class TestImportBoundary:
    def test_an_unknown_pack_id_imports_no_pack(self, entry_file: Path) -> None:
        result = _probe_imports(
            ["--pack-id", "for_you.skin_care.does_not_exist.v1", str(entry_file)]
        )
        assert result["status"] != 0
        assert result["imported"] == []

    def test_a_valid_selection_imports_exactly_the_selected_pack(
        self, entry_file: Path
    ) -> None:
        result = _probe_imports(["--pack-id", REVIEWED_PACK_ID, str(entry_file)])
        assert result["status"] == 0
        assert result["imported"] == [PACK_MODULE]

    def test_bad_input_is_rejected_before_any_pack_is_imported(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        result = _probe_imports(["--pack-id", REVIEWED_PACK_ID, str(path)])
        assert result["status"] != 0
        assert result["imported"] == []

    def test_the_builder_never_walks_or_bulk_imports_the_package(self) -> None:
        # One import call in the whole file, and no way to enumerate the
        # package. `_select` does iterate the descriptors — that is a linear
        # exact-match scan over metadata the inspector already produced, and
        # it imports nothing.
        called = _called_names(GENERIC_BUILDER)
        for forbidden in ("iter_modules", "walk_packages", "discover_pack_modules"):
            assert forbidden not in called, forbidden
        assert "pkgutil" not in _top_level_imports(GENERIC_BUILDER)
        assert _executable_source(GENERIC_BUILDER).count("import_module") == 1

    def test_the_builder_uses_no_execution_primitive(self) -> None:
        called = _called_names(GENERIC_BUILDER)
        for forbidden in ("eval", "exec", "compile", "__import__", "exec_module", "run_path"):
            assert forbidden not in called, forbidden
        assert "runpy" not in _top_level_imports(GENERIC_BUILDER)

    def test_the_builder_holds_no_registry_of_its_own(self) -> None:
        # The static inspector is the inventory authority. A dictionary here
        # would be a second one, and the two would disagree the first time a
        # pack was renamed.
        source = _executable_source(GENERIC_BUILDER)
        assert PACK_MODULE not in source
        assert "petrolatum" not in source
        assert REVIEWED_PACK_ID not in source


# ---------------------------------------------------------------------------
# The compiler boundary
# ---------------------------------------------------------------------------
class TestCompilerBoundary:
    def test_the_callable_comes_from_the_descriptor_not_a_literal(self) -> None:
        source = _executable_source(GENERIC_BUILDER)
        assert COMPILER_ATTRIBUTE not in source
        assert "descriptor.compiler_name" in source

    def test_a_module_that_lost_its_compiler_at_runtime_is_refused(
        self, entry_file: Path, monkeypatch
    ) -> None:
        # Static inspection read the file; this runs it. A module whose import
        # rebinds the compiler away would satisfy the first and has to fail
        # here, because the callable about to be invoked is the one that
        # matters.
        builder = _load_builder()
        descriptor = next(
            d for d in builder.inspect_packs().packs if d.pack_id == REVIEWED_PACK_ID
        )
        stub = ModuleType("stub_pack_without_a_compiler")
        monkeypatch.setattr(builder.importlib, "import_module", lambda name: stub)
        with pytest.raises(builder.Refusal) as raised:
            builder._compiler(descriptor)
        assert raised.value.code == builder.RefusalCode.COMPILER_MISSING

    def test_a_compiler_returning_something_that_is_not_a_manifest_is_refused(
        self, monkeypatch
    ) -> None:
        builder = _load_builder()
        descriptor = next(
            d for d in builder.inspect_packs().packs if d.pack_id == REVIEWED_PACK_ID
        )
        with pytest.raises(builder.Refusal) as raised:
            builder._compile(lambda entry: {"schema_version": 1}, {}, descriptor)
        assert raised.value.code == builder.RefusalCode.INVALID_COMPILED_MANIFEST

    def test_an_invalid_manifest_leaves_no_output_file(self, tmp_path: Path) -> None:
        builder = _load_builder()
        descriptor = next(
            d for d in builder.inspect_packs().packs if d.pack_id == REVIEWED_PACK_ID
        )
        output = tmp_path / "must_not_exist.json"
        with pytest.raises(builder.Refusal):
            builder._compile(lambda entry: {"nonsense": True}, {}, descriptor)
        assert not output.exists()

    def test_the_builder_validates_through_the_existing_release_authority(self) -> None:
        called = _called_names(GENERIC_BUILDER)
        assert "parse_release_manifest" in called
        assert "manifest_content_hash" in called


# ---------------------------------------------------------------------------
# Output file safety
# ---------------------------------------------------------------------------
class TestOutputFileSafety:
    def test_a_failed_compile_writes_no_output_file(self, tmp_path: Path) -> None:
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps({"claim_key": "not the reviewed entry"}), encoding="utf-8")
        output = tmp_path / "manifest.json"
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(wrong), "--output", str(output))
        assert completed.returncode != 0
        assert not output.exists()

    def test_a_failed_compile_leaves_an_existing_file_untouched(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "manifest.json"
        output.write_text("PREVIOUS CONTENT\n", encoding="utf-8")
        wrong = tmp_path / "wrong.json"
        wrong.write_text("null", encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(wrong), "--output", str(output))
        assert completed.returncode != 0
        assert output.read_text(encoding="utf-8") == "PREVIOUS CONTENT\n"

    def test_a_successful_compile_overwrites_as_the_reviewed_builder_does(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "manifest.json"
        output.write_text("PREVIOUS CONTENT\n", encoding="utf-8")
        completed = _run("--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(output))
        assert completed.returncode == 0, completed.stderr
        assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1

    def test_no_temporary_file_is_left_behind(self, entry_file: Path, tmp_path: Path) -> None:
        output = tmp_path / "manifest.json"
        assert _run(
            "--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(output)
        ).returncode == 0
        assert sorted(path.name for path in tmp_path.iterdir()) == ["manifest.json"]


# ---------------------------------------------------------------------------
# Offline
# ---------------------------------------------------------------------------
class TestItTouchesNothing:
    def test_it_compiles_with_no_environment_at_all(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "manifest.json"
        completed = subprocess.run(
            [
                sys.executable, str(GENERIC_BUILDER),
                "--pack-id", REVIEWED_PACK_ID, str(entry_file), "--output", str(output),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"},
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == f"content_hash={REVIEWED_CONTENT_HASH}"

    def test_a_deliberately_wrong_production_configuration_changes_nothing(
        self, entry_file: Path
    ) -> None:
        nonsense = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/tmp",
            "POSTGRES_URL": "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none",
            "OFF_DATABASE_URL": "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none",
            "SUPABASE_URL": "not-a-url",
            "SUPABASE_SERVICE_ROLE_KEY": "not-a-key",
            "GEMINI_API_KEY": "",
            "SENTRY_BACKEND_DSN": "not-a-dsn",
        }
        with_nonsense = subprocess.run(
            [sys.executable, str(GENERIC_BUILDER), "--pack-id", REVIEWED_PACK_ID, str(entry_file)],
            cwd=REPOSITORY_ROOT, capture_output=True, text=True, env=nonsense, timeout=180,
        )
        bare = subprocess.run(
            [sys.executable, str(GENERIC_BUILDER), "--pack-id", REVIEWED_PACK_ID, str(entry_file)],
            cwd=REPOSITORY_ROOT, capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"}, timeout=180,
        )
        assert with_nonsense.returncode == 0, with_nonsense.stderr
        assert with_nonsense.stdout == bare.stdout

    def test_it_opens_no_socket(self, entry_file: Path, monkeypatch, capsys) -> None:
        def refuse(*args: object, **kwargs: object):
            raise AssertionError("the compiler must not open a socket")

        builder = _load_builder()
        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        assert builder.main(["--pack-id", REVIEWED_PACK_ID, str(entry_file)]) == 0
        assert REVIEWED_CONTENT_HASH in capsys.readouterr().out

    def test_it_reaches_for_no_database_or_provider(self) -> None:
        assert _top_level_imports(GENERIC_BUILDER) == {
            "__future__", "argparse", "importlib", "json", "os", "sys",
            "tempfile", "collections", "enum", "pathlib", "app",
        }
        called = _called_names(GENERIC_BUILDER)
        for forbidden in ("getenv", "environ", "connect", "create_engine", "urlopen", "request"):
            assert forbidden not in called, forbidden


# ---------------------------------------------------------------------------
# Boundaries this milestone must not have crossed
# ---------------------------------------------------------------------------
class TestBoundariesHeld:
    def test_the_reviewed_pack_specific_builder_is_still_there(self) -> None:
        # The independent oracle the compatibility proof compares against.
        # Folding it into the generic tool would leave nothing to compare.
        assert LEGACY_BUILDER.is_file()
        assert "petrolatum_dry_skin_v1" in LEGACY_BUILDER.read_text(encoding="utf-8")

    def test_the_production_operator_was_not_generalised(self) -> None:
        source = _executable_source(OPERATOR)
        assert "--pack-id" not in source
        assert "inspect_packs" not in source
        assert "build_knowledge_pack_release" not in source

    def test_the_generic_builder_performs_no_release_operation(self) -> None:
        called = _called_names(GENERIC_BUILDER)
        for forbidden in (
            "activate", "deactivate", "prepare", "publish", "approve", "rollback",
            "create_release", "record_release", "commit", "execute",
        ):
            assert forbidden not in called, forbidden

    def test_the_package_root_did_not_become_a_registry(self) -> None:
        init = BACKEND_ROOT / "app" / "knowledge_packs" / "__init__.py"
        assert _executable_source(init).strip() == "pass"

    def test_the_ci_inventory_gate_is_still_the_inspector(self) -> None:
        # Inspection and compilation answer different questions. The
        # unconditional gate must not become "compile every pack on every PR":
        # compilation needs an operator's deliberate selection and a published
        # entry, neither of which CI has.
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        assert "run: python scripts/inspect_knowledge_packs.py --json" in workflow
        assert "build_knowledge_pack_release" not in workflow


def test_the_new_builder_selects_backend_qualification() -> None:
    completed = subprocess.run(
        ["bash", str(REPOSITORY_ROOT / ".github" / "scripts" / "detect-ci-scope.sh"),
         "base", "head", "pull_request"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CI_SCOPE_CHANGED_FILES": "scripts/build_knowledge_pack_release.py",
        },
    )
    assert completed.returncode == 0, completed.stderr
    emitted = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
    assert emitted["backend"] == "true"
    for key in ("schema", "frontend", "mobile", "web", "container", "security", "release"):
        assert emitted[key] == "false", key
