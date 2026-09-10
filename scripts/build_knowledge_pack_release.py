"""Compile one reviewed knowledge pack's published Step 8G entry into its Step 8H manifest.

    python scripts/build_knowledge_pack_release.py \\
        --pack-id for_you.skin_care.petrolatum_dry_skin.v1 \\
        published_entry.json

    python scripts/build_knowledge_pack_release.py \\
        --pack-id for_you.skin_care.petrolatum_dry_skin.v1 \\
        published_entry.json \\
        --output manifest.json

Exit ``0`` means a manifest was compiled and validated; any non-zero exit means
the tool refused and produced nothing. A machine driving this can trust the
process status: there is no "succeeded with an error object" path.

**Why the pack id is mandatory.** There is one governed pack today, and it
would be trivial to default to it. That default would be a landmine: the day a
second pack exists, an operator's muscle memory compiles the wrong knowledge
and the mistake looks exactly like success. So there is no ``--latest``, no
``--default``, no "fall back to the only one", and no prefix or fuzzy
matching. The id is compared with ``==``, case-sensitively, in full.

**Why the whole inventory must be valid first.** The tool refuses if *any*
committed pack fails the Step 14A contract, not merely the one requested. A
duplicated ``PACK_ID`` or a shared ``REASON_KEY`` means the repository can no
longer say which pack owns which identity — and "the pack I asked for looks
fine" is not an answer to that, because which one you got is the question.

**Where execution begins.** Step 14A's inspector deliberately never runs
candidate source; that is what makes discovery safe. This tool is different by
design: its entire purpose is to run the selected pack's reviewed compiler. The
sequence keeps the two apart — inspect every pack statically, refuse on any
structural failure, resolve exactly one id, and only then import that one
module. No pack other than the requested one is ever imported, and nothing is
imported merely to find out what a pack is called.

**What this tool does not know.** It has no opinion about petrolatum, dry
skin, evidence strength, sources, signals or actions. Deciding whether a
published entry is the exact reviewed evidence is the pack compiler's job, and
validating the result is the Step 8H manifest authority's. This file only
orchestrates: select, invoke, validate, canonicalise, serialise, hash.

**It is not activation.** No database, no provider, no network, no credential,
no release row, no Phase B operation. Compiling a manifest offline and putting
one in front of customers remain different acts, performed by different tools,
and this is the first one.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.personal_decision_release.manifest import (  # noqa: E402
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.knowledge_packs.inspection import inspect_packs  # noqa: E402


class RefusalCode(StrEnum):
    """Closed set of reasons this tool declines to produce a manifest."""

    INVALID_INVENTORY = "INVALID_INVENTORY"
    PACK_NOT_FOUND = "PACK_NOT_FOUND"
    INPUT_UNREADABLE = "INPUT_UNREADABLE"
    INVALID_INPUT_JSON = "INVALID_INPUT_JSON"
    INPUT_NOT_OBJECT = "INPUT_NOT_OBJECT"
    SELECTED_PACK_IMPORT_FAILED = "SELECTED_PACK_IMPORT_FAILED"
    COMPILER_MISSING = "COMPILER_MISSING"
    COMPILER_REJECTED_INPUT = "COMPILER_REJECTED_INPUT"
    INVALID_COMPILED_MANIFEST = "INVALID_COMPILED_MANIFEST"
    OUTPUT_WRITE_FAILED = "OUTPUT_WRITE_FAILED"


class Refusal(Exception):
    """A controlled refusal: a code, and at most a coordinate to go look at.

    Never an exception message, a repr or a traceback. A published entry can
    contain anything an author put in it, and an unexpected exception inside a
    pack compiler can quote it straight back — into a terminal, a CI log, or a
    ticket. Class names are static identifiers from committed source and are
    safe to name; values derived from the input are not, and are dropped.
    """

    def __init__(self, code: RefusalCode, detail: str = "") -> None:
        super().__init__(str(code))
        self.code = code
        self.detail = detail

    def render(self) -> str:
        return f"refused: {self.code}" + (f" ({self.detail})" if self.detail else "")


def _valid_inventory():
    """Every committed pack, or a refusal naming what is wrong with the set."""
    result = inspect_packs()
    if result.errors:
        findings = ", ".join(
            f"{error.module}:{error.field}:{error.code}" for error in result.errors
        )
        raise Refusal(RefusalCode.INVALID_INVENTORY, findings)
    return result


def _select(inventory, pack_id: str):
    """Exactly one pack, chosen by exact id.

    ``==`` and nothing else. No prefix, no case folding, no version
    resolution, and emphatically no "there is only one, they must have meant
    that". The available ids are listed on failure because an operator who
    mistyped needs to see the real spelling, and the ids are already public
    repository metadata.
    """
    for descriptor in inventory.packs:
        if descriptor.pack_id == pack_id:
            return descriptor
    available = ", ".join(descriptor.pack_id for descriptor in inventory.packs) or "none"
    raise Refusal(RefusalCode.PACK_NOT_FOUND, f"known pack ids: {available}")


def _published_entry(path: Path) -> Mapping[str, object]:
    """The one published Step 8G entry, read from disk and no further.

    Nothing is fetched, authored, reviewed, approved or published here. The
    file the operator names is the whole input.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refusal(RefusalCode.INPUT_UNREADABLE, str(path)) from None
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        raise Refusal(RefusalCode.INVALID_INPUT_JSON, str(path)) from None
    if not isinstance(entry, Mapping):
        raise Refusal(RefusalCode.INPUT_NOT_OBJECT, f"{path} holds {type(entry).__name__}")
    return entry


def _compiler(descriptor):
    """Import the one selected pack and take the compiler it declared.

    This is the only import of pack source the tool performs, and it happens
    after the id has been resolved against a valid inventory.

    The ``getattr`` check is not redundant with static inspection. Inspection
    read the file; this runs it. A module whose source declares the function
    but whose import rebinds it away would pass the first and fail here, and
    failing here is the point — the callable about to be invoked is the one
    that matters.
    """
    try:
        module = importlib.import_module(descriptor.module)
    except Exception as error:
        raise Refusal(
            RefusalCode.SELECTED_PACK_IMPORT_FAILED,
            f"{descriptor.module} raised {type(error).__name__}",
        ) from None
    compiler = getattr(module, descriptor.compiler_name, None)
    if not callable(compiler):
        raise Refusal(
            RefusalCode.COMPILER_MISSING,
            f"{descriptor.module} does not expose a callable {descriptor.compiler_name}",
        )
    return compiler


def _compile(
    compiler, entry: Mapping[str, object], descriptor
) -> tuple[dict[str, object], str]:
    """Run the pack's compiler, then hold its output to the Step 8H contract.

    A pack returning a dictionary is not the same as a pack returning a valid
    manifest, so the result is parsed by the existing release authority rather
    than trusted. There is no second validator here and there must not be:
    two definitions of a valid manifest is one too many.

    What comes back is the *canonical* manifest, not the compiler's raw
    dictionary, and the hash is taken from the same parsed object. Emitting
    the raw dictionary would be a quiet scalability bug: Step 8H sorts
    semantic rules, policy rules, explanation rules and the identity set
    inside each policy, so two runs of a future multi-rule compiler that
    happened to build its lists in different orders are the same manifest and
    hash the same — but would have written different bytes to disk. "Same
    hash, different file" is not a state this tool may produce.

    The reviewed petrolatum pack has one rule of each kind and already emits
    canonical order, which is exactly why this could not be noticed there.

    Parsing happens once. ``main`` receives the canonical document and the
    hash together so the bytes written and the hash reported can only ever
    describe the same manifest.

    All three Step 8H calls sit inside one ``try``. Parsing is not the only
    step that can fail: canonicalisation and hashing read the same fields
    again, and hashing encodes them to UTF-8. Today the parser rejects
    everything they would choke on — every text field is validated there —
    but "today the first stage happens to catch it" is a property of the
    manifest authority, not of this tool, and it is not this tool's to rely
    on. Whatever any of the three raises becomes the same closed refusal.
    """
    try:
        raw = compiler(entry)
    except Exception as error:
        raise Refusal(
            RefusalCode.COMPILER_REJECTED_INPUT,
            f"{descriptor.pack_id} raised {type(error).__name__}",
        ) from None
    try:
        parsed = parse_release_manifest(raw)
        canonical = canonical_manifest(parsed)
        content_hash = manifest_content_hash(parsed)
    except Exception as error:
        raise Refusal(
            RefusalCode.INVALID_COMPILED_MANIFEST,
            f"{descriptor.pack_id} produced {type(error).__name__}",
        ) from None
    return canonical, content_hash


def _write(path: Path, encoded: str) -> None:
    """Replace the output file in one step, or leave it as it was.

    Everything above has already succeeded by the time this runs, so the only
    failure left is the filesystem's. Writing through a temporary sibling and
    renaming means a full disk cannot leave half a manifest on disk looking
    like a compiled release.
    """
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            # Recorded before the write, not after. The file exists from the
            # moment it is opened, so a failure inside ``write`` — or in the
            # flush that happens on close — would otherwise leave a partial
            # dotfile that the cleanup below never learned about.
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise Refusal(RefusalCode.OUTPUT_WRITE_FAILED, str(path)) from None
    finally:
        # A failure anywhere between creating the temporary sibling and
        # renaming it — the write, the flush on close, or the rename itself —
        # would otherwise leave a stray dotfile next to the real output. The
        # cleanup is suppressed rather than reported: the write failure is
        # what the operator needs to know, and a second exception raised
        # while tidying up would replace that refusal with a traceback.
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compile one reviewed knowledge pack's published Step 8G entry into its "
            "Step 8H release manifest. Offline: no database, no network, no credentials, "
            "and no activation."
        )
    )
    parser.add_argument(
        "--pack-id",
        required=True,
        help="Exact PACK_ID of the pack to compile. No default, no prefix matching.",
    )
    parser.add_argument("input", type=Path, help="Published Step 8G entry JSON file")
    parser.add_argument("--output", type=Path, help="Optional canonical manifest output file")
    args = parser.parse_args(argv)

    try:
        inventory = _valid_inventory()
        descriptor = _select(inventory, args.pack_id)
        entry = _published_entry(args.input)
        manifest, content_hash = _compile(_compiler(descriptor), entry, descriptor)
        encoded = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        if args.output is not None:
            _write(args.output, encoded)
    except Refusal as refusal:
        print(refusal.render(), file=sys.stderr)
        return 1

    if args.output is None:
        print(encoded, end="")
    print(f"content_hash={content_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
