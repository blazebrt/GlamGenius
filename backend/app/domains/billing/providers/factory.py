"""Choosing the payment provider.

Resolution is deliberately strict. If ``BILLING_PROVIDER`` names Razorpay but
the keys are missing, this returns :class:`UnconfiguredProvider` — which raises
on every call — rather than quietly falling back to the manual one. A silent
downgrade from "real payments" to "pretend payments" is the worst outcome
available in this file, so it cannot happen.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import BILLING_PROVIDER
from app.domains.billing.providers.base import UnconfiguredProvider
from app.domains.billing.providers.manual import ManualProvider
from app.domains.billing.providers.razorpay import RazorpayProvider

logger = logging.getLogger(__name__)

_override: Optional[Any] = None


def set_provider(provider: Optional[Any]) -> None:
    """Force a provider. For tests only."""
    global _override
    _override = provider


def get_provider() -> Any:
    if _override is not None:
        return _override

    name = (BILLING_PROVIDER or "manual").strip().lower()
    if name == "manual":
        return ManualProvider()
    if name == "razorpay":
        provider = RazorpayProvider()
        if not provider.is_configured():
            logger.warning("razorpay_selected_but_unconfigured")
            return UnconfiguredProvider(
                "Razorpay is selected but its keys are not configured, so payments are off."
            )
        return provider
    return UnconfiguredProvider(f"'{name}' is not a payment provider we support.")


def provider_status() -> dict:
    """What the config endpoint reports. Never includes key material."""
    provider = get_provider()
    return {
        "provider": provider.name,
        "configured": provider.is_configured(),
        "simulated": provider.name == "manual",
    }
