# Running GlamGenius

Operations, backup, restore, monitoring, and incident response for the GlamGenius V2-only Personal Appearance Operating System.

---

## 1. Architecture Components

The current greenfield architecture consists of:

*   **Supabase PostgreSQL:** The primary application database storing profiles, inventory, looks, plans, routines, progress, and memory.
*   **Supabase Auth:** Handles all user authentication and registration.
*   **Supabase Storage:** Private object storage for all user media and uploads.
*   **FastAPI:** The backend API application server.
*   **Account-deletion worker:** A durable background worker that securely erases data across all storage layers.
*   **Gemini:** The sole AI provider accessed through the backend AI gateway.
*   **Sentry:** Used for approved privacy-scrubbed monitoring and crash reporting.

---

## 2. Backup and Restore

### PostgreSQL — Nightly and Point-in-time

Configure continuous archiving and nightly logical backups for the PostgreSQL database.

```bash
# Nightly logical backup
pg_dump \
  --dbname="$POSTGRES_URL_SYNC" \
  --format=custom \
  --file="glamgenius-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Keep 35 days of dumps and WAL.

### Object Storage

Enable versioning and cross-region replication on the Supabase Storage bucket. Do not write a custom copier.

### Restore Verification

**Once a month**, restore the latest dump into a scratch database and verify the schema and reference data.

```bash
createdb glamgenius_restore
pg_restore --dbname=glamgenius_restore --clean --if-exists glamgenius-<timestamp>.dump
POSTGRES_URL=postgresql+asyncpg://.../glamgenius_restore python -m alembic upgrade head
```

Verify reference data:
```sql
SELECT count(*) FROM ingredients;
SELECT count(*) FROM metric_definitions;
SELECT count(*) FROM plans;
```

---

## 3. Monitoring

### Crash-free sessions

Configure the mobile crash reporter with `SENTRY_DSN`.

**Never send an image, an ingredient list, a memory fact or any personal data to the crash reporter.** Scrub request bodies before they leave the device.

### Application Health

`GET /api/v2/health` reports liveness.
`GET /api/v2/ready` reports readiness for traffic, including PostgreSQL connectivity and configuration validity.

Alert on:
*   `postgres: down` (Page immediately)
*   Container crash loops or readiness probe failures

### Cost and Abuse Controls

GlamGenius is a private beta. Cost and abuse controls are enforced via rate limits.

Monitor the following metrics to ensure abuse limits are effective:
*   BETA_AI_REQUESTS_PER_HOUR
*   BETA_SCAN_LIMIT_PER_MONTH
*   BETA_STYLE_LIMIT_PER_MONTH
*   BETA_SHOPPING_CHECK_LIMIT_PER_MONTH

These are cost controls, not payment plans.

---

## 4. Incident Response

*   **Supabase Outage:** The app relies on Supabase for Auth, DB, and Storage. If Supabase is down, the app is down. Monitor the Supabase status page.
*   **Gemini Outage:** The AI gateway acts as a circuit breaker. When the provider is down, the system will timeout gracefully and fall back to deterministic responses where applicable.
*   **Sentry Outage:** Telemetry will be lost, but the application will continue to function normally.

---

## 5. Account Deletion Worker

The account deletion worker operates continuously to ensure user data is erased completely across the database, object storage, and Supabase Auth.
Check the durable worker heartbeat in the database to ensure the worker is processing the queue.

---

## 6. Notification Worker (hourly)

The proactive notification worker is a **scheduled batch**, not a daemon. It has
to be invoked by the host once per hour:

```bash
python -m app.workers.notifications
```

### What it does per run

It looks at every account that has both notifications and native push switched
on, compiles that account's Today plan through the same canonical compiler the
`/today` endpoint uses, and queues at most the one delivery the account's own
preferences allow. Quiet hours, the daily cap and deduplication are applied
inside that decision, not by the scheduler.

### Operational properties

* **Repeat-safe.** A delivery is claimed in the database and committed *before*
  the Expo call, so a second run in the same hour cannot send it twice. No
  transaction is held open across the network request.
* **Isolated per account.** One account's failure is caught, rolled back and
  logged as `notification_account_failed`, and the batch continues. The loop
  works from plain account identifiers rather than ORM rows precisely so a
  rollback cannot poison the accounts still queued behind it.
* **No late catch-up.** A run outside an account's preferred local hour does not
  fire a backdated notification. A missed hour is simply a missed hour.
* **Disabled devices self-heal.** An Expo `DeviceNotRegistered` outcome disables
  that specific token, and nothing else.

### Scheduling requirement

* Invoke it **once per hour**. More often is wasted work; less often silently
  drops notifications for the hours you skip.
* **Avoid overlapping invocations** where the scheduler supports it. The claim
  boundary makes overlap safe rather than duplicating, but a run that overlaps
  itself is a sign the batch is taking longer than an hour and should be
  investigated.
* Exit status is not a delivery count. Treat a non-zero exit as a failed run.

This repository deliberately does **not** pick your scheduler. Any of cron, a
systemd timer, or a managed scheduled-job feature is fine; the requirement is
only "once an hour, one process". The application cannot detect whether the
schedule exists, so `python -m app.release_readiness` reports the worker as
`requires_host_scheduler` rather than claiming it is configured.

Illustrative only — not a mandated architecture:

```cron
# crontab: hourly, on the hour
0 * * * * cd /srv/glamgenius/backend && /srv/glamgenius/venv/bin/python -m app.workers.notifications >> /var/log/glamgenius/notifications.log 2>&1
```

### If the scheduler is not running

Proactive push stops. Nothing else breaks: Today, Style, Care, Plan and You all
remain fully usable, because the worker only *pushes* what the app already
computes on demand. This is a degradation, not an outage.

### Running one cycle by hand (staging smoke test)

```bash
cd backend
python -m app.workers.notifications
```

It processes the current eligible set once and exits. Safe to run repeatedly:
anything already claimed for the hour will not be sent again.

---

## 7. Release Readiness Check

Before deploying to a staging or production tier:

```bash
cd backend
python -m app.release_readiness          # human-readable
python -m app.release_readiness --json   # machine-readable
```

Exit `0` means ready for the feature set that is actually configured; exit `1`
means something required is missing, placeholder, or invalid. The report prints
configuration **key names and statuses only** — never a secret's value — so it
is safe to paste into a ticket or a deployment log.

It is an explanation layer over `validate_production_configuration()`, which is
what actually refuses to start a misconfigured process. If that validation
rejects the environment, the report repeats its reason verbatim and reports
`not_ready`. The two cannot disagree.
