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
