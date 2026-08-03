# Emergent hosting metadata audit

**Scope:** the `.emergent/` directory and `memory/PRD.md`.
**Baseline:** commit `89c57e5b1f786de3b631d90f29aa257109feb409` (main).
**Owner action required:** yes — items are marked below.
**Removed on this branch:** no `.emergent` files are deleted on
Work Package 1. See §5 "Decision" for the reasoning.

The document you are reading is the "audit unexpected files"
deliverable of Work Package 1 (Fix 3 + Fix 20). It answers the
questions the stabilisation brief asks:

- Are these files required by the active hosting platform?
- Who owns them?
- Do they execute in production?
- What secrets can they access?
- Should they be committed?
- Do they belong in this repository?
- Could the cron dispatcher's use of `curl --location-trusted` forward
  an Authorization header to an unintended redirected host?

Nothing here deletes files unless we have evidence they are
unnecessary. The stabilisation brief is explicit about that: **do not
delete hosting-required files without evidence**.

## 1. Inventory

### `.emergent/emergent.yml`

```json
{ "job_id": "<uuid>", "created_at": "<iso timestamp>" }
```

Two-field JSON identifying the preview job. Not executable. No
secrets. Committed by the platform when the preview environment is
first materialised.

- **Required by the hosting platform?** Yes. It is the anchor the
  Emergent agent-service uses to identify the preview environment
  when it reconciles crons (see §1.4).
- **Executes in production?** No — it is data, not code.
- **Secrets accessible?** None.
- **Decision:** keep as-is.

### `.emergent/system_deps.txt`

Blank / minimal file. Placeholder for system dependency declarations
the platform bootstrap may consume.

- **Executes?** No.
- **Secrets?** None.
- **Decision:** keep as-is.

### `.emergent/cron/webhook-crons`

`/etc/cron.d`-style crontab. In the audited baseline it contains one
line that runs `watch_crons.sh` every minute.

- **Executes?** Yes, inside the preview pod's `cron` daemon.
- **Secrets accessible?** Only the environment variables named in the
  file (`JOB_ID`, `CRON_API_URL`). No app secret in the crontab
  itself; the dispatcher reads `WEBHOOK_CRON_SECRET` from
  `/app/backend/.env` at dispatch time.
- **Decision:** keep as-is.

### `.emergent/cron/watch_crons.sh`

Reconciliation trigger. Compares the sha256 of `/app/.emergent/crons.yml`
against `applied.hash` and, on divergence, POSTs
`{"job_id": …, "scope": "preview"}` to
`$CRON_API_URL/internal/crons/reconcile`. Fire-and-forget, no
Authorization header, no user data in the body.

- **Executes?** Yes, once a minute.
- **Secrets accessible?** No app secret is read. The URL and JOB_ID
  are both non-secret.
- **Redirect handling?** `curl -sS -o /dev/null --max-time 15
  -X POST …`. No `--location`, so a 3xx is not followed. Safe.
- **Decision:** keep as-is.

### `.emergent/cron/webhook_crond.sh`

Supervisor entrypoint. Restores the crontab from the persistent PVC
copy, self-heals the `/etc/cron.d/webhook-crons` file, best-effort
runtime-installs `cron` if absent, then `exec`s `cron -f` (or
`crond -f`).

- **Executes?** Yes, as a supervisord program.
- **Secrets accessible?** None directly. It only sets up the crond
  process.
- **Runtime `apt-get install`.** The best-effort install path runs
  `sudo apt-get update && sudo apt-get install -y cron`. This is a
  hosting-platform decision, not app code. Flagged in §4 as a
  reviewer curiosity, not a change on this branch.
- **Decision:** keep as-is; flagged in §4.

### `.emergent/cron/dispatch_webhook.sh`

The dispatcher script. Fires **one** authenticated HTTP request per
scheduled cron entry with a Bearer token read from
`/app/backend/.env` (`WEBHOOK_CRON_SECRET`).

- **Executes?** Yes, per scheduled entry.
- **Secrets accessible?** Yes — `WEBHOOK_CRON_SECRET`. This is the
  per-app cron secret; treat it as an app secret.
- **Redirect handling — the reviewer question.**
  Baseline used
  `curl --location-trusted --max-redirs 2`. `--location-trusted`
  forwards the Authorization header on redirect even to a different
  host. In a well-behaved same-platform preview environment this is
  fine; if the DNS or ingress config ever redirects to an
  attacker-controlled host, that Bearer would leak.
- **Change on this branch:** the dispatcher now allow-lists the
  redirect targets. It follows a redirect **only** when the redirect
  target is on a hostname suffix approved for this deployment (the
  preview-domain suffixes owned by the platform). If the redirect
  points to anything else, the dispatcher falls back to a
  no-redirect request, so the Bearer is never sent to an
  unapproved host. See the diff in
  `.emergent/cron/dispatch_webhook.sh`.
- **Decision:** keep, with the redirect-allow-list change.

### `.emergent/cron/applied.hash`

Empty file in the audited baseline. It stores the sha256 the
reconciler has applied. Written by the platform on successful
reconcile.

- **Executes?** No.
- **Secrets?** None.
- **Decision:** keep as-is.

### `memory/PRD.md`

A rolling human-readable product-requirements document that the
Emergent agent workflow (the coding-assistant harness) writes and
reads at every session boundary. It is not read by application code
in production. It exists so the assistant does not lose stateful
context between agent runs.

- **Executes?** No.
- **Secrets?** None.
- **In production?** Not consumed at runtime. It ships in the git
  repository like any other Markdown file.
- **Decision:** keep, but move to a clearer location on a later work
  package if the file starts to conflict with product documentation.
  Not renamed on Work Package 1 because renaming it would rewrite
  every prior agent trace's expected path and add churn without a
  security benefit.

## 2. Secrets exposure summary

Only one file in the inventory reads an app secret:
`.emergent/cron/dispatch_webhook.sh` reads
`WEBHOOK_CRON_SECRET` from `/app/backend/.env`.

That secret is used exactly once, as the Bearer token on the outbound
POST. It is:

- never logged (the dispatcher's only stdout is the `dispatch complete`
  summary line — no headers, no body, no secret);
- never redirected to an untrusted host (after the change on this
  branch);
- rotated by rewriting `.env` (a platform operation, not a repository
  operation).

No other `.emergent` script has access to any user data, PII, image
byte, or Razorpay credential.

## 3. Cron dispatcher hardening — the redirect fix

### Baseline behaviour (unsafe under a mis-configured redirect)

```sh
curl -sS -o /dev/null -w '%{http_code}' \
  --max-time 10 \
  --location-trusted --max-redirs 2 \
  -X "$METHOD" \
  -H "Authorization: Bearer $WEBHOOK_CRON_SECRET" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Id: $RUN_ID" \
  -H "X-Webhook-Timestamp: $DISPATCH_TIME" \
  -d "$ENVELOPE" \
  "$ENDPOINT"
```

`--location-trusted` forwards the Authorization header on any
redirect, including to a hostname unrelated to the original.

### Behaviour on this branch

The dispatcher now:

1. Consults an allow-list of hostname suffixes
   (`WEBHOOK_ALLOWED_REDIRECT_SUFFIXES`, with a compiled-in default of
   the Emergent preview-domain suffixes).
2. Performs a first request with `--max-redirs 0`.
3. If the response is a 3xx, extracts the `Location` header.
4. Parses the Location host; if the host suffix matches the
   allow-list, re-issues the request against the redirect target with
   the Authorization header. If it does not match, logs
   `dispatch refused: redirect to unapproved host` and exits 0
   without following the redirect (fail closed — the Bearer is not
   sent to the unapproved host).

Same-platform redirects (e.g. from `preview.<host>` to
`internal.<host>`) still work, because both hostname suffixes are on
the allow-list. Any cross-host redirect (attacker-controlled DNS,
mis-configured ingress) is refused and logged.

### Test-mode

`WEBHOOK_ALLOWED_REDIRECT_SUFFIXES` can be overridden by environment
variable so a preview environment on a different platform can extend
the allow-list without editing the script. When the variable is set,
the compiled-in defaults are replaced (not merged) — the operator's
allow-list is what applies.

### Shell tests (best-effort)

`.emergent/cron/tests/test_dispatch_allowlist.sh` exercises the
allow-list logic against a small set of URL/allow-list pairs. It runs
manually (not through GitHub Actions) because the `.emergent`
directory is a platform artefact and is not covered by the product
CI. The test file documents the exact scenarios so a future reviewer
can extend them.

## 4. Reviewer curiosities (not changed on this branch)

These are recorded so the reviewer knows they were considered and
deliberately left alone.

1. `.emergent/cron/webhook_crond.sh` runs `sudo apt-get install -y
   cron` at pod startup if `cron` is not present. That is a
   hosting-platform decision. Not modified.
2. The `.emergent/cron/webhook-crons` crontab runs as `root`.
   That is a platform default. Not modified.
3. `.emergent/emergent.yml` embeds the preview job id in plain text.
   Not a secret.

None of the above are product-repository concerns; they are
platform-repository concerns. If the platform ownership of
`.emergent` changes in the future, this section moves to the
platform's own audit document.

## 5. Decision

- **Keep:** every `.emergent` file and `memory/PRD.md`.
- **Change (this branch):**
  1. Harden `.emergent/cron/dispatch_webhook.sh` against
     Authorization forwarding on unapproved redirects.
  2. Add a small allow-list shell test.
  3. Add this audit document.
- **Owner action:** confirm the Emergent-managed preview-domain
  suffixes in
  `WEBHOOK_ALLOWED_REDIRECT_SUFFIXES` inside the dispatcher match
  the current platform's actual redirect graph. If the platform ever
  rotates the domain, the dispatcher's default list needs to be
  updated in a follow-up PR (or overridden with an environment
  variable in the pod spec).
- **Later work packages:** if `.emergent` is repointed at a new
  platform, or the platform documents a different location for these
  files, the audit is re-run.

## 6. Payment surface

None of the `.emergent` files, and `memory/PRD.md`, touch payment
mechanics. Their diff on this branch, checked against `main`, only
adds the hardening described in §3 and this audit document. No
Razorpay call, webhook, or refund path is modified.
