# Scale readiness, SLOs and operational decision gates

**Status: CURRENT planning authority, not a contractual SLA.** Audited against
`main` `485729ab3ddba92c53257e7eea7fa2876212e3f7` on 2026-10-02.
Current live traffic, paid backup/PITR, provider account configuration and
observed latency were **not** accessed or verified. The canonical current
deployment procedure is [Operations — Render Pre-PMF Runtime](../OPERATIONS.md#render-pre-pmf-runtime-zero-cost).

`NOW` = zero-cost instrumentation/review or current control; `NEXT — trigger
required` = decision gate and separate founder spend approval; `LATER` =
option; `REJECTED / NOT JUSTIFIED` = do not deploy. No number below is a
current promise to a customer.

## Measurement first: capability, target, contract

Current observed capability is limited to passing exact-head CI and code
properties: one free API instance, health/readiness, request IDs, redacted
logs, worker heartbeats, the **Gateway-backed** AI ledger, direct-route `Scan`
rows and B2B usage counters. There is no
verified production SLO measurement history. Free hosting has no external SLA.

The table contains **proposed planning targets** for a future rolling 30-day
internal review, subject to baselining and founder approval. Availability
denominator is eligible requests or scheduled obligations, not scan attempts
that the product correctly refuses for safety. Governed insufficient answers
are not automatically errors; an incorrect confident answer is always a
safety incident. A future B2B SLA requires separate commercial/legal signoff
and capacity evidence.

| Class | Availability / latency SLI and proposed internal target | Correctness/safety SLI | Current source → NEXT measurement/alert |
| --- | --- | --- | --- |
| Consumer Product Result | Eligible route success ≥99.5%; stored-label p95 ≤2s, p99 reviewed separately. Exclude AI capture time; never hide it inside this SLI. | Zero known ungoverned grade, stale authority or evidence bypass; insufficient state is safe. | Route-class status/request ID + CI golden replay → privacy-safe histogram; alert on 5xx burn or any integrity violation. |
| B2B Product Truth V1 | Eligible authenticated success ≥99.5%; p95 ≤2s for confirmed-label path. | Byte-consistent with governed shared truth, no commercial steering, no key oracle or quota overspend. | B2B outcome/usage rows + CI contract tests → aggregate latency; alert on sustained 5xx, key/oracle failure or quota mismatch. |
| Auth | Valid token verification success ≥99.5%; p95 ≤1s excluding provider login UI. | No unverified JWT accepted, no cross-account access. | 401/5xx class + readiness/JWKS checks → aggregate alerts; any auth bypass SEV-1. |
| Critical writes | Eligible writes committed ≥99.9%; p95 ≤2s per route class after baseline. | Atomicity, idempotency, account isolation, privacy invariants. | DB commit/errors and regression tests → lock/pool metrics; alert on repeated failures or invariant breach. |
| Scheduled deletion | Accepted requests reach verified complete state within a **proposed 24h** objective; cycle every 5 min, heartbeat age ≤10 min when work exists. | No false completion while bytes/Auth identity remain; no cross-account erasure. | Job state/age, `cron.job_run_details` + `net._http_response` + heartbeat; page on stuck/false-complete risk. |
| Notifications | Due and opted-in sends attempted in their valid hour ≥99% once measured; cycle hourly, heartbeat age ≤2h. | No duplicate or post-opt-out send; never late catch-up blast. | Delivery/worker outcomes and provider acceptance; alert on missed hour/backlog or final-send invariant. |
| AI-assisted scan/extraction | Gateway-backed eligible attempts return validated output or explicit unavailable; direct `/scan/analyse` returns its persisted analysis or explicit failure. Proposed success ≥99% and p95 ≤15s are **not** provider promises; baseline the paths separately. | Gateway schema/provenance validation; direct route currently parses a JSON object under its own contract. No invented science or silent weaker model. | Gateway-backed paths: `ai_runs`/`ai_run_outputs` provide latency, token/cost estimate and provenance evidence. Direct `/scan/analyse`: `Scan` rows provide status and successful-result provider/model/prompt/schema/latency, but no Gateway token/cost accounting. Reconcile provider usage; alert on errors or invalid output. |
| Commerce handoff | Eligible handoff route success ≥99.5%; p95 ≤2s, external merchant navigation measured separately. | Commerce never changes grade/verdict or ranks for payout; outbound open ≠ purchase. | Handoff outcome and CI independence tests; alert on steering/integrity breach or high 5xx. |

`error_budget = eligible_opportunities × (1 - target_fraction)` for the
window. Use both a fast burn (e.g. >10% of monthly budget in 1 hour) and a
slow burn (e.g. >25% in 24 hours) as **proposed alert gates**, then tune from
real traffic and false-positive evidence. At low sample sizes, use counts
and incident review rather than misleading percentages. Safety, privacy and
license boundary breaches have no spendable error budget. External SLAs remain
**none** until a separately approved contract specifies scope, exclusions,
remedies, measurement and support capacity.

## Safe metrics contract and review cadence

**NOW:** inventory existing route status/request IDs, Sentry crashes, worker
heartbeats, deletion states, Gateway AI run counts/tokens, direct-route `Scan`
outcomes, B2B counters and CI run results. **Current gap:** direct
`/scan/analyse` bypasses Gateway token/cost accounting; neither its per-scan
cost nor complete AI spend is proven by the `ai_runs` ledger. Record gaps; do
not claim SLO compliance or complete cost coverage from tests.
**NEXT — trigger required:** low-cardinality aggregate counters/histograms for
route class, status class, latency, DB pool checkout/usage, query/lock time,
scheduled cycle duration and age, AI latency/failure/tokens/estimated versus
invoiced cost, B2B 429 and quota outcomes, Commerce outbound opens and SHA.
No raw JWT, key, barcode, health facts, media, ingredient lists, verdict
payload or AI prompt/output in labels/logs. Set retention and access before
adding tooling. Tracing is LATER after actual service boundaries; profiling
only on demonstrated bottlenecks.

Once usage warrants it, the founder and owners review one compact monthly
sheet: TRUST (evidence/safety incidents and coverage), PRODUCT (activation,
retention, repeat scanning), GROWTH (acquisition/referral), COMMERCE (eligible
handoffs and *outbound opens*), B2B (active clients, requests/coverage),
FINANCIAL (recognized revenue, gross margin, cost-to-serve), RELIABILITY
(availability, latency, errors, deletion/notification lag). A lower layer
never overrides TRUST. Escalate a threshold breach immediately rather than
waiting for a meeting. With a solo team, this is one review note, not a new
committee.

## Incident roles and severity — usable by one founder

| Severity | Example and response principle |
| --- | --- |
| SEV-1 | Product Truth corruption/unsafe confident answer, Store A/B license-wall breach, confirmed cross-account exposure, deletion falsely complete, key/auth bypass. Stop affected path, preserve evidence, involve science/privacy/security; do not optimize for uptime. |
| SEV-2 | Sustained API or Store B outage, deletion target at risk, widespread B2B failure, scheduler miss or provider outage affecting material journeys. Bound impact, communicate with affected customers/clients and restore safely. |
| SEV-3 | Limited degraded feature, missed noncritical notification, observability loss, isolated recoverable performance regression. Track owner and recovery. |

Every incident names an **incident commander** (decision/log), **technical
lead** (diagnosis/rollback), **communications owner** (consumer/B2B status),
and **privacy/security and Product Truth/science escalation** when relevant.
One founder can wear several hats today but must write which hat is deciding.
Preserve request IDs, exact SHA, safe aggregate metrics and timeline without
copying secrets or personal payloads into a ticket. Contain, assess integrity,
restore from a verified authority, communicate within counsel-approved rules,
and record corrective action/test. No automatic scientific downgrade to
"restore service". A post-incident review tracks trigger, detection delay,
customer impact, rollback proof, cost and owner without blame.

## Continuity, backup and restore

**NOW:** Git proves migration upgrade/check/round-trip in disposable CI
PostgreSQL. That is **not** a production backup or a restore drill.
[Current recovery qualification status](BACKUP_RESTORE_QUALIFICATION.md)
records the executed October 10 corrective live-source dump → isolated PG17.11
restore, complete row/column parity, measured durations and local synthetic
Storage/Store A recovery, while preserving the October 8 historical evidence.
Formal F15 closure still depends on the audit's independent repository authority. Live production
`d0e1f2g3h4` intentionally predates undeployed repository `o3p4q5r6s7` because
Render auto-deploy is OFF. Vault credentials require reconnection; hosted Auth
signing/provider configuration and existing-session validity remain separate
recovery boundaries. No guaranteed production RPO/RTO or recovery SLA is claimed. Paid PITR and paid restore destinations are
prohibited under the current allowance. The old draft
`docs/production/BACKUP_AND_RESTORE_DRILL.md` remains historical/unverified;
the fake-success simulator was removed. Neither is successful restore proof.

**NEXT — trigger required:** before a paid enterprise SLA or critical paid
product, obtain approved backup/restore capability for Store B, physically
separate Store A, Auth and Storage. Propose RPO/RTO per domain only *after*
impact analysis and a real measured qualification; illustrative schedules
must not be reported as guarantees.
Test restoration into an isolated, authorized non-production environment:
checksum/schema/seed, account/privacy state, Store A wall, key and media
access, and service startup at an exact compatible SHA. Record drill date,
source age, recovered counts, duration and failed steps; repeat after major
schema/storage changes and on a reviewed cadence. A backup file alone proves
nothing. Never copy real personal data into an ungoverned test environment.
**LATER:** cross-region recovery only with residence/legal, consistency,
latency, contract and cost evidence. Active-active is not justified now.

For founder absence, maintain a private, access-controlled succession record
for GitHub, Render, Supabase projects, Auth/Storage, Gemini, Sentry, app stores,
scientific review, contracts, incident contacts and any future domain. Use
least privilege and break-glass recovery; no credentials in Git or this doc.
Practice access handover before a single person becomes a permanent 24×7 SPOF.

## Security and enterprise maturity gates

**NOW:** exact-head CI includes Ruff/tests, Alembic, dependency governance,
secret scan, container scan/SBOM where scoped, and PR gate. Production
validation refuses unsafe config; backend auth, audit hashes, redaction and
secret-only environment groups are current code controls. Verify access and
provider settings independently before describing them as deployed facts.

The time-bounded `node-forge@1.4.0` exception for
`GHSA-86w9-cpqp-85rv` is a **real vulnerability**, not a false positive:
review **2026-10-09**, expiry **2026-10-16**. The governance file remains
untouched. Recheck official remediation and reachability at review; remove
the exception when fixed or its assumptions change. Do not auto-extend it.

**NEXT — trigger required:** named owners for secret/key rotation, B2B key
revocation, least privilege and periodic access review; evidence of auditable
admin changes and provider account recovery; dependency/vulnerability response
times proportional to severity and reachability; threat-model review before
replicas, a queue, batch B2B, payment or a warehouse. Penetration testing and
security questionnaires become justified by contractual exposure or materially
broader attack surface, not prestige. No SOC 2/ISO claim exists here.

Enterprise readiness checklist before a serious commitment: stable API
contract/version and proposed deprecation process; cross-tenant isolation
tests; credential rotation drill; usage/coverage report; exact-SHA change
record; incident contact/communication; security evidence; restore drill;
support/on-call capacity; counsel-reviewed DPA/privacy terms and any actual
SLA. India and future markets require counsel to review privacy, food/health
claims, data residency, consumer notices, retention and ODbL/commercial use.
This is a control/evidence map, **not legal advice or a legal conclusion**.

## Ranked risk register (qualitative, not invented probabilities)

`Likelihood` is an unmeasured judgement pending operational data. Owners are
roles, not assertions that those hires exist. `Open` means the control/evidence
is incomplete; `Monitored` means code controls exist but live proof is needed.

| Rank / risk | Current evidence; likelihood / impact | Detection → mitigation / decision trigger | Owner / status |
| --- | --- | --- | --- |
| 1 Product Truth or Store A/B corruption | Constitution, ODbL wall/tests; likelihood unknown, critical impact. | Golden/evidence/license tests and incidents → fail closed, independent review; any breach immediate. | Science + data / monitored. |
| 2 Founder key-person/access loss | Solo operating model; likelihood unknown, critical recovery impact. | Access inventory/failed handover → private succession and second authorized operator before paid commitments. | Founder / open. |
| 3 Free single web instance/quotas | One free Render service, no SLA; likelihood unknown, high availability impact. | Readiness, provider status, error-budget burn → approve paid compute after measured harm/PMF. | Founder + platform / monitored. |
| 4 Store B connection ceiling/restore gap | Pool max 10/process; dated ordinary logical restore qualified; recurring backup/service-authority recovery remains a gap; likelihood unknown, high impact. | Pool/lock/restore exercise → optimize, budget connections, then tier/backup. | Data + platform / open. |
| 5 Multi-replica limiter mismatch/B2B abuse | In-process limits; one process now; likelihood conditional, high contractual/security impact. | Replica plan, 429 fairness and usage → shared admission **before** replica 2. | Security + B2B / open future gate. |
| 6 Deletion/privacy backlog | HTTP Cron, persisted deletion state; likelihood unknown, critical privacy impact. | Cron HTTP + heartbeat + oldest job → improve cycle, then separate executor when lag breaches objective. | Privacy + platform / monitored. |
| 7 Scheduler/notification dependency | Supabase Cron/pg_net/Vault and Expo Push; likelihood unknown, medium/high impact. | Missed run, send/lag metrics → recover schedule/provider; no late catch-up. | Reliability / monitored. |
| 8 AI/Gemini concentration, split execution and cost gap | Gateway-backed ledger/fallback plus direct `/scan/analyse` Gemini path without Gateway token/cost rows; likelihood unknown, medium/high impact. | Compare Gateway and `Scan` outcomes with provider usage/invoice; budget and quality-tested alternate only when justified. Any unification needs a separate allowance/idempotency/failure/provenance review. | AI + Product Truth / open measurement gap. |
| 9 Store A/OFF coverage or source freshness | Distinct DB and external source; likelihood unknown, medium trust impact. | Lookup/coverage/freshness → safe insufficient state, licensed ingestion review. | Data + science / monitored. |
| 10 Node-forge expiry | Exact governed exception expires 2026-10-16; certain deadline, security/CI impact. | Scheduled audit gate and 2026-10-09 review → official fix/removal; never extend silently. | Security / open time-bound. |
| 11 B2B credential/support/enterprise gap | V1 key/quota design, no external SLA; likelihood conditional, high contract impact. | Key events/usage and client pipeline → rotation, support and readiness evidence before commitments. | B2B + security / open. |
| 12 Observability gap | Sentry crash-only; no production SLO baseline; likelihood unknown, medium response impact. | Incident detection delay → low-cardinality safe metrics first. | Reliability + privacy / open. |
| 13 Storage/media growth and recovery | Supabase private storage, live zero objects and synthetic local byte restore only; likelihood unknown, high privacy impact. | Bytes/age/deletion drill → retention and funded recovery policy after counsel review. | Privacy + data / open. |

## Architecture decision record template and gates

For each future purchase/change record: **problem; measurement window and
source; customer/trust impact; software optimization and free-tier alternative;
provider quote in ₹/month and ₹/year; risk reduction/capacity gain; migration
test; rollback; owner; founder decision.** The
[central trigger matrix](../architecture/SCALE_ARCHITECTURE_100CR.md#central-transitiondecision-matrix)
provides the warning/hard triggers for paid API compute, second replica,
shared B2B limiter, worker separation, queue, DB upgrade, safe replica,
partitioning, telemetry and enterprise operations. SRE/on-call hiring follows
the [organization bottleneck gates](../company/SCALING_ORGANIZATION_100CR.md),
not an ARR number. Revisit a gate only when measured evidence changes; no
Step 18 text itself authorizes provisioning or deployment.
