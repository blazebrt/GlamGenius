"""Step 15 Consumer Growth.

Four small pieces that turn a useful answer into a repeatable habit without
spending trust:

* **activation** — derived, never stored, from the account's own useful
  ``ScanEvent`` rows (:mod:`app.domains.growth.activation`);
* **referral** — a consumer referral is an ordinary :class:`Invite`, issued
  under a lifetime ceiling and bound to its inviter by
  :class:`~app.domains.growth.models.ConsumerReferralInvite`
  (:mod:`app.domains.growth.referral`);
* **analytics** — a strict whitelist over the existing ``app_events`` table,
  for the few interactions the server cannot otherwise see
  (:mod:`app.domains.growth.analytics`);
* **metrics** — admin-only aggregates with written definitions
  (:mod:`app.domains.growth.metrics`).

See ``docs/architecture/CONSUMER_GROWTH.md``.
"""
