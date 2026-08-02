# Running GlamGenius

Backup, restore, monitoring, rate limits and the payment runbook. Written for
whoever is on call, which may be somebody who has never seen this codebase.

---

## 1. What the data is, and what losing it costs

| Store | Holds | If you lose it |
|---|---|---|
| **PostgreSQL** | Everything V2: profiles, inventory, looks, plans, routines, progress, memory, **and all billing** | Catastrophic. Billing history is a legal record |
| **MongoDB** | V1: users, auth, invites, scans | Users cannot sign in |
| **Object storage** | Uploaded images | Photos gone; the app still works |

Two of these have different urgency. Object storage is recoverable in the sense
that a user can re-upload. **`billing_audit_events`, `payment_events`, `orders`
and `refunds` are not** — they are the record that answers a chargeback, and
they are append-only for exactly that reason.

---

## 2. Backup

### PostgreSQL — nightly, plus point-in-time

```bash
# Nightly logical backup. Custom format so you can restore selected tables.
pg_dump \
  --dbname="$POSTGRES_URL_SYNC" \
  --format=custom \
  --file="glamgenius-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

`POSTGRES_URL_SYNC` is the same database as `POSTGRES_URL` with the
`+asyncpg` driver suffix removed — `pg_dump` speaks libpq, not asyncpg.

Turn on continuous archiving as well. A nightly dump means up to 24 hours of
lost payments, which is not acceptable for the billing tables:

```
wal_level = replica
archive_mode = on
archive_command = 'test ! -f /archive/%f && cp %p /archive/%f'
```

Keep **35 days** of dumps and WAL. That covers a monthly billing cycle plus a
few days, so any disputed charge is inside the window.

### MongoDB — nightly

```bash
mongodump --uri="$MONGO_URL" --db="$DB_NAME" \
  --archive="glamgenius-mongo-$(date -u +%Y%m%dT%H%M%SZ).archive" --gzip
```

### Object storage

Enable versioning and cross-region replication on the bucket. Do not write your
own copier — the storage provider does this better, and media is the least
critical of the three.

### Verify the backup, not just the job

A backup nobody has restored is a hope. **Once a month**, restore the latest
dump into a scratch database and run the check in §3.3. Put it in the calendar.

---

## 3. Restore

### 3.1 Full restore

```bash
createdb glamgenius_restore
pg_restore --dbname=glamgenius_restore --clean --if-exists glamgenius-<timestamp>.dump

# Bring the schema to the version the running code expects.
POSTGRES_URL=postgresql+asyncpg://.../glamgenius_restore python -m alembic upgrade head
```

Then point the application at it and restart.

### 3.2 Point-in-time (the one you want after a bad deploy)

```bash
# In recovery.conf / postgresql.auto.conf on the restored data directory:
restore_command = 'cp /archive/%f %p'
recovery_target_time = '2026-08-02 14:30:00+05:30'
```

Choose a target **before** the incident, not after.

### 3.3 Verify a restore

```sql
-- Migrations are at the version the code expects.
SELECT version_num FROM alembic_version;

-- Reference data seeded (Phases 6, 7, 8).
SELECT count(*) FROM ingredients;          -- 44
SELECT count(*) FROM metric_definitions;   -- 13
SELECT count(*) FROM plans;                -- 3

-- Billing history is intact. These must never go backwards.
SELECT count(*) FROM payment_events;
SELECT count(*) FROM billing_audit_events;

-- Nobody has access they did not pay for.
SELECT plan_key, count(*) FROM entitlements
WHERE revoked_at IS NULL GROUP BY plan_key;
```

Then, from the application:

```bash
curl -s https://<host>/api/v2/health | jq
# status: healthy, postgres: up, billing.configured as expected
```

### 3.4 After any restore, re-check billing

A restore rewinds the database but **not the payment provider**. Anything that
happened between the backup and the restore point exists at Razorpay and not
here.

1. Pull the provider's event list for the gap window from their dashboard.
2. Replay each one at `POST /api/v2/billing/webhook` with a valid signature.

This is safe to do bluntly. Every webhook is deduplicated on
`(provider, provider_event_id)`, so replaying events that did survive the
restore is a no-op rather than a double grant. That property is what makes this
runbook short.

---

## 4. Monitoring

### Crash-free sessions

Configure the mobile crash reporter with `SENTRY_DSN` (or the equivalent for
your chosen tool). What to watch:

| Signal | Target | Why |
|---|---|---|
| Crash-free sessions | > 99.5% | Below this, people stop opening the app |
| Crash-free users | > 99.0% | One user crashing every session is invisible in the session metric |

**Never send an image, an ingredient list, a memory fact or a billing
identifier to the crash reporter.** Scrub request bodies before they leave the
device. A crash report containing somebody's face defeats the entire privacy
position of this product.

### Application health

`GET /api/v2/health` reports PostgreSQL, the AI provider and the billing
provider. Alert on:

| Condition | Severity |
|---|---|
| `postgres: down` | Page immediately |
| `billing.available: true` **and** `billing.configured: false` | Page — the app is offering to take money it cannot take |
| `status: degraded` for > 2 minutes | Page |
| `ai_provider_configured: false` | Warn — the app still works, deterministically |

That second row is worth reading twice. It is the state where a user taps
"Continue with Plus" and nothing can happen.

### Payment health

```sql
-- Webhooks that could not be matched to an order. Should be zero.
SELECT count(*) FROM payment_events
WHERE outcome = 'unmatched' AND created_at > now() - interval '1 day';

-- Orders stuck unpaid for over an hour: the webhook may not be arriving.
SELECT count(*) FROM orders
WHERE status = 'created' AND created_at < now() - interval '1 hour';
```

Alert on either being non-zero. An unmatched payment means somebody paid and
did not get what they paid for, which is the worst bug this system can have.

### AI cost

`operations.roll_up_costs()` writes per-feature daily spend into
`feature_cost_daily`. Run it nightly:

```bash
python -c "
import asyncio
from app.domains.billing import operations
from app.shared.database import sql

async def main():
    factory = sql.get_sessionmaker()
    async with factory() as session:
        await operations.roll_up_costs(session)
        await session.commit()

asyncio.run(main())
"
```

Watch **cost per run per feature**, not total spend. Total spend tells you the
bill; cost per run tells you which plan limit is mispriced.

---

## 5. Rate limits, retries and circuit breakers

| Boundary | Behaviour | Where |
|---|---|---|
| Signed-out preview | 3 per IP per window, then 429 | `security._assert_preview_quota` |
| Paid features | Entitlement check before work, credited back on failure | `billing/entitlements.py` |
| AI provider | Timeout `AI_TIMEOUT_SECONDS`, failure recorded, deterministic result stands | `ai_gateway/gateway.py` |
| Payment provider | 20s timeout; failure raises `ProviderUnavailableError`, never a false success | `providers/razorpay.py` |
| Webhooks | Deduplicated by unique constraint; safe to retry forever | `billing/service.py` |

The AI gateway is the circuit breaker that matters: when the provider is down,
every phase falls back to its deterministic engine and says
`explanation_source: deterministic`. Nothing fabricates a result.

---

## 6. Payment incident runbook

**"I was charged twice."**

```sql
SELECT id, provider_order_id, amount_inr, status, created_at
FROM orders WHERE account_id = '<account>' ORDER BY created_at DESC;

SELECT provider_event_id, kind, outcome, amount_inr, created_at
FROM payment_events WHERE account_id = '<account>' ORDER BY created_at DESC;
```

Two `orders` rows with `status = 'paid'` is a genuine double charge — refund one
at the provider; the `refund.processed` webhook revokes exactly what that order
bought. Two `payment_events` with `outcome = 'duplicate'` is the system working
correctly: the provider retried and we processed once.

**"I paid and nothing happened."**

Check `payment_events` for the order. No row means the webhook never arrived —
re-send it from the provider dashboard. A row with `outcome = 'unmatched'` means
it arrived but could not be matched; the detail column says why.

**"I want a refund."**

Refund at the provider, not in this database. The webhook does the rest, and
doing it by hand would leave the audit trail saying something different from
what happened.

---

## 7. Deploying a price change

Prices are configuration. There is no code change and no deploy of new logic:

```bash
PLUS_MONTHLY_INR=449 PLUS_YEARLY_INR=3999 EVENT_PASS_PRICE_INR=549
# restart the API
```

Orders already placed keep the amount they were placed at — `orders.amount_inr`
is frozen at checkout, so a price change never rewrites what somebody was
actually charged.

---

## 8. Switching billing off in a hurry

```bash
SUBSCRIPTIONS_AVAILABLE=false   # restart
```

Every checkout route refuses at the first step, the app hides payment buttons,
and `/api/v2/billing/offers` still lists the plans with an honest "nothing to
pay for yet". Existing entitlements keep working — turning off sales does not
take away what people have already bought.
