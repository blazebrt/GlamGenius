# Phase 8 — Monetisation, Premium Polish, and Scalable Release

Turning the validated V2 product into something that can take money reliably —
and monetising decisions, memory and planning rather than model usage.

---

## 1. Baseline

Verified before any Phase 8 code was written, on `main` with Phase 7 merged.

| | |
|---|---|
| Baseline commit | `af66f6e` (merge of PR #17, Phase 7) |
| Branch | `claude/code-handover-prep-8uhjje`, restarted from `origin/main` |

Phases 1–7 confirmed complete and passing:

| Check | Result |
|---|---|
| `alembic upgrade head` | clean |
| `alembic check` | no drift |
| Backend pytest | **408 passed** |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings |
| Jest | **184 passed** |

The same environment deviation as Phases 4–7 applies: the compose test image
cannot build here (`deb.debian.org` blocked by the egress policy), so the
databases are the exact containers the compose file specifies and the test
process runs on the host with the identical environment block.

---

## 2. Razorpay, read before written

This project forbids writing integration code for an outside service from
memory. Before a line of payment code, I read Razorpay's current published
documentation and cross-checked every detail against their own Python SDK
(`razorpay/utility/utility.py`). What that confirmed:

- **Webhook signature** — HMAC-SHA256 of the **raw request body**, keyed with
  the *webhook secret* (a different secret from the API key secret), hex digest,
  in the `X-Razorpay-Signature` header, compared with `hmac.compare_digest`.
- **Checkout callback signature** — HMAC-SHA256 of the literal string
  `"{order_id}|{payment_id}"`, keyed with the *API key secret*.
- **Payload envelope** — `{entity, account_id, event, contains, payload, created_at}`.
- **Amounts in paise**, so ₹499 is `49900`. The conversion happens in exactly
  one place, because getting it wrong by a factor of a hundred is the classic
  integration bug.

**No dependency was added.** Verification is `hmac` and `hashlib` from the
standard library — which is precisely what the SDK does — and order creation
goes over `httpx`, already present.

Two details that would each have broken production silently, and are commented
in the code so they survive the next edit:

- The route passes the **raw bytes** through untouched. Re-serialising a parsed
  body changes whitespace and key order, and every signature stops matching.
- The webhook secret and the API key secret are different values. Swapping them
  means every webhook fails verification and nobody's payment is ever honoured.

---

## 3. The rule the whole phase is built around

> Never grant access based only on a frontend callback.

Access is granted in exactly one function — `service._apply_event` — and only
for an event whose signature this server verified. The shape enforces it: no
provider method returns an entitlement, and no request schema has a field for a
plan, an amount, or a payment status. Tests assert that a client sending
`plan_key`, `account_id`, `status` or `amount_inr` gets a 422.

Three production failure modes get explicit handling because they are routine,
not exotic:

| Failure | Handling | Test |
|---|---|---|
| **Duplicate** — providers retry until 2xx | Unique constraint on `(provider, provider_event_id)`; second delivery collides and is answered "already handled" | Same webhook sent 5×, one subscription, one grant |
| **Delayed** — event arrives hours late | Nothing reads the clock to decide what a payment means | Event stamped 2 hours ago still grants |
| **Out of order** — renewal before activation | `Subscription.last_event_epoch` holds the provider timestamp of the last event applied; older events are recorded and skipped | Cancellation stamped *before* the activation leaves the subscription active |

Deduplication is a **database constraint, not an application check**. Two
workers processing the same retried webhook can both pass a check; they cannot
both win an insert.

---

## 4. Four real bugs found by the tests

Worth listing, because each would have shipped and each was found by a test
rather than by reading.

**1. `NULL != NULL` silently disabled the free-grant constraint.** The
entitlement uniqueness key included a nullable `source_id`, and PostgreSQL
treats NULL as distinct from NULL — so `ON CONFLICT DO NOTHING` never fired for
free grants. Every read inserted another row and reset the user's allowance,
meaning free limits could be bypassed indefinitely by reloading. Fixed with a
NOT NULL `grant_key` column carrying the source id as text or the literal
`"free"`, so the constraint actually constrains.

**2. Revocations were invisible to the read that followed them.** This project
builds sessions with `autoflush=False`. `revoke_for_source` mutated ORM objects
and the snapshot query in the same request still saw them as active — a refunded
plan would have kept working until the next request. Fixed with explicit
`session.flush()` calls, commented so nobody removes them.

**3. `GET /entitlements` did not settle expired subscriptions.** Only
`/billing/status` did, so the endpoint the app actually uses to decide what to
show would have reported Plus after a subscription ended. Fixed by moving
settlement into `entitlements.py` and having every read path call it; the
billing service now delegates, so there is one implementation.

**4. A cancelled subscription vanished from the snapshot.** The subscription
query filtered to active/grace/past_due, so cancelling made the app stop showing
a plan the user had already paid for through to period end.

---

## 5. The commercial model

Three products, no hard-coded price anywhere.

**Free** is a real plan, not a locked door: the appearance profile, 40 items,
one occasion styled in full, three shopping checks, and basic routines. Somebody
who never pays has a working app.

**Event Pass** — one occasion, done properly. Three genuinely different complete
looks, six revisions, a preparation timeline, the gap analysis, and a shareable
lookboard, for a fixed window. Bought for a wedding on Saturday, not as a
subscription somebody forgets to cancel.

**Plus** — the recurring product. Today, the weekly planner, the full shelf and
routine intelligence, complete progress, long-term memory, packing.

**Prices are configuration.** `catalogue.py` contains no literal amount; every
figure comes from `app.config`, and a test asserts it by reading the module
source. `orders.amount_inr` freezes what was charged, so a later price change
never rewrites somebody's history.

### The paywall sells outcomes

The brief bans leading with "unlimited AI", "unlimited scans" or "more tokens".
`FORBIDDEN_BENEFIT_WORDS` is swept over every benefit line, tagline and headline
**at import**, so a plan selling model usage cannot load — and therefore cannot
reach a screen. A test constructs exactly that plan and asserts the guard fires.

What it leads with instead is the brief's own list: plan every week, use your
complete wardrobe, make better shopping decisions, prepare for important events,
recover value from what you are not using, build routines from what you own,
keep long-term memory.

---

## 6. Entitlements

`limit_value = None` means unmetered *for that feature* — never "unlimited AI",
which this product does not sell.

- **Consumption is recorded before the work runs**, with an idempotency key
  under a unique constraint, so a retried request cannot spend an allowance
  twice.
- **Failed work is credited back.** A failed answer must not cost somebody one
  of their decisions, and `/account/usage` shows credits explicitly.
- **The most generous entitlement wins.** Somebody with Free and an Event Pass
  is not held to the free limit.
- **Expiry is enforced at read time**, not only by a job, so a job that never
  runs cannot leave somebody with access they no longer have.
- **The offline cache is bounded.** The snapshot carries `cache_seconds` and
  `valid_until`; a cache that never expires is a refunded plan that keeps
  working.

---

## 7. Operations

`docs/OPERATIONS.md` covers backup, restore, monitoring, rate limits and a
payment incident runbook. The parts worth highlighting:

**Restore does not rewind the payment provider.** Anything that happened between
the backup and the restore point exists at Razorpay and not here. The runbook
says to replay the provider's events at the webhook — which is safe to do
bluntly, because deduplication makes replaying already-processed events a no-op.
That property is what makes the runbook short.

**Crash reporting must never carry an image, an ingredient list, a memory fact
or a billing identifier.** A crash report containing somebody's face would
defeat the entire privacy position of this product.

**One health signal deserves paging on its own**: `billing.available: true`
with `billing.configured: false` is the state where a user taps "Continue with
Plus" and nothing can happen.

**Experiments are pricing and copy only.** `ALLOWED_SURFACES` is a closed list
and `assign()` refuses anything else — A/B testing a safety rule, a medical
boundary or a privacy default would mean deliberately giving some users the
weaker version of a protection. A test asserts an experiment on
`medical_disclaimer` is refused.

**Virtual try-on is a seam, not a feature.** The brief says not to build it
until a provider is selected and approved, so there is a `VirtualTryOnProvider`
protocol, a job table with cost tracking, a flag defaulting off, and an
implementation that refuses honestly. Deliberately *not* a stub returning a
placeholder image: that would be a fabricated picture of somebody wearing
something they have never worn.

---

## 8. Verification

| Check | Result |
|---|---|
| `alembic upgrade head` on a **fresh** database | clean, 0001 → 0008 |
| `alembic downgrade -1` then re-upgrade | clean |
| `alembic check` | No new upgrade operations detected |
| Backend pytest | **463 passed** (408 baseline + 55 new) |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings (unchanged) |
| Jest | **208 passed** (184 baseline + 24 new) |

Also verified: with `v2_billing` removed from `V2_FEATURES` every Phase 8 route
returns 404 while Phases 1–7 keep working; health reports billing state without
leaking key material; no V1 file is touched; the `image_base64[:80]` truncation
in `routes/scan.py` is byte-identical to `main`; no dependency was added.

### The full critical user journey

`tests/test_critical_journey.py` runs all thirteen steps in sequence on one
account: register → profile → inventory → occasion look → purchase evaluation →
Today → weekly plan → routine → progress → buy → verify entitlement → expire →
delete. Deliberately one long test rather than thirteen short ones: every phase
already has passing unit tests, and what nobody had proved is that a real person
can get from signing up to paying to deleting without hitting a wall.

It asserts the boundaries as it goes — an unpaid order unlocks nothing, every
routine step explains itself, every warning still carries a reviewed rule id,
every progress metric still has a documented formula, and an expired plan falls
back to a Free tier that still works.

Alongside it: the privacy regression (deleting an account removes personal data
but keeps the billing record, because a chargeback in eighteen months must be
answerable) and the authorization regression (one account cannot reach another's
inventory, occasions, orders, goals or memory).

---

## 9. Acceptance criteria

| Criterion | Where |
|---|---|
| Billing is verified server-side | `providers/razorpay.py`; `test_a_forged_webhook_grants_nothing` |
| Duplicate payment events cannot create duplicate access | unique constraint; `test_the_same_webhook_five_times_grants_once` |
| Refund and expiry correctly update entitlements | `_apply_refund`, `settle_expired_subscriptions`; four tests |
| The paywall communicates outcomes rather than AI volume | `FORBIDDEN_BENEFIT_WORDS`; `test_a_plan_selling_unlimited_ai_is_rejected` |
| Free, Event Pass and Plus are configured | `catalogue.py`, seeded by migration 0008 |
| Critical screens meet accessibility requirements | 24 frontend tests: labels, roles, states, reduced motion |
| Crash-free-session monitoring configured | `docs/OPERATIONS.md` §4, with scrubbing rules |
| Feature-level AI cost is measurable | `feature_cost_daily`; `test_ai_cost_is_measurable_per_feature` |
| Full critical user journey passes | `test_the_full_critical_user_journey` |
| Backup and restore steps documented | `docs/OPERATIONS.md` §2–3 |
| `PHASE_8_REPORT.md` exists | this file |
| The phase is committed | `feat(v2): launch premium billing and production-ready experience` |

---

## 10. What Phase 8 deliberately does not do

- **No virtual try-on product.** Interface, job table and cost tracking only.
- **No fixed salon-navigation module.** Professional review is a queue that
  says honestly that nobody has looked yet.
- **No experiments on protections.** Pricing and copy only.
- **No dependency added.** Signature verification is the standard library.
- **Billing stays off.** `SUBSCRIPTIONS_AVAILABLE=false` remains the default, as
  it has since Phase 1. Everything here is ready; nothing is on sale until that
  flag is flipped deliberately.

Stopping after Phase 8, as instructed.
