"""Expo Push notification adapter (Fix 6, WP5).

Sends push notifications through Expo's Push API. **No secret is
required** to send — Expo's push service accepts unauthenticated
POSTs and identifies the target by the device's Expo Push Token.
The token itself is what proves ownership of a device; a leaked
token can only send a notification to that one device.

If the deployment ever moves to APNs / FCM direct, this adapter is
replaced. During the private-beta phase Expo Push is enough.

Design rules:

* **No secret in the outbound.** No Authorization header.
* **Bounded batch.** Expo's API accepts up to 100 messages per
  request; the adapter batches at that boundary.
* **No retry loop.** A failed batch is logged with the count that
  failed; the caller decides whether to try again.
* **Never send content the user has not seen.** The message body
  passes through the safety-classifier layer before it lands here.
* **No PII in logs.** Log the receipt id, not the token or the
  message body.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app import config

logger = logging.getLogger(__name__)

_ENDPOINT = "https://exp.host/--/api/v2/push/send"
_MAX_PER_REQUEST = 100

# Reported against every message a dry run drops, so the reason a delivery did
# not go out is legible in the database rather than looking like a provider bug.
DRY_RUN_ERROR = "push_dry_run"


def _delivery_mode() -> str:
    """Read the mode per call, not at import, so a manual run can set it in-process."""
    return (os.environ.get("PUSH_DELIVERY_MODE") or config.PUSH_DELIVERY_MODE or "live").lower()


@dataclass(frozen=True)
class PushMessage:
    to: str                      # Expo Push Token, format: ExponentPushToken[...]
    title: str
    body: str
    data: dict | None = None


@dataclass(frozen=True)
class PushOutcome:
    token: str
    accepted: bool
    error: str | None = None
    ticket_id: str | None = None


@dataclass(frozen=True)
class PushResult:
    sent: int
    failed: int
    receipts: list[str]          # server-side receipt ids for later delivery check
    errors: list[str] | None = None
    outcomes: list[PushOutcome] | None = None


async def send(messages: list[PushMessage]) -> PushResult:
    """POST a batch of messages to Expo. Returns the receipt ids and counts.

    Never raises for a network/provider error — a push failure is not a
    request failure. The caller sees ``failed > 0`` and can decide.
    """
    if not messages:
        return PushResult(sent=0, failed=0, receipts=[], errors=[], outcomes=[])

    # The dry-run kill switch. Read here, at the transport boundary, rather than
    # in a caller, so no code path can route around it: if this is on, no socket
    # is opened and nothing can reach a real device. Every message is reported
    # as failed with a distinctive error, because "delivered" would be a lie and
    # would let a dry run mark deliveries as accepted and burn the daily cap.
    if _delivery_mode() != "live":
        logger.info("push_dry_run_skipped count=%s", len(messages))
        return PushResult(
            sent=0,
            failed=len(messages),
            receipts=[],
            errors=[DRY_RUN_ERROR],
            outcomes=[PushOutcome(m.to, False, error=DRY_RUN_ERROR) for m in messages],
        )

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        logger.warning("httpx not installed; push adapter disabled")
        return PushResult(sent=0, failed=len(messages), receipts=[])

    sent = 0
    failed = 0
    receipts: list[str] = []
    errors: list[str] = []
    outcomes: list[PushOutcome] = []

    for start in range(0, len(messages), _MAX_PER_REQUEST):
        batch = messages[start : start + _MAX_PER_REQUEST]
        payload = [
            {"to": m.to, "title": m.title, "body": m.body, **({"data": m.data} if m.data else {})}
            for m in batch
        ]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    _ENDPOINT,
                    json=payload,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
            response.raise_for_status()
            data = response.json() or {}
            per_message = data.get("data") or []
            for index, message in enumerate(batch):
                entry = per_message[index] if index < len(per_message) else {"status": "error", "message": "missing_result"}
                if isinstance(entry, dict) and entry.get("status") == "ok":
                    sent += 1
                    rid = entry.get("id")
                    if rid:
                        receipts.append(rid)
                    outcomes.append(PushOutcome(message.to, True, ticket_id=str(rid) if rid else None))
                else:
                    failed += 1
                    error = None
                    if isinstance(entry, dict):
                        error = entry.get("details", {}).get("error") or entry.get("message")
                    if error:
                        errors.append(str(error))
                    outcomes.append(PushOutcome(message.to, False, error=str(error) if error else "provider_failed"))
        except Exception as exc:  # noqa: BLE001
            logger.info("push_batch_failed error=%s size=%s", type(exc).__name__, len(batch))
            failed += len(batch)
            errors.append(type(exc).__name__)
            outcomes.extend(PushOutcome(message.to, False, error=type(exc).__name__) for message in batch)

    return PushResult(sent=sent, failed=failed, receipts=receipts, errors=errors, outcomes=outcomes)
