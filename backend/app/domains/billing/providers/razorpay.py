"""Razorpay, for production in India.

Written against Razorpay's current published documentation and cross-checked
against their own Python SDK's ``razorpay/utility/utility.py``, as this project
requires for any outside service. The details that matter, all confirmed there:

* **Webhook signature** — HMAC-SHA256 of the **raw request body**, keyed with
  the *webhook secret* (which is a different secret from the API key secret),
  hex digest, delivered in the ``X-Razorpay-Signature`` header, compared with
  ``hmac.compare_digest``.
* **Checkout callback signature** — HMAC-SHA256 of the literal string
  ``"{order_id}|{payment_id}"``, keyed with the *API key secret*, hex digest.
* **Payload envelope** — ``{entity, account_id, event, contains, payload, created_at}``,
  where ``payload`` maps an entity name to ``{"entity": {...}}``.

Two deliberate implementation choices.

**No SDK dependency.** Verification is `hmac` and `hashlib` from the standard
library — which is exactly what the SDK does — so this adds no package to the
project. Order creation goes over plain HTTPS with `httpx`, already present.

**The raw body, not the parsed JSON.** Re-serialising a parsed body changes
whitespace and key order and the signature stops matching. The route hands the
raw bytes straight through, and this module never re-encodes them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
from app.domains.billing.providers.base import (
    EVENT_PAYMENT_FAILED, EVENT_PAYMENT_SUCCEEDED, EVENT_REFUNDED,
    EVENT_SUBSCRIPTION_ACTIVATED, EVENT_SUBSCRIPTION_CANCELLED,
    EVENT_SUBSCRIPTION_COMPLETED, EVENT_SUBSCRIPTION_PAYMENT_FAILED,
    EVENT_SUBSCRIPTION_RENEWED, EVENT_UNKNOWN, CheckoutSession,
    ProviderUnavailableError, SignatureInvalidError, WebhookEvent,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.razorpay.com/v1"
SIGNATURE_HEADER = "X-Razorpay-Signature"
REQUEST_TIMEOUT_SECONDS = 20.0

# Razorpay's event names, mapped onto our provider-neutral vocabulary. Anything
# not listed is recorded and ignored rather than guessed at.
EVENT_MAP: Dict[str, str] = {
    "order.paid": EVENT_PAYMENT_SUCCEEDED,
    "payment.captured": EVENT_PAYMENT_SUCCEEDED,
    "payment.failed": EVENT_PAYMENT_FAILED,
    "subscription.activated": EVENT_SUBSCRIPTION_ACTIVATED,
    "subscription.authenticated": EVENT_SUBSCRIPTION_ACTIVATED,
    "subscription.charged": EVENT_SUBSCRIPTION_RENEWED,
    "subscription.pending": EVENT_SUBSCRIPTION_PAYMENT_FAILED,
    "subscription.halted": EVENT_SUBSCRIPTION_PAYMENT_FAILED,
    "subscription.cancelled": EVENT_SUBSCRIPTION_CANCELLED,
    "subscription.completed": EVENT_SUBSCRIPTION_COMPLETED,
    "refund.created": EVENT_REFUNDED,
    "refund.processed": EVENT_REFUNDED,
}


class RazorpayProvider:
    name = "razorpay"

    def __init__(
        self,
        key_id: str = RAZORPAY_KEY_ID,
        key_secret: str = RAZORPAY_KEY_SECRET,
        webhook_secret: str = RAZORPAY_WEBHOOK_SECRET,
    ) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret

    def is_configured(self) -> bool:
        return bool(self.key_id and self.key_secret and self.webhook_secret)

    # --- Checkout -------------------------------------------------------------

    async def create_checkout(
        self, *, amount_inr: int, currency: str, receipt: str, notes: Dict[str, Any]
    ) -> CheckoutSession:
        """Create an order with Razorpay and return what the app needs to pay it.

        Amounts go to Razorpay in the smallest currency unit — paise — so a
        ₹499 pass is 49900. Getting this wrong by a factor of a hundred is the
        classic integration bug, so the conversion happens once, here.
        """
        if not self.is_configured():
            raise ProviderUnavailableError(
                "Razorpay is selected but its keys are not configured."
            )

        auth = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        body = {
            "amount": amount_inr * 100,
            "currency": currency,
            "receipt": receipt,
            "notes": notes,
            # Capture automatically, so a paid order does not sit authorised
            # and silently expire.
            "payment_capture": 1,
        }

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{API_BASE}/orders",
                    json=body,
                    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning("razorpay_order_transport_error type=%s", type(exc).__name__)
            raise ProviderUnavailableError("We could not reach the payment provider.") from exc

        if response.status_code >= 400:
            # Never log the response body — it echoes the request, which
            # contains our notes and could contain identifiers.
            logger.warning("razorpay_order_rejected status=%s", response.status_code)
            raise ProviderUnavailableError("The payment provider rejected the order.")

        data = response.json()
        return CheckoutSession(
            provider=self.name,
            provider_order_id=data["id"],
            amount_inr=amount_inr,
            currency=currency,
            public_key=self.key_id,
            notes=notes,
        )

    # --- Verification ----------------------------------------------------------

    def verify_webhook(self, *, raw_body: bytes, signature: Optional[str]) -> Dict[str, Any]:
        """Verify a webhook and return its parsed payload.

        Raises on anything suspicious. There is no "process it anyway" branch:
        the webhook URL is public, so an unverified body is an anonymous
        stranger's opinion about who has paid us.
        """
        if not self.webhook_secret:
            raise ProviderUnavailableError("No Razorpay webhook secret is configured.")
        if not signature:
            raise SignatureInvalidError("The webhook carried no signature.")

        expected = hmac.new(
            self.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()

        # Constant-time, so the comparison cannot be probed a byte at a time.
        if not hmac.compare_digest(expected, signature):
            raise SignatureInvalidError("The webhook signature did not match.")

        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SignatureInvalidError("The webhook body was not readable JSON.") from exc

    def verify_payment_signature(
        self, *, order_id: str, payment_id: str, signature: str
    ) -> bool:
        """Check the signature the app gets back from the Razorpay sheet.

        Useful for showing the user an immediate "payment received" screen. It
        is **not** what grants access — the webhook is. A client that can call
        this can also lie about calling it.
        """
        if not self.key_secret:
            return False
        expected = hmac.new(
            self.key_secret.encode("utf-8"),
            f"{order_id}|{payment_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # --- Parsing ----------------------------------------------------------------

    def parse_event(self, payload: Dict[str, Any]) -> WebhookEvent:
        """Translate a verified Razorpay event into our own vocabulary."""
        name = str(payload.get("event") or "")
        kind = EVENT_MAP.get(name, EVENT_UNKNOWN)
        entities = payload.get("payload") or {}

        def entity(key: str) -> Dict[str, Any]:
            row = entities.get(key) or {}
            return row.get("entity") or {}

        payment = entity("payment")
        order = entity("order")
        subscription = entity("subscription")
        refund = entity("refund")

        amount_paise = (
            refund.get("amount") or payment.get("amount") or order.get("amount")
        )
        amount_inr = int(amount_paise) // 100 if isinstance(amount_paise, int) else None

        # Razorpay does not put an event id in the body, so identity for
        # deduplication is derived from the event name plus the entity ids it
        # carries. Two deliveries of the same event hash identically; a genuine
        # later event (a renewal, say) carries a different payment id.
        parts = [
            name,
            str(order.get("id") or payment.get("order_id") or ""),
            str(payment.get("id") or ""),
            str(subscription.get("id") or ""),
            str(refund.get("id") or ""),
        ]
        provider_event_id = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

        return WebhookEvent(
            provider_event_id=provider_event_id,
            kind=kind,
            provider_order_id=order.get("id") or payment.get("order_id"),
            provider_payment_id=payment.get("id"),
            provider_subscription_id=subscription.get("id") or payment.get("subscription_id"),
            provider_refund_id=refund.get("id"),
            amount_inr=amount_inr,
            occurred_at_epoch=payload.get("created_at"),
            raw_event_name=name,
            notes=order.get("notes") or payment.get("notes") or {},
        )
