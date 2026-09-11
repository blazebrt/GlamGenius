"""The two endpoints an external scheduler may call, and nothing else.

The pre-PMF runtime has one free web service and no worker process. The two
batch jobs that used to be separate containers — account deletion and
notifications — are now invoked over HTTP by Supabase Cron, every five minutes
and hourly respectively.

**These routes are not customer routes, and a customer token cannot open
them.** They run privileged batch work on nobody's behalf: deleting an account
that asked to be deleted, sending the day's notifications to whoever is due
one. Authorising that with a Supabase JWT would mean any signed-in user could
trigger it, which is a different and much worse thing than an authenticated
customer endpoint. So the authority here is a single shared secret that only
the scheduler and this service know.

What that buys, precisely:

* the secret is compared with :func:`hmac.compare_digest`, so a wrong guess
  takes the same time as any other wrong guess;
* every refusal is the same 401 with the same body, so the response cannot be
  used to learn whether the scheme, the header shape or the value was the
  problem;
* the submitted credential is never logged, and the configured one is never
  logged, echoed or described — not its length, not a prefix, not a hash;
* the routes are POST only, so the credential can never be put in a URL by a
  browser, a proxy log or a redirect.

**The work itself is not implemented here.** These handlers call the existing
bounded cycles and return what those cycles report. The deletion state machine,
the quiet hours, the outbox claim, the push transport and both heartbeats stay
where they are. This module is a door, not a second implementation.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app import config
from app.workers import account_deletion, notifications

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/scheduler", tags=["v2-internal-scheduler"])

#: The words of the one refusal. Missing header, wrong scheme, empty bearer
#: and wrong value all produce exactly this, because telling them apart is
#: telling an attacker which half of the problem to work on.
_DENIED_STATUS = status.HTTP_401_UNAUTHORIZED
_DENIED_DETAIL = "Scheduler credentials are not valid."
_DENIED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _denied() -> HTTPException:
    """A fresh refusal each time, identical to every other one.

    A module-level exception instance would be simpler and would say the same
    thing, but raising one object over and over attaches a new traceback to
    shared state on every failed request — on the one path an attacker can
    call as often as they like.
    """
    return HTTPException(
        status_code=_DENIED_STATUS,
        detail=_DENIED_DETAIL,
        headers=dict(_DENIED_HEADERS),
    )


_BEARER = "bearer"


def require_scheduler_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Authorise one scheduler call, or refuse it identically.

    Raises the same refusal for every failure mode. Nothing about the
    submitted value reaches a log line, and nothing about the configured value
    reaches the response.
    """
    configured = config.INTERNAL_SCHEDULER_TOKEN.strip()
    if not configured:
        # Unconfigured is a refusal, never an open door. Production startup
        # already refuses to boot without it; this covers every other
        # environment, where the safe default is "nobody may call this".
        logger.error("internal_scheduler_token_not_configured")
        raise _denied()

    if not authorization:
        raise _denied()
    scheme, _, presented = authorization.partition(" ")
    if scheme.strip().lower() != _BEARER:
        raise _denied()
    presented = presented.strip()
    if not presented:
        raise _denied()
    if not hmac.compare_digest(presented, configured):
        raise _denied()


@router.post("/account-deletion")
async def run_account_deletion_cycle(
    _: None = Depends(require_scheduler_token),
) -> dict[str, object]:
    """Process at most one claimed deletion job.

    Bounded by design: one HTTP request runs one cycle. A handler that drained
    the queue would be a handler that times out under exactly the backlog it
    was meant to clear, and the scheduler comes back in five minutes anyway.

    The response carries whether a job was processed and whether it succeeded.
    It carries no account id, no email, no job payload and no database error
    text — the caller holds a shared secret, not a person's consent.
    """
    summary = await account_deletion.run_cycle()
    return summary.as_dict()


@router.post("/notifications")
async def run_notification_cycle(
    _: None = Depends(require_scheduler_token),
) -> dict[str, object]:
    """Run one production notification cycle.

    Calls the worker's existing :func:`run_cycle` — the same one the hourly
    batch has always run — so quiet hours, local-time due checks, the outbox
    claim, the push transport and the heartbeat are unchanged and unbypassed.

    The worker's ``--account`` path is a manual testing affordance and is
    deliberately not reachable from here: a scheduler has no business naming
    one account, and an endpoint that let it would be an endpoint that could
    notify a chosen person on demand.
    """
    try:
        summary = await notifications.run_cycle()
    except RuntimeError:
        # run_cycle raises after it has already reported and written its
        # heartbeat. The failure is recorded; what must not happen is the
        # exception text reaching the caller, since it is built from the
        # underlying exception and can quote data.
        logger.error("notification_scheduled_cycle_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The notification cycle failed. See worker status.",
        ) from None
    return {
        "ok": summary.ok,
        "accounts_considered": summary.accounts_considered,
        "notifications_sent": summary.notifications_sent,
    }


__all__ = ["require_scheduler_token", "router"]
