"""The payment provider seam.

One interface, two implementations: a manual provider that is honest about
doing nothing, and Razorpay for production in India.

The important property of this design is what a provider is *not* allowed to
do. A provider can start a checkout and it can verify a signature. It cannot
grant entitlements — that only ever happens in
:mod:`app.domains.billing.service` in response to a **verified webhook**, and
never in response to anything the client says. The brief states that rule and
the shape here enforces it: no provider method returns an entitlement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

# The provider-neutral vocabulary. Razorpay's event names are mapped onto these
# so the state machine never has to know whose webhook it is reading.
EVENT_PAYMENT_SUCCEEDED = "payment_succeeded"
EVENT_PAYMENT_FAILED = "payment_failed"
EVENT_SUBSCRIPTION_ACTIVATED = "subscription_activated"
EVENT_SUBSCRIPTION_RENEWED = "subscription_renewed"
EVENT_SUBSCRIPTION_PAYMENT_FAILED = "subscription_payment_failed"
EVENT_SUBSCRIPTION_CANCELLED = "subscription_cancelled"
EVENT_SUBSCRIPTION_COMPLETED = "subscription_completed"
EVENT_REFUNDED = "refunded"
EVENT_UNKNOWN = "unknown"

KNOWN_EVENTS = (
    EVENT_PAYMENT_SUCCEEDED, EVENT_PAYMENT_FAILED, EVENT_SUBSCRIPTION_ACTIVATED,
    EVENT_SUBSCRIPTION_RENEWED, EVENT_SUBSCRIPTION_PAYMENT_FAILED,
    EVENT_SUBSCRIPTION_CANCELLED, EVENT_SUBSCRIPTION_COMPLETED, EVENT_REFUNDED,
)


class ProviderUnavailableError(RuntimeError):
    """The provider cannot be used — usually because it is not configured."""


class SignatureInvalidError(RuntimeError):
    """A webhook did not carry a signature we could verify.

    Always fatal. A webhook that fails verification is discarded, never
    processed "just in case" — anyone on the internet can POST to that URL.
    """


@dataclass
class CheckoutSession:
    """What the client needs to open a payment sheet.

    Carries no entitlement and no promise. Completing this in the app does not
    grant anything; the webhook does.
    """

    provider: str
    provider_order_id: str
    amount_inr: int
    currency: str
    # Publishable key. Never the secret.
    public_key: Optional[str] = None
    notes: Dict[str, Any] = field(default_factory=dict)
    # True when the provider is a stand-in that cannot take real money.
    simulated: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_order_id": self.provider_order_id,
            "amount_inr": self.amount_inr,
            "currency": self.currency,
            "public_key": self.public_key,
            "notes": self.notes,
            "simulated": self.simulated,
            "note": (
                "Completing payment in the app does not by itself unlock anything. "
                "Access is granted when our server has verified the payment with the provider."
            ),
        }


@dataclass
class WebhookEvent:
    """One verified provider event, translated into our own vocabulary."""

    # The provider's own id for this event, used for deduplication.
    provider_event_id: str
    kind: str
    provider_order_id: Optional[str] = None
    provider_payment_id: Optional[str] = None
    provider_subscription_id: Optional[str] = None
    provider_refund_id: Optional[str] = None
    amount_inr: Optional[int] = None
    # Provider's own timestamp, used to spot out-of-order delivery.
    occurred_at_epoch: Optional[int] = None
    raw_event_name: str = ""
    notes: Dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    """What every provider must do, and the limit of what any may do."""

    name: str

    def is_configured(self) -> bool: ...

    async def create_checkout(
        self, *, amount_inr: int, currency: str, receipt: str, notes: Dict[str, Any]
    ) -> CheckoutSession: ...

    def verify_webhook(self, *, raw_body: bytes, signature: Optional[str]) -> Dict[str, Any]:
        """Return the parsed payload, or raise :class:`SignatureInvalidError`."""

    def parse_event(self, payload: Dict[str, Any]) -> WebhookEvent: ...


class UnconfiguredProvider:
    """A provider that refuses rather than pretending.

    Used when ``BILLING_PROVIDER`` names something we do not implement, or when
    the named provider has no credentials. It raises on every operation, which
    is the honest behaviour — silently falling back to "payment succeeded"
    would be the worst possible bug in this file.
    """

    name = "unconfigured"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def is_configured(self) -> bool:
        return False

    async def create_checkout(self, **_: Any) -> CheckoutSession:
        raise ProviderUnavailableError(self.reason)

    def verify_webhook(self, **_: Any) -> Dict[str, Any]:
        raise ProviderUnavailableError(self.reason)

    def parse_event(self, payload: Dict[str, Any]) -> WebhookEvent:
        raise ProviderUnavailableError(self.reason)
