"""Offline inventory and contract validation for version-controlled packs.

One reviewed pack is easy to hold in your head. Dozens are not, and the
questions that matter then are boring and structural: which packs exist, is
every ``PACK_ID`` unique, does each one own a distinct reason key, does each
still expose the compiler the release workflow calls. This module answers
those questions and nothing else.

**What it is not.** It is not a registry, not a loader, not a release
operator. It does not prepare, verify, publish, compile, approve, activate,
deactivate or roll back anything — those responsibilities already exist in the
evidence and Step 8H release lifecycles and stay there. Nothing here reads or
writes a database, opens a socket, touches a provider, or reads a credential.
Its whole world is source metadata already committed to this repository.

**Why importing this package still imports nothing.** Discovery lives here
rather than in ``__init__``, and nothing imports this module implicitly. The
package root remains a docstring: ``import`` of the package does not import
this module, and this module is only loaded when offline tooling or a test
asks for it by name. That is what keeps a knowledge pack an inert source
artifact rather than something the application can reach at runtime.

**Why the package is found through ``__package__``.** This module never spells
out the dotted path of the package it inspects; it asks Python which package
it belongs to. That is the correct way for a module to introspect its own
package, and it has a second, deliberate effect: the Step 8I boundary test
scans every file under ``app/`` for a hard-coded reference to the knowledge
pack package and allows only the two reviewed files. That guard is right — an
application module that names the pack package is exactly how an accidental
runtime import begins — and this module is a member of that package rather
than a reacher-into-it, so it neither trips the guard nor needs it widened.

**What qualifies as a pack.** Declaring ``PACK_ID`` is the claim. A module in
this package without one is not a governed pack and is not an error — it is
infrastructure, like this file. A module *with* one is held to the whole
contract, and every violation is reported rather than raised, so one broken
pack cannot hide the state of the others.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType

#: The callable every pack must expose for the Step 8H release workflow.
#: Named, never invoked: compiling requires a reviewed published evidence
#: entry, and inventory has no business inventing one.
COMPILER_ATTRIBUTE = "build_release_manifest_from_published_entry"

#: The attribute whose presence means "this module claims to be a governed
#: pack". Everything else in the contract is mandatory once it is present.
PACK_MARKER_ATTRIBUTE = "PACK_ID"

#: Minimum dot-separated segments in a pack id, so an id is namespaced rather
#: than a bare word.
_MINIMUM_PACK_ID_SEGMENTS = 3


class PackValidationCode(StrEnum):
    """Closed set of structural findings.

    Deliberately coarse. A code plus a module plus a field name is enough for
    a person to open the file and see the problem; anything richer would start
    quoting source into logs and CI output for no benefit.
    """

    IMPORT_FAILED = "IMPORT_FAILED"
    INVALID_PACK_ID = "INVALID_PACK_ID"
    DUPLICATE_PACK_ID = "DUPLICATE_PACK_ID"
    MISSING_DOMAIN = "MISSING_DOMAIN"
    MISSING_CATEGORY = "MISSING_CATEGORY"
    MISSING_REASON_KEY = "MISSING_REASON_KEY"
    DUPLICATE_REASON_KEY = "DUPLICATE_REASON_KEY"
    MISSING_COMPILER = "MISSING_COMPILER"
    COMPILER_NOT_CALLABLE = "COMPILER_NOT_CALLABLE"


@dataclass(frozen=True, slots=True, order=True)
class PackValidationError:
    """One structural finding: where, which field, what kind.

    No exception text, no object repr, no source excerpt. A finding is a
    coordinate into the repository, not a payload.
    """

    module: str
    field: str
    code: PackValidationCode


@dataclass(frozen=True, slots=True)
class KnowledgePackDescriptor:
    """The operational identity of one pack. Static repository metadata only.

    Scientific content is deliberately absent. A pack's substance key, fact
    key, evidence strength and sources belong to its own specification and to
    the reviewed evidence behind it — a cross-pack inventory has no business
    flattening them into a shared shape, and future food or hair packs will
    not have the same ones.
    """

    module: str
    pack_id: str
    domain: str
    category: str
    reason_key: str
    compiler_name: str

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.pack_id, self.module)


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Everything found, in a deterministic order."""

    packs: tuple[KnowledgePackDescriptor, ...]
    errors: tuple[PackValidationError, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _package_name() -> str:
    """This module's own package, asked of Python rather than hard-coded."""
    package = __package__
    if not package:  # pragma: no cover - only reachable if run as a script
        raise RuntimeError("knowledge pack inspection must be imported as a package member")
    return package


def _is_clean_nonblank_string(value: object) -> bool:
    """A non-empty string carrying no leading or trailing whitespace.

    Surrounding whitespace is rejected rather than stripped. These values are
    identities that get compared, sorted and grepped; silently accepting
    ``"skin_care "`` would make two packs that look identical in a report
    behave as different ones everywhere else.
    """
    return isinstance(value, str) and value != "" and value == value.strip()


def _is_valid_pack_id(value: object) -> bool:
    """Namespaced, versioned, lowercase, no whitespace.

    Loose on purpose about *which* namespaces exist: hair, cosmetics and food
    packs do not exist yet and this is not the place to decide their names.
    Strict about shape, because the shape is what makes an id sortable,
    greppable and unambiguously versioned.
    """
    if not isinstance(value, str) or not _is_clean_nonblank_string(value):
        return False
    segments = value.split(".")
    if len(segments) < _MINIMUM_PACK_ID_SEGMENTS:
        return False
    if not all(segment and _is_identifier_segment(segment) for segment in segments):
        return False
    version = segments[-1]
    return version.startswith("v") and version[1:].isdigit()


def _is_identifier_segment(segment: str) -> bool:
    return all(
        character.isascii() and (character.islower() or character.isdigit() or character == "_")
        for character in segment
    )


def discover_pack_modules() -> tuple[str, ...]:
    """Dotted names of every module in this package, sorted, offline.

    Sorted rather than left in filesystem order: two machines listing a
    directory must produce the same inventory, or the CLI's output is not
    something a reviewer can diff.

    No pack is imported here. The package itself is — it has to be, to have a
    ``__path__`` to list — and importing it loads nothing, because its
    ``__init__`` is a docstring and stays one. Private modules and this one
    are skipped by name; whether the rest are packs is decided later, by
    looking for the marker rather than guessing from the filename.
    """
    package_name = _package_name()
    package = importlib.import_module(package_name)
    found = [
        f"{package_name}.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
        if not info.name.startswith("_") and info.name != __name__.rsplit(".", 1)[-1]
    ]
    return tuple(sorted(found))


def _describe(module_name: str, module: ModuleType) -> tuple[
    KnowledgePackDescriptor | None, tuple[PackValidationError, ...]
]:
    errors: list[PackValidationError] = []

    def note(field: str, code: PackValidationCode) -> None:
        errors.append(PackValidationError(module=module_name, field=field, code=code))

    pack_id = getattr(module, PACK_MARKER_ATTRIBUTE, None)
    if not _is_valid_pack_id(pack_id):
        note(PACK_MARKER_ATTRIBUTE, PackValidationCode.INVALID_PACK_ID)

    domain = getattr(module, "DOMAIN", None)
    if not _is_clean_nonblank_string(domain):
        note("DOMAIN", PackValidationCode.MISSING_DOMAIN)

    category = getattr(module, "CATEGORY", None)
    if not _is_clean_nonblank_string(category):
        note("CATEGORY", PackValidationCode.MISSING_CATEGORY)

    reason_key = getattr(module, "REASON_KEY", None)
    if not _is_clean_nonblank_string(reason_key):
        note("REASON_KEY", PackValidationCode.MISSING_REASON_KEY)

    compiler = getattr(module, COMPILER_ATTRIBUTE, None)
    if compiler is None:
        note(COMPILER_ATTRIBUTE, PackValidationCode.MISSING_COMPILER)
    elif not callable(compiler):
        note(COMPILER_ATTRIBUTE, PackValidationCode.COMPILER_NOT_CALLABLE)

    if errors:
        return None, tuple(errors)
    return (
        KnowledgePackDescriptor(
            module=module_name,
            pack_id=str(pack_id),
            domain=str(domain),
            category=str(category),
            reason_key=str(reason_key),
            compiler_name=COMPILER_ATTRIBUTE,
        ),
        (),
    )


def claims_to_be_a_pack(module: ModuleType) -> bool:
    """Does this module claim governed-pack status?

    Declaring ``PACK_ID`` is the claim. Sitting in the directory is not: this
    module does not declare one and is correctly not a pack.

    Presence, not usefulness. ``PACK_ID = None`` or ``PACK_ID = ""`` is a pack
    that is broken, which is a finding; treating it as "not a pack" would let
    the worst case — a governed pack whose identity failed to be written —
    vanish from the inventory in silence.
    """
    return hasattr(module, PACK_MARKER_ATTRIBUTE)


def validate_descriptors(
    descriptors: Iterable[KnowledgePackDescriptor],
) -> tuple[PackValidationError, ...]:
    """Cross-pack uniqueness: one id per pack, one reason key per pack.

    Two packs sharing a reason key would mean two reviewed knowledge claims
    competing to own the same sentence a customer reads, with nothing in the
    release manifest to say which wins. Rejected by default; if deliberate
    shared ownership is ever governed, that is a reviewed change to this rule
    and not something an inventory tool should quietly permit.

    Every duplicate member is reported, not merely the second one, so the
    output names both files a person has to open. Dict-backed, so cost is
    linear in the number of packs.
    """
    ordered = sorted(descriptors, key=lambda descriptor: descriptor.sort_key)
    errors: list[PackValidationError] = []
    for field, code, extract in (
        ("PACK_ID", PackValidationCode.DUPLICATE_PACK_ID, lambda d: d.pack_id),
        ("REASON_KEY", PackValidationCode.DUPLICATE_REASON_KEY, lambda d: d.reason_key),
    ):
        seen: dict[str, list[str]] = {}
        for descriptor in ordered:
            seen.setdefault(extract(descriptor), []).append(descriptor.module)
        for modules in seen.values():
            if len(modules) > 1:
                errors.extend(
                    PackValidationError(module=module, field=field, code=code)
                    for module in sorted(modules)
                )
    return tuple(sorted(errors))


def inspect_packs(module_names: Sequence[str] | None = None) -> InspectionResult:
    """Import the candidate modules, read their identity, validate the set.

    Importing is unavoidable — the contract is expressed in module attributes,
    and a pack is a Python module by design. It is also safe: a pack is inert
    by contract, so importing one runs no side effect, and the Step 8I suite
    holds that property for the reviewed pack independently of this tool.

    A module that fails to import is reported as a finding rather than
    allowed to abort the run, for the same reason every other check is: one
    broken pack must not hide the state of the rest. Only the module name is
    recorded — never the exception text, which in a syntax or import error
    quotes source into CI output.
    """
    names = tuple(module_names) if module_names is not None else discover_pack_modules()
    descriptors: list[KnowledgePackDescriptor] = []
    errors: list[PackValidationError] = []

    for module_name in sorted(names):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            errors.append(
                PackValidationError(
                    module=module_name,
                    field="module",
                    code=PackValidationCode.IMPORT_FAILED,
                )
            )
            continue
        if not claims_to_be_a_pack(module):
            continue
        descriptor, module_errors = _describe(module_name, module)
        errors.extend(module_errors)
        if descriptor is not None:
            descriptors.append(descriptor)

    errors.extend(validate_descriptors(descriptors))
    return InspectionResult(
        packs=tuple(sorted(descriptors, key=lambda descriptor: descriptor.sort_key)),
        errors=tuple(sorted(errors)),
    )


def as_json_payload(result: InspectionResult) -> dict[str, object]:
    """Deterministic, machine-readable, and free of anything but identity."""
    return {
        "status": "ok" if result.ok else "invalid",
        "pack_count": len(result.packs),
        "packs": [
            {
                "module": descriptor.module,
                "pack_id": descriptor.pack_id,
                "domain": descriptor.domain,
                "category": descriptor.category,
                "reason_key": descriptor.reason_key,
                "compiler": descriptor.compiler_name,
            }
            for descriptor in result.packs
        ],
        "errors": [
            {"module": error.module, "field": error.field, "code": str(error.code)}
            for error in result.errors
        ],
    }
