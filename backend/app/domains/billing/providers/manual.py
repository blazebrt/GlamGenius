"""A payment provider that takes no money and says so.

This is what runs in development, in tests, and in any deployment that has not
configured a real provider. It exists so the entire billing pipeline —
checkout, webhook, verification, entitlement grant, refund, expiry — can be
exercised end to end without a Razorpay account.

Two things keep it honest.

Every session it returns is marked ``simulated=True``, and that flag travels all
the way to the client, so no screen can imply a real payment was taken.

Its webhook signature is a real HMAC over the real raw body, using the same
algorithm as the production provider. It is not a check that always passes —
a test that forges a signature must fail here exactly as it would in
production, or the verification tests would be proving nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any, Dict, Optional

from app.domains.billing.providers.base import (
    EVENT_PAYMENT_FAILED, EVENT_PAYMENT_SUCCEEDED, EVENT_REFUNDED,
    EVENT_SUBSCRIPTION_ACTIVATED, EVENT_SUBSCRIPTION_CANCELLED,
    EVENT_SUBSCRIPTION_COMPLETED, EVENT_SUBSCRIPTION_PAYMENT_FAILED,
    EVENT_SUBSCRIPTION_RENEWED, EVENT_UNKNOWN, CheckoutSession,
    SignatureInvalidError, WebhookEvent,
)

SIGNATURE_HEADER = "X-Signature"

# The same names the production provider maps from, so a test written against
# one reads correctly against the other.
EVENT_MAP: Dict[str, str] = {
    "payment.succeeded": EVENT_PAYMENT_SUCCEEDED,
    "payment.failed": EVENT_PAYMENT_FAILED,
    "subscription.activated": EVENT_SUBSCRIPTION_ACTIVATED,
    "subscription.renewed": EVENT_SUBSCRIPTION_RENEWED,
    "subscription.payment_failed": EVENT_SUBSCRIPTION_PAYMENT_FAILED,
    "subscription.cancelled": EVENT_SUBSCRIPTION_CANCELLED,
    "subscription.completed": EVENT_SUBSCRIPTION_COMPLETED,
    "refund.processed": EVENT_REFUNDED,
}


class ManualProvider:
    """Takes no money. Verifies signatures for real."""

    name = "manual"

    def __init__(self, webhook_secret: str = "manual-webhook-secret") -> None:
        self.webhook_secret = webhook_secret

    def is_configured(self) -> bool:
        return True

    async def create_checkout(
        self, *, amount_inr: int, currency: str, receipt: str, notes: Dict[str, Any]
    ) -> CheckoutSession:
        return CheckoutSession(
            provider=self.name,
            provider_order_id=f"manual_order_{uuid.uuid4().hex[:16]}",
            amount_inr=amount_inr,
            currency=currency,
            public_key=None,
            notes=notes,
            simulated=True,
        )

    def sign(self, raw_body: bytes) -> str:
        """Sign a body the way this provider expects. For tests and tooling."""
        return hmac.new(
            self.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()

    def verify_webhook(self, *, raw_body: bytes, signature: Optional[str]) -> Dict[str, Any]:
        if not signature:
            raise SignatureInvalidError("The webhook carried no signature.")
        if not hmac.compare_digest(self.sign(raw_body), signature):
            raise SignatureInvalidError("The webhook signature did not match.")
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SignatureInvalidError("The webhook body was not readable JSON.") from exc

    def parse_event(self, payload: Dict[str, Any]) -> WebhookEvent:
        name = str(payload.get("event") or "")
        data = payload.get("data") or {}
        return WebhookEvent(
            provider_event_id=str(payload.get("id") or uuid.uuid4()),
            kind=EVENT_MAP.get(name, EVENT_UNKNOWN),
            provider_order_id=data.get("order_id"),
            provider_payment_id=data.get("payment_id"),
            provider_subscription_id=data.get("subscription_id"),
            provider_refund_id=data.get("refund_id"),
            amount_inr=data.get("amount_inr"),
            occurred_at_epoch=payload.get("created_at"),
            raw_event_name=name,
            notes=data.get("notes") or {},
        )
