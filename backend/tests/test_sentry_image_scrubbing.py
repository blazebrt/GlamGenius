"""An image must not reach Sentry inside an exception message.

CLAUDE.md §4 is absolute: the base64 goes to the AI gateway and nowhere else,
and no column, field or log line may retain it — whole, truncated or hashed.
``scrub_event`` is the last thing between an exception and an external service,
so it is where that promise is finally kept or lost.

The gap these tests close: ``_BASE64_LIKE`` is a ``fullmatch``, so it redacted a
value that was *nothing but* base64 and let the same payload through when it
arrived inside a longer string. The most likely way it arrives is inside a
longer string — a provider error quoting the request it rejected, chained onto
``ProviderCallFailed`` by ``raise ... from exc`` and serialised by sentry-sdk
into ``exception.values[].value``, where no key name says "image".
"""
from __future__ import annotations

from app.shared.observability.sentry_privacy import REDACTED, scrub_event

#: Shaped like a real JPEG payload: the magic prefix plus a long run.
IMAGE = "/9j/4AAQSkZJRgABAQAAAQABAAD" + "A" * 400


def _flat(event: dict) -> str:
    return repr(scrub_event(event))


class TestAnImageInsideAStringIsRedacted:
    def test_a_chained_provider_error_quoting_the_request(self) -> None:
        event = {
            "exception": {
                "values": [
                    {"type": "ProviderCallFailed", "value": "ClientError from Gemini"},
                    {
                        "type": "ClientError",
                        "value": f"400 INVALID_ARGUMENT request: {{'data': '{IMAGE}'}}",
                    },
                ]
            }
        }
        assert IMAGE[:40] not in _flat(event)

    def test_a_breadcrumb_carrying_one(self) -> None:
        assert IMAGE[:40] not in _flat(
            {"breadcrumbs": [{"message": f"sending image {IMAGE}"}]}
        )

    def test_an_extra_under_a_harmless_key(self) -> None:
        # "prompt" is not a sensitive-looking key name, which is the point:
        # key-name redaction cannot save this one.
        assert IMAGE[:40] not in _flat({"extra": {"prompt": f"analyse: {IMAGE}"}})

    def test_a_data_uri_shorter_than_the_run_threshold(self) -> None:
        short = IMAGE[:60]
        assert short not in _flat(
            {"extra": {"src": f"data:image/jpeg;base64,{short}"}}
        )

    def test_the_whole_string_case_still_works(self) -> None:
        assert scrub_event({"extra": {"m": IMAGE}})["extra"]["m"] == REDACTED


class TestOrdinaryDiagnosticsSurvive:
    """Redacting everything would be safe and useless."""

    def test_prose_is_untouched(self) -> None:
        text = "The account deletion job failed at the storage stage and will retry."
        assert scrub_event({"extra": {"m": text}})["extra"]["m"] == text

    def test_a_commit_sha_is_untouched(self) -> None:
        sha = "321c273e67b6a28e8c43c22e00d1d06fcbfaf8d4"
        assert scrub_event({"extra": {"m": sha}})["extra"]["m"] == sha

    def test_a_route_path_is_untouched(self) -> None:
        path = "/api/v2/internal/scheduler/account-deletion"
        assert scrub_event({"extra": {"m": path}})["extra"]["m"] == path

    def test_a_worker_event_name_is_untouched(self) -> None:
        name = "account_deletion_scheduled_cycle_failed"
        assert scrub_event({"extra": {"m": name}})["extra"]["m"] == name

    def test_a_hex_uuid_is_untouched(self) -> None:
        value = "a3f2c1d0-9b8e-4c7a-b6d5-e4f3a2b1c0d9"
        assert scrub_event({"extra": {"m": value}})["extra"]["m"] == value
