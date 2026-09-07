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

Nothing in this repository schedules it. Until you install the schedule in
§6.3, proactive notifications do not happen at all.

### 6.1 What it does per run

It looks at every account that has both notifications and native push switched
on, compiles that account's Today plan through the same canonical compiler the
`/today` endpoint uses, and queues at most the one delivery the account's own
preferences allow. Quiet hours, the daily cap and deduplication are applied
inside that decision, not by the scheduler.

Each run ends with one log line and one database heartbeat.

### 6.2 Operational properties

* **Repeat-safe.** A delivery is claimed in the database and committed *before*
  the Expo call, so a second run in the same hour cannot send it twice. No
  transaction is held open across the network request. Proven by
  `backend/tests/test_notification_worker_operations.py`.
* **Isolated per account.** One account's failure is caught, rolled back and
  logged as `notification_account_failed`, and the batch continues. The loop
  works from plain account identifiers rather than ORM rows precisely so a
  rollback cannot poison the accounts still queued behind it.
* **No late catch-up.** A run outside an account's preferred local hour does not
  fire a backdated notification. A missed hour is simply a missed hour.
* **Disabled devices self-heal.** An Expo `DeviceNotRegistered` outcome disables
  that specific token, and nothing else.

### 6.3 Installing the schedule

Two supported ways. **systemd is recommended** — it gives you failure alerting
and a way to ask "is this actually scheduled?", which cron does not.

Unit files are in `scripts/systemd/`. Adjust the paths (`/srv/glamgenius`,
`glamgenius` user) to your deployment, then:

```bash
# 1. Environment file — secrets live here, never in the unit.
sudo install -d -m 0755 /etc/glamgenius
sudo cp env.example /etc/glamgenius/notifications.env
sudo chmod 0600 /etc/glamgenius/notifications.env
sudo editor /etc/glamgenius/notifications.env      # POSTGRES_URL, SUPABASE_*, etc.

# 2. Install the units.
sudo cp scripts/systemd/glamgenius-notifications.service \
        scripts/systemd/glamgenius-notifications.timer \
        scripts/systemd/glamgenius-notifications-alert@.service \
        /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Prove one cycle works before scheduling it.
sudo systemctl start glamgenius-notifications.service
sudo journalctl -u glamgenius-notifications -n 30 --no-pager

# 4. Turn on the hourly schedule.
sudo systemctl enable --now glamgenius-notifications.timer

# 5. Confirm it is really scheduled, and when it next fires.
systemctl list-timers glamgenius-notifications.timer
```

`Persistent=false` in the timer is deliberate: after a reboot, systemd must not
fire a catch-up run for an hour that has already passed.

**Cron alternative.** Cron has no failure alerting; if you use it, rely on the
`/api/v2/admin/workers` check in §6.5 instead.

```cron
# Hourly, on the hour. MAILTO makes cron email a failing run.
MAILTO=ops@example.com
0 * * * * cd /srv/glamgenius/backend && /srv/glamgenius/venv/bin/python -m app.workers.notifications >> /var/log/glamgenius/notifications.log 2>&1
```

Whichever you use, the requirement is the same: **once an hour, one process.**
More often is wasted work; less often silently drops the hours you skip.

### 6.4 Reading a run

Every run logs exactly one summary line:

```
notification_worker_run outcome=ok accounts_considered=412 accounts_failed=0 notifications_sent=37 duration_ms=1840
```

| Field | Meaning |
| --- | --- |
| `outcome` | `ok`, or `degraded` when at least one account failed |
| `accounts_considered` | Accounts with notifications and push switched on |
| `accounts_failed` | Accounts that raised; each also logs `notification_account_failed` |
| `notifications_sent` | Deliveries Expo accepted. Routinely far below `accounts_considered` — most accounts are not in their preferred hour |
| `duration_ms` | Wall-clock time for the cycle |

Exit codes, which is what the scheduler acts on:

| Code | Meaning |
| --- | --- |
| `0` | The cycle completed |
| `2` | The cycle failed. systemd runs the alert unit; cron emails `MAILTO` |
| `3` | A manual run was refused — see §6.6 |

```bash
journalctl -u glamgenius-notifications --since "24 hours ago" | grep notification_worker_run
```

### 6.5 Noticing a run that never happened

A batch process cannot report its own absence, so the *last* run is the
evidence. Every cycle writes a heartbeat to `system_worker_status`, and
`GET /api/v2/admin/workers` (admin token required) reports it under
`scheduled_workers`:

```json
{"worker_name": "notification_worker", "expected_interval_seconds": 3600,
 "state": "healthy", "last_heartbeat_age_seconds": 812,
 "detail": "Last run 812s ago."}
```

| `state` | What it means | What to do |
| --- | --- | --- |
| `healthy` | A run finished within the last two hours | Nothing |
| `never_run` | No run has ever been recorded | The schedule was never installed. Do §6.3 |
| `missed` | The last run is more than two hours old | The timer or cron is stopped, or the host is down. `systemctl list-timers` |
| `failing` | The last run reported an error | `journalctl -u glamgenius-notifications -n 50` |

**Alerting on it.** Poll that endpoint every 15 minutes from whatever you
already use for uptime checks, and alert when `state` is not `healthy`. This is
the check that catches the failure mode that matters most — a scheduler nobody
ever installed — because it does not depend on the worker running to fire.

If Sentry is configured, a failed or degraded run also captures an event. A
missing Sentry DSN is a no-op.

### 6.6 Testing it by hand, without notifying customers

Two independent guards, both outside the worker's decision logic:

```bash
# Full cycle, transport switched off. No socket is opened, so nothing can
# reach a device. Safe to run against production data.
python -m app.workers.notifications --dry-run

# One account, and only an account you have nominated. Really delivers.
export NOTIFICATION_TEST_ACCOUNT_IDS=8f14e45f-ceea-467a-9f6a-1c0e5a2e0000
python -m app.workers.notifications --account 8f14e45f-ceea-467a-9f6a-1c0e5a2e0000
```

* `--dry-run` is enforced inside `push.send()` itself, so no caller can route
  around it. A dry run never marks a delivery as accepted, so it cannot consume
  an account's daily cap.
* `--account` refuses (exit `3`) when `NOTIFICATION_TEST_ACCOUNT_IDS` is empty,
  and refuses any account not in it. Testing by hand therefore cannot become
  notifying the customer base.
* `PUSH_DELIVERY_MODE=dry_run` set in the environment does the same thing
  globally. `validate_production_configuration()` refuses to start staging or
  production with it set, so it cannot be left on by accident.

### 6.7 If the scheduler is not running

Proactive push stops. Nothing else breaks: Today, Style, Care, Plan and You all
remain fully usable, because the worker only *pushes* what the app already
computes on demand. This is a degradation, not an outage.

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

---

## 8. First governed skin-care knowledge activation

The first time GlamGenius tells a customer BUY, it will be because an operator
ran the steps below, in order, by hand. Nothing here happens on deploy, on
startup, on migration, on a schedule, or in CI, and nothing here is idempotent
in the sense of "safe to wire into automation": it is safe to *re-run*, which is
a different thing and exists so that a human who loses their place can look
rather than guess.

The pack being installed is the reviewed Step 8I specification for petrolatum on
dry or tight-feeling skin. It is one exact pack. The tool that installs it
cannot install any other, cannot alter this one, and cannot decide anything.

```bash
cd /path/to/GlamGenius        # repository root, not backend/
python scripts/operate_step8i_petrolatum_release.py <operation> [options]
```

Every operation prints JSON. That output **may** contain explicit actor
attribution — a successful state-changing operation echoes the `--actor` you
supplied, because a governed write nobody can be traced to is worse than a noisy
one — together with governed release and evidence metadata: ids, versions,
statuses, content hashes, reason and verdict keys, and the reviewed BUY label.

It **must never** contain credentials, tokens, passwords, connection strings,
raw unexpected-exception messages, or customer or personal data. A failure the
tool did not anticipate prints a fixed structural result — `UNEXPECTED_ERROR`,
the exception's class name, and nothing else — because an arbitrary driver or
configuration exception can carry a connection string in its message and this
tool cannot prove otherwise. That failure is rolled back before it is reported.

### The sequence

```text
status
  → prepare
      → human identity review / verify / approve / publish
      → human personal-applicability review / verify / approve / publish
  → compile
      → human release review verification
      → validate
      → approve
  → activate  (exact release ID + exact expected content hash)
  → status
```

Emergency stop, at any point after activation:

```text
deactivate  (exact release ID + exact expected content hash)
```

### 1. `status`

```bash
python scripts/operate_step8i_petrolatum_release.py status
```

Read-only. It writes nothing, ever. It reports whether the substance, the
identity claim, the two reviewed sources, and the personal-applicability entry
exist and whether each **exactly** matches the reviewed pack; the content hash
the pack would compile to, when compilation is possible; every release carrying
that hash; the currently active release, if any; and whether the reviewed
customer sentence and the BUY label are present.

Where two records could both be the authority, `status` says so under
`ambiguities` and refuses to name one. That is the correct answer, not a
limitation: a human has to decide which record is real.

Run it before every other step and again after the last one.

### 2. `prepare`

```bash
python scripts/operate_step8i_petrolatum_release.py prepare --actor "your.name"
```

Creates only the **draft** authoring material that is missing: the substance,
the identity claim and its source, and the personal-applicability entry with its
two reviewed sources. It uses the ordinary Step 7A and Step 8G authoring paths.

It does not verify, approve, publish, compile or activate anything. Re-running
it creates nothing new. If a record already exists whose governed identity or
source metadata disagrees with the reviewed pack, it refuses and names the
conflict rather than overwriting the record or authoring a second one beside it.

`--actor` is required. There is no default operator.

### 3. Human review of the evidence — not automatable

Two separate governance events, both performed by people through the existing
admin surfaces:

* the **identity** claim earns source verification, approval and publication;
* the **personal-applicability** entry earns its own verification, approval and
  publication.

The operator tool cannot do any of this and must not be extended to. Source code
that matches the pack is not a review of the pack; it only means nobody has
edited the file.

### 4. `compile`

```bash
python scripts/operate_step8i_petrolatum_release.py compile --actor "your.name"
```

Succeeds only once exactly one published personal-applicability entry matches
the pack. It serialises that entry, hands it to the pack's own compiler, and
creates a **draft** decision release from the result. It records the release ID,
version, status and content hash.

Nothing about the decision is chosen here. The signal, the policy, the BUY
action, the reason key and the citation all come from the reviewed pack.

Re-running reuses the exact matching release rather than creating another
version. A retired release carrying that hash is reported and **not** revived.

### 5. Human review of the release — not automatable

Through the existing Step 8H admin lifecycle, in this order: record the release
review verification, validate, approve. Three events, three decisions.

### 6. `activate`

```bash
python scripts/operate_step8i_petrolatum_release.py activate \
  --release-id "<uuid from compile>" \
  --expected-content-hash "<sha256 from compile>" \
  --actor "your.name"
```

All three arguments are required. There is no default release, no "latest", and
no automatic selection.

Before anything changes, the tool independently proves that the release exists,
that it is APPROVED, that its persisted hash is the one you named, that its
stored manifest still hashes to that value, that it is the Step 8I pack, that
recompiling from currently published evidence reproduces it exactly, that the
governed evidence still passes Step 8H validation, and that the reviewed
customer sentence and BUY label both resolve through the copy catalogue. Then it
calls the existing Step 8H activation. Afterwards it re-reads through the
runtime loader and proves that the release now serving production is the one you
named. Any failure rolls the whole thing back.

**If a different release is already active, activation is refused and this V1
path stops.** It never replaces live knowledge, and there is deliberately no
`--force` and no `--replace`.

It also **cannot retire that other release for you.** The `deactivate` command
below operates only on the exact Step 8I pack and refuses anything else, by
design — this tool is not a general release manager. A different active release
must be retired through its own existing governed release lifecycle, as an
explicit human decision by whoever owns it. Once that has happened, run `status`
again, confirm what the database actually holds, and only then consider
activating the Step 8I release.

Re-running with the same ID and hash on an already-active release reports
"already active" and changes nothing.

### 7. `deactivate` — the emergency stop, for the Step 8I release only

```bash
python scripts/operate_step8i_petrolatum_release.py deactivate \
  --release-id "<uuid>" \
  --expected-content-hash "<sha256>" \
  --actor "your.name"
```

Retires that exact release and activates nothing in its place. Production falls
back to no reviewed decision knowledge and shows a governed non-decision — no
BUY, no WAIT, no SKIP, no product reason, no product citation.

It refuses if the hash does not match, if the release is not the Step 8I pack,
or if it is not the release currently active. That second refusal is deliberate
and is not a gap: this is an emergency stop for one exact pack, not a general
release-deactivation command, so it will not retire somebody else's active
release even when an operator names it correctly. It never restores a previous
release either: reinstating retired knowledge is a fresh decision with its own
review.

### Rules that do not bend

* **Never use bootstrap or reference-data seeding to create this authority.**
  Seeded data is not reviewed evidence, and the seed path deliberately creates
  none of it.
* **Never run any of this automatically** — not on deploy, not on startup, not
  from a migration, not from a cron job, not from CI.
* **Never use "latest".** There is no such selector anywhere in this path, and
  adding one would defeat it.
* **Activation requires an exact release ID and the exact reviewed content
  hash.** The hash is what makes the ID safe: naming a release without naming
  what it should contain is how the wrong one gets activated by a command that
  looks correct.
* **Zero active releases is safer than an uncertain active release.** If you are
  unsure what is live, deactivate — but only the exact Step 8I release; this
  tool will not touch anybody else's.
* **The tool does not replace human review.** It writes drafts and it installs
  things people have already approved. That is all.
* **A different active release blocks this path, and this tool cannot clear
  it.** That is intended. Retiring it belongs to its own governed lifecycle and
  its own human decision.
* **Production activation happens only after the implementation itself has
  independently passed review and merged** — never from the branch that
  introduces it.

### Admin API examples

Where the review steps are performed through the admin API rather than a
console, use placeholders and never commit real values:

```bash
curl -X POST "https://<your-api-host>/api/v2/admin/personal-decision-releases/<release-id>/verification" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{ ... reviewed attestations ... }'
```

Never paste a real token, password, production hostname or connection string
into a ticket, a log, or this document.
