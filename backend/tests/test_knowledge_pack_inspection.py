"""Step 14A — offline, static knowledge-pack inventory and contract validation.

Three things are being proved here, and they pull in different directions.

The first is that the inspector *works*: it finds every pack committed to the
repository, reports its operational identity, and rejects every structural
violation the release workflow would otherwise discover far too late — a
duplicated id, two packs competing for one reason key, a compiler that was
renamed away.

The second is that it *never runs a pack*. A knowledge pack is an inert
specification, and an inventory tool that imported packs in order to inspect
them would execute every import-time side effect in the repository at once —
the exact failure mode it exists to catch. So inspection is static: read the
file, parse it, read the tree. The proof is not a code review; it is a
synthetic pack that writes a sentinel file at module level, inspected, with
the sentinel asserted absent.

The third is that adding this tool *changes nothing*. Importing the package
must still load no pack, the running application must still be unable to
reach either, and no filename convention may let a governed pack slip out of
the inventory. Those properties were true before this milestone and the tests
below hold them afterwards.

What is deliberately **not** tested here: which files a pull request touches.
An earlier draft asserted that against ``origin/main``, which does not exist
in a GitHub Actions checkout, and against ``HEAD``, which on a committed
branch proves nothing at all. Changed-file policy belongs to CI scope
detection and to the reviewer reading the diff. What a repository unit test
can honestly hold is behaviour determinable from the checked-out tree, and
the pinned manifest content hash below is the guard that actually matters.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
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
    DESCRIPTOR_FIELDS,
    INFRASTRUCTURE_FILENAMES,
    InspectionResult,
    KnowledgePackDescriptor,
    PackValidationCode,
    PackValidationError,
    as_json_payload,
    claims_to_be_a_pack,
    discover_pack_sources,
    inspect_packs,
    pack_source_directory,
    validate_descriptors,
)

from tests.test_step8i_first_production_knowledge_pack import _valid_entry

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PACK_DIRECTORY = BACKEND_ROOT / "app" / "knowledge_packs"
INSPECTION_SOURCE = PACK_DIRECTORY / "inspection.py"
PACKAGE_INIT = PACK_DIRECTORY / "__init__.py"
REVIEWED_PACK_SOURCE = PACK_DIRECTORY / "petrolatum_dry_skin_v1.py"
CLI_PATH = REPOSITORY_ROOT / "scripts" / "inspect_knowledge_packs.py"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
SCOPE_SCRIPT = REPOSITORY_ROOT / ".github" / "scripts" / "detect-ci-scope.sh"

REVIEWED_MODULE = "app.knowledge_packs.petrolatum_dry_skin_v1"
SECOND_REVIEWED_MODULE = "app.knowledge_packs.glycerin_dry_skin_v1"
#: The exact reviewed inventory. Asserted as an equality, never as a
#: minimum: "at least one pack" would pass on an inventory that had
#: silently lost one, which is the failure this module exists to catch.
REVIEWED_PACK_COUNT = 2
SYNTHETIC_PACKAGE = "synthetic_knowledge_packs"

#: The exact content hash of the manifest the reviewed pack compiles from the
#: reviewed published entry, measured on the commit before Step 14A began.
#:
#: Step 14A adds an inventory tool and nothing else. If this number moves, the
#: milestone changed compiled customer knowledge, which it is not allowed to
#: do, and the change is a defect regardless of how sensible it looks.
BASELINE_MANIFEST_CONTENT_HASH = "be1fbbf8ae6435e18fdcfba86d85d7697a6257959bc2c6c75c452bcd434e75be"

VALID_SOURCE = '''\
PACK_ID = "for_you.skin_care.synthetic.v1"
DOMAIN = "skin_care"
CATEGORY = "skin_care"
REASON_KEY = "for_you.skin_care.synthetic.reason"


def build_release_manifest_from_published_entry(entry):
    return {}
'''


def _source(**overrides: str | None) -> str:
    """The valid synthetic pack source with individual declarations swapped.

    ``None`` removes a declaration outright; a string replaces the whole
    statement, so a test can say ``PACK_ID='PACK_ID = make_pack_id()'`` and get
    exactly that line.
    """
    lines = {
        "PACK_ID": 'PACK_ID = "for_you.skin_care.synthetic.v1"',
        "DOMAIN": 'DOMAIN = "skin_care"',
        "CATEGORY": 'CATEGORY = "skin_care"',
        "REASON_KEY": 'REASON_KEY = "for_you.skin_care.synthetic.reason"',
        COMPILER_ATTRIBUTE: (
            f"def {COMPILER_ATTRIBUTE}(entry):\n    return {{}}"
        ),
    }
    for name, replacement in overrides.items():
        if replacement is None:
            lines.pop(name, None)
        else:
            lines[name] = replacement
    return "\n\n".join(lines.values()) + "\n"


def _run_python(code: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a fresh interpreter with the backend importable.

    A subprocess is the only honest way to ask "what does this load". In-process
    the answer is always contaminated: the test session has already imported
    the pack, the inspector and most of the application.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.fixture
def pack_dir(tmp_path: Path):
    """A throwaway pack directory, and a helper that writes candidates into it.

    Every negative case needs a file with a deliberately wrong contract.
    Committing those would mean shipping broken packs to satisfy a test, so
    they are written to a temporary directory that is inspected exactly as the
    real one is — same discovery, same parsing, same rules.
    """
    directory = tmp_path / "packs"
    directory.mkdir()

    def write(stem: str, source: str) -> str:
        (directory / f"{stem}.py").write_text(source, encoding="utf-8")
        return f"{SYNTHETIC_PACKAGE}.{stem}"

    return directory, write


def _inspect(directory: Path) -> InspectionResult:
    return inspect_packs(directory, package_name=SYNTHETIC_PACKAGE)


def _codes(result: InspectionResult) -> list[tuple[str, str, str]]:
    return [(error.module, error.field, str(error.code)) for error in result.errors]


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


# ---------------------------------------------------------------------------
# What is actually committed
# ---------------------------------------------------------------------------
class TestTheRepositoryInventory:
    def test_the_committed_inventory_is_valid(self) -> None:
        result = inspect_packs()
        assert result.errors == ()
        assert result.ok is True
        assert len(result.packs) == REVIEWED_PACK_COUNT

    def test_the_committed_packs_have_distinct_identities(self) -> None:
        packs = inspect_packs().packs
        assert len({descriptor.pack_id for descriptor in packs}) == REVIEWED_PACK_COUNT
        assert len({descriptor.reason_key for descriptor in packs}) == REVIEWED_PACK_COUNT
        assert {descriptor.module for descriptor in packs} == {
            REVIEWED_MODULE,
            SECOND_REVIEWED_MODULE,
        }

    def test_the_reviewed_pack_is_reported_with_its_real_identity(self) -> None:
        (descriptor,) = (d for d in inspect_packs().packs if d.module == REVIEWED_MODULE)
        assert descriptor.module == REVIEWED_MODULE
        assert descriptor.pack_id == pack.PACK_ID
        assert descriptor.domain == pack.DOMAIN
        assert descriptor.category == pack.CATEGORY
        assert descriptor.reason_key == pack.REASON_KEY
        assert descriptor.compiler_name == COMPILER_ATTRIBUTE

    def test_the_reported_identity_matches_the_module_python_actually_loads(self) -> None:
        # The inspector reads the file; this test compares what it read with
        # what importing the same file produces. Static and dynamic agree on
        # the reviewed pack, which is what makes the static route safe to
        # trust rather than merely cheaper.
        (descriptor,) = (d for d in inspect_packs().packs if d.module == REVIEWED_MODULE)
        for field, value in (
            ("PACK_ID", descriptor.pack_id),
            ("DOMAIN", descriptor.domain),
            ("CATEGORY", descriptor.category),
            ("REASON_KEY", descriptor.reason_key),
        ):
            assert getattr(pack, field) == value
        assert callable(getattr(pack, COMPILER_ATTRIBUTE))

    def test_discovery_finds_the_pack_file_and_excludes_only_infrastructure(self) -> None:
        found = discover_pack_sources()
        assert REVIEWED_PACK_SOURCE in found
        assert INSPECTION_SOURCE not in found
        assert PACKAGE_INIT not in found
        on_disk = {path.name for path in PACK_DIRECTORY.glob("*.py")}
        assert {path.name for path in found} == on_disk - set(INFRASTRUCTURE_FILENAMES)

    def test_discovery_is_sorted_and_repeatable(self) -> None:
        first = discover_pack_sources()
        assert list(first) == sorted(first)
        assert first == discover_pack_sources()

    def test_the_pack_directory_is_found_from_this_module_not_a_hard_coded_path(self) -> None:
        assert pack_source_directory() == PACK_DIRECTORY

    def test_the_inspector_and_the_package_root_are_not_packs(self) -> None:
        for path in (INSPECTION_SOURCE, PACKAGE_INIT):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            assert claims_to_be_a_pack(tree) is False
        assert claims_to_be_a_pack(ast.parse(REVIEWED_PACK_SOURCE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# A filename is never an exemption
# ---------------------------------------------------------------------------
class TestGovernanceCannotBeEscapedByRenaming:
    def test_a_leading_underscore_does_not_hide_a_pack(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("_hidden_pack", VALID_SOURCE)
        result = _inspect(directory)
        assert [descriptor.module for descriptor in result.packs] == [module]
        assert result.ok is True

    def test_a_hidden_pack_is_held_to_the_whole_contract(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("_hidden_pack", _source(REASON_KEY='REASON_KEY = ""'))
        assert _codes(_inspect(directory)) == [(module, "REASON_KEY", "MISSING_REASON_KEY")]

    def test_a_hidden_pack_still_collides_with_a_visible_one(self, pack_dir) -> None:
        directory, write = pack_dir
        visible = write("visible_pack", VALID_SOURCE)
        hidden = write("_hidden_pack", VALID_SOURCE)
        assert _codes(_inspect(directory)) == sorted(
            [
                (hidden, "PACK_ID", "DUPLICATE_PACK_ID"),
                (visible, "PACK_ID", "DUPLICATE_PACK_ID"),
                (hidden, "REASON_KEY", "DUPLICATE_REASON_KEY"),
                (visible, "REASON_KEY", "DUPLICATE_REASON_KEY"),
            ]
        )

    def test_only_the_two_named_infrastructure_files_are_exempt(self) -> None:
        assert set(INFRASTRUCTURE_FILENAMES) == {"__init__.py", "inspection.py"}

    def test_an_infrastructure_name_in_a_pack_directory_is_still_skipped(self, pack_dir) -> None:
        directory, write = pack_dir
        write("__init__", VALID_SOURCE)
        write("inspection", VALID_SOURCE)
        real = write("real_pack", VALID_SOURCE)
        result = _inspect(directory)
        assert [descriptor.module for descriptor in result.packs] == [real]

    def test_a_file_without_the_marker_is_not_a_pack_and_not_an_error(self, pack_dir) -> None:
        directory, write = pack_dir
        write("helpers", "CONSTANT = 1\n\n\ndef helper():\n    return None\n")
        result = _inspect(directory)
        assert result.packs == ()
        assert result.errors == ()


# ---------------------------------------------------------------------------
# The inspector never executes a candidate
# ---------------------------------------------------------------------------
class TestCandidateSourceIsNeverExecuted:
    def test_a_top_level_file_write_does_not_happen(self, pack_dir, tmp_path: Path) -> None:
        directory, write = pack_dir
        sentinel = tmp_path / "sentinel.txt"
        module = write(
            "side_effect_pack",
            'PACK_ID = "for_you.skin_care.side_effect.v1"\n'
            'DOMAIN = "skin_care"\n'
            'CATEGORY = "skin_care"\n'
            'REASON_KEY = "for_you.skin_care.side_effect.reason"\n'
            "\n"
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('EXECUTED')\n"
            "\n"
            f"def {COMPILER_ATTRIBUTE}(entry):\n"
            "    return {}\n",
        )
        result = _inspect(directory)
        assert not sentinel.exists(), "the inspector executed candidate pack source"
        assert [descriptor.module for descriptor in result.packs] == [module]

    def test_a_top_level_network_call_does_not_happen(self, pack_dir, monkeypatch) -> None:
        directory, write = pack_dir
        module = write(
            "network_pack",
            'PACK_ID = "for_you.skin_care.network.v1"\n'
            'DOMAIN = "skin_care"\n'
            'CATEGORY = "skin_care"\n'
            'REASON_KEY = "for_you.skin_care.network.reason"\n'
            "\n"
            "import socket\n"
            "socket.create_connection(('198.51.100.1', 9), timeout=1)\n"
            "\n"
            f"def {COMPILER_ATTRIBUTE}(entry):\n"
            "    return {}\n",
        )

        def refuse(*args: object, **kwargs: object):
            raise AssertionError("the inspector opened a socket")

        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "socket", refuse)
        result = _inspect(directory)
        assert [descriptor.module for descriptor in result.packs] == [module]

    def test_a_top_level_raise_does_not_stop_the_inventory(self, pack_dir) -> None:
        directory, write = pack_dir
        exploding = write(
            "exploding_pack",
            'PACK_ID = "for_you.skin_care.exploding.v1"\n'
            'DOMAIN = "skin_care"\n'
            'CATEGORY = "skin_care"\n'
            'REASON_KEY = "for_you.skin_care.exploding.reason"\n'
            "\n"
            'raise RuntimeError("postgresql://someone:hunter2@db.internal:5432/prod")\n'
            "\n"
            f"def {COMPILER_ATTRIBUTE}(entry):\n"
            "    return {}\n",
        )
        healthy = write("healthy_pack", VALID_SOURCE)
        result = _inspect(directory)
        assert {descriptor.module for descriptor in result.packs} == {exploding, healthy}
        assert result.ok is True

    def test_no_candidate_module_ends_up_in_sys_modules(self, pack_dir) -> None:
        directory, write = pack_dir
        write("tracked_pack", VALID_SOURCE)
        before = set(sys.modules)
        _inspect(directory)
        assert {name for name in set(sys.modules) - before if "pack" in name.lower()} == set()
        assert f"{SYNTHETIC_PACKAGE}.tracked_pack" not in sys.modules

    def test_inspecting_the_repository_does_not_import_the_reviewed_pack(self) -> None:
        completed = _run_python(
            "import sys;"
            "from app.knowledge_packs.inspection import inspect_packs;"
            "result = inspect_packs();"
            "print(len(result.packs), result.ok,"
            f" {REVIEWED_MODULE!r} in sys.modules)"
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == f"{REVIEWED_PACK_COUNT} True False"

    def test_inspection_works_with_pack_imports_made_impossible(self) -> None:
        # The strongest available form of the claim. A meta-path finder in a
        # fresh interpreter raises if anything tries to import a pack module
        # or SQLAlchemy — which the reviewed pack reaches transitively. The
        # inventory still comes back complete and valid, so nothing was
        # loaded; had inspection still imported packs, this would explode
        # rather than merely report differently.
        completed = _run_python(
            "import sys\n"
            "class Forbid:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        blocked = name.split('.')[0] == 'sqlalchemy' or (\n"
            "            name.startswith('app.knowledge_packs.')\n"
            "            and name != 'app.knowledge_packs.inspection'\n"
            "        )\n"
            "        if blocked:\n"
            "            raise AssertionError('forbidden import: ' + name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Forbid())\n"
            "from app.knowledge_packs.inspection import inspect_packs\n"
            "result = inspect_packs()\n"
            "print(len(result.packs), result.ok)\n"
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == f"{REVIEWED_PACK_COUNT} True"

    def test_the_inspector_never_reaches_for_an_execution_primitive(self) -> None:
        for path in (INSPECTION_SOURCE, CLI_PATH):
            called = _called_names(path)
            for forbidden in (
                "eval",
                "exec",
                "compile",
                "import_module",
                "__import__",
                "exec_module",
                "module_from_spec",
                "run_path",
                "run_module",
                "spec_from_file_location",
            ):
                assert forbidden not in called, (path.name, forbidden)
        assert "importlib" not in _top_level_imports(INSPECTION_SOURCE)
        assert "runpy" not in _top_level_imports(INSPECTION_SOURCE)


# ---------------------------------------------------------------------------
# Static metadata: identity must be legible without running anything
# ---------------------------------------------------------------------------
class TestStaticMetadata:
    def test_a_well_formed_pack_passes(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("good", VALID_SOURCE)
        result = _inspect(directory)
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [module]

    def test_an_annotated_declaration_is_read_the_same_way(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "annotated", _source(PACK_ID='PACK_ID: str = "for_you.skin_care.synthetic.v1"')
        )
        (descriptor,) = _inspect(directory).packs
        assert descriptor.module == module
        assert descriptor.pack_id == "for_you.skin_care.synthetic.v1"

    @pytest.mark.parametrize(
        "declaration",
        [
            "PACK_ID = make_pack_id()",
            "PACK_ID = _PREFIX + '.v1'",
            "PACK_ID = f'for_you.skin_care.{NAME}.v1'",
            "PACK_ID = PACK_IDS[0]",
            "PACK_ID = None",
            "PACK_ID = 42",
            "PACK_ID = ('for_you', 'skin_care')",
            "PACK_ID = 'for_you' if TOGGLE else 'other'",
            "PACK_ID: str",
            'PACK_ID, other = ("for_you.skin_care.synthetic.v1", 1)',
            '[PACK_ID] = ["for_you.skin_care.synthetic.v1"]',
            'PACK_ID, *rest = ("for_you.skin_care.synthetic.v1",)',
            '(PACK_ID := "for_you.skin_care.synthetic.v1")',
            'from somewhere import PACK_ID',
            'for PACK_ID in ["for_you.skin_care.synthetic.v1"]:\n    pass',
            'import contextlib\nwith contextlib.nullcontext("x") as PACK_ID:\n    pass',
            'try:\n    pass\nexcept ValueError as PACK_ID:\n    pass',
            'if TOGGLE:\n    PACK_ID = "for_you.skin_care.synthetic.v1"',
        ],
    )
    def test_a_pack_id_that_must_be_computed_fails_closed(
        self, pack_dir, declaration: str
    ) -> None:
        # A bare annotation is on this list on purpose. It binds nothing at
        # runtime, so it could be argued the file never claims pack status —
        # but "someone wrote PACK_ID at module scope and gave it no value" is
        # exactly the case that must not slip out of the inventory in silence.
        directory, write = pack_dir
        module = write("dynamic", _source(PACK_ID=declaration))
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "PACK_ID", "NON_STATIC_METADATA")]

    def test_a_chained_assignment_binds_both_names_and_neither_is_approved(
        self, pack_dir
    ) -> None:
        # `PACK_ID = DOMAIN = "..."` is statically readable, but it is not one
        # of the two reviewed forms, and it quietly binds a second governed
        # name — which here collides with that name's own declaration. Both
        # facts are reported.
        directory, write = pack_dir
        module = write(
            "chained", _source(PACK_ID='PACK_ID = DOMAIN = "for_you.skin_care.synthetic.v1"')
        )
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == sorted(
            [
                (module, "PACK_ID", "NON_STATIC_METADATA"),
                (module, "DOMAIN", "DUPLICATE_DECLARATION"),
            ]
        )

    @pytest.mark.parametrize("field", ["DOMAIN", "CATEGORY", "REASON_KEY"])
    def test_any_computed_descriptor_field_fails_closed(self, pack_dir, field: str) -> None:
        directory, write = pack_dir
        module = write("dynamic_field", _source(**{field: f"{field} = compute()"}))
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, field, "NON_STATIC_METADATA")]

    @pytest.mark.parametrize(
        "value",
        [
            '""',
            '"   "',
            '" for_you.skin_care.synthetic.v1"',
            '"for_you.skin_care.synthetic.v1 "',
            '"petrolatum"',
            '"for_you.v1"',
            '"For_You.Skin_Care.Synthetic.v1"',
            '"for_you.skin care.synthetic.v1"',
            '"for_you.skin_care.synthetic.version1"',
            '"for_you.skin_care.synthetic.v"',
            '"for_you.skin_care..v1"',
            '"for_you.skin_care.synthetic.1"',
        ],
    )
    def test_an_unusable_pack_id_is_reported(self, pack_dir, value: str) -> None:
        directory, write = pack_dir
        module = write("badid", _source(PACK_ID=f"PACK_ID = {value}"))
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "PACK_ID", "INVALID_PACK_ID")]

    @pytest.mark.parametrize("value", ['""', '"  "', '" skin_care"', '"skin_care "'])
    @pytest.mark.parametrize(
        ("field", "code"),
        [
            ("DOMAIN", "MISSING_DOMAIN"),
            ("CATEGORY", "MISSING_CATEGORY"),
            ("REASON_KEY", "MISSING_REASON_KEY"),
        ],
    )
    def test_an_unusable_identity_field_is_reported(
        self, pack_dir, field: str, code: str, value: str
    ) -> None:
        directory, write = pack_dir
        module = write("badfield", _source(**{field: f"{field} = {value}"}))
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, field, code)]

    @pytest.mark.parametrize(
        ("field", "code"),
        [
            ("DOMAIN", "MISSING_DOMAIN"),
            ("CATEGORY", "MISSING_CATEGORY"),
            ("REASON_KEY", "MISSING_REASON_KEY"),
        ],
    )
    def test_an_absent_identity_field_is_reported(self, pack_dir, field: str, code: str) -> None:
        directory, write = pack_dir
        module = write("absent", _source(**{field: None}))
        assert _codes(_inspect(directory)) == [(module, field, code)]

    def test_a_field_declared_twice_is_ambiguous_rather_than_last_wins(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "twice",
            _source(
                DOMAIN='DOMAIN = "skin_care"\nDOMAIN = "hair_care"',
            ),
        )
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "DOMAIN", "DUPLICATE_DECLARATION")]

    def test_a_declaration_inside_a_conditional_is_seen_and_rejected(self, pack_dir) -> None:
        # It really does bind DOMAIN when the branch runs, so the collector
        # must see it — reporting it as simply absent would be a different,
        # wronger story. It runs conditionally, so it is not a declaration.
        directory, write = pack_dir
        module = write(
            "conditional",
            _source(DOMAIN='if TOGGLE:\n    DOMAIN = "skin_care"'),
        )
        assert _codes(_inspect(directory)) == [(module, "DOMAIN", "NON_STATIC_METADATA")]

    def test_every_violation_in_one_file_is_reported_not_just_the_first(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "allwrong",
            _source(
                PACK_ID='PACK_ID = ""',
                DOMAIN='DOMAIN = ""',
                CATEGORY="CATEGORY = compute()",
                REASON_KEY=None,
                **{COMPILER_ATTRIBUTE: None},
            ),
        )
        assert _codes(_inspect(directory)) == sorted(
            [
                (module, "PACK_ID", "INVALID_PACK_ID"),
                (module, "DOMAIN", "MISSING_DOMAIN"),
                (module, "CATEGORY", "NON_STATIC_METADATA"),
                (module, "REASON_KEY", "MISSING_REASON_KEY"),
                (module, COMPILER_ATTRIBUTE, "MISSING_COMPILER"),
            ]
        )

    def test_one_broken_pack_does_not_hide_the_others(self, pack_dir) -> None:
        directory, write = pack_dir
        good = write("fine", VALID_SOURCE)
        broken = write(
            "broken",
            _source(
                PACK_ID='PACK_ID = "nope"',
                REASON_KEY='REASON_KEY = "for_you.other.reason"',
            ),
        )
        result = _inspect(directory)
        assert [descriptor.module for descriptor in result.packs] == [good]
        assert _codes(result) == [(broken, "PACK_ID", "INVALID_PACK_ID")]

    def test_unparsable_source_is_a_finding_with_no_source_in_it(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "broken_syntax",
            'PACK_ID = "for_you.skin_care.synthetic.v1"\n'
            'SECRET = "postgresql://someone:hunter2@db.internal/prod"\n'
            "def (:\n",
        )
        healthy = write("healthy", VALID_SOURCE)
        result = _inspect(directory)
        assert [descriptor.module for descriptor in result.packs] == [healthy]
        assert _codes(result) == [(module, "source", "UNPARSABLE_SOURCE")]
        encoded = json.dumps(as_json_payload(result))
        for leaked in ("hunter2", "postgresql://", "db.internal", "SyntaxError", "def (:"):
            assert leaked not in encoded

    def test_source_that_cannot_be_decoded_is_a_finding(self, pack_dir) -> None:
        directory, _ = pack_dir
        (directory / "binary_pack.py").write_bytes(b'PACK_ID = "\xff\xfe not utf-8"\n')
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [
            (f"{SYNTHETIC_PACKAGE}.binary_pack", "source", "UNREADABLE_SOURCE")
        ]


# ---------------------------------------------------------------------------
# Module-scope binding: every attempt is seen, only two forms are accepted
# ---------------------------------------------------------------------------
class TestModuleScopeBinding:
    """Layer one must be complete, or layer two is checking the wrong file.

    The failure this guards against is not a wrong answer but no answer: a
    governed name bound in a form the collector does not recognise makes the
    pack vanish from the inventory, and a pack nobody can see is a pack nobody
    reviews.
    """

    def test_a_destructured_pack_id_does_not_escape_governance(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "tuple_marker",
            'PACK_ID, other = ("for_you.skin_care.escape.v1", 1)\n'
            'DOMAIN = "skin_care"\n'
            'CATEGORY = "skin_care"\n'
            'REASON_KEY = "for_you.skin_care.escape.reason"\n'
            "\n"
            f"def {COMPILER_ATTRIBUTE}(entry):\n"
            "    return {}\n",
        )
        result = _inspect(directory)
        assert result.ok is False, "a destructured PACK_ID left the inventory silently"
        assert result.packs == ()
        assert _codes(result) == [(module, "PACK_ID", "NON_STATIC_METADATA")]

    def test_a_pack_id_bound_only_by_a_loop_still_claims_pack_status(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "loop_marker",
            'for PACK_ID in ["for_you.skin_care.escape.v1"]:\n    pass\n',
        )
        result = _inspect(directory)
        assert result.ok is False
        assert (module, "PACK_ID", "NON_STATIC_METADATA") in _codes(result)
        # and the rest of the contract is reported as missing, not skipped
        assert {code for _, _, code in _codes(result)} == {
            "NON_STATIC_METADATA",
            "MISSING_DOMAIN",
            "MISSING_CATEGORY",
            "MISSING_REASON_KEY",
            "MISSING_COMPILER",
        }

    @pytest.mark.parametrize("field", ["DOMAIN", "CATEGORY", "REASON_KEY"])
    @pytest.mark.parametrize(
        "template",
        [
            '{field}, other = ("skin_care", 1)',
            "{field} += 'x'",
            '({field} := "skin_care")',
            'for {field} in ["skin_care"]:\n    pass',
            'import contextlib\nwith contextlib.nullcontext("x") as {field}:\n    pass',
            "from somewhere import {field}",
            "del {field}",
        ],
    )
    def test_an_unsupported_binding_of_any_descriptor_fails_closed(
        self, pack_dir, field: str, template: str
    ) -> None:
        directory, write = pack_dir
        module = write("descriptor_binding", _source(**{field: template.format(field=field)}))
        result = _inspect(directory)
        assert result.packs == ()
        assert [module for module, _, _ in _codes(result)] == [module]
        assert {code for _, _, code in _codes(result)} <= {
            "NON_STATIC_METADATA",
            "DUPLICATE_DECLARATION",
        }

    def test_a_rebinding_after_a_good_declaration_is_reported(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("rebound", VALID_SOURCE + '\nPACK_ID += ".v2"\n')
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "PACK_ID", "DUPLICATE_DECLARATION")]

    def test_deleting_a_descriptor_after_declaring_it_is_reported(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("deleted", VALID_SOURCE + "\ndel PACK_ID\n")
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "PACK_ID", "DUPLICATE_DECLARATION")]

    def test_the_reviewed_literal_forms_still_pass(self, pack_dir) -> None:
        directory, write = pack_dir
        plain = write("plain", VALID_SOURCE)
        annotated = write(
            "annotated_forms",
            _source(
                PACK_ID='PACK_ID: str = "for_you.skin_care.annotated.v1"',
                DOMAIN='DOMAIN: str = "skin_care"',
                REASON_KEY='REASON_KEY: str = "for_you.skin_care.annotated.reason"',
            ),
        )
        result = _inspect(directory)
        assert result.ok is True
        assert {descriptor.module for descriptor in result.packs} == {plain, annotated}


# ---------------------------------------------------------------------------
# Definition time is enclosing scope
# ---------------------------------------------------------------------------
class TestEnclosingScopeBinding:
    """A ``def`` is a statement before it is a scope.

    Its body belongs to a new scope; its decorators, defaults and annotations
    do not — they are evaluated where the ``def`` sits, when it executes. Same
    for a lambda's defaults and a class's bases. A walrus in any of them binds
    a module name, and a collector that stopped at the ``def`` keyword would
    never see it. These are the cases a hand-picked list of "expression-shaped
    children" misses, which is why the walker is generic and prunes only at
    real scope boundaries.
    """

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "function default",
                "def helper(\n"
                '    value=(PACK_ID := "for_you.skin_care.default_escape.v1")\n'
                "):\n"
                "    pass\n",
            ),
            (
                "keyword-only default",
                'def helper(*, value=(PACK_ID := "for_you.skin_care.kwonly.v1")):\n'
                "    pass\n",
            ),
            (
                "async function default",
                'async def helper(value=(PACK_ID := "for_you.skin_care.async.v1")):\n'
                "    pass\n",
            ),
            (
                "function decorator",
                "decorator = None\n@((PACK_ID := decorator))\ndef helper():\n    pass\n",
            ),
            ("argument annotation", "def helper(value: (PACK_ID := str)):\n    pass\n"),
            ("return annotation", "def helper() -> (PACK_ID := str):\n    pass\n"),
            ("class base", "class Helper((PACK_ID := object)):\n    pass\n"),
            (
                "class decorator",
                "decorator = None\n@((PACK_ID := decorator))\nclass Helper:\n    pass\n",
            ),
            ("class keyword", "class Helper(metaclass=(PACK_ID := type)):\n    pass\n"),
            (
                "lambda default",
                'helper = lambda value=(PACK_ID := "for_you.skin_care.lambda.v1"): value\n',
            ),
            (
                "keyword argument wrapper",
                "def some_call(**kw):\n"
                "    return kw\n"
                "\n"
                "\n"
                'some_call(value=(PACK_ID := "for_you.skin_care.keyword.v1"))\n',
            ),
            (
                "match guard",
                "value = 1\n"
                "match value:\n"
                '    case _ if (PACK_ID := "for_you.skin_care.match.v1"):\n'
                "        pass\n",
            ),
            (
                "comprehension filter",
                "values = [1]\n"
                "[\n"
                "    item\n"
                "    for item in values\n"
                '    if (PACK_ID := "for_you.skin_care.comprehension.v1")\n'
                "]\n",
            ),
            (
                "with-item expression",
                "import contextlib\n"
                'with contextlib.nullcontext((PACK_ID := "for_you.skin_care.with.v1")):\n'
                "    pass\n",
            ),
            (
                "except-handler type",
                "try:\n    pass\nexcept (PACK_ID := ValueError):\n    pass\n",
            ),
            (
                "buried inside a default",
                "def helper(value=[{'k': (PACK_ID := 'for_you.skin_care.deep.v1')}]):\n"
                "    pass\n",
            ),
        ],
    )
    def test_a_walrus_evaluated_in_module_scope_is_seen(
        self, pack_dir, label: str, source: str
    ) -> None:
        directory, write = pack_dir
        module = write("enclosing", source)
        result = _inspect(directory)
        assert result.packs == (), label
        assert (module, "PACK_ID", "NON_STATIC_METADATA") in _codes(result), label

    @pytest.mark.parametrize(
        ("label", "rebinding"),
        [
            (
                "function default",
                f"def helper(value=({COMPILER_ATTRIBUTE} := None)):\n    pass",
            ),
            (
                "function decorator",
                f"@(({COMPILER_ATTRIBUTE} := None))\ndef helper():\n    pass",
            ),
            ("class base", f"class Helper(({COMPILER_ATTRIBUTE} := object)):\n    pass"),
            (
                "lambda default",
                f"maker = lambda value=({COMPILER_ATTRIBUTE} := None): value",
            ),
            (
                "keyword wrapper",
                "def some_call(**kw):\n"
                "    return kw\n"
                "\n"
                "\n"
                f"some_call(value=({COMPILER_ATTRIBUTE} := None))",
            ),
        ],
    )
    def test_a_definition_time_walrus_invalidates_the_compiler(
        self, pack_dir, label: str, rebinding: str
    ) -> None:
        # The stale earlier ``def`` must not survive a rebinding hidden in
        # something that executes at definition time.
        directory, write = pack_dir
        module = write("enclosing_compiler", VALID_SOURCE + "\n" + rebinding + "\n")
        result = _inspect(directory)
        assert result.packs == (), label
        assert _codes(result) == sorted(
            [
                (module, COMPILER_ATTRIBUTE, "DUPLICATE_DECLARATION"),
                (module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION"),
            ]
        ), label

    def test_the_semantic_wrappers_are_traversed_not_dead_ends(self) -> None:
        # A regression guard on the shape of the walker rather than on one
        # syntax. If it ever goes back to following only ast.expr children,
        # every wrapper below becomes a hiding place at once.
        source = (
            "def some_call(**kw):\n"
            "    return kw\n"
            "\n"
            "\n"
            "values = [1]\n"
            "some_call(a=[item for item in values if (FOUND_ONE := item)])\n"
        )
        statement = ast.parse(source).body[-1]
        reached = {
            type(node).__name__ for node in inspection._enclosing_scope_nodes(statement)
        }
        assert {"keyword", "comprehension", "NamedExpr"} <= reached

    def test_a_definition_time_walrus_does_not_disturb_a_valid_pack(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "valid_with_definition_time_helpers",
            VALID_SOURCE
            + "\n"
            "def helper(value=1, *, other=2):\n"
            '    PACK_ID = "local"\n'
            "    return PACK_ID, value, other\n"
            "\n"
            "\n"
            "class Holder(object):\n"
            '    DOMAIN = "class-local"\n'
            "\n"
            "\n"
            'maker = lambda: (CATEGORY := "lambda-local")\n',
        )
        result = _inspect(directory)
        assert result.ok is True
        (descriptor,) = result.packs
        assert descriptor.module == module
        assert descriptor.domain == "skin_care"
        assert descriptor.category == "skin_care"


# ---------------------------------------------------------------------------
# Nested scopes are not the module
# ---------------------------------------------------------------------------
class TestNestedScopesAreNotModuleScope:
    def test_a_pack_id_local_to_a_function_does_not_make_a_pack(self, pack_dir) -> None:
        directory, write = pack_dir
        write(
            "helper_only",
            "def helper():\n"
            '    PACK_ID = "not-a-pack"\n'
            "    return PACK_ID\n",
        )
        result = _inspect(directory)
        assert result.packs == ()
        assert result.errors == ()

    @pytest.mark.parametrize(
        "source",
        [
            'async def helper():\n    PACK_ID = "not-a-pack"\n',
            'class Holder:\n    PACK_ID = "not-a-pack"\n',
            'make = lambda: (PACK_ID := "not-a-pack")\n',
            'def helper():\n    for PACK_ID in []:\n        pass\n',
            'def helper():\n    del PACK_ID\n',
            'def helper():\n    from somewhere import PACK_ID\n',
            'def outer():\n    @((PACK_ID := None))\n    def inner():\n        pass\n',
            'def outer():\n    def inner(value=(PACK_ID := 1)):\n        pass\n',
            'class Holder:\n    maker = lambda: (PACK_ID := "class-lambda-local")\n',
        ],
    )
    def test_no_nested_scope_binding_claims_pack_status(self, pack_dir, source: str) -> None:
        directory, write = pack_dir
        write("nested", source)
        result = _inspect(directory)
        assert result.packs == ()
        assert result.errors == ()

    def test_a_nested_compiler_name_does_not_satisfy_the_contract(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "nested_compiler",
            _source(
                **{
                    COMPILER_ATTRIBUTE: (
                        "class Holder:\n"
                        f"    def {COMPILER_ATTRIBUTE}(self, entry):\n"
                        "        return {}"
                    )
                }
            ),
        )
        assert _codes(_inspect(directory)) == [
            (module, COMPILER_ATTRIBUTE, "MISSING_COMPILER")
        ]

    def test_a_nested_scope_does_not_disturb_a_valid_pack(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "valid_with_helpers",
            VALID_SOURCE
            + "\n"
            "def helper():\n"
            '    PACK_ID = "not-a-pack"\n'
            f"    def {COMPILER_ATTRIBUTE}(entry):\n"
            "        return None\n"
            "    return PACK_ID\n"
            "\n"
            "\n"
            "class Holder:\n"
            '    DOMAIN = "not-the-domain"\n',
        )
        result = _inspect(directory)
        assert result.ok is True
        (descriptor,) = result.packs
        assert descriptor.module == module
        assert descriptor.domain == "skin_care"


# ---------------------------------------------------------------------------
# `global` is the one way a nested body reaches module state
# ---------------------------------------------------------------------------
class TestGovernedNamesMayNotBeDeclaredGlobal:
    """A class body is not module scope — until ``global`` says it is.

    ``class Holder: PACK_ID = "x"`` binds a class attribute and the collector
    is right to prune it. Add one ``global PACK_ID`` line above it and the
    same assignment writes to the module namespace instead, which the pruning
    hides completely.

    The rule adopted is blunter than Python's semantics on purpose: a governed
    name in *any* ``global`` statement, anywhere, is a finding — even in a
    function nobody calls or a branch nobody takes. Deciding case by case
    would mean modelling when each enclosing block runs, which is a small
    interpreter and a new place for holes. A specification file has no
    legitimate reason to redirect these five identities.
    """

    def test_an_ordinary_class_attribute_is_still_not_a_module_binding(
        self, pack_dir
    ) -> None:
        # The control this whole rule has to leave standing.
        directory, write = pack_dir
        write("class_local", 'class Holder:\n    PACK_ID = "class-local"\n')
        result = _inspect(directory)
        assert result.packs == ()
        assert result.errors == ()

    def test_a_class_body_global_cannot_smuggle_a_pack_id_in(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "class_global",
            "class Holder:\n"
            "    global PACK_ID\n"
            '    PACK_ID = "for_you.skin_care.escape.v1"\n',
        )
        result = _inspect(directory)
        assert result.ok is False, "a class-body global escaped governance"
        assert result.packs == ()
        assert (module, "PACK_ID", "NON_STATIC_METADATA") in _codes(result)

    def test_a_nested_class_global_is_caught_too(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "nested_class_global",
            "class Outer:\n"
            "    class Inner:\n"
            "        global PACK_ID\n"
            '        PACK_ID = "for_you.skin_care.escape.v1"\n',
        )
        result = _inspect(directory)
        assert result.ok is False
        assert (module, "PACK_ID", "NON_STATIC_METADATA") in _codes(result)

    def test_a_class_global_invalidates_a_descriptor_that_was_declared_properly(
        self, pack_dir
    ) -> None:
        directory, write = pack_dir
        module = write(
            "class_global_domain",
            VALID_SOURCE + '\nclass Holder:\n    global DOMAIN\n    DOMAIN = "other"\n',
        )
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "DOMAIN", "DUPLICATE_DECLARATION")]

    def test_a_class_global_invalidates_the_compiler(self, pack_dir) -> None:
        # The sharpest form: after the class statement runs, the name is None,
        # and the stale top-level ``def`` must not be reported as valid.
        directory, write = pack_dir
        module = write(
            "class_global_compiler",
            VALID_SOURCE
            + "\nclass Holder:\n"
            f"    global {COMPILER_ATTRIBUTE}\n"
            f"    {COMPILER_ATTRIBUTE} = None\n",
        )
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == sorted(
            [
                (module, COMPILER_ATTRIBUTE, "DUPLICATE_DECLARATION"),
                (module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION"),
            ]
        )

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("bare function global", "def helper():\n    global PACK_ID\n"),
            (
                "function global with assignment",
                "def helper():\n"
                "    global PACK_ID\n"
                '    PACK_ID = "for_you.skin_care.escape.v1"\n',
            ),
            (
                "async function global",
                "async def helper():\n    global PACK_ID\n",
            ),
            (
                "global in a branch nobody takes",
                "if False:\n    def helper():\n        global PACK_ID\n",
            ),
            (
                "global inside a method",
                "class Holder:\n    def method(self):\n        global PACK_ID\n",
            ),
            (
                "global naming several names at once",
                "def helper():\n    global other, PACK_ID, another\n",
            ),
        ],
    )
    def test_a_global_declaration_anywhere_claims_pack_status_and_fails_closed(
        self, pack_dir, label: str, source: str
    ) -> None:
        # Deliberately stricter than runtime-at-import semantics: none of
        # these mutate the module until something calls them, and they are
        # rejected anyway.
        directory, write = pack_dir
        module = write("function_global", source)
        result = _inspect(directory)
        assert result.ok is False, label
        assert result.packs == (), label
        assert (module, "PACK_ID", "NON_STATIC_METADATA") in _codes(result), label

    def test_a_global_after_a_good_declaration_is_a_duplicate(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "valid_then_global",
            VALID_SOURCE + "\ndef helper():\n    global PACK_ID\n",
        )
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, "PACK_ID", "DUPLICATE_DECLARATION")]

    def test_a_global_naming_something_else_is_not_our_business(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "unrelated_global",
            VALID_SOURCE
            + "\n_counter = 0\n"
            "\n\ndef helper():\n"
            "    global _counter\n"
            "    _counter += 1\n",
        )
        result = _inspect(directory)
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [module]

    def test_nonlocal_is_not_covered_because_it_cannot_reach_the_module(
        self, pack_dir
    ) -> None:
        # `nonlocal` binds in an enclosing *function* scope. It can never
        # write module state, so it is not a governed-name escape and the
        # pack stays valid.
        directory, write = pack_dir
        module = write(
            "nonlocal_pack",
            VALID_SOURCE
            + "\ndef outer():\n"
            "    PACK_ID = 1\n"
            "\n"
            "    def inner():\n"
            "        nonlocal PACK_ID\n"
            "        PACK_ID = 2\n"
            "\n"
            "    return inner\n",
        )
        result = _inspect(directory)
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [module]

    def test_the_governed_name_set_is_exactly_the_five(self) -> None:
        assert set(inspection.GOVERNED_NAMES) == {
            "PACK_ID",
            "DOMAIN",
            "CATEGORY",
            "REASON_KEY",
            COMPILER_ATTRIBUTE,
        }

    def test_the_detector_reads_the_tree_not_the_text(self) -> None:
        # The reviewed pack has the word "global" in a comment. A text scan
        # would flag it; an AST walk cannot.
        source = REVIEWED_PACK_SOURCE.read_text(encoding="utf-8")
        assert "global" in source
        assert inspection._governed_global_names(ast.parse(source)) == []


# ---------------------------------------------------------------------------
# The compiler, verified structurally and never called
# ---------------------------------------------------------------------------
class TestCompilerDeclaration:
    def test_the_reviewed_pack_declares_the_compiler_as_a_top_level_function(self) -> None:
        tree = ast.parse(REVIEWED_PACK_SOURCE.read_text(encoding="utf-8"))
        declarations = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == COMPILER_ATTRIBUTE
        ]
        assert len(declarations) == 1

    def test_a_missing_compiler_is_reported(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("nocompiler", _source(**{COMPILER_ATTRIBUTE: None}))
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, COMPILER_ATTRIBUTE, "MISSING_COMPILER")]

    @pytest.mark.parametrize(
        "declaration",
        [
            f"{COMPILER_ATTRIBUTE} = 'build it yourself'",
            f"{COMPILER_ATTRIBUTE} = None",
            f"{COMPILER_ATTRIBUTE} = some_other_function",
            f"class {COMPILER_ATTRIBUTE}:\n    pass",
            f"async def {COMPILER_ATTRIBUTE}(entry):\n    return {{}}",
            f"from elsewhere import {COMPILER_ATTRIBUTE}",
        ],
    )
    def test_a_compiler_that_is_not_a_plain_function_is_reported(
        self, pack_dir, declaration: str
    ) -> None:
        directory, write = pack_dir
        module = write("badcompiler", _source(**{COMPILER_ATTRIBUTE: declaration}))
        result = _inspect(directory)
        assert result.packs == ()
        assert _codes(result) == [(module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION")]

    def test_a_function_shadowed_by_a_later_assignment_is_reported(self, pack_dir) -> None:
        # Last binding wins at import time, so the release workflow would
        # reach the string, not the function.
        directory, write = pack_dir
        module = write(
            "shadowed",
            _source(
                **{
                    COMPILER_ATTRIBUTE: (
                        f"def {COMPILER_ATTRIBUTE}(entry):\n"
                        "    return {}\n"
                        "\n"
                        f"{COMPILER_ATTRIBUTE} = 'not a function'"
                    )
                }
            ),
        )
        assert _codes(_inspect(directory)) == sorted(
            [
                (module, COMPILER_ATTRIBUTE, "DUPLICATE_DECLARATION"),
                (module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION"),
            ]
        )

    def test_two_function_declarations_are_ambiguous(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "twice_defined",
            _source(
                **{
                    COMPILER_ATTRIBUTE: (
                        f"def {COMPILER_ATTRIBUTE}(entry):\n"
                        "    return {}\n"
                        "\n"
                        f"def {COMPILER_ATTRIBUTE}(entry):\n"
                        "    return {'other': True}"
                    )
                }
            ),
        )
        assert _codes(_inspect(directory)) == [
            (module, COMPILER_ATTRIBUTE, "DUPLICATE_DECLARATION")
        ]

    @pytest.mark.parametrize(
        ("label", "rebinding"),
        [
            ("tuple shadow", f"({COMPILER_ATTRIBUTE}, other) = (None, 1)"),
            ("list shadow", f"[{COMPILER_ATTRIBUTE}] = [None]"),
            ("starred shadow", f"{COMPILER_ATTRIBUTE}, *rest = (None,)"),
            ("chained shadow", f"{COMPILER_ATTRIBUTE} = other = None"),
            ("deletion", f"del {COMPILER_ATTRIBUTE}"),
            ("walrus", f"({COMPILER_ATTRIBUTE} := None)"),
            ("loop target", f"for {COMPILER_ATTRIBUTE} in []:\n    pass"),
            (
                "conditional shadow",
                f"if TOGGLE:\n    {COMPILER_ATTRIBUTE} = None",
            ),
            (
                "with target",
                "import contextlib\n"
                f"with contextlib.nullcontext(None) as {COMPILER_ATTRIBUTE}:\n    pass",
            ),
            ("import shadow", f"from elsewhere import {COMPILER_ATTRIBUTE}"),
        ],
    )
    def test_a_later_module_scope_rebinding_invalidates_the_declaration(
        self, pack_dir, label: str, rebinding: str
    ) -> None:
        # The stale earlier ``def`` must not be reported as the valid
        # compiler: at import time the last binding wins, and the release
        # workflow would reach whatever that is.
        directory, write = pack_dir
        module = write("rebound_compiler", VALID_SOURCE + "\n" + rebinding + "\n")
        result = _inspect(directory)
        assert result.packs == (), label
        assert _codes(result) == sorted(
            [
                (module, COMPILER_ATTRIBUTE, "DUPLICATE_DECLARATION"),
                (module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION"),
            ]
        ), label

    def test_a_compiler_bound_only_by_a_loop_is_not_a_function(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "loop_compiler",
            _source(**{COMPILER_ATTRIBUTE: f"for {COMPILER_ATTRIBUTE} in []:\n    pass"}),
        )
        assert _codes(_inspect(directory)) == [
            (module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION")
        ]

    def test_a_conditional_definition_is_not_a_plain_declaration(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "conditional_compiler",
            _source(
                **{
                    COMPILER_ATTRIBUTE: (
                        "if TOGGLE:\n"
                        f"    def {COMPILER_ATTRIBUTE}(entry):\n"
                        "        return {}"
                    )
                }
            ),
        )
        assert _codes(_inspect(directory)) == [
            (module, COMPILER_ATTRIBUTE, "COMPILER_NOT_A_FUNCTION")
        ]

    def test_a_compiler_that_would_explode_is_never_called(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write(
            "exploding_compiler",
            _source(
                **{
                    COMPILER_ATTRIBUTE: (
                        f"def {COMPILER_ATTRIBUTE}(entry):\n"
                        "    raise AssertionError('the inspector called the compiler')"
                    )
                }
            ),
        )
        result = _inspect(directory)
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [module]


# ---------------------------------------------------------------------------
# Uniqueness across packs
# ---------------------------------------------------------------------------
class TestCrossPackUniqueness:
    def test_two_packs_sharing_a_pack_id_are_both_named(self, pack_dir) -> None:
        directory, write = pack_dir
        first = write("dup_a", _source(REASON_KEY='REASON_KEY = "for_you.skin_care.a.reason"'))
        second = write("dup_b", _source(REASON_KEY='REASON_KEY = "for_you.skin_care.b.reason"'))
        result = _inspect(directory)
        assert result.ok is False
        assert _codes(result) == sorted(
            [
                (first, "PACK_ID", "DUPLICATE_PACK_ID"),
                (second, "PACK_ID", "DUPLICATE_PACK_ID"),
            ]
        )

    def test_two_packs_sharing_a_reason_key_are_both_named(self, pack_dir) -> None:
        directory, write = pack_dir
        first = write("rk_a", _source(PACK_ID='PACK_ID = "for_you.skin_care.a.v1"'))
        second = write("rk_b", _source(PACK_ID='PACK_ID = "for_you.skin_care.b.v1"'))
        result = _inspect(directory)
        assert result.ok is False
        assert _codes(result) == sorted(
            [
                (first, "REASON_KEY", "DUPLICATE_REASON_KEY"),
                (second, "REASON_KEY", "DUPLICATE_REASON_KEY"),
            ]
        )

    def test_three_way_duplication_names_all_three(self, pack_dir) -> None:
        directory, write = pack_dir
        modules = [
            write(
                f"triple_{index}",
                _source(REASON_KEY=f'REASON_KEY = "for_you.skin_care.{index}.reason"'),
            )
            for index in range(3)
        ]
        assert _codes(_inspect(directory)) == sorted(
            (module, "PACK_ID", "DUPLICATE_PACK_ID") for module in modules
        )

    def test_distinct_packs_do_not_collide(self, pack_dir) -> None:
        directory, write = pack_dir
        first = write(
            "sep_a",
            _source(
                PACK_ID='PACK_ID = "for_you.skin_care.a.v1"',
                REASON_KEY='REASON_KEY = "for_you.skin_care.a.reason"',
            ),
        )
        second = write(
            "sep_b",
            _source(
                PACK_ID='PACK_ID = "for_you.hair_care.b.v2"',
                DOMAIN='DOMAIN = "hair_care"',
                CATEGORY='CATEGORY = "hair_care"',
                REASON_KEY='REASON_KEY = "for_you.hair_care.b.reason"',
            ),
        )
        result = _inspect(directory)
        assert result.ok is True
        assert [descriptor.module for descriptor in result.packs] == [second, first]

    def test_a_broken_pack_is_excluded_from_uniqueness_checking(self, pack_dir) -> None:
        # Both declare the same reason key, but one has no usable domain, so
        # it never becomes a descriptor. Reporting a duplicate against a pack
        # that failed its own contract is noise on top of the real finding.
        directory, write = pack_dir
        write("uniq_good", _source(PACK_ID='PACK_ID = "for_you.skin_care.a.v1"'))
        broken = write(
            "uniq_broken",
            _source(PACK_ID='PACK_ID = "for_you.skin_care.b.v1"', DOMAIN='DOMAIN = ""'),
        )
        assert _codes(_inspect(directory)) == [(broken, "DOMAIN", "MISSING_DOMAIN")]

    def test_a_version_bump_is_not_a_duplicate(self, pack_dir) -> None:
        directory, write = pack_dir
        write(
            "v1",
            _source(
                PACK_ID='PACK_ID = "for_you.skin_care.thing.v1"',
                REASON_KEY='REASON_KEY = "for_you.skin_care.thing.one"',
            ),
        )
        write(
            "v2",
            _source(
                PACK_ID='PACK_ID = "for_you.skin_care.thing.v2"',
                REASON_KEY='REASON_KEY = "for_you.skin_care.thing.two"',
            ),
        )
        assert _inspect(directory).ok is True


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_file_order_does_not_change_output_order(self, pack_dir) -> None:
        directory, write = pack_dir
        for index in (3, 0, 4, 1, 2):
            write(
                f"order_{index}",
                _source(
                    PACK_ID=f'PACK_ID = "for_you.skin_care.p{index}.v1"',
                    REASON_KEY=f'REASON_KEY = "for_you.skin_care.p{index}.reason"',
                ),
            )
        result = _inspect(directory)
        assert [descriptor.pack_id for descriptor in result.packs] == sorted(
            descriptor.pack_id for descriptor in result.packs
        )
        assert result == _inspect(directory)

    def test_error_order_is_stable(self, pack_dir) -> None:
        directory, write = pack_dir
        for index in range(4):
            write(f"err_{index}", _source(PACK_ID='PACK_ID = "bad"', DOMAIN='DOMAIN = ""'))
        assert _inspect(directory).errors == _inspect(directory).errors

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
        assert payload["pack_count"] == REVIEWED_PACK_COUNT
        assert payload["errors"] == []
        assert len(payload["packs"]) == REVIEWED_PACK_COUNT
        for entry in payload["packs"]:
            assert set(entry) == {
                "module",
                "pack_id",
                "domain",
                "category",
                "reason_key",
                "compiler",
            }

    def test_a_failing_inventory_says_invalid(self, pack_dir) -> None:
        directory, write = pack_dir
        module = write("badjson", _source(PACK_ID='PACK_ID = "bad"'))
        payload = as_json_payload(_inspect(directory))
        assert payload["status"] == "invalid"
        assert payload["pack_count"] == 0
        assert payload["errors"] == [
            {"module": module, "field": "PACK_ID", "code": "INVALID_PACK_ID"}
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

    def test_every_finding_code_is_a_plain_uppercase_word(self) -> None:
        for code in PackValidationCode:
            assert str(code) == code.name
            assert code.name.replace("_", "").isalpha()


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
            "import sys, app.knowledge_packs;"
            "print(sorted(m for m in sys.modules if m.startswith('app.knowledge_packs')))"
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "['app.knowledge_packs']"

    def test_the_package_root_is_still_only_a_docstring(self) -> None:
        # The moment __init__ imports a pack, "inert source artifact" stops
        # being true: importing the package would execute pack code, and every
        # inertness proof here would be measuring the wrong thing.
        assert _executable_source(PACKAGE_INIT).strip() == "pass"

    def test_importing_the_inspector_does_not_import_a_pack(self) -> None:
        completed = _run_python(
            "import sys, app.knowledge_packs.inspection;"
            f"print({REVIEWED_MODULE!r} in sys.modules)"
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
        # its own __file__, so it is a member rather than a reacher-in; if a
        # later change hard-codes the dotted path inside app/, this fails and
        # it should.
        allowed = {PACKAGE_INIT, REVIEWED_PACK_SOURCE}
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
        assert inspect_packs().ok is True

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
            "ast",
            "collections",
            "dataclasses",
            "enum",
            "pathlib",
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

    def test_a_directory_of_two_hundred_source_files_inspects_correctly(self, pack_dir) -> None:
        directory, write = pack_dir
        for index in range(200):
            write(
                f"scaled_{index:03d}",
                _source(
                    PACK_ID=f'PACK_ID = "for_you.skin_care.scaled_{index:03d}.v1"',
                    REASON_KEY=f'REASON_KEY = "for_you.skin_care.scaled_{index:03d}.reason"',
                ),
            )
        result = _inspect(directory)
        assert result.ok is True
        assert len(result.packs) == 200


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

    def test_the_reviewed_descriptor_values_are_exactly_these(self) -> None:
        # Belt and braces on the same property from the other side: the four
        # governed identity strings a customer-visible decision hangs on,
        # written out rather than read from the module under test.
        assert pack.PACK_ID == "for_you.skin_care.petrolatum_dry_skin.v1"
        assert pack.DOMAIN == "skin_care"
        assert pack.CATEGORY == "skin_care"
        assert pack.REASON_KEY == "for_you.skin_care.petrolatum.dry_skin.dermatologist_guidance"
        assert DESCRIPTOR_FIELDS == ("PACK_ID", "DOMAIN", "CATEGORY", "REASON_KEY")


# ---------------------------------------------------------------------------
# Step 14B: the inventory gate is wired into CI, and cannot quietly be removed
# ---------------------------------------------------------------------------
def _workflow_job_block(name: str) -> list[str]:
    """The lines belonging to one job in ``ci.yml``, comment-only lines removed.

    Not a YAML parser, and deliberately not: the production-runtime suite
    already carries a bespoke reader for ``render.yaml`` and it is a large
    thing to maintain. The question here is narrow — is this step inside the
    always-run job — and indentation answers it. Jobs are the two-space keys
    under ``jobs:``; a job's block runs from its key to the next key at that
    indentation. Comments are dropped so the workflow's own prose about the
    gate cannot satisfy an assertion about the gate.
    """
    lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.rstrip() == "jobs:")
    block: list[str] = []
    collecting = False
    for line in lines[start + 1 :]:
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", line.rstrip()):
            if collecting:
                break
            collecting = line.strip() == f"{name}:"
            continue
        if collecting and line.strip() and not line.lstrip().startswith("#"):
            block.append(line)
    assert block, f"no job named {name!r} in {CI_WORKFLOW}"
    return block


def _scope_for(path: str, key: str) -> str:
    """Ask the real scope script what one changed path selects."""
    completed = subprocess.run(
        ["bash", str(SCOPE_SCRIPT), "base", "head", "pull_request"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CI_SCOPE_CHANGED_FILES": path},
    )
    assert completed.returncode == 0, completed.stderr
    for line in completed.stdout.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{key} not emitted for {path}")


class TestTheInventoryGateIsWiredIntoCi:
    """Step 14A made the inventory checkable. This makes it checked.

    Until now somebody had to remember to run the command. That is fine for
    one pack and untenable for dozens: the inventory that most needs checking
    is the one in a pull request nobody thought was about packs. The gate is
    therefore unconditional, in the one job that always runs, ahead of the
    step that decides what else may skip.

    A gate that can be deleted in the same pull request it would have blocked
    is not a gate, so its presence is asserted here — from the backend suite,
    which a change to the workflow now selects.
    """

    def test_the_scope_job_runs_the_exact_inventory_command(self) -> None:
        block = _workflow_job_block("scope")
        assert any(
            line.strip() == "run: python scripts/inspect_knowledge_packs.py --json"
            for line in block
        ), "the always-run scope job does not invoke the knowledge-pack inspector"

    def test_the_gate_lives_nowhere_but_the_always_run_job(self) -> None:
        # A copy in some conditional job would look like coverage and provide
        # none, and would make the assertion above pass for the wrong reason.
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        occurrences = workflow.count("scripts/inspect_knowledge_packs.py")
        assert occurrences == 1, f"the inspector is named {occurrences} times in the workflow"

    def test_the_gate_is_not_conditional_on_anything(self) -> None:
        # Neither the job nor the step may carry an `if:`. The whole point is
        # that no scope rule, correct or buggy, gets to decide whether the
        # inventory is checked today.
        block = _workflow_job_block("scope")
        assert not any(line.strip().startswith("if:") for line in block)
        gate = block.index("        run: python scripts/inspect_knowledge_packs.py --json")
        assert block[gate - 1].strip() == "- name: Knowledge-pack governance"

    def test_the_gate_cannot_fail_softly(self) -> None:
        block = _workflow_job_block("scope")
        for escape in ("continue-on-error", "|| true", "|| exit 0", "set +e"):
            assert not any(escape in line for line in block), escape

    def test_the_gate_runs_before_the_scope_outputs_are_emitted(self) -> None:
        # Ordering is the difference between "the inventory was checked" and
        # "the inventory was checked unless scope detection said otherwise".
        block = _workflow_job_block("scope")
        gate = block.index("        run: python scripts/inspect_knowledge_packs.py --json")
        detect = block.index("        run: bash .github/scripts/detect-ci-scope.sh "
                             '"$BASE_SHA" "$HEAD_SHA" "$EVENT_NAME"')
        assert gate < detect

    def test_the_gate_installs_no_backend_dependencies(self) -> None:
        # The inspector needs none, and a dependency install here would make
        # the always-run job slow enough that somebody would want to make it
        # conditional — which is the property being protected.
        block = _workflow_job_block("scope")
        joined = "\n".join(block)
        for forbidden in ("requirements", "pip install", "services:", "postgres"):
            assert forbidden not in joined, forbidden

    def test_the_interpreter_is_the_governed_one_and_immutably_pinned(self) -> None:
        block = _workflow_job_block("scope")
        setup = [line for line in block if "actions/setup-python@" in line]
        assert len(setup) == 1, setup
        assert re.search(r"actions/setup-python@[0-9a-f]{40}\b", setup[0]), setup[0]
        assert any("python-version: ${{ env.PYTHON_VERSION }}" in line for line in block)

    def test_the_pr_gate_still_refuses_to_read_a_failed_scope_as_no_work(self) -> None:
        # This is how a failed inventory reaches branch protection: the gate
        # job checks needs.scope.result before anything else. Without it, a
        # failed scope job would surface as skipped implementation jobs and a
        # green gate.
        block = _workflow_job_block("pr-gate")
        joined = "\n".join(block)
        assert "needs.scope.result" in joined
        assert "needs: [scope," in joined or "needs: [scope]" in joined


class TestKnowledgeOperationScriptsSelectBackendQualification:
    """The scope rule, asserted against the real filenames.

    The shell harness under ``.github/`` proves the pattern but may not spell
    the Phase B operator's filename — a Phase B test forbids that name
    anywhere in that tree, because naming the production activation script
    inside CI is how it stops being manual-only. This file is outside that
    tree and may name it, so the two halves together cover the pattern and the
    identity without breaching the boundary.
    """

    @pytest.mark.parametrize(
        "script",
        [
            "scripts/inspect_knowledge_packs.py",
            "scripts/build_step8i_petrolatum_release.py",
            "scripts/operate_step8i_petrolatum_release.py",
        ],
    )
    def test_the_real_script_selects_the_backend_suite(self, script: str) -> None:
        assert (REPOSITORY_ROOT / script).is_file(), f"{script} does not exist"
        assert _scope_for(script, "backend") == "true"

    @pytest.mark.parametrize(
        "script",
        [
            "scripts/inspect_knowledge_packs.py",
            "scripts/build_step8i_petrolatum_release.py",
            "scripts/operate_step8i_petrolatum_release.py",
        ],
    )
    def test_the_real_script_widens_nothing_else(self, script: str) -> None:
        for key in ("schema", "frontend", "mobile", "web", "container", "security", "release"):
            assert _scope_for(script, key) == "false", (script, key)


class TestTheGateActuallyBlocks:
    """The command CI runs, run for real, on a good inventory and a bad one."""

    def test_the_exact_ci_command_passes_on_the_real_repository(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/inspect_knowledge_packs.py", "--json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["status"] == "ok"
        assert payload["pack_count"] == REVIEWED_PACK_COUNT

    def test_the_exact_ci_command_fails_on_an_invalid_inventory(self, tmp_path: Path) -> None:
        # The real CLI file, unmodified, run over a pack directory that is not
        # the repository's. Copying the script rather than editing anything is
        # what makes this an integration proof: the resolution of the pack
        # directory is the inspector's own, and the exit code is the one CI
        # would see.
        package = tmp_path / "backend" / "app" / "knowledge_packs"
        package.mkdir(parents=True)
        (tmp_path / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text('"""Temporary pack directory."""\n', encoding="utf-8")
        shutil.copy(INSPECTION_SOURCE, package / "inspection.py")
        (package / "broken_pack.py").write_text(
            "def make_pack_id():\n"
            '    return "for_you.skin_care.broken.v1"\n'
            "\n"
            "\n"
            "PACK_ID = make_pack_id()\n"
            'DOMAIN = "skin_care"\n'
            'CATEGORY = "skin_care"\n'
            'REASON_KEY = "for_you.skin_care.broken.reason"\n'
            "\n"
            "\n"
            f"def {COMPILER_ATTRIBUTE}(entry):\n"
            "    return {}\n",
            encoding="utf-8",
        )
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        shutil.copy(CLI_PATH, scripts / CLI_PATH.name)

        completed = subprocess.run(
            [sys.executable, str(scripts / CLI_PATH.name), "--json"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode != 0, "an invalid inventory produced a passing CI command"
        payload = json.loads(completed.stdout)
        assert payload["status"] == "invalid"
        assert payload["errors"] == [
            {
                "module": "app.knowledge_packs.broken_pack",
                "field": "PACK_ID",
                "code": "NON_STATIC_METADATA",
            }
        ]


# ---------------------------------------------------------------------------
# The module under test is the one in the repository
# ---------------------------------------------------------------------------
def test_the_inspector_module_is_the_file_these_tests_read() -> None:
    assert Path(inspection.__file__).resolve() == INSPECTION_SOURCE
