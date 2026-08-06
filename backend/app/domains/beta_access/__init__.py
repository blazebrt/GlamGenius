"""Beta access: invites, invite redemptions and beta usage events.

**Not financial.** This domain replaces the V1 ``invite_codes`` Mongo collection
and the commercial ``FREE_SCANS_PER_MONTH`` / ``INVITE_SCANS_PER_MONTH``
counters with a single neutral abuse-and-cost control surface.

* ``Invite`` — an admin-issued code with a usage limit and an expiry.
* ``InviteRedemption`` — one row per successful redemption. Atomic.
* ``BetaUsageEvent`` — one row per counted, successful, cost-bearing action
  (an AI call, a scan, a style run, a shopping check). Failed work is not
  written; retried idempotent work is deduplicated by ``idempotency_key``.

Everything is scoped to the Supabase Auth user UUID.
"""
