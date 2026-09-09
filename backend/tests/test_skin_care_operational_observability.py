"""Skin-Care Operational Observability & Privacy Guard V1.

Two things are being proven, and only the second one is interesting.

The first is that the new telemetry works: three endpoints emit one bounded
event each, with a duration and at most one boolean.

The second is that it cannot be made to carry anything else. Step 8K's contract
is that ephemeral safety state is never stored, logged, echoed **or counted**,
and a milestone that adds observability is exactly where that contract quietly
breaks — a `handoff_required=true` counter looks like operations and is health
data. So most of what follows is adversarial: sentinel values pushed through the
real Sentry scrubber, forbidden field names pushed at the real event API, and
the real FOR YOU endpoint driven with every safety flag set while the whole
operational stream is watched.

Nothing here mocks the boundary it is testing.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.api.v2 import skin_care_personal_decision as for_you_api
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.personal_decision_release.validation import (
    PersonalDecisionReleaseInvariantError,
)
from app.domains.product import care_extraction
from app.domains.product.personal_decision import CurrentPackSnapshotUnresolved
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import AppError, ValidationFailedError
from app.shared.observability import operational_events
from app.shared.observability.logging import OAuthRedactionFilter
from app.shared.observability.operational_events import (
    EVENT_FOR_YOU,
    EVENT_LABEL_CONFIRMATION,
    EVENT_LABEL_TRANSCRIPTION,
    MAX_DURATION_MS,
    OperationalEventContractError,
    OperationalFailureClass,
    OperationalOutcome,
)
from app.shared.observability.sentry_privacy import REDACTED, scrub_event

from tests.conftest import auth, png_bytes

BACKEND_ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = BACKEND_ROOT / "app" / "shared" / "observability" / "operational_events.py"

TRANSCRIBE_URL = "/api/v2/scan/skin-care/label/transcribe"
CONFIRM_URL = "/api/v2/scan/skin-care/label/confirm"
FOR_YOU_URL = "/api/v2/scan/skin-care/for-you"

BARCODE = "8901030000097"

#: Unmistakable in any output. Each one is placed under the key that would
#: really carry it, so a pass means the scrubber recognises the field rather
#: than that the test put the value somewhere convenient.
EMAIL_SENTINEL = "privacy-test@example.com"
PHONE_SENTINEL = "+91 9876543210"
JWT_SENTINEL = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJQUklWQUNZX1RFU1QiLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
PREGNANCY_SENTINEL = "PREGNANCY_SECRET_SENTINEL"
BREASTFEEDING_SENTINEL = "BREASTFEEDING_SECRET_SENTINEL"
MEDICATION_SENTINEL = "MEDICATION_SECRET_SENTINEL"
DIAGNOSIS_SENTINEL = "DIAGNOSIS_SECRET_SENTINEL"
INGREDIENT_SENTINEL = "INGREDIENT_SECRET_SENTINEL"
CUSTOMER_SENTINEL = "CUSTOMER_SECRET_SENTINEL"
DATABASE_URL_SENTINEL = "postgresql://secret-user:secret-password@secret-host/db"
SNAPSHOT_SENTINEL = "SNAPSHOT_SECRET_SENTINEL"
PRODUCT_SENTINEL = "PRODUCT_SECRET_SENTINEL"
RELEASE_SENTINEL = "RELEASE_SECRET_SENTINEL"

#: Personal context and network identity, which the contract also prohibits.
PROFILE_FACT_KEY = "care_skin_usual_feel"
PROFILE_FACT_VALUE = "often_dry_or_tight"
IPV4_SENTINEL = "203.0.113.42"
IPV6_SENTINEL = "2001:db8::42"
#: A software version, not an address. Redacting this would be a bug.
HARMLESS_VERSION = "2.12.5"

ALL_SENTINELS = (
    EMAIL_SENTINEL,
    PHONE_SENTINEL,
    JWT_SENTINEL,
    PREGNANCY_SENTINEL,
    BREASTFEEDING_SENTINEL,
    MEDICATION_SENTINEL,
    DIAGNOSIS_SENTINEL,
    INGREDIENT_SENTINEL,
    CUSTOMER_SENTINEL,
    DATABASE_URL_SENTINEL,
    "secret-password",
    "secret-user",
    "secret-host",
    SNAPSHOT_SENTINEL,
    PRODUCT_SENTINEL,
    RELEASE_SENTINEL,
)

#: The synthetic pack. Every one of these strings is customer/product content
#: and must never reach the operational stream.
PACK = {
    "product_name": "Synthetic Petrolatum Ointment",
    "brand": "Synthetic Brand",
    "ingredients_text": "Petrolatum, Aqua, Glycerin",
}

#: Every structured safety flag the Step 8K request model accepts, all set.
#: This is the hard-handoff path, and the most dangerous thing to observe.
ALL_SAFETY_FLAGS = {
    "pregnancy": True,
    "breastfeeding": True,
    "medication_involved": True,
    "diagnosed_condition_involved": True,
    "subject_is_child": True,
    "stated_age": 15,
}

#: Field names that must never be accepted by the operational event API.
FORBIDDEN_TELEMETRY_FIELDS = (
    "barcode",
    "account_id",
    "user_id",
    "device_id",
    "ingredients_text",
    "safety",
    "pregnancy",
    "medication_involved",
    "diagnosis",
    "stated_age",
    "scan_id",
    "ai_run_id",
    "media_asset_id",
    "content_fingerprint",
    "source_url",
    "verdict_text",
    "reason_text",
    # Personal context, by container and by fact.
    "profile",
    "profile_facts",
    "personal_context",
    "personal_lens",
    "care_skin_usual_feel",
    # Network identity.
    "ip_address",
    "client_ip",
    "remote_ip",
)


# ---------------------------------------------------------------------------
# Capturing the real operational stream
# ---------------------------------------------------------------------------
@pytest.fixture
def operational_log():
    """Every line the operational logger actually emits, unmodified.

    Attached to the operational logger itself rather than to the root handler,
    so the captured record is what the module wrote — not what a downstream
    redaction filter rescued. Telemetry that needs rescuing has already failed.
    """
    lines: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    # Set the level explicitly rather than inheriting it. Without this the
    # logger's effective level comes from the root, which is only INFO because
    # some earlier test happened to start the app and configure logging — so
    # this class passed in a full run and captured nothing when run alone.
    previous_level = operational_events.logger.level
    operational_events.logger.setLevel(logging.INFO)
    operational_events.logger.addHandler(handler)
    try:
        yield lines
    finally:
        operational_events.logger.removeHandler(handler)
        operational_events.logger.setLevel(previous_level)


def _events(lines: list[str], name: str) -> list[str]:
    return [line for line in lines if line.startswith(f"event={name} ")]


def _fields(line: str) -> dict[str, str]:
    parts = line.split()
    assert parts[0].startswith("event=")
    return dict(part.split("=", 1) for part in parts[1:])


#: The only three lines this stream may ever contain.
KNOWN_EVENTS = (EVENT_LABEL_TRANSCRIPTION, EVENT_LABEL_CONFIRMATION, EVENT_FOR_YOU)


def _assert_only_known_events(lines: list[str]) -> None:
    """Nothing in the operational stream except the three reviewed events.

    The load-bearing assertion. Checking the *fields* of a recognised event
    cannot see a developer who bypasses the typed API and writes
    ``logger.info("ai_run=%s", ...)`` on a line of its own — and an opaque
    identifier matches no banned word, so a vocabulary check would miss it too.
    Requiring that every line be a known event closes that off by construction,
    for identifiers nobody has thought of yet as much as for the ones here.
    """
    for line in lines:
        assert any(
            line.startswith(f"event={name} ") for name in KNOWN_EVENTS
        ), f"unrecognised line in the operational stream: {line!r}"


#: Fragments that may never appear anywhere, in any form.
_FORBIDDEN_FRAGMENTS = (
    *ALL_SENTINELS,
    *PACK.values(),
    PROFILE_FACT_VALUE,
    IPV4_SENTINEL,
    IPV6_SENTINEL,
    "petrolatum",
    "pregnan",
    "breastfeed",
    "medication",
    "diagnos",
    "handoff",
    "barcode",
)

#: Short words that must be matched whole. `age` as a bare substring also
#: appears in `message`, `usage` and `storage`, and `wait` in `waiting` -- the
#: same class of accident this milestone is meant to avoid, so it is not
#: repeated in the assertion that checks for it.
_FORBIDDEN_WORDS = (
    "age", "child", "account", "device", "verdict", "buy", "wait", "skip",
    "profile", "ip", "address",
)


def _assert_nothing_sensitive(text: str) -> None:
    """No customer, product, personal-context or health content in this text."""
    lowered = text.lower()
    for forbidden in _FORBIDDEN_FRAGMENTS:
        assert forbidden.lower() not in lowered, f"{forbidden!r} leaked into: {text!r}"
    for word in _FORBIDDEN_WORDS:
        assert not re.search(rf"\b{re.escape(word)}\b", lowered), (
            f"{word!r} leaked into: {text!r}"
        )


# ---------------------------------------------------------------------------
# Endpoint helpers — the real routes
# ---------------------------------------------------------------------------
async def _register_device(app_client) -> dict[str, str]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


async def _claim(app_client, headers, token) -> None:
    claimed = await app_client.post(
        "/api/v2/scan/device/claim", headers={**headers, **auth(token)},
    )
    assert claimed.status_code == 200, claimed.text


async def _seed_run(payload: dict[str, Any], account_id: uuid.UUID) -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature=care_extraction.FEATURE, provider="test",
            model="test-model", prompt_version=care_extraction.PROMPT_VERSION,
            schema_version=care_extraction.SCHEMA_VERSION,
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(
            ai_run_id=run.id, schema_version=care_extraction.SCHEMA_VERSION, payload=payload,
        ))
        await session.commit()
        return run.id


async def _upload(app_client, token) -> str:
    asset = await app_client.post(
        "/api/v2/media/upload", headers=auth(token),
        files={"file": ("label.png", png_bytes(), "image/png")},
    )
    assert asset.status_code in (200, 201), asset.text
    return asset.json()["id"]


async def _confirmed_pack(app_client, registered_supabase_user) -> dict[str, Any]:
    """An account with a claimed device holding one confirmed skin-care pack."""
    token, account_id = await registered_supabase_user()
    headers = await _register_device(app_client)
    await _claim(app_client, headers, token)
    run_id = await _seed_run(
        {**PACK, "product_type": "Ointment", "confidence": 0.9, "uncertain_fields": []},
        account_id,
    )
    confirmed = await app_client.post(
        CONFIRM_URL, headers={**headers, **auth(token)},
        json={
            "barcode": BARCODE,
            "ai_run_id": str(run_id),
            "client_scan_id": uuid.uuid4().hex,
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    return {"token": token, "headers": headers, "account_id": account_id}


# ---------------------------------------------------------------------------
# The Sentry privacy boundary
# ---------------------------------------------------------------------------
class TestSentryScrubbing:
    def test_an_adversarial_serialized_request_survives_nothing(self) -> None:
        """Every sentinel, under the key that would really carry it."""
        event = {
            "message": f"upstream failed connecting to {DATABASE_URL_SENTINEL}",
            "user": {"email": EMAIL_SENTINEL, "phone": PHONE_SENTINEL},
            "request": {
                "headers": {"Authorization": f"Bearer {JWT_SENTINEL}"},
                "data": {
                    "barcode": BARCODE,
                    "safety": {
                        "pregnancy": PREGNANCY_SENTINEL,
                        "breastfeeding": BREASTFEEDING_SENTINEL,
                        "medication_involved": MEDICATION_SENTINEL,
                        "diagnosed_condition_involved": DIAGNOSIS_SENTINEL,
                        "stated_age": 15,
                    },
                },
            },
            "contexts": {
                "label": {"ingredients_text": INGREDIENT_SENTINEL},
                "decision": {
                    "verdict_text": CUSTOMER_SENTINEL,
                    "reason_text": CUSTOMER_SENTINEL,
                },
            },
            "extra": {
                "database_url": DATABASE_URL_SENTINEL,
                "note": f"contact {EMAIL_SENTINEL} on {PHONE_SENTINEL}",
            },
        }

        rendered = json.dumps(scrub_event(event))

        for sentinel in ALL_SENTINELS:
            assert sentinel not in rendered, f"{sentinel!r} survived scrubbing"
        assert BARCODE not in rendered
        assert REDACTED in rendered

    def test_a_nested_safety_subtree_is_redacted_whole(self) -> None:
        """The exact shape a serialized Step 8K request would take."""
        event = {
            "request": {
                "data": {
                    "safety": {
                        "pregnancy": True,
                        "medication_involved": True,
                        "stated_age": 17,
                    }
                }
            }
        }

        scrubbed = scrub_event(event)

        assert scrubbed["request"]["data"]["safety"] == REDACTED
        rendered = json.dumps(scrubbed)
        assert "pregnancy" not in rendered
        assert "medication_involved" not in rendered
        assert "17" not in rendered

    @pytest.mark.parametrize(
        "key",
        [
            "safety", "pregnancy", "pregnant", "breastfeeding", "medication_involved",
            "medicine", "medical_condition", "diagnosis", "diagnosed_condition_involved",
            "symptoms", "conditions", "stated_age", "age", "subject_is_child",
            "handoff", "handoff_reason", "handoff_message",
        ],
    )
    def test_every_health_and_safety_key_is_redacted(self, key: str) -> None:
        scrubbed = scrub_event({"extra": {key: "LEAKED_HEALTH_STATE"}})
        assert scrubbed["extra"][key] == REDACTED

    @pytest.mark.parametrize(
        "key",
        [
            "barcode", "account_id", "user_id", "device_id", "client_scan_id",
            "scan_event_id", "ai_run_id", "media_asset_id", "label_snapshot_id",
            "content_fingerprint", "product_name", "brand", "product_type",
            "verdict_text", "reason_text", "source_url", "canonical_url",
        ],
    )
    def test_every_identifying_key_is_redacted(self, key: str) -> None:
        scrubbed = scrub_event({"extra": {key: "LEAKED_IDENTIFIER"}})
        assert scrubbed["extra"][key] == REDACTED

    @pytest.mark.parametrize(
        "key",
        [
            "status", "reason", "message", "code", "outcome", "duration_ms",
            "page", "usage", "language", "storage_used", "http_status",
            "endpoint", "method", "failure_class", "request_id",
        ],
    )
    def test_generic_operational_keys_are_not_redacted(self, key: str) -> None:
        """Blanket redaction would blind every crash report to protect nothing."""
        scrubbed = scrub_event({"extra": {key: "operational-detail"}})
        assert scrubbed["extra"][key] == "operational-detail"

    def test_existing_oauth_and_credential_redaction_still_holds(self) -> None:
        """The hardening must not have displaced what was already protected."""
        event = {
            "extra": {
                "access_token": JWT_SENTINEL,
                "SUPABASE_SERVICE_ROLE_KEY": "sbp_live_service_role_value",
                "gemini_api_key": "AIza-not-a-real-key",
            },
            "message": f"callback ?code=abc123&state=xyz789 for {JWT_SENTINEL}",
        }

        scrubbed = scrub_event(event)

        assert scrubbed["extra"]["access_token"] == REDACTED
        assert scrubbed["extra"]["SUPABASE_SERVICE_ROLE_KEY"] == REDACTED
        assert scrubbed["extra"]["gemini_api_key"] == REDACTED
        assert JWT_SENTINEL not in scrubbed["message"]
        assert "abc123" not in scrubbed["message"]
        assert "xyz789" not in scrubbed["message"]


class TestLoggingBoundary:
    def test_a_credentialed_url_never_reaches_a_handler(self) -> None:
        record = logging.LogRecord(
            "t", logging.ERROR, "", 1, "connect failed: %s", (DATABASE_URL_SENTINEL,), None,
        )
        OAuthRedactionFilter().filter(record)
        message = record.getMessage()
        assert "secret-password" not in message
        assert "secret-host" not in message
        assert "[REDACTED]" in message

    def test_oauth_redaction_is_preserved(self) -> None:
        record = logging.LogRecord(
            "t", logging.ERROR, "", 1, "auth %s", (f"Bearer {JWT_SENTINEL}",), None,
        )
        OAuthRedactionFilter().filter(record)
        assert JWT_SENTINEL not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()


# ---------------------------------------------------------------------------
# The event contract
# ---------------------------------------------------------------------------
class TestEventContract:
    def test_the_vocabulary_is_exactly_three_events(self) -> None:
        assert set(operational_events._ALLOWED_FIELDS) == {
            EVENT_LABEL_TRANSCRIPTION,
            EVENT_LABEL_CONFIRMATION,
            EVENT_FOR_YOU,
        }

    def test_each_event_permits_only_its_documented_fields(self) -> None:
        allowed = operational_events._ALLOWED_FIELDS
        assert allowed[EVENT_LABEL_TRANSCRIPTION] == frozenset(
            {"outcome", "duration_ms", "ingredients_readable", "failure_class"}
        )
        assert allowed[EVENT_LABEL_CONFIRMATION] == frozenset(
            {"outcome", "duration_ms", "created", "failure_class"}
        )
        assert allowed[EVENT_FOR_YOU] == frozenset(
            {"outcome", "duration_ms", "failure_class"}
        )

    @pytest.mark.parametrize("field", FORBIDDEN_TELEMETRY_FIELDS)
    @pytest.mark.parametrize(
        "event", [EVENT_LABEL_TRANSCRIPTION, EVENT_LABEL_CONFIRMATION, EVENT_FOR_YOU]
    )
    def test_an_unsafe_field_is_refused_not_dropped(self, event: str, field: str) -> None:
        """Refused loudly. Silently dropping it would let a developer believe
        it was being observed safely."""
        with pytest.raises(OperationalEventContractError) as exc_info:
            operational_events._emit(
                event,
                {
                    "outcome": OperationalOutcome.COMPLETED,
                    "duration_ms": 5,
                    field: "SHOULD_NEVER_BE_EMITTED",
                },
            )
        assert field in str(exc_info.value)

    def test_an_unknown_event_name_is_refused(self) -> None:
        with pytest.raises(OperationalEventContractError):
            operational_events._emit("customer_funnel_step", {"duration_ms": 1})

    @pytest.mark.parametrize(
        "value", ["Petrolatum, Aqua", 1, {"ingredients": "x"}, ["a"]]
    )
    def test_a_boolean_field_refuses_anything_but_a_bool(self, value: Any) -> None:
        """`ingredients_readable="Petrolatum"` is how an ingredient list arrives."""
        with pytest.raises(OperationalEventContractError):
            operational_events._emit(
                EVENT_LABEL_TRANSCRIPTION,
                {
                    "outcome": OperationalOutcome.COMPLETED,
                    "duration_ms": 5,
                    "ingredients_readable": value,
                },
            )

    @pytest.mark.parametrize("value", ["12", True, -1, 10**9, 1.5, None])
    def test_duration_must_be_a_bounded_non_negative_int(self, value: Any) -> None:
        with pytest.raises(OperationalEventContractError):
            operational_events._emit(
                EVENT_FOR_YOU,
                {"outcome": OperationalOutcome.COMPLETED, "duration_ms": value},
            )

    @pytest.mark.parametrize("value", ["completed", 1, None])
    def test_outcome_must_be_the_enum(self, value: Any) -> None:
        with pytest.raises(OperationalEventContractError):
            operational_events._emit(
                EVENT_FOR_YOU, {"outcome": value, "duration_ms": 1},
            )

    @pytest.mark.parametrize("value", ["boom", "ValueError: secret", 1])
    def test_failure_class_must_be_the_enum_not_an_exception_string(
        self, value: Any
    ) -> None:
        with pytest.raises(OperationalEventContractError):
            operational_events._emit(
                EVENT_FOR_YOU,
                {
                    "outcome": OperationalOutcome.FAILED,
                    "duration_ms": 1,
                    "failure_class": value,
                },
            )

    @pytest.mark.parametrize("field", FORBIDDEN_TELEMETRY_FIELDS)
    def test_the_observation_objects_expose_no_unsafe_setter(self, field: str) -> None:
        transcription = operational_events._TranscriptionObservation()
        confirmation = operational_events._ConfirmationObservation()
        for observation in (transcription, confirmation):
            assert not hasattr(observation, field)
            # __slots__ makes even an accidental assignment impossible.
            with pytest.raises(AttributeError):
                setattr(observation, field, "SHOULD_NEVER_BE_EMITTED")

    def test_no_public_function_accepts_arbitrary_keyword_arguments(self) -> None:
        """There must be no `emit_event(name, **anything)` seam."""
        tree = ast.parse(EVENTS_PATH.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.args.kwarg is not None or node.args.vararg is not None
            ):
                offenders.append(node.name)
        assert offenders == []

    def test_classify_failure_never_reads_the_exception_text(self) -> None:
        secret = RuntimeError(DATABASE_URL_SENTINEL)
        assert (
            operational_events.classify_failure(secret)
            is OperationalFailureClass.UNEXPECTED_ERROR
        )
        assert (
            operational_events.classify_failure(ValidationFailedError("x", field="f"))
            is OperationalFailureClass.VALIDATION_ERROR
        )
        assert (
            operational_events.classify_failure(
                AppError("unavailable", status_code=503)
            )
            is OperationalFailureClass.DEPENDENCY_ERROR
        )

    def test_the_failure_vocabulary_is_three_safe_words(self) -> None:
        assert {c.value for c in OperationalFailureClass} == {
            "dependency_error",
            "validation_error",
            "unexpected_error",
        }


# ---------------------------------------------------------------------------
# The real endpoints
# ---------------------------------------------------------------------------
class TestTranscriptionTelemetry:
    async def test_a_readable_transcription_emits_one_safe_event(
        self, db_clean, app_client, registered_supabase_user, fake_provider,
        media_root, operational_log,
    ) -> None:
        fake_provider.text = json.dumps(
            {**PACK, "product_type": "Ointment", "confidence": 0.93, "uncertain_fields": []}
        )
        token, _ = await registered_supabase_user()
        asset_id = await _upload(app_client, token)

        response = await app_client.post(
            TRANSCRIBE_URL, headers=auth(token),
            json={"barcode": BARCODE, "media_asset_id": asset_id},
        )
        assert response.status_code == 200, response.text
        # The response itself is unchanged — the person still sees what was read.
        assert response.json()["facts"]["ingredients_text"] == PACK["ingredients_text"]

        events = _events(operational_log, EVENT_LABEL_TRANSCRIPTION)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "completed"
        assert fields["ingredients_readable"] == "true"
        assert 0 <= int(fields["duration_ms"]) <= operational_events.MAX_DURATION_MS
        assert set(fields) == {"outcome", "ingredients_readable", "duration_ms"}
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))

    async def test_an_unreadable_transcription_reports_only_that(
        self, db_clean, app_client, registered_supabase_user, fake_provider,
        media_root, operational_log,
    ) -> None:
        fake_provider.text = json.dumps({
            "product_name": "Synthetic Petrolatum Ointment",
            "brand": "Synthetic Brand",
            "product_type": "Ointment",
            "confidence": 0.4,
            "uncertain_fields": ["ingredients_text"],
            "photo_quality_notes": "blurred label, glare across the ingredient panel",
        })
        token, _ = await registered_supabase_user()
        asset_id = await _upload(app_client, token)

        response = await app_client.post(
            TRANSCRIBE_URL, headers=auth(token),
            json={"barcode": BARCODE, "media_asset_id": asset_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ingredients_readable"] is False

        events = _events(operational_log, EVENT_LABEL_TRANSCRIPTION)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["ingredients_readable"] == "false"
        assert set(fields) == {"outcome", "ingredients_readable", "duration_ms"}
        # Neither the extracted facts nor the model's notes about the photo.
        stream = "\n".join(operational_log)
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive(stream)
        assert "blurred" not in stream
        assert "glare" not in stream

    async def test_a_failed_transcription_reports_a_class_not_a_message(
        self, db_clean, app_client, registered_supabase_user, media_root,
        operational_log, monkeypatch,
    ) -> None:
        async def _explode(*args: Any, **kwargs: Any):
            raise RuntimeError(
                f"provider died: {DATABASE_URL_SENTINEL} while reading "
                f"{PACK['ingredients_text']}"
            )

        monkeypatch.setattr(care_extraction, "transcribe_label", _explode)
        token, _ = await registered_supabase_user()
        asset_id = await _upload(app_client, token)

        with pytest.raises(RuntimeError):
            await app_client.post(
                TRANSCRIBE_URL, headers=auth(token),
                json={"barcode": BARCODE, "media_asset_id": asset_id},
            )

        events = _events(operational_log, EVENT_LABEL_TRANSCRIPTION)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "failed"
        assert fields["failure_class"] == "unexpected_error"
        assert set(fields) == {"outcome", "failure_class", "duration_ms"}
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))


class TestConfirmationTelemetry:
    async def test_a_successful_confirmation_emits_one_safe_event(
        self, db_clean, app_client, registered_supabase_user, operational_log,
    ) -> None:
        pack = await _confirmed_pack(app_client, registered_supabase_user)
        assert pack["token"]

        events = _events(operational_log, EVENT_LABEL_CONFIRMATION)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "completed"
        assert fields["created"] == "true"
        assert set(fields) == {"outcome", "created", "duration_ms"}
        # Neither what was sent nor what was returned.
        stream = "\n".join(operational_log)
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive(stream)
        assert "fingerprint" not in stream
        assert "snapshot" not in stream

    async def test_a_refused_confirmation_reports_a_class_only(
        self, db_clean, app_client, registered_supabase_user, operational_log,
    ) -> None:
        token, account_id = await registered_supabase_user()
        headers = await _register_device(app_client)
        await _claim(app_client, headers, token)

        response = await app_client.post(
            CONFIRM_URL, headers={**headers, **auth(token)},
            json={
                "barcode": BARCODE,
                "ai_run_id": str(uuid.uuid4()),  # no such transcription
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        assert response.status_code >= 400

        events = _events(operational_log, EVENT_LABEL_CONFIRMATION)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "failed"
        assert fields["failure_class"] in {c.value for c in OperationalFailureClass}
        assert "created" not in fields
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))


class TestForYouTelemetry:
    async def test_a_for_you_request_reports_only_endpoint_health(
        self, db_clean, app_client, registered_supabase_user, operational_log,
    ) -> None:
        pack = await _confirmed_pack(app_client, registered_supabase_user)
        operational_log.clear()

        response = await app_client.post(
            FOR_YOU_URL, headers={**pack["headers"], **auth(pack["token"])},
            json={"barcode": BARCODE},
        )
        assert response.status_code == 200, response.text

        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "completed"
        assert set(fields) == {"outcome", "duration_ms"}
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))

    async def test_every_safety_flag_set_changes_nothing_that_is_observed(
        self, db_clean, app_client, registered_supabase_user, operational_log,
    ) -> None:
        """The hard-handoff path. The response may carry its governed contract;
        the telemetry may carry nothing about it at all."""
        pack = await _confirmed_pack(app_client, registered_supabase_user)
        operational_log.clear()

        response = await app_client.post(
            FOR_YOU_URL, headers={**pack["headers"], **auth(pack["token"])},
            json={"barcode": BARCODE, "safety": ALL_SAFETY_FLAGS},
        )
        assert response.status_code == 200, response.text

        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 1
        fields = _fields(events[0])
        # Byte-for-byte the same shape as the request with no flags at all.
        assert set(fields) == {"outcome", "duration_ms"}
        assert fields["outcome"] == "completed"

        stream = "\n".join(operational_log)
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive(stream)
        # Structural, not numeric. `duration_ms=15` is legitimate telemetry and
        # `duration_ms=150` contains "15" too, so banning the digits that happen
        # to spell an age would fail on a fast request and prove nothing about
        # privacy. What matters is that no health-derived *key* exists at all.
        for flag in ALL_SAFETY_FLAGS:
            assert flag not in stream
        for key in (
            "stated_age", "age", "subject_is_child", "pregnancy", "breastfeeding",
            "medication_involved", "diagnosed_condition_involved", "handoff",
        ):
            assert key not in fields
            assert f"{key}=" not in stream
        # And nothing survives the Sentry boundary either.
        scrubbed = json.dumps(
            scrub_event({"request": {"data": {"barcode": BARCODE, "safety": ALL_SAFETY_FLAGS}}})
        )
        for flag in ALL_SAFETY_FLAGS:
            assert flag not in scrubbed

    async def test_no_event_is_counted_per_safety_state(
        self, db_clean, app_client, registered_supabase_user, operational_log,
    ) -> None:
        """Three requests, three different safety states, three identical events.

        If any counter were grouped by health state, these would differ.
        """
        pack = await _confirmed_pack(app_client, registered_supabase_user)
        headers = {**pack["headers"], **auth(pack["token"])}
        operational_log.clear()

        for safety in (None, {"pregnancy": True}, ALL_SAFETY_FLAGS):
            payload: dict[str, Any] = {"barcode": BARCODE}
            if safety is not None:
                payload["safety"] = safety
            assert (await app_client.post(FOR_YOU_URL, headers=headers, json=payload)).status_code == 200

        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 3
        shapes = {frozenset(_fields(line)) for line in events}
        assert shapes == {frozenset({"outcome", "duration_ms"})}
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))

    async def test_a_failed_for_you_request_reports_a_class_only(
        self, db_clean, app_client, registered_supabase_user, operational_log,
    ) -> None:
        """A device that owns nothing: refused before any decision is reached."""
        token, _ = await registered_supabase_user()
        other_token, _ = await registered_supabase_user()
        headers = await _register_device(app_client)
        await _claim(app_client, headers, token)
        operational_log.clear()

        response = await app_client.post(
            FOR_YOU_URL, headers={**headers, **auth(other_token)},
            json={"barcode": BARCODE, "safety": ALL_SAFETY_FLAGS},
        )
        assert response.status_code >= 400

        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "failed"
        assert set(fields) == {"outcome", "failure_class", "duration_ms"}
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))


# ---------------------------------------------------------------------------
# The application log — what a caught exception must not carry into it
# ---------------------------------------------------------------------------
@pytest.fixture
def application_log():
    """Every line the real application log path would produce.

    Attached to the root logger with the production redaction filter, because
    that is where a route's `logger.error(...)` actually ends up. The filter
    materialises `exc_info` into text exactly as it does in production, so if
    anybody restores `logger.exception(...)` the traceback lands here in full
    and the assertions below see it.
    """
    lines: list[str] = []
    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    redactor = OAuthRedactionFilter()

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            redactor.filter(record)
            lines.append(formatter.format(record))

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


class TestCaughtExceptionsNeverReachTheLog:
    async def test_an_unresolved_snapshot_logs_a_name_and_nothing_else(
        self, db_clean, app_client, registered_supabase_user,
        operational_log, application_log, monkeypatch,
    ) -> None:
        """The exact leak independent review found.

        `CurrentPackSnapshotUnresolved` really does say "label snapshot <uuid>
        does not match the capture that created it". Dropping the identifiers
        from the format string left `logger.exception` attaching `exc_info`, so
        the uuid reached the log through the traceback instead. The customer
        gets a governed fixed 503 either way, so the exception text buys
        nothing the request id does not.
        """
        pack = await _confirmed_pack(app_client, registered_supabase_user)

        async def _explode(session: Any, *, pack: Any):
            raise CurrentPackSnapshotUnresolved(
                f"label snapshot {SNAPSHOT_SENTINEL} does not match {PRODUCT_SENTINEL}"
            )

        monkeypatch.setattr(
            for_you_api, "resolve_current_pack_label_snapshot", _explode
        )
        operational_log.clear()
        application_log.clear()

        response = await app_client.post(
            FOR_YOU_URL, headers={**pack["headers"], **auth(pack["token"])},
            json={"barcode": BARCODE},
        )

        # The governed response is unchanged: still fail-closed, still 503.
        assert response.status_code == 503, response.text
        assert SNAPSHOT_SENTINEL not in response.text
        assert PRODUCT_SENTINEL not in response.text

        logged = "\n".join(application_log)
        assert "for_you_snapshot_unresolved" in logged
        assert SNAPSHOT_SENTINEL not in logged
        assert PRODUCT_SENTINEL not in logged
        assert "does not match" not in logged
        assert "Traceback" not in logged
        assert "CurrentPackSnapshotUnresolved" not in logged

        # And the operational stream carries only its safe failed event.
        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields["outcome"] == "failed"
        assert set(fields) == {"outcome", "failure_class", "duration_ms"}
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))

    async def test_an_invalid_active_release_logs_a_name_and_nothing_else(
        self, db_clean, app_client, registered_supabase_user,
        operational_log, application_log, monkeypatch,
    ) -> None:
        """The same treatment for the other caught invariant.

        `PersonalDecisionReleaseInvariantError` carries manifest and rule detail
        in its message, which is exactly what must not describe a governed 503.
        """
        pack = await _confirmed_pack(app_client, registered_supabase_user)

        async def _explode(session: Any):
            raise PersonalDecisionReleaseInvariantError(
                f"release manifest {RELEASE_SENTINEL} does not hash to its recorded value"
            )

        monkeypatch.setattr(
            for_you_api, "load_active_personal_decision_release", _explode
        )
        operational_log.clear()
        application_log.clear()

        response = await app_client.post(
            FOR_YOU_URL, headers={**pack["headers"], **auth(pack["token"])},
            json={"barcode": BARCODE},
        )
        assert response.status_code == 503, response.text
        assert RELEASE_SENTINEL not in response.text

        logged = "\n".join(application_log)
        assert "for_you_active_release_invalid" in logged
        assert RELEASE_SENTINEL not in logged
        assert "does not hash" not in logged
        assert "Traceback" not in logged
        assert "PersonalDecisionReleaseInvariantError" not in logged

        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 1
        assert _fields(events[0])["outcome"] == "failed"
        _assert_only_known_events(operational_log)

    def test_the_for_you_route_never_calls_logger_exception(self) -> None:
        """A static backstop for the two tests above.

        `logger.exception` is the leak: it attaches `exc_info` whatever the
        format string says. There is no caught invariant in this route whose
        traceback the customer's governed 503 needs.
        """
        tree = ast.parse(
            (BACKEND_ROOT / "app" / "api" / "v2" / "skin_care_personal_decision.py")
            .read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = ast.unparse(node.func)
                assert target != "logger.exception", ast.unparse(node)
                if target.startswith("logger."):
                    # And every log call takes a fixed string, never a value.
                    assert node.args and isinstance(node.args[0], ast.Constant), (
                        ast.unparse(node)
                    )
                    assert len(node.args) == 1, ast.unparse(node)
                    assert not node.keywords, ast.unparse(node)


# ---------------------------------------------------------------------------
# Personal context and network identity at the Sentry boundary
# ---------------------------------------------------------------------------
class TestProfileFactsAndAddresses:
    def test_the_reviewed_adversarial_payload_survives_nothing(self) -> None:
        """Exactly the payload independent review asked for."""
        event = {
            "user": {"ip_address": IPV4_SENTINEL},
            "request": {
                "headers": {
                    "X-Forwarded-For": IPV4_SENTINEL,
                    "X-Real-IP": IPV6_SENTINEL,
                },
                "data": {"profile_facts": {PROFILE_FACT_KEY: PROFILE_FACT_VALUE}},
            },
            "contexts": {"personal_context": {PROFILE_FACT_KEY: PROFILE_FACT_VALUE}},
            "message": f"upstream peer {IPV4_SENTINEL} failed",
        }

        rendered = json.dumps(scrub_event(event))

        for forbidden in (IPV4_SENTINEL, IPV6_SENTINEL, PROFILE_FACT_VALUE, PROFILE_FACT_KEY):
            assert forbidden not in rendered, f"{forbidden!r} survived scrubbing"
        assert REDACTED in rendered

    @pytest.mark.parametrize(
        "key", ["profile", "profile_facts", "personal_context", "personal_lens"]
    )
    def test_a_profile_container_is_redacted_whole(self, key: str) -> None:
        """The container, not an enumeration of the facts inside it.

        A fact nobody has invented yet is covered on the day it is added.
        """
        scrubbed = scrub_event(
            {"contexts": {key: {PROFILE_FACT_KEY: PROFILE_FACT_VALUE, "future_fact": "x"}}}
        )
        assert scrubbed["contexts"][key] == REDACTED
        rendered = json.dumps(scrubbed)
        assert PROFILE_FACT_KEY not in rendered
        assert "future_fact" not in rendered

    def test_a_bare_profile_fact_is_redacted_too(self) -> None:
        scrubbed = scrub_event({"extra": {PROFILE_FACT_KEY: PROFILE_FACT_VALUE}})
        assert scrubbed["extra"][PROFILE_FACT_KEY] == REDACTED

    @pytest.mark.parametrize(
        "key",
        [
            "ip_address", "client_ip", "remote_ip", "remote_addr",
            "x_forwarded_for", "X-Forwarded-For", "x_real_ip", "X-Real-IP",
        ],
    )
    def test_every_address_key_is_redacted(self, key: str) -> None:
        scrubbed = scrub_event({"request": {"headers": {key: IPV4_SENTINEL}}})
        assert scrubbed["request"]["headers"][key] == REDACTED

    @pytest.mark.parametrize(
        "address",
        [
            "203.0.113.42", "8.8.8.8", "10.0.0.1", "255.255.255.255",
            "2001:db8::42", "2001:0db8:0000:0000:0000:0000:0000:0042",
            "::1", "fe80::1", "2001:db8:85a3::8a2e:370:7334",
            # IPv6 with an embedded dotted IPv4 tail. Both are ordinary
            # addresses a proxy or resolver will happily write into a message.
            "::ffff:192.0.2.128", "2001:db8::192.0.2.33",
            "0:0:0:0:0:ffff:192.1.56.10",
        ],
    )
    def test_an_address_inside_free_text_is_redacted(self, address: str) -> None:
        """A proxy header echoed into an exception is still an address."""
        scrubbed = scrub_event({"message": f"upstream peer {address} refused the connection"})
        assert address not in scrubbed["message"]
        assert REDACTED in scrubbed["message"]

    @pytest.mark.parametrize(
        "harmless",
        [
            "2.12.5", "1.0.0", "0.3.5", "16.4", "v2.12.5",
            "sentry-sdk 2.12.5 initialised", "python 3.11.9", "postgres 16.4",
            "took 12.5 ms", "99.9% availability",
        ],
    )
    def test_a_version_number_is_not_mistaken_for_an_address(self, harmless: str) -> None:
        """Redacting these would destroy exactly the diagnostics we kept."""
        scrubbed = scrub_event({"extra": {"note": harmless}})
        assert scrubbed["extra"]["note"] == harmless

    @pytest.mark.parametrize(
        "not_an_address", ["12:34", "00:57:11", "de:ad:be:ef:ca:fe", "abc", "1.2.3"]
    )
    def test_a_colon_separated_value_that_is_not_an_address_survives(
        self, not_an_address: str
    ) -> None:
        scrubbed = scrub_event({"extra": {"note": f"saw {not_an_address} here"}})
        assert not_an_address in scrubbed["extra"]["note"]


# ---------------------------------------------------------------------------
# IPv6 addresses that carry a dotted IPv4 tail
# ---------------------------------------------------------------------------
#: The IPv4 part of the mapped addresses below. Named on its own because the
#: point of these tests is that it must not be left behind after the IPv6
#: prefix is redacted -- a surviving dotted quad still names the endpoint.
MAPPED_IPV4_TAIL = "192.0.2.128"
EMBEDDED_IPV4_TAIL = "192.0.2.33"
MAPPED_IPV6 = f"::ffff:{MAPPED_IPV4_TAIL}"
EMBEDDED_IPV6 = f"2001:db8::{EMBEDDED_IPV4_TAIL}"


class TestMixedIPv6AndDottedIPv4:
    """One address must produce one redaction, with nothing left over.

    Independent review found that the candidate extractor's IPv6 branch
    excluded ``.``, so an address with an embedded IPv4 part could not be
    matched whole. The two halves were matched separately instead, and the
    failure was not symmetrical:

    * ``::ffff:192.0.2.128`` became two redactions where one address had been
    * ``2001:db8::192.0.2.33`` became ``2001:db8::[Redacted]``, leaving the
      network prefix in the clear

    So asserting only "the full original string is absent" would have passed
    against the broken code in both cases. These tests assert the exact
    scrubbed output instead: the whole address gone, replaced by exactly one
    marker, with the surrounding sentence intact.

    Everything goes through the real ``scrub_event`` boundary rather than the
    candidate helper, because the boundary is what Sentry actually calls.
    """

    @pytest.mark.parametrize("address", [MAPPED_IPV6, EMBEDDED_IPV6])
    def test_a_mixed_address_alone_is_replaced_by_exactly_one_marker(
        self, address: str
    ) -> None:
        scrubbed = scrub_event({"extra": {"note": address}})["extra"]["note"]
        assert scrubbed == REDACTED

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (f"peer={MAPPED_IPV6}", f"peer={REDACTED}"),
            (f"peer={EMBEDDED_IPV6}", f"peer={REDACTED}"),
            (f"[{MAPPED_IPV6}]:443", f"[{REDACTED}]:443"),
            (f"upstream {EMBEDDED_IPV6} failed", f"upstream {REDACTED} failed"),
            # An address that ends a sentence picks up the full stop.
            (f"connect to {MAPPED_IPV6}.", f"connect to {REDACTED}."),
        ],
    )
    def test_realistic_surrounding_syntax_keeps_only_the_syntax(
        self, raw: str, expected: str
    ) -> None:
        assert scrub_event({"extra": {"note": raw}})["extra"]["note"] == expected

    @pytest.mark.parametrize(
        ("address", "tail"),
        [(MAPPED_IPV6, MAPPED_IPV4_TAIL), (EMBEDDED_IPV6, EMBEDDED_IPV4_TAIL)],
    )
    def test_no_fragment_of_a_mixed_address_survives(
        self, address: str, tail: str
    ) -> None:
        """The four properties, stated one at a time.

        The third is the one the old code failed: a redaction that leaves
        ``[Redacted by application]:192.0.2.128`` behind has redacted a prefix
        and published an endpoint.
        """
        sentence = f"ConnectionResetError: upstream {address} closed the connection"
        scrubbed = scrub_event({"exception": {"value": sentence}})["exception"]["value"]

        # 1. the original address text is gone
        assert address not in scrubbed
        # 2. the dotted IPv4 tail is not left behind
        assert tail not in scrubbed
        # 3. no fragment of the IPv6 side survives either
        for fragment in ("::ffff", "2001:db8", "db8::"):
            if fragment in address:
                assert fragment not in scrubbed
        # 4. a marker is present and the rest of the sentence is still usable
        assert REDACTED in scrubbed
        assert scrubbed == f"ConnectionResetError: upstream {REDACTED} closed the connection"

    def test_a_mapped_address_inside_a_real_exception_message(self) -> None:
        """The shape this actually arrives in: an exception, nested in an event."""
        event = {
            "exception": {
                "values": [
                    {
                        "type": "OSError",
                        "value": (
                            f"[Errno 104] Connection reset by peer "
                            f"[{MAPPED_IPV6}]:443 while reading upstream"
                        ),
                    }
                ]
            },
            "breadcrumbs": [{"message": f"resolved upstream to {EMBEDDED_IPV6}"}],
        }
        scrubbed = scrub_event(event)
        rendered = json.dumps(scrubbed)

        for forbidden in (
            MAPPED_IPV6,
            EMBEDDED_IPV6,
            MAPPED_IPV4_TAIL,
            EMBEDDED_IPV4_TAIL,
            "2001:db8",
            "::ffff",
        ):
            assert forbidden not in rendered, forbidden
        # The diagnostics that make the report worth having are still there.
        assert "[Errno 104] Connection reset by peer" in rendered
        assert "while reading upstream" in rendered
        assert "OSError" in rendered
        assert "443" in rendered

    @pytest.mark.parametrize(
        "harmless",
        ["2.12.5", "3.11.9", "16.4", "v2.12.5", "took 12.5 ms", "99.9% availability"],
    )
    def test_widening_the_candidate_did_not_swallow_version_strings(
        self, harmless: str
    ) -> None:
        """The false-positive guard, restated against the widened pattern.

        The widened branch requires a colon, which is what keeps dotted
        version numbers out of it entirely.
        """
        assert scrub_event({"extra": {"note": harmless}})["extra"]["note"] == harmless


# ---------------------------------------------------------------------------
# Safe latency that happens to look like an age
# ---------------------------------------------------------------------------
class TestDurationIsNotAnAge:
    @pytest.mark.parametrize("duration", [15, 150, 151, 0, 1, MAX_DURATION_MS])
    def test_a_duration_equal_to_an_age_is_still_valid_telemetry(
        self, duration: int, operational_log,
    ) -> None:
        """`duration_ms=15` is latency. It is not somebody's age.

        A privacy assertion that banned the digits would fail here, on a fast
        request, having proved nothing. The event is judged by its keys.
        """
        operational_events._emit(
            EVENT_FOR_YOU,
            {"outcome": OperationalOutcome.COMPLETED, "duration_ms": duration},
        )
        assert len(operational_log) == 1
        fields = _fields(operational_log[0])
        assert set(fields) == {"outcome", "duration_ms"}
        assert fields["duration_ms"] == str(duration)
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))

    async def test_a_forced_fifteen_millisecond_request_is_indistinguishable(
        self, db_clean, app_client, registered_supabase_user,
        operational_log, monkeypatch,
    ) -> None:
        """The stopwatch forced to exactly 15, on a request carrying age 15.

        The two numbers coincide and nothing about the request can be recovered
        from the event either way.
        """
        monkeypatch.setattr(
            operational_events._Stopwatch, "duration_ms", lambda self: 15
        )
        pack = await _confirmed_pack(app_client, registered_supabase_user)
        operational_log.clear()

        response = await app_client.post(
            FOR_YOU_URL, headers={**pack["headers"], **auth(pack["token"])},
            json={"barcode": BARCODE, "safety": ALL_SAFETY_FLAGS},
        )
        assert response.status_code == 200, response.text

        events = _events(operational_log, EVENT_FOR_YOU)
        assert len(events) == 1
        fields = _fields(events[0])
        assert fields == {"outcome": "completed", "duration_ms": "15"}
        # The 15 that is present is a latency; the 15 that is absent is an age.
        assert "stated_age" not in operational_log[0]
        _assert_only_known_events(operational_log)
        _assert_nothing_sensitive("\n".join(operational_log))


# ---------------------------------------------------------------------------
# Boundaries this milestone must not have crossed
# ---------------------------------------------------------------------------
class TestScope:
    def test_no_analytics_sdk_was_introduced(self) -> None:
        requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for vendor in (
            "segment", "mixpanel", "amplitude", "posthog", "firebase",
            "google-analytics", "analytics-python", "statsd", "datadog",
        ):
            assert vendor not in requirements, vendor

    def test_no_analytics_table_was_added(self) -> None:
        """This milestone persists no telemetry at all.

        Asked of the imports rather than of substrings: the module reaches for
        no database, no ORM and no session, so there is nowhere for an event to
        be written even if somebody wanted to write one.
        """
        tree = ast.parse(EVENTS_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("app.shared.database"), module
            assert not module.startswith("app.domains"), module
        # And no ORM model is declared here.
        assert not any(
            isinstance(node, ast.ClassDef)
            and any("Base" in ast.unparse(base) for base in node.bases)
            for node in ast.walk(tree)
        )

    def test_the_endpoints_import_the_bounded_helper_and_nothing_else(self) -> None:
        for relative in (
            "app/api/v2/skin_care_scan.py",
            "app/api/v2/skin_care_personal_decision.py",
        ):
            source = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
            assert "operational_events" in source, relative
            for vendor in ("mixpanel", "posthog", "segment", "amplitude"):
                assert vendor not in source.lower(), (relative, vendor)

    def test_the_for_you_route_never_names_a_safety_field_in_telemetry(self) -> None:
        """A static reading of the handler: no flag reaches an observation."""
        source = (
            BACKEND_ROOT / "app" / "api" / "v2" / "skin_care_personal_decision.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = ast.unparse(node.func)
            if "operational_events" not in target:
                continue
            # The only permitted call takes no arguments at all.
            assert node.args == [] and node.keywords == [], ast.unparse(node)
