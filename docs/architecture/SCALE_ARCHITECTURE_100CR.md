# Scale readiness architecture — path toward ₹100 Cr, not an infrastructure order

**Status: CURRENT / AUTHORITATIVE architecture assessment, 2026-10-02.** Code
baseline: `main` `485729ab3ddba92c53257e7eea7fa2876212e3f7`, tree
`edffd7cc39d0ec7ad4d44b5caf190c11f6b9d15c`. This is repository evidence,
not a claim that provider-side settings, traffic or backups were inspected.
`NOW` means the current policy or a zero-cost measurement activity; `NEXT —
trigger required` needs measured evidence and, for any spend, a **separate
explicit founder decision**; `LATER` is a scale-stage option; `REJECTED / NOT
JUSTIFIED` must not be implemented from this document.

## Invariants that outrank throughput and revenue

AI reads. Structured intelligence knows. Deterministic rules decide. AI
explains. **No source → no claim.** Never serve a scientifically stale or
weaker answer to hit latency: do not skip official-record or evidence checks,
guess missing label data, let a queue defer deletion indefinitely, or disable
admission controls under load. Return the governed unavailable/not-enough-
information result when safe truth cannot be produced.

```
                          ┌→ consumer
Product Truth ─────────────┼→ Purchase OS → Commerce
                          └→ B2B read-only API
```

Commercial and scientific authorities never point backwards. Contract value,
affiliate payout, advertising, enterprise account importance and growth goals
cannot change grade, BUY/WAIT/SKIP, evidence sufficiency, official-record
interpretation or alternative ranking. Sales, Growth and Commerce cannot buy
or approve scientific outcomes. Store A (Open Food Facts/ODbL) remains
physically distinct from proprietary Store B; their transient in-memory
barcode pairing must not become an ungoverned warehouse or B2B table.

**NOW — ₹0/free-tier:** no payment method, paid provider tier, Redis, queue,
replica, worker service, Kubernetes, cloud project, staging service, deployment
or Phase B activation is authorized by Step 18. The modular monolith is the
default, not a failure. Any future paid transition requires a separately
recorded founder approval after its decision gate below.

## A. Current topology and boundaries — code-verified

```
Expo mobile / web ──HTTPS──→ one Render Singapore free Docker web service
                             FastAPI modular monolith, one uvicorn process
                             ├─ consumer /api/v2; B2B /api/b2b/v1
                             ├─ Product Truth, Purchase OS, Commerce
                             ├─ governed structured AI callers
                             │    └─ AI Gateway/run_structured() ──→ Gemini
                             ├─ legacy POST /api/v2/scan/analyse
                             │    └─ Gemini adapter directly (Gateway bypass)
                             ├─ Supabase Auth/JWKS + private Storage
                             ├─ Store B Supabase PostgreSQL (SQLAlchemy)
                             ├─ Store A distinct Supabase DB / OFF
                             ├─ Expo Push (notification delivery)
                             └─ Sentry (crash/error only)
Supabase Cron + pg_net + Vault ──HTTP bearer──→ internal scheduler endpoints
                             ├─ deletion cycle every 5 min
                             └─ notification cycle hourly (includes Watch)
```

`render.yaml` declares exactly one `plan: free` web service, no Render DB,
Redis, disk, worker or cron; `autoDeployTrigger: "off"`. Render Blueprint Auto
Sync is a **separate dashboard control** and cannot be proven off from Git.
The production image is `deploy/render/Dockerfile`; `backend/Dockerfile` serves
local/compose builds. Both are pinned to a base digest, run non-root and use
live `apt-get upgrade`, so Git SHA alone does not reproduce the OS package
set. No microservices or paid deployment platform exist in the Blueprint.

`backend/server.py` mounts the FastAPI routers. `python -m app.release` runs
before uvicorn on every container start: production config validation, a
PostgreSQL advisory lock, Alembic upgrade/check, Store A provisioning,
reference seeding and consistency checks. Liveness is `/api/v2/health`;
readiness is `/api/v2/ready`. CI is `.github/workflows/ci.yml` (PR, push,
manual and weekly schedule); no `release.yml` exists. Deployment is a manual
exact-reviewed-SHA decision, not a tag-triggered release. Phase B knowledge
activation is a separate human operation, not a deploy side effect.

Store B's async SQLAlchemy engine defaults to `pool_size=5` and
`max_overflow=5`, `pool_pre_ping=True`, one request session rolled back on
error and hidden bound SQL parameters. Store A has separate metadata,
connection and export/field allowlist; production config refuses equal URLs.
Supabase Auth is verified through issuer/JWKS on the backend; Storage is
private/server-side. These are code/config claims, **not** verification of
provider project settings. **The Gateway is not universal:** governed
structured callers use `run_structured()`, which records `ai_runs` and
validated `ai_run_outputs`, provider/model, prompt/schema version, latency,
token counts when supplied, estimated cost and an authenticated hourly AI
allowance. The legacy `POST /api/v2/scan/analyse` calls the Gemini adapter
directly. It has its own monthly beta-scan reservation, idempotency and `Scan`
result persistence; successful rows record provider/model, `scan.v2` prompt,
`scan.v1` schema and latency. Direct calls do **not** receive Gateway
`ai_runs` token/cost accounting or its hourly allowance. This is a current
measurement and execution-contract exception, not a new scientific authority.
Sentry is crash/error only, without performance tracing. Request IDs, logging redaction and Sentry
scrubbing are current controls.

`app.workers.account_deletion.run_cycle` and
`app.workers.notifications.run_cycle` are bounded HTTP-invoked cycles, not
separate Render services. The notification cycle also queues Product Watch,
environment, protocol, running-out, deferred-purchase and agenda triggers;
final-send eligibility is rechecked transactionally. The scheduler endpoints
use `INTERNAL_SCHEDULER_TOKEN`. The current operations document explains why
cron-enqueue success, HTTP completion and worker heartbeat are three different
proofs. Account deletion has its own persisted state machine. The in-process
`FixedWindowLimiter` bounds anonymous routes and Step 17 B2B per-IP/global
pre-auth and per-client burst traffic; B2B daily allowance is atomic in Store B.

**Synchronous path:** request → authentication/admission → authoritative
Store A and/or B reads → deterministic decision → response. Database writes
commit at their domain boundary, not at a global cross-provider transaction.
**Scheduled path:** Cron enqueues HTTP → token check → bounded cycle → DB
claims/commit → optional external push; provider acceptance is not identical
to customer delivery. Stateless: API code, request context. Stateful: both
databases, Auth, Storage, scheduler tables/heartbeats, Sentry/provider state;
in-process limiter state is transient and instance-local.

## Failure-domain map

Detection below is a current observable signal, not proof that somebody is
already paging on it. Detailed incident actions are in
[scale readiness](../operations/SCALE_READINESS_AND_SLOS.md).

| Dependency/failure | Customer and Product Truth effect; fail behavior | Current detection / recovery | NEXT mitigation trigger |
| --- | --- | --- | --- |
| Render/API process, free quota or cold start | API unavailable/slow; no substitute truth. Fail closed. | Health/readiness, Render status/logs; exact-SHA restart or rollback after schema compatibility check. | Repeated availability-budget burn, quota suspension or justified external SLA → approved paid compute. |
| Store B primary / pool | Authenticated state, confirmed labels, evidence, B2B key/quota and writes unavailable; do not invent a verdict. | Readiness/Postgres ping, error logs, CI migration proof; recover provider/restore only through verified procedure. | Connection/CPU/lock/query saturation or restore objectives missed → optimize, then tier/discipline. |
| Store A / OFF DB or upstream OFF | Consumer OFF fallback/coverage degrades; no OFF values may be fabricated or persisted in B. B2B confirmed-label path remains separately governed. | OFF lookup logs, ODbL tests; show safe insufficient state where needed, repair distinct store. | Sustained fallback failure or licensed-data freshness breach → improve ingestion/availability without joining stores. |
| Supabase Auth/JWKS | New authentication fails or is unavailable; do not accept unverifiable tokens. | 401/5xx class and readiness/config; provider recovery and controlled key rotation. | Repeated auth SLO burn/contract obligation → redundancy and rotation drill. |
| Supabase Storage | Upload/media access and deletion purge degrade; text-safe paths may continue; deletion must not claim completion without purge proof. | Storage/readiness and deletion state/errors; repair access then resume idempotently. | Backlog/restore failure or storage growth → approved tier and media lifecycle review. |
| Gemini | Photo analysis/extraction unavailable; deterministic truth from verified data must not be replaced by AI guess. | Gateway `ai_runs`/outputs for structured callers; direct `/scan/analyse` `Scan` status/latency but no Gateway token/cost ledger. Reconcile provider usage before cost claims; preserve each path's bounded failure semantics. | Provider saturation/failure or verified unit-cost breach → concurrency budget/approved alternate quality-tested model. |
| Expo Push | Proactive notification degrades, Product Truth stays up; no late catch-up blast. | Delivery outcomes and heartbeat; fix credentials/provider, preserve final-send authority. | Persistent miss rate or send backlog → separate executor with same idempotency. |
| Supabase Cron/pg_net/Vault | Deletion/notification cycles missed; Product Truth unaffected immediately, privacy risk grows with deletion lag. | `cron.job_run_details`, `net._http_response`, worker heartbeat; repair token/schedule, bounded manual run only by operator. | Cycle near interval, missed heartbeat or deletion SLO breach → dedicated execution. |
| Sentry | Crash telemetry lost; API truth must not depend on telemetry. | DSN/init status and independent health/logs; repair integration. | Incident detection gap → privacy-safe metrics, not unbounded event capture. |
| Official/evidence sources | Published local source/rule authority remains versioned; an unavailable runtime source must not be guessed or bypassed. | Evidence/official-record outcome and validation tests; governed insufficient state/review. | Source freshness/availability materially harms safe coverage → approved ingestion reliability. |
| B2B key/admission/quota | Key/DB failure returns generic auth/error; in-process limit is per replica, daily DB quota remains authoritative. Product Truth unchanged. | 401/429/5xx aggregates, usage rows, PR gate; rotate/suspend keys and investigate. | Before replica 2 or contracted strict rate limit → reviewed shared admission. |

## B. NEXT — trigger-required minimal scale

Keep the monolith and Store A/B wall. First exhaust query/index and code
optimizations; then obtain founder approval for paid managed web/DB capacity
only when measured free-tier failures, PMF/revenue or a signed obligation
justify it. A second API replica is **not** a safe one-line setting:
`in-process rate limit × N replicas != global rate limit`. Before replica 2,
review and test a shared admission authority, release concurrency, DB
connection budget and backwards-compatible schema rollout. Scheduled HTTP
cycles may remain until duration/backlog/retry evidence crosses a gate. Add
aggregated operational metrics before expensive tracing.

Shared-limit candidates, to benchmark under the actual contract:

| Candidate | Correctness | Latency and operational cost | When justified |
| --- | --- | --- | --- |
| Store B transactional counters | Strong DB atomicity, easy audit; must bound contention and cleanup. | Extra primary writes/locks on every admission. | Modest B2B traffic and spare DB write budget; test under bursts. |
| Managed Redis/Key Value | Shared fast atomic scripts possible; failure policy and durability must be explicit. | New paid service, secret/network/operations, failover complexity. | Admission volume makes DB counters harmful and founder approves cost. |
| Trusted gateway/WAF plus DB allowance | Network-edge abuse control; cannot by itself express every client contract. | Provider configuration, trust/IP topology and contract review. | Abuse or public enterprise exposure warrants edge control. |

Do not select Redis by fashion or let an unavailable shared limiter fail open
on a paid B2B contract. Test known/unknown-key oracle equality, per-IP/global
fairness, cross-replica burst and rollback before switching.

## Central transition/decision matrix

All thresholds below are **proposed planning triggers**, not measured current
SLOs or external SLAs. Sample the metric for a representative week where
possible; a safety breach escalates immediately. `Cost` is a variable to be
quoted and approved, never a price claim.

| CURRENT STATE | MEASURE | WARNING | HARD TRIGGER | NEXT ARCHITECTURE | MIGRATION | ROLLBACK | COST TO QUOTE | OWNER |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Free one-process Render API | Availability, error-budget burn, cold starts | Two customer-impacting incidents/month | Repeated failure or signed availability obligation | Approved paid managed web | Optimize first; exact-SHA deploy with same readiness | Previous compatible SHA | `C_web/month` | Founder + platform |
| One API replica | Sustained p95/p99, concurrency, CPU | 70% tested safe headroom used | 85% or objective breach after optimization | Second replica | Connection, limiter and schema review; gradual traffic | Route to one replica | `ΔC_web+C_shared` | Platform |
| In-process B2B/anonymous limiter | Replica count, 429 fairness, aggregate rate | Plan for replica 2 | **Before replica 2** | Shared DB/gateway/Key Value authority | Benchmark and cross-replica admission tests | One replica and old limiter | `C_limit` | Security + platform |
| HTTP Cron cycles | Cycle p95, heartbeat lag, deletion age, lock waits | Cycle repeatedly >50% interval | >80%, missed cycle or privacy target breach | Bounded separate worker if optimization fails | Optimize batch/claims; evaluate Postgres jobs first | Old scheduler only after claim/idempotency proof | `C_worker` | Privacy + platform |
| One managed Store B primary | Pool use, CPU, locks, query p95, bloat | Sustained 70% verified budget | Sustained 85% or errors | Larger primary if query/index work fails | Connection discipline then rehearsed upgrade | Prior compatible config if capacity permits | `ΔC_db` | Data + platform |
| Primary-only reads | Read load, replica lag tolerance | Read-induced write SLO burn | Verified stale-tolerant read workload still impacts primary | Read replica for eligible reads only | Prove fresh truth, quota and deletion never route there | Return reads to primary | `C_replica` | Data + Product Truth |
| Ordinary large tables | Size/growth, vacuum, query plan | Repeated bloat/query regression | Index/retention optimization insufficient | Selective partition | Backfill and dual-read proof for one table | Compatible old read path | `C_migration` | Data |
| Split AI paths | Gateway and direct-route concurrency, timeout/429; provider-invoice cost | Cost coverage incomplete or 70% verified quota/budget | Sustained provider errors/contract breach | Bounded budget and a reviewed single execution authority only if safe | Reconcile direct route first; any unification needs separate contract tests | Preserve current route semantics and governed unavailable answer | `C_ai` | AI + Product Truth |
| Logs/request IDs/Sentry | Detection delay, missing attribution | Incident cannot be diagnosed | Repeated SLO miss or enterprise evidence gap | Safe aggregate metrics; tracing only for multiple services | Scrubbed instrumentation and access review | Disable new telemetry, not API | `C_obs` | Reliability + privacy |
| Manual B2B operations | Clients, support load, rotation and incident obligations | Repeated manual error | Contracted response unmet | Controlled report, staffed on-call | Audited runbook and limited pilot | Keep V1; suspend pilots | `C_support` | B2B + security |

Every spend decision records problem, measurement window, current impact,
software/free-tier alternatives, quote in ₹/month and ₹/year, capacity/risk
gain, migration test, rollback and founder approval. ARR informs affordability;
it is **not** a sharding or replica-count signal.

## Database, state and migration progression

**NOW:** calculate possible connections before changing process count:

```
connections_possible = api_replicas × (pool_size + max_overflow)
                     + worker_and_scheduler_connections
                     + migration_and_release_connections
                     + admin_and_maintenance_headroom
```

With current defaults, one API process alone can ask for **10** Store B
connections; this is a maximum pool demand, not observed usage or the
provider's connection allowance. Do not assume a second process fits. Store
A's independent pool must be budgeted against **its own** provider ceiling.
The editable capacity worksheet and cost allocation are in
[unit economics](../company/UNIT_ECONOMICS_AND_CAPACITY_MODEL.md).

**NEXT — trigger required:** inspect slow queries/plans and indexes, cap
long transactions, monitor lock waits and pool checkout, maintain vacuum and
bloat, then resize the managed primary if justified. Isolate background
workloads before they starve decision reads. Read replicas are for explicitly
stale-tolerant reads with lag evidence; never a shortcut for fresh quota,
official-record, evidence, consent or deletion authority. Partition only a
table whose growth and plan evidence show an index/retention limit. Retain or
archive only under privacy/legal policy, including deletion propagation.
**LATER:** data/service decomposition only with clear ownership, independent
load and failure domains. **REJECTED / NOT JUSTIFIED:** sharding first.

Multi-instance schema policy is expand/contract: (1) additive,
backwards-compatible expansion, (2) compatible code deployment, (3) separate
bounded backfill with checkpoint/rollback, (4) switch reads and writes behind
a reviewed gate, (5) contract only after old code is gone. Never assume all
instances restart together. Test upgrade, drift and downgrade where supported;
an old image cannot be rolled back across an incompatible schema merely by
redeploying its SHA. The existing migration chain is unchanged by Step 18.

## Background execution and queue decision

**NOW:** Supabase Cron invokes the two internal HTTP routes; persisted jobs,
claims, worker heartbeat and notification send authority stay in Store B.
Measure cycle duration, backlog age, retries, lock wait and scheduler HTTP
outcome. A successful cron enqueue is not a successful deletion or push.
Product Watch's epoch/eligibility and notifications' final-send lock must stay
correct under any executor.

**NEXT — trigger required:** optimize scanning and batch/claim sizes before
introducing a worker. If cycle duration approaches its interval, privacy
deletion age breaches its target, independent scaling/deploy is needed or
retry/recovery exceeds the batch model, move the same idempotent domain
operations to separately bounded execution. Evaluate a Postgres-backed job
table first for modest volume, because it is already the transaction
authority; benchmark lock contention. A managed queue becomes justified when
DB job writes contend with Product Truth or throughput/retention requires
separation. A Redis-backed queue is not automatically durable and would add a
paid stateful service. No queue is installed here.

Any future queue needs durable enqueue or transactional outbox, account
isolation, deterministic idempotency key, bounded retries/backoff, lease/claim
expiry, dead-letter/recovery policy, cancellation and deletion propagation,
lag/age metrics, operator replay and proof of no duplicate customer action.
Acceptance tests must include crash-after-claim, crash-after-provider-accept,
concurrent worker, opt-out during send, Watch epoch change and deletion
request during backlog. Throughput never weakens deletion integrity.

## Product Truth and data analytics

**NOW:** no unsafe Product Truth response cache. If a future hot path is
measured, cache only after a complete authority graph and invalidation test:
product/version identity; confirmed label snapshot and content hash; evidence
and published ruleset version; official-record version/freshness where
relevant; category methodology; and any other decision input. Invalidate on
every governing change before serving a new answer. Do not cache a personal
FOR YOU result across accounts, elevate physical-pack facts to global truth,
or use stale data to meet a latency target. Keep Commerce and B2B downstream.

**LATER — evidence required:** a warehouse only when aggregate questions
cannot be answered affordably from minimized operational views. Separate
product operations, consumer analytics, security/audit, B2B usage, finance
and scientific evidence by purpose and access. Preserve Store A provenance
and license, data minimization, consent, retention, pseudonymization where
appropriate and deletion propagation. A lake that silently joins OFF with
proprietary decisions is rejected. Counsel reviews legal/licensing questions;
this document is not a legal determination.

## AI and observability evolution

**NOW:** `run_structured()` records Gateway-backed attempts and validated
outputs with provenance, latency, token counts when returned and an
*estimate* using configurable input/output rates; defaults are not verified
provider invoices. The direct `/scan/analyse` Gemini route instead persists
`Scan` status and successful-result provider/model/prompt/schema/latency, with
its own monthly reservation and idempotency. It does not write the Gateway
token/cost ledger or consume its hourly allowance. The Gemini adapter has a
bounded timeout and configured fallback chain, but **current total AI cost
and per-scan cost coverage are incomplete** until direct usage is reconciled
with provider invoice/usage evidence. Measure calls, retries and failures on
both paths before budgeting. No prompt/output or health facts in default
telemetry. The Gateway module's universal-call docstring is known internal
documentation debt; Step 18 does not change it or runtime code.

**NEXT — trigger required:** cap concurrent calls and retries per account
and globally, respect provider quotas, bound total request time and money,
consider circuit breaking when measured outages create retry storms, and
deduplicate only where account/privacy/label authority makes it safe.
Alternative models require extraction-contract quality tests and explicit
approval; a cheaper silent substitution is not cost optimization. A future
single governed AI execution authority is a **separate reviewed runtime
milestone**: routing `/scan/analyse` through `run_structured()` could alter
the hourly allowance versus monthly scan allowance, idempotency, run
recording, failure persistence, provider errors and provenance. Do not make
that substitution as an architecture-document cleanup. B2B
Product Truth should have **zero AI calls**; investigate any nonzero value.

Observability progression is structured redacted logs/request IDs → safe
aggregate counters and histograms → SLO/error-budget dashboard → distributed
tracing only when multiple services justify it → profiling only on measured
bottlenecks. Metrics vocabulary: route-class request count, 5xx/429 class,
p50/p95/p99 latency, DB pool wait/saturation, query duration, lock wait,
scheduled cycle duration/lag, deletion age, AI latency/failure/tokens/cost,
B2B admission/quota outcomes, Commerce *outbound opens* and deployed SHA.
Aggregate by bounded dimensions; never label with raw JWT/key, barcode,
account/health fact, media, ingredient list, Product Truth payload or AI
prompt/output. Review cardinality and retention before buying telemetry.

## C. LATER — high-scale architecture, not today's build

```
client → governed edge/admission → stateless monolith replicas
                               ├→ Store B managed primary + safe read paths
                               ├→ physically separate Store A / OFF
                               ├→ bounded background execution
                               └→ AI Gateway with explicit budget
                         → aggregate privacy-safe SLO instrumentation
```

Even at ₹100 Cr-scale, this may remain a modular monolith. Extract a domain
only when **several** are true: independent load/failure isolation, separate
release cadence, clear data and team ownership, security boundary, high
operational burden. Candidate evaluation: AI execution, notifications/jobs,
B2B gateway, ingestion. Product Truth should resist fragmentation because
one governed decision is more important than service count. **REJECTED / NOT
JUSTIFIED now:** Kubernetes, Kafka, service mesh, ElasticSearch, vector stack,
active-active regions and microservices merely because code folders are large.

Single-region operation may remain correct. Multi-region requires measured
Indian latency, resilience/RPO/RTO, data-residency advice, enterprise contract,
cross-region consistency design and cost approval. Never infer it from ARR.

## B2B and release lifecycle

**NOW:** Step 17 is a single-barcode, read-only V1; no customer can steer
Product Truth. **NEXT — customer evidence:** higher quota, usage reporting,
shared admission before replicas, security/tenant isolation evidence, support
and contractual response model. **LATER:** scoped enterprise features; batch,
bulk, webhooks, developer portal, billing, organization members, OAuth and IP
allowlists are not Step 18 work.

**Proposed, not contracted:** version the B2B schema/contract; additive
changes keep old clients working; changing field meaning, removing a field,
or weakening a safety/availability state is breaking. Publish migration
guidance and a sunset proposal for business/legal approval before any
deprecation date is promised. Contract tests must run on old and new clients.
Do not call an internal SLO an enterprise SLA or claim SOC 2/ISO certification.

Release evolution: current exact-SHA single-instance deploy and release gate
→ expand/contract compatible multi-instance rollout → canary/rolling only
when the host and evidence justify it. Preserve readiness, migration lock,
rollback compatibility and a separate scientific activation event. Existing
feature flags and B2B client suspension are bounded gates; a future kill
switch/tenant pilot gate needs review and audit. Scientific rules may not be
arbitrary per-customer remote-config state.

Business continuity and risk/incident ownership are in
[scale readiness](../operations/SCALE_READINESS_AND_SLOS.md) and
[organization](../company/SCALING_ORGANIZATION_100CR.md). This architecture
does not create a service, deploy a SHA or authorize paid spend.
