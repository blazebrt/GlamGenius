#!/usr/bin/env python3
"""Sentry Validation Probe.

Validates:
1. Sentry initialization logic handles missing DSN gracefully.
2. The custom privacy scrubber (scrub_event) successfully redacts PII
   from simulated error events before they would be sent.
"""

import os
import sys
import copy
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))
load_dotenv(os.path.join(os.path.dirname(__file__), "../backend/.env"))

from app.shared.observability.sentry_privacy import scrub_event
from app.shared.observability.sentry_bootstrap import init_sentry

def main():
    print("Running Sentry Validation Probe...")

    dsn = os.environ.get("SENTRY_BACKEND_DSN")
    if not dsn:
        print("[SKIP] SENTRY_BACKEND_DSN is not set, skipping live Sentry connection.")
    else:
        try:
            init_sentry()
            print("[OK] Sentry successfully initialized.")
        except Exception as e:
            print(f"[ERROR] Sentry initialization failed: {e}")

    # Test Privacy Scrubber
    simulated_event = {
        "request": {
            "headers": {
                "Authorization": "Bearer real_token_here",
                "Cookie": "session_id=12345",
                "User-Agent": "Mozilla/5.0"
            },
            "data": {
                "password": "my_secret_password",
                "email": "user@example.com",
                "first_name": "Test",
                "last_name": "User"
            }
        },
        "exception": {
            "values": [
                {"type": "ValueError", "value": "Invalid email: user@example.com"}
            ]
        }
    }

    print("\nValidating PII Redaction in Sentry payload...")
    scrubbed = scrub_event(copy.deepcopy(simulated_event), hint={})

    # Assert Headers Redaction
    headers = scrubbed.get("request", {}).get("headers", {})
    if headers.get("Authorization") == "[Filtered]" and headers.get("Cookie") == "[Filtered]":
        print("  [OK] Sensitive headers redacted.")
    else:
        print("  [ERROR] Sensitive headers NOT redacted!")

    # Assert Body Redaction (Based on the app's privacy rules)
    # The app's specific rules might redact 'password', 'email', 'name', etc.
    data = scrubbed.get("request", {}).get("data", {})
    if data.get("password") != "my_secret_password" and data.get("email") != "user@example.com":
        print("  [OK] Sensitive request body fields redacted.")
    else:
        print(f"  [WARN] Request body fields not fully redacted: {data}")
    
    print("Sentry validation completed.")

if __name__ == "__main__":
    main()
