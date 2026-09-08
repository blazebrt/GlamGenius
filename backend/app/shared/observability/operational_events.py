"""Operational telemetry for the skin-care surfaces. Deliberately almost empty.

This module exists to answer four questions and no others:

* Is label transcription working, and how often is a photo unreadable?
* Is label confirmation working?
* Is the FOR YOU endpoint responding?
* Are any of them getting slower or failing?

It is **operational telemetry, not customer analytics.** Nothing here observes a
person, a product, a decision or a body. There is no account, no device, no
barcode, no ingredient, no verdict, no reason, no citation, no release — and,
above all, nothing about pregnancy, breastfeeding, medication, a diagnosed
condition, age or a child. Step 8K's contract is that ephemeral safety state is
never stored, logged, echoed or counted; a counter grouped by "handoff_required"
would break that contract just as surely as a stored column would, so no such
counter exists and no event here can carry one.

**Callers cannot log arbitrary values.** There is no ``emit_event(name, **kw)``.
Each event has its own typed function, so a field this vocabulary does not know
is a ``TypeError`` at the call site rather than a value that quietly becomes
telemetry. A second, independent allowlist inside ``_emit`` checks the field
names again, and every value is type-checked against what that field is allowed
to be -- ``ingredients_readable`` must be a real ``bool``, so it cannot become a
smuggled ingredient list.

A contract violation raises. That is on purpose: an unknown field or a
wrong-typed value cannot happen without editing this module, so it is a bug that
tests must catch loudly rather than a runtime condition to swallow. Genuine
runtime trouble -- a broken handler, a full disk -- is swallowed, because
telemetry must never take a customer's request down with it and must never
become decision authority.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Final

from app.shared.errors.exceptions import AppError

#: One logger for the whole operational stream, so an operator can route it
#: separately from application logs. The root handler stamps the request id, so
#: correlation needs no identifier of our own -- and adding one would be exactly
#: the customer-linking this module exists to avoid.
logger = logging.getLogger("glamgenius.operational")

# ---------------------------------------------------------------------------
# The closed vocabulary
# ---------------------------------------------------------------------------
EVENT_LABEL_TRANSCRIPTION: Final = "skin_care_label_transcription_completed"
EVENT_LABEL_CONFIRMATION: Final = "skin_care_label_confirmation_completed"
EVENT_FOR_YOU: Final = "skin_care_for_you_completed"


class OperationalOutcome(StrEnum):
    """Whether the operation finished. Not whether the customer got a verdict."""

    COMPLETED = "completed"
    FAILED = "failed"


class OperationalFailureClass(StrEnum):
    """Why it failed, at the coarsest useful grain.

    Three words, chosen so that no exception text, SQL fragment, HTTP body or
    model output is ever needed to describe a failure. An operator learns
    *which kind* of thing broke; the detail lives in the existing global error
    boundary and in the request id, not here.
    """

    DEPENDENCY_ERROR = "dependency_error"
    VALIDATION_ERROR = "validation_error"
    UNEXPECTED_ERROR = "unexpected_error"


#: Exactly which field names each event may carry. Checked independently of the
#: typed signatures above, so widening one without the other still fails.
_ALLOWED_FIELDS: Final[dict[str, frozenset[str]]] = {
    EVENT_LABEL_TRANSCRIPTION: frozenset(
        {"outcome", "duration_ms", "ingredients_readable", "failure_class"}
    ),
    EVENT_LABEL_CONFIRMATION: frozenset(
        {"outcome", "duration_ms", "created", "failure_class"}
    ),
    EVENT_FOR_YOU: frozenset({"outcome", "duration_ms", "failure_class"}),
}

#: Fields every event must carry, with a real value. The optional ones may be
#: ``None``, which means "not observed" and emits nothing; these may not, because
#: an event with no outcome or no duration is not telemetry, it is noise.
_REQUIRED_FIELDS: Final = frozenset({"outcome", "duration_ms"})

#: Which type each field must be. A field whose value is not exactly this is a
#: contract violation, not something to coerce: "ingredients_readable" being a
#: string is how an ingredient list would arrive.
_BOOLEAN_FIELDS: Final = frozenset({"ingredients_readable", "created"})

#: Ten minutes. Long enough for any real request, short enough that a clock
#: anomaly cannot turn into an absurd number in a dashboard.
MAX_DURATION_MS: Final = 600_000


class OperationalEventContractError(RuntimeError):
    """A caller tried to observe something this vocabulary does not permit.

    Never caught inside this module. It means code was written that would emit
    telemetry nobody reviewed, and the only useful response is to fail.
    """


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
class _Stopwatch:
    """Monotonic, so a clock adjustment mid-request cannot produce nonsense."""

    __slots__ = ("_started",)

    def __init__(self) -> None:
        self._started = time.monotonic()

    def duration_ms(self) -> int:
        elapsed = int((time.monotonic() - self._started) * 1000)
        return max(0, min(elapsed, MAX_DURATION_MS))


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------
def _checked_value(event: str, field: str, value: Any) -> str:
    if field in ("outcome",):
        if not isinstance(value, OperationalOutcome):
            raise OperationalEventContractError(
                f"{event}.{field} must be an OperationalOutcome"
            )
        return value.value
    if field == "failure_class":
        if not isinstance(value, OperationalFailureClass):
            raise OperationalEventContractError(
                f"{event}.{field} must be an OperationalFailureClass"
            )
        return value.value
    if field == "duration_ms":
        # bool is a subclass of int; a boolean duration is a bug, not a 0/1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise OperationalEventContractError(f"{event}.{field} must be an int")
        if not 0 <= value <= MAX_DURATION_MS:
            raise OperationalEventContractError(
                f"{event}.{field} must be between 0 and {MAX_DURATION_MS}"
            )
        return str(value)
    if field in _BOOLEAN_FIELDS:
        if not isinstance(value, bool):
            raise OperationalEventContractError(f"{event}.{field} must be a bool")
        return "true" if value else "false"
    raise OperationalEventContractError(f"{event}.{field} has no permitted type")


def _emit(event: str, fields: Mapping[str, Any]) -> None:
    """Render one event as a fixed key=value line, or refuse.

    The contract checks run before anything is written, so a refusal never
    leaves a half-emitted line. Everything after them is swallowed: a logging
    handler that throws is an operational annoyance, not a reason to fail a
    customer's request.
    """
    allowed = _ALLOWED_FIELDS.get(event)
    if allowed is None:
        raise OperationalEventContractError(f"{event} is not a known operational event")
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise OperationalEventContractError(
            f"{event} does not permit the field(s): {', '.join(unknown)}"
        )
    missing = sorted(
        name for name in _REQUIRED_FIELDS if fields.get(name) is None
    )
    if missing:
        raise OperationalEventContractError(
            f"{event} requires: {', '.join(missing)}"
        )
    rendered = [
        f"{name}={_checked_value(event, name, fields[name])}"
        for name in sorted(fields)
        if fields[name] is not None
    ]
    try:
        logger.info("event=%s %s", event, " ".join(rendered))
    except Exception:  # noqa: BLE001 - telemetry never breaks a request
        pass


def classify_failure(error: BaseException) -> OperationalFailureClass:
    """Which of the three words describes this failure.

    Read off the existing error hierarchy's own status code rather than a list
    of exception classes, so a new ``AppError`` subclass classifies correctly
    the day it is written. Nothing about the exception's message, arguments or
    traceback is consulted, and none of it is kept.
    """
    if isinstance(error, AppError):
        status = getattr(error, "status_code", 500)
        if isinstance(status, int) and 400 <= status < 500:
            return OperationalFailureClass.VALIDATION_ERROR
        return OperationalFailureClass.DEPENDENCY_ERROR
    return OperationalFailureClass.UNEXPECTED_ERROR


# ---------------------------------------------------------------------------
# The three observations
# ---------------------------------------------------------------------------
class _TranscriptionObservation:
    """Carries the single safe detail a transcription may report."""

    __slots__ = ("ingredients_readable",)

    def __init__(self) -> None:
        self.ingredients_readable: bool | None = None

    def readable(self, value: bool) -> None:
        """Whether an ingredient list was legible. Never *what* it said."""
        if not isinstance(value, bool):
            raise OperationalEventContractError(
                "ingredients_readable must be a bool"
            )
        self.ingredients_readable = value


class _ConfirmationObservation:
    """Carries the single safe detail a confirmation may report."""

    __slots__ = ("created",)

    def __init__(self) -> None:
        self.created: bool | None = None

    def was_created(self, value: bool) -> None:
        """Whether this confirmation created a new record, or matched one."""
        if not isinstance(value, bool):
            raise OperationalEventContractError("created must be a bool")
        self.created = value


@asynccontextmanager
async def observe_label_transcription() -> AsyncIterator[_TranscriptionObservation]:
    """Time one transcription and record exactly one event for it."""
    watch = _Stopwatch()
    observation = _TranscriptionObservation()
    try:
        yield observation
    except Exception as error:
        _emit(
            EVENT_LABEL_TRANSCRIPTION,
            {
                "outcome": OperationalOutcome.FAILED,
                "duration_ms": watch.duration_ms(),
                "failure_class": classify_failure(error),
            },
        )
        raise
    _emit(
        EVENT_LABEL_TRANSCRIPTION,
        {
            "outcome": OperationalOutcome.COMPLETED,
            "duration_ms": watch.duration_ms(),
            "ingredients_readable": observation.ingredients_readable,
        },
    )


@asynccontextmanager
async def observe_label_confirmation() -> AsyncIterator[_ConfirmationObservation]:
    """Time one confirmation and record exactly one event for it.

    The success event is emitted after the governed write has been committed,
    so nothing here can roll back a confirmation that already happened.
    """
    watch = _Stopwatch()
    observation = _ConfirmationObservation()
    try:
        yield observation
    except Exception as error:
        _emit(
            EVENT_LABEL_CONFIRMATION,
            {
                "outcome": OperationalOutcome.FAILED,
                "duration_ms": watch.duration_ms(),
                "failure_class": classify_failure(error),
            },
        )
        raise
    _emit(
        EVENT_LABEL_CONFIRMATION,
        {
            "outcome": OperationalOutcome.COMPLETED,
            "duration_ms": watch.duration_ms(),
            "created": observation.created,
        },
    )


@asynccontextmanager
async def observe_for_you() -> AsyncIterator[None]:
    """Time one FOR YOU request. Endpoint health only.

    There is deliberately nothing to set. Whether the customer saw a verdict, a
    non-decision or a hand-over to a clinician is not observed here, and no
    object is yielded that could grow such a field by accident.
    """
    watch = _Stopwatch()
    try:
        yield None
    except Exception as error:
        _emit(
            EVENT_FOR_YOU,
            {
                "outcome": OperationalOutcome.FAILED,
                "duration_ms": watch.duration_ms(),
                "failure_class": classify_failure(error),
            },
        )
        raise
    _emit(
        EVENT_FOR_YOU,
        {
            "outcome": OperationalOutcome.COMPLETED,
            "duration_ms": watch.duration_ms(),
        },
    )


__all__ = [
    "EVENT_FOR_YOU",
    "EVENT_LABEL_CONFIRMATION",
    "EVENT_LABEL_TRANSCRIPTION",
    "MAX_DURATION_MS",
    "OperationalEventContractError",
    "OperationalFailureClass",
    "OperationalOutcome",
    "classify_failure",
    "logger",
    "observe_for_you",
    "observe_label_confirmation",
    "observe_label_transcription",
]
