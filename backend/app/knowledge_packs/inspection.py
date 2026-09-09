"""Offline, static inventory and contract validation for version-controlled packs.

One reviewed pack is easy to hold in your head. Dozens are not, and the
questions that matter then are boring and structural: which packs exist, is
every ``PACK_ID`` unique, does each one own a distinct reason key, does each
still declare the compiler the release workflow calls. This module answers
those questions and nothing else.

**It reads source. It never runs it.** A knowledge pack is an inert
specification, and this module treats it as one: candidate files are read as
text and parsed with :mod:`ast`. No candidate is imported, executed,
``eval``-ed, ``exec``-ed or run through :mod:`runpy`. That is not a stylistic
preference. Part of the point of an inventory tool is to notice a pack that
has grown an import-time side effect — a database call, a network call, a file
write, a provider call, a release operation — and a tool that imported packs
in order to inspect them would trigger exactly the thing it exists to catch,
across every pack at once, on an operator's laptop.

The cost of that choice is that identity must be *statically legible*, and the
check runs in two layers. Layer one records **every** module-scope statement
that binds, rebinds or deletes a governed name — assignment plain, annotated,
destructured, starred, chained or augmented; a walrus; an import; a ``del``; a
loop target; a ``with ... as``; an ``except ... as``; a ``match`` capture; a
function or class definition — including inside module-scope control flow,
whose bodies really do bind module names when they run. That layer has to be
complete rather than convenient: the failure it prevents is not a wrong answer
but no answer, because a collector that missed ``PACK_ID, other = (...)`` would
classify the file as infrastructure and drop a governed pack out of the
inventory in silence. Bindings inside a nested scope — a function, a class, a
lambda — are not module bindings and are not counted.

Layer two then accepts only two forms: ``NAME = "literal"`` (or its annotated
equivalent) for a descriptor field, and a single plain top-level ``def`` for
the compiler. Everything else is reported and the pack fails closed.

Layer one also covers *definition time*, which is easy to forget: a ``def`` is
a statement before it is a scope, and its decorators, defaults and annotations
are evaluated in the enclosing scope when the statement runs. So are a
lambda's defaults and a class's bases. The walk that finds these follows every
AST child rather than only expression-shaped ones, because Python hangs
expressions off semantic wrappers that a type-filtered walk treats as dead
ends, and prunes only at genuine scope boundaries.

Nested lexical and class-body locals are not module declarations — with one
exception that has to be named, because ``global`` can redirect an otherwise
nested binding into module state. Rather than model when a nested block runs,
a governed name appearing in any ``global`` statement anywhere in a pack is
rejected outright.

Be precise about what this buys — four claims, and no more:

* this tool does not execute candidate source;
* every statically represented binding attempt occurring in the module's own
  execution scope is observed, for the five governed names;
* nested lexical and class-body locals are not treated as module declarations;
* a governed name may never be declared ``global`` in a pack, anywhere.

It is not a claim that any pack has been proven side-effect-free, and no
attempt is made to see through ``exec(...)``, ``globals()[...]`` assignment,
``setattr`` on the module object, or any other dynamic write. This is not a
sandbox.

**What it is not.** Not a registry, not a loader, not a release operator. It
does not prepare, verify, publish, compile, approve, activate, deactivate or
roll back anything — those responsibilities already exist in the evidence and
Step 8H release lifecycles and stay there. Nothing here reads or writes a
database, opens a network connection, touches a provider, or reads a
credential or an environment variable. Its whole world is source text already
committed to this repository.

**Why importing this package still imports nothing.** Discovery lives here
rather than in ``__init__``, and nothing imports this module implicitly. The
package root remains a docstring: importing the package does not import this
module, and this module is only loaded when offline tooling or a test asks for
it by name.

**Why the package is found through ``__package__``.** This module never spells
out the dotted path of the package it inspects; it asks Python which package
it belongs to, and it finds the directory through its own ``__file__``. That
is the correct way for a module to introspect its own package, and it has a
second, deliberate effect: the Step 8I boundary test scans every file under
``app/`` for a hard-coded reference to the knowledge pack package and allows
only the two reviewed files. That guard is right — an application module that
names the pack package is exactly how an accidental runtime import begins —
and this module is a member of that package rather than a reacher-into-it, so
it neither trips the guard nor needs it widened.

**What qualifies as a pack.** Any module-scope attempt to bind ``PACK_ID`` is
the claim — a literal, a computed value, a destructuring target, a bare
annotation, even a ``del`` — and only the two files named in
:data:`INFRASTRUCTURE_FILENAMES` are exempt from being asked. A filename is never an exemption: a governed pack
cannot slip out of the inventory by being called ``_hidden_pack.py``. A file
that simply does not declare the marker is not a governed pack and is not an
error — it is infrastructure. A file *with* the marker is held to the whole
contract, and every violation is reported rather than raised, so one broken
pack cannot hide the state of the others.
"""
from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: The callable every pack must declare for the Step 8H release workflow.
#: Named, never invoked: compiling requires a reviewed published evidence
#: entry, and inventory has no business inventing one.
COMPILER_ATTRIBUTE = "build_release_manifest_from_published_entry"

#: The attribute whose presence means "this module claims to be a governed
#: pack". Everything else in the contract is mandatory once it is present.
PACK_MARKER_ATTRIBUTE = "PACK_ID"

#: The governed descriptor fields, in report order.
DESCRIPTOR_FIELDS = (PACK_MARKER_ATTRIBUTE, "DOMAIN", "CATEGORY", "REASON_KEY")

#: Every name whose binding this tool governs. A pack may not redirect any of
#: them through a ``global`` declaration — see :func:`_governed_global_names`.
GOVERNED_NAMES = frozenset(DESCRIPTOR_FIELDS) | {COMPILER_ATTRIBUTE}

#: The only files in the pack directory that are not asked to be packs.
#: A closed list, deliberately: anything else added to the directory is
#: inspected, whatever it is called.
INFRASTRUCTURE_FILENAMES = frozenset({"__init__.py", "inspection.py"})

#: Minimum dot-separated segments in a pack id, so an id is namespaced rather
#: than a bare word.
_MINIMUM_PACK_ID_SEGMENTS = 3


class PackValidationCode(StrEnum):
    """Closed set of structural findings.

    Deliberately coarse. A code plus a module plus a field name is enough for
    a person to open the file and see the problem; anything richer would start
    quoting source into logs and CI output for no benefit. That applies most
    of all to :data:`UNPARSABLE_SOURCE`, where the underlying exception would
    happily print the offending line.
    """

    UNREADABLE_SOURCE = "UNREADABLE_SOURCE"
    UNPARSABLE_SOURCE = "UNPARSABLE_SOURCE"
    #: The value cannot be read without running the module.
    NON_STATIC_METADATA = "NON_STATIC_METADATA"
    #: The name is bound at module scope more than once — a second
    #: declaration, a rebinding, or a ``del``. Whichever runs last wins, which
    #: in a specification file is ambiguity rather than shorthand.
    DUPLICATE_DECLARATION = "DUPLICATE_DECLARATION"
    INVALID_PACK_ID = "INVALID_PACK_ID"
    DUPLICATE_PACK_ID = "DUPLICATE_PACK_ID"
    MISSING_DOMAIN = "MISSING_DOMAIN"
    MISSING_CATEGORY = "MISSING_CATEGORY"
    MISSING_REASON_KEY = "MISSING_REASON_KEY"
    DUPLICATE_REASON_KEY = "DUPLICATE_REASON_KEY"
    MISSING_COMPILER = "MISSING_COMPILER"
    COMPILER_NOT_A_FUNCTION = "COMPILER_NOT_A_FUNCTION"


#: Which "this value is unusable" code belongs to which descriptor field.
_MISSING_CODE_BY_FIELD = {
    "DOMAIN": PackValidationCode.MISSING_DOMAIN,
    "CATEGORY": PackValidationCode.MISSING_CATEGORY,
    "REASON_KEY": PackValidationCode.MISSING_REASON_KEY,
}


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


def pack_source_directory() -> Path:
    """The directory holding the governed packs: the one this file is in."""
    return Path(__file__).resolve().parent


def discover_pack_sources(directory: Path | None = None) -> tuple[Path, ...]:
    """Every ``.py`` file in the directory bar the closed infrastructure list.

    Sorted rather than left in filesystem order: two machines listing a
    directory must produce the same inventory, or the CLI's output is not
    something a reviewer can diff.

    Note what is *not* here: no filename-prefix rule. An earlier version
    skipped names beginning with an underscore, which meant a governed pack
    could leave the inventory by being renamed. Governance is not something a
    filename gets to opt out of.
    """
    root = pack_source_directory() if directory is None else Path(directory)
    return tuple(
        sorted(path for path in root.glob("*.py") if path.name not in INFRASTRUCTURE_FILENAMES)
    )


def _is_clean_nonblank_string(value: object) -> bool:
    """A non-empty string carrying no leading or trailing whitespace.

    Surrounding whitespace is rejected rather than stripped. These values are
    identities that get compared, sorted and grepped; silently accepting
    ``"skin_care "`` would make two packs that look identical in a report
    behave as different ones everywhere else.
    """
    return isinstance(value, str) and value != "" and value == value.strip()


def _is_identifier_segment(segment: str) -> bool:
    return all(
        character.isascii() and (character.islower() or character.isdigit() or character == "_")
        for character in segment
    )


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


def _module_name(path: Path, package_name: str) -> str:
    return f"{package_name}.{path.stem}"


class _BindingForm(StrEnum):
    """How a module-scope statement binds a name.

    Only two forms are approved declarations. Everything else — a
    destructuring assignment, an augmented assignment, a walrus, a loop
    target, a ``with ... as``, an import, a ``del``, a binding buried in an
    ``if`` — is :data:`OTHER`. Not because those forms are exotic curiosities,
    but because the inspector must *see* them: a governed name touched in a
    way this contract does not accept has to produce a finding, never silence.
    """

    LITERAL = "literal"
    FUNCTION = "function"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class _Binding:
    """One module-scope attempt to bind, rebind or delete a name."""

    form: _BindingForm
    value: str | None = None


#: Statements that open a new scope. Their *bodies* belong to that scope, not
#: to the module — a ``PACK_ID`` local to a helper function is not a pack
#: marker — but their decorators, defaults, annotations, bases and keywords
#: are evaluated where the statement sits, at the moment it executes, and so
#: are part of the enclosing scope.
_SCOPE_STATEMENTS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _target_names(target: ast.expr) -> list[str]:
    """Every bare name an assignment target binds.

    Recursive, because ``PACK_ID, DOMAIN = ...`` and ``[a, *rest] = ...`` bind
    names just as surely as ``PACK_ID = ...`` does. An attribute or subscript
    target binds no bare name and yields nothing.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return [name for element in target.elts for name in _target_names(element)]
    return []


def _literal_string(value: ast.expr | None) -> str | None:
    """The string this expression states outright, or ``None``.

    ``None`` means "not knowable without running the module" — a call, a name,
    an f-string, a concatenation, a conditional expression, a non-string
    constant, or no value at all. The distinction matters: a value that must
    be computed is a finding, not a value.
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _definition_time_expressions(node: ast.AST) -> Iterator[ast.expr]:
    """The parts of a ``def``, ``lambda`` or ``class`` that run *outside* it.

    A function's body is a new scope. Its decorators, default values,
    annotations and return annotation are not: they are evaluated where the
    statement sits, in the enclosing scope, at the moment the ``def``
    executes. The same holds for a lambda's defaults and for a class's
    decorators, bases and keywords. Miss these and a walrus tucked into a
    default binds a governed name at module scope, invisibly.

    Annotations are included. Under ``from __future__ import annotations``
    they are never evaluated, so reading them can over-report — which is the
    direction to be wrong in, since the alternative is a governed pack
    quietly leaving the inventory.
    """
    yield from getattr(node, "decorator_list", None) or ()

    arguments = getattr(node, "args", None)
    if isinstance(arguments, ast.arguments):
        yield from arguments.defaults
        yield from (default for default in arguments.kw_defaults if default is not None)
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            arguments.vararg,
            arguments.kwarg,
        ):
            if argument is not None and argument.annotation is not None:
                yield argument.annotation

    returns = getattr(node, "returns", None)
    if returns is not None:
        yield returns

    yield from getattr(node, "bases", None) or ()
    for keyword in getattr(node, "keywords", None) or ():
        yield keyword.value


def _enclosing_scope_nodes(statement: ast.stmt) -> Iterator[ast.AST]:
    """Every AST node this statement evaluates in its own (module) scope.

    Deliberately generic. An earlier version followed only children that were
    themselves :class:`ast.expr`, which meant the semantic wrappers Python's
    grammar uses — :class:`ast.keyword`, :class:`ast.comprehension`,
    :class:`ast.arguments`, :class:`ast.arg`, :class:`ast.match_case`,
    :class:`ast.withitem`, :class:`ast.ExceptHandler` — were dead ends, and a
    walrus behind any of them was invisible. Following every child and pruning
    explicitly at scope boundaries is the version that cannot quietly grow a
    new hole when the grammar does.

    Three prunes, each for a reason:

    * **Nested statements.** Owned by the statement walker, which visits them
      with the right ``nested`` flag; walking them here would double-count.
    * **A ``def``/``class``'s body.** A new scope. Only its definition-time
      expressions are followed, via :func:`_definition_time_expressions`.
    * **A lambda's body.** A new scope too — but its defaults are not, so
      those are followed and the body is not.

    Comprehensions are *not* pruned: a walrus inside one binds in the
    enclosing scope. The comprehension's own iteration target is reached but
    never treated as a binding, because only :class:`ast.NamedExpr` nodes are
    acted on.
    """
    stack: list[ast.AST] = []

    def follow(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                continue
            stack.append(child)

    if isinstance(statement, _SCOPE_STATEMENTS):
        stack.extend(_definition_time_expressions(statement))
    else:
        follow(statement)

    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, ast.Lambda):
            stack.extend(_definition_time_expressions(current))
            continue
        follow(current)


def _statement_bindings(node: ast.stmt, *, nested: bool) -> Iterator[tuple[str, _Binding]]:
    """Every name this one statement binds at module scope, and how.

    ``nested`` is true inside module-scope control flow — an ``if``, a loop, a
    ``try``. Such a statement really does bind a module name when it runs, so
    it is recorded; but it runs conditionally, so it is never an approved
    declaration.
    """
    other = _Binding(_BindingForm.OTHER)

    # Sub-expressions run before the statement binds its own targets — a
    # function's defaults before its name, an assignment's value before its
    # target — so walrus bindings are reported first. That order is what makes
    # "whatever binds last wins" mean the right thing for the compiler.
    for candidate in _enclosing_scope_nodes(node):
        if isinstance(candidate, ast.NamedExpr):
            for name in _target_names(candidate.target):
                yield name, other

    if isinstance(node, ast.FunctionDef):
        yield node.name, (other if nested else _Binding(_BindingForm.FUNCTION))
        return
    if isinstance(node, ast.AsyncFunctionDef | ast.ClassDef):
        yield node.name, other
        return

    if isinstance(node, ast.Assign):
        literal = _literal_string(node.value)
        plain = len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        approved = plain and literal is not None and not nested
        for target in node.targets:
            for name in _target_names(target):
                yield name, (_Binding(_BindingForm.LITERAL, literal) if approved else other)
    elif isinstance(node, ast.AnnAssign):
        literal = _literal_string(node.value)
        approved = literal is not None and not nested
        for name in _target_names(node.target):
            yield name, (_Binding(_BindingForm.LITERAL, literal) if approved else other)
    elif isinstance(node, ast.AugAssign):
        for name in _target_names(node.target):
            yield name, other
    elif isinstance(node, ast.Import | ast.ImportFrom):
        for alias in node.names:
            yield (alias.asname or alias.name.split(".")[0]), other
    elif isinstance(node, ast.Delete):
        for target in node.targets:
            for name in _target_names(target):
                yield name, other
    elif isinstance(node, ast.For | ast.AsyncFor):
        for name in _target_names(node.target):
            yield name, other
    elif isinstance(node, ast.With | ast.AsyncWith):
        for item in node.items:
            if item.optional_vars is not None:
                for name in _target_names(item.optional_vars):
                    yield name, other
    elif isinstance(node, ast.Try | ast.TryStar):
        for handler in node.handlers:
            if handler.name:
                yield handler.name, other
    elif isinstance(node, ast.Match):
        for case in node.cases:
            for pattern in ast.walk(case.pattern):
                if isinstance(pattern, ast.MatchAs | ast.MatchStar) and pattern.name:
                    yield pattern.name, other
                elif isinstance(pattern, ast.MatchMapping) and pattern.rest:
                    yield pattern.rest, other


def _nested_bodies(node: ast.stmt) -> Iterator[list[ast.stmt]]:
    """The statement blocks inside this statement that still run at module scope.

    An ``if``, a loop, a ``try``, a ``with`` and a ``match`` all execute their
    bodies in the module's namespace, so a governed name bound in one is a
    module binding and must be seen. A function or a class does not, and is
    not descended into at all.
    """
    if isinstance(node, _SCOPE_STATEMENTS):
        return
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and all(isinstance(item, ast.stmt) for item in block):
            yield block
    for handler in getattr(node, "handlers", None) or ():
        yield handler.body
    for case in getattr(node, "cases", None) or ():
        yield case.body


def _governed_global_names(tree: ast.Module) -> list[str]:
    """Governed names declared ``global`` anywhere in this source.

    A class body is not module scope — ``class Holder: PACK_ID = "x"`` binds a
    class attribute and nothing else, and the collector prunes class bodies
    for exactly that reason. But ``global`` changes what an assignment
    *means*:

    .. code-block:: python

        class Holder:
            global PACK_ID
            PACK_ID = "for_you.skin_care.escape.v1"

    When that class statement executes, the assignment writes to the module
    namespace. Pruning the body misses it, and missing it is fail-open.

    The rule here is deliberately blunter than Python's semantics: a governed
    name appearing in **any** ``global`` statement, anywhere in the file — a
    class body, a nested class, a function that may never be called, a branch
    that may never be taken — is a finding. Deciding case by case would mean
    modelling when each enclosing block runs, which is a small interpreter and
    a new place for holes. A version-controlled specification has no
    legitimate reason to redirect these five identities, so refusing all of
    them costs nothing real and closes the whole class of escape.

    ``nonlocal`` is not covered, and does not need to be: it binds in an
    enclosing *function* scope and can never reach module state.
    """
    return [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Global)
        for name in node.names
        if name in GOVERNED_NAMES
    ]


def _module_scope_bindings(tree: ast.Module) -> dict[str, list[_Binding]]:
    """Every module-scope binding attempt, by name, in source order.

    This is the layer that must be complete rather than convenient. If a form
    of binding is missing from it, a governed name written that way becomes
    invisible — and invisible is the one outcome this tool exists to prevent.
    Layer two, in :func:`_describe`, is where the short list of *approved*
    forms is applied.
    """
    bindings: dict[str, list[_Binding]] = {}

    def walk(statements: list[ast.stmt], *, nested: bool) -> None:
        for node in statements:
            for name, binding in _statement_bindings(node, nested=nested):
                bindings.setdefault(name, []).append(binding)
            for block in _nested_bodies(node):
                walk(block, nested=True)

    walk(tree.body, nested=False)

    # Appended after the walk, so a governed name declared ``global`` anywhere
    # is always the *last* attempt on that name. That is what stops an earlier
    # top-level ``def`` from surviving as the accepted compiler when the file
    # also reserves the right to reassign it from somewhere else.
    for name in _governed_global_names(tree):
        bindings.setdefault(name, []).append(_Binding(_BindingForm.OTHER))

    return bindings


def claims_to_be_a_pack(tree: ast.Module) -> bool:
    """Does this source claim governed-pack status?

    Any module-scope attempt to bind ``PACK_ID`` is the claim — a literal, a
    computed value, a destructuring assignment, a loop target, a bare
    annotation, even a ``del``. Sitting in the directory is not.

    Deliberately an attempt rather than a success. ``PACK_ID = None``,
    ``PACK_ID = make_pack_id()`` and ``PACK_ID, other = (...)`` are all packs
    that are broken, which is a finding; treating any of them as "not a pack"
    would let the worst case — a governed pack whose identity cannot be read —
    vanish from the inventory in silence.
    """
    return PACK_MARKER_ATTRIBUTE in _module_scope_bindings(tree)


def _describe(
    module_name: str, bindings: dict[str, list[_Binding]]
) -> tuple[KnowledgePackDescriptor | None, tuple[PackValidationError, ...]]:
    """Layer two: accept only the approved declaration forms.

    Layer one has already found every module-scope binding attempt. Here each
    governed name must have exactly one, in exactly the reviewed form —
    ``NAME = "literal"`` (or its annotated equivalent) for a descriptor field,
    a plain top-level ``def`` for the compiler. Everything else is a finding.
    """
    errors: list[PackValidationError] = []

    def note(field: str, code: PackValidationCode) -> None:
        errors.append(PackValidationError(module=module_name, field=field, code=code))

    values: dict[str, str] = {}
    for field in DESCRIPTOR_FIELDS:
        attempts = bindings.get(field, [])
        if len(attempts) > 1:
            note(field, PackValidationCode.DUPLICATE_DECLARATION)
            continue
        if not attempts:
            if field != PACK_MARKER_ATTRIBUTE:
                note(field, _MISSING_CODE_BY_FIELD[field])
            continue
        binding = attempts[0]
        if binding.form is not _BindingForm.LITERAL or binding.value is None:
            note(field, PackValidationCode.NON_STATIC_METADATA)
            continue
        values[field] = binding.value

    pack_id = values.get(PACK_MARKER_ATTRIBUTE)
    if pack_id is not None and not _is_valid_pack_id(pack_id):
        note(PACK_MARKER_ATTRIBUTE, PackValidationCode.INVALID_PACK_ID)
    for field in ("DOMAIN", "CATEGORY", "REASON_KEY"):
        value = values.get(field)
        if value is not None and not _is_clean_nonblank_string(value):
            note(field, _MISSING_CODE_BY_FIELD[field])

    compiler_attempts = bindings.get(COMPILER_ATTRIBUTE, [])
    if not compiler_attempts:
        note(COMPILER_ATTRIBUTE, PackValidationCode.MISSING_COMPILER)
    else:
        if len(compiler_attempts) > 1:
            note(COMPILER_ATTRIBUTE, PackValidationCode.DUPLICATE_DECLARATION)
        # Whatever comes last is what the release workflow would reach for, so
        # a later rebinding or a ``del`` invalidates the earlier ``def``
        # rather than being outvoted by it. A plain top-level ``def`` is the
        # contract: Step 8H calls this synchronously, so a coroutine does not
        # satisfy it and is rejected here rather than at release time.
        if compiler_attempts[-1].form is not _BindingForm.FUNCTION:
            note(COMPILER_ATTRIBUTE, PackValidationCode.COMPILER_NOT_A_FUNCTION)

    if errors:
        return None, tuple(errors)
    return (
        KnowledgePackDescriptor(
            module=module_name,
            pack_id=values[PACK_MARKER_ATTRIBUTE],
            domain=values["DOMAIN"],
            category=values["CATEGORY"],
            reason_key=values["REASON_KEY"],
            compiler_name=COMPILER_ATTRIBUTE,
        ),
        (),
    )


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


def inspect_packs(
    directory: Path | None = None,
    *,
    package_name: str | None = None,
) -> InspectionResult:
    """Parse every candidate in the directory, read its identity, check the set.

    Reading and parsing are the only things done to a candidate. Nothing is
    imported and nothing is executed, so a pack that has grown a top-level
    side effect is inventoried without that side effect happening — which is
    the whole reason this is static.

    Both arguments exist for tests and for nothing else; the defaults are the
    governed pack directory and this module's own package.
    """
    package = _package_name() if package_name is None else package_name
    paths = discover_pack_sources(directory)
    descriptors: list[KnowledgePackDescriptor] = []
    errors: list[PackValidationError] = []

    for path in paths:
        module_name = _module_name(path, package)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append(
                PackValidationError(
                    module=module_name,
                    field="source",
                    code=PackValidationCode.UNREADABLE_SOURCE,
                )
            )
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            # Deliberately not the exception text: a SyntaxError quotes the
            # offending source line, and findings do not carry source.
            errors.append(
                PackValidationError(
                    module=module_name,
                    field="source",
                    code=PackValidationCode.UNPARSABLE_SOURCE,
                )
            )
            continue
        bindings = _module_scope_bindings(tree)
        if PACK_MARKER_ATTRIBUTE not in bindings:
            continue
        descriptor, module_errors = _describe(module_name, bindings)
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
