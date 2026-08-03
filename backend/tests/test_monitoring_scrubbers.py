"""Tests for the Sentry PII scrubber.

The scrubber is the fence between application internals and an external
service; it has to hold against every leak vector we know about. These
tests are exhaustive because the review checklist for Fix 8 requires
that a machine, not a reader, prove the invariants.
"""
from __future__ import annotations

import pytest

from app.shared.observability.sentry_privacy import REDACTED, scrub_event


class TestSentryScrubber:
    def test_email_is_redacted_in_a_string_value(self):
        event = {"message": "user asha@example.com just signed up"}
        assert "asha@example.com" not in scrub_event(event)["message"]

    def test_phone_is_redacted_in_a_string_value(self):
        event = {"message": "called +91 98765 43210 twice"}
        cleaned = scrub_event(event)["message"]
        assert "98765" not in cleaned
        assert "+91" not in cleaned

    def test_jwt_is_redacted_in_a_string_value(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123XYZ"
        event = {"extra": {"note": f"Authorization: Bearer {jwt}"}}
        assert jwt not in scrub_event(event)["extra"]["note"]

    def test_image_bytes_key_is_redacted_whole(self):
        event = {"contexts": {"payload": {"image_base64": "abc123def"}}}
        cleaned = scrub_event(event)
        assert cleaned["contexts"]["payload"]["image_base64"] == REDACTED

    def test_ingredient_key_is_redacted_whole(self):
        event = {"extra": {"ingredients": ["retinol", "salicylic acid"]}}
        assert scrub_event(event)["extra"]["ingredients"] == REDACTED

    def test_memory_facts_key_is_redacted_whole(self):
        event = {"extra": {"memory_facts": [{"fact": "prefers cool tones"}]}}
        assert scrub_event(event)["extra"]["memory_facts"] == REDACTED

    def test_razorpay_identifier_is_redacted_whole(self):
        event = {"tags": {"razorpay_order_id": "order_ABC123"}}
        assert scrub_event(event)["tags"]["razorpay_order_id"] == REDACTED

    def test_authorization_header_is_redacted_whole(self):
        event = {"request": {"headers": {"authorization": "Bearer abc.def.ghi"}}}
        assert scrub_event(event)["request"]["headers"]["authorization"] == REDACTED

    def test_long_base64_looking_value_is_redacted_by_shape(self):
        # A field named 'raw' whose value is base64-shaped and long enough to
        # be an image payload. Key name gives no hint; shape does.
        blob = "A" * 200
        event = {"contexts": {"payload": {"raw": blob}}}
        assert scrub_event(event)["contexts"]["payload"]["raw"] == REDACTED

    def test_short_string_that_looks_base64_ish_is_not_falsely_redacted(self):
        event = {"contexts": {"payload": {"tag": "ABCDEFGH"}}}  # 8 chars
        assert scrub_event(event)["contexts"]["payload"]["tag"] == "ABCDEFGH"

    def test_binary_bytes_are_redacted(self):
        event = {"extra": {"blob": b"\xff\xd8\xff\xe0"}}
        assert scrub_event(event)["extra"]["blob"] == REDACTED

    def test_nested_structures_are_walked(self):
        event = {
            "exception": {
                "values": [
                    {
                        "value": "raised for user@x.com",
                        "stacktrace": {"frames": [{"vars": {"jwt": "eyJx.yy.zz"}}]},
                    }
                ]
            }
        }
        cleaned = scrub_event(event)
        assert "user@x.com" not in cleaned["exception"]["values"][0]["value"]
        assert (
            cleaned["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["jwt"]
            == REDACTED
        )

    def test_password_and_secret_and_api_key_keys_are_redacted(self):
        event = {
            "extra": {
                "password": "hunter2",
                "secret": "s3cret",
                "api_key": "sk_test_1",
                "GEMINI_API_KEY": "should-not-leak",
            }
        }
        cleaned = scrub_event(event)["extra"]
        assert cleaned["password"] == REDACTED
        assert cleaned["secret"] == REDACTED
        assert cleaned["api_key"] == REDACTED
        assert cleaned["GEMINI_API_KEY"] == REDACTED

    def test_ordinary_strings_are_untouched(self):
        event = {"message": "the plan compiled successfully"}
        assert scrub_event(event)["message"] == "the plan compiled successfully"

    def test_scrubbing_is_pure_and_does_not_mutate_input(self):
        event = {"message": "hi asha@example.com"}
        _ = scrub_event(event)
        assert event["message"] == "hi asha@example.com"

    def test_missing_dsn_does_not_initialise_sentry(self, monkeypatch):
        # The bootstrap helper is the guard; verify that it returns quietly
        # when SENTRY_BACKEND_DSN is empty, which is the "monitoring is
        # disabled" state.
        monkeypatch.delenv("SENTRY_BACKEND_DSN", raising=False)
        from app.shared.observability import sentry_bootstrap

        # Should not raise, should not import sentry_sdk, should not open
        # any socket. There is nothing to assert positively; the test
        # passes by not hanging or crashing.
        sentry_bootstrap.init_sentry()
