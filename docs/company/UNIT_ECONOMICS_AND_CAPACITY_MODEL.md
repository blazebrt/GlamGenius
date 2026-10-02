# Unit economics and capacity model — replace assumptions with observations

Status: Step 18 planning model, not a forecast, price list, business plan or
spend approval. Read with [scale architecture](../architecture/SCALE_ARCHITECTURE_100CR.md)
and [operational gates](../operations/SCALE_READINESS_AND_SLOS.md). The current
invite/free-tier system does not establish paying-consumer ARPU, partner
commission, enterprise ACV, cloud prices or achieved gross margin. The model
separates Consumer, Commerce and B2B; none depends on the success of another.

## Definitions and accounting firewall

- Active consumer: distinct consented account with a qualifying product
  decision in the defined period. Paying consumer: account with recognized
  recurring consumer subscription revenue, not merely an invite or trial.
- Scan: completed scan attempt; Product Truth decision: evaluated decision
  with a recorded provenance/version. Attempts and decisions can differ.
- Commerce eligible action: governed offer/handoff eligible for attribution;
  outbound open, click and GMV are **not** GlamGenius revenue. Commission is
  recognized only under actual agreement, attribution and accounting rules.
- B2B client: contracted organization. API requests are usage, not revenue.
  B2B ARR is recurring annual contract value recognized under the contract;
  setup fees and one-off usage are not ARR.
- GMV: gross merchandise value through a merchant, not GlamGenius revenue.
  Bookings, cash receipts, recognized revenue and ARR are different measures.
- ARR = annualized recurring consumer revenue + recurring B2B contract value
  + only commerce recurring revenue if accounting actually supports that
  classification. Transactional commerce commission is shown separately as
  annualized transaction revenue. No metric is inflated by adding GMV or
  uncontracted API volume.
- Gross margin below is revenue less variable serving/AI/allocated
  infrastructure/support costs as defined per scenario. It is not net margin;
  sales, R&D, tax, payment and legal costs must be separately modeled before
  an investment or hiring decision.

Product Truth outputs, evidence and ranking are independent of all three
revenue engines. A payer may receive transport/entitlement differences, never
a purchased scientific result.

## Replaceable revenue and cost equations

Let `P` be paying consumers, `ARPUy` annual net recognized recurring revenue
per paying consumer, `B_i` each recognized recurring B2B annual contract,
`E` eligible commerce actions/year, `q` realized commission conversion and
`N` net commission per converted action. Then:

```text
Consumer_ARR = P × ARPUy
B2B_ARR = Σ B_i
Transactional_commerce_annual_revenue = E × q × N
Total_ARR = Consumer_ARR + B2B_ARR + proven_recurring_commerce_ARR
Annualized_revenue = Total_ARR + transactional_commerce_annual_revenue
```

Track cohort, period, refund, tax and recognition basis. Never silently add
transactional commerce to ARR. The **₹100 Cr ARR** label is a strategic target,
not a statement about present traction or an automatic infra trigger.

For operational cost, measure by month and annualize only when stable:

```text
AI_cost = Σ(model_calls × input_tokens/1000 × input_rate
           + output_tokens/1000 × output_rate + retry/fallback charges)
Serving_cost = web + DB + storage + egress + push/cron + observability
               + any measured queue/shared limiter/replica cost
Support_cost = handling_time × loaded_hourly_cost + vendor costs
Gross_profit = recognized_revenue - AI_cost - Serving_cost - Support_cost
Gross_margin = Gross_profit / recognized_revenue  (only if revenue > 0)
```

The app has an estimated AI cost ledger and configurable per-token estimates,
not a verified provider invoice. Reconcile it to invoices, model mix and
fallbacks before using it for pricing or gross margin. Do not put these
scenario assumptions into runtime configuration.

| Unit | Numerator / denominator | Scope and required measurement |
| --- | --- | --- |
| Cost per active consumer | Attributable Consumer AI + serving + support / active consumers | Consent-safe cohort, period and allocation method. |
| Cost per scan | Scan ingest, moderation/storage/compute and attributable support / completed scan attempts | Include failures/retries separately to expose abuse and waste. |
| Cost per Product Truth decision | Evaluation compute + governed data/update cost / completed decisions | Never price truth changes by outcome. |
| Cost per Commerce handoff | Attributable partner-link, support and fraud cost / eligible handoffs | Compare separately with recognized commission; never treat click as sale. |
| Cost per B2B request | API compute, DB, rate/admission, monitoring and support / admitted requests | Separate rejected/unknown-key abuse load and per-client quota. |
| Cost per B2B client | Contract-specific request cost + onboarding + support + security/compliance / active clients | Compare with recognized ACV and contractual obligations. |
| Database/storage cost | Provider invoice allocated by measured GB, IOPS/query load, backups, egress | Keep Store A/OFF Store B and retention purpose distinct. |

AI-specific denominators must be reported separately: `AI cost / completed
scan`, `AI cost / successful validated label extraction`, `AI cost / active
consumer`, and `AI failure/retry spend / all AI spend`. `AI cost / B2B request`
should be zero for the current read-only Product Truth contract; a nonzero
measurement is an investigation trigger, not a new price tier. Include calls
that timed out or failed, rather than counting only successful extractions.

Do not collect raw personal, photo, token, key, medical or private client data
to build these metrics. Use safe aggregates and documented retention; where a
provider does not expose cost attribution, label allocation as estimated.

## Illustrative scenarios — assumptions, not observations or forecasts

All rupee values below are **invented example inputs solely to test the
equations**. The company currently has no verified paid-conversion, ACV,
commission or cloud-spend evidence supporting them. `Cr` = ₹1 crore.
`Commerce` is transactional annualized revenue and excluded from ARR. AI
assumes 24 calls per active consumer per year at an all-in *illustrative*
₹0.10/call; actual token/model costs must replace this. Infra and support
figures are explicitly assumed annual input cells, not quoted provider prices.

| Input / computed result | Conservative | Base | Upside |
| --- | ---: | ---: | ---: |
| Active consumers (assumed) | 50,000 | 500,000 | 2,000,000 |
| Paying consumers (assumed) | 10,000 | 100,000 | 500,000 |
| Consumer annual net ARPU (assumed) | ₹1,000 | ₹1,500 | ₹1,800 |
| B2B clients × annual ACV (assumed) | 5 × ₹2 lakh | 40 × ₹10 lakh | 100 × ₹10 lakh |
| Commerce eligible actions × conversion × net commission (assumed) | 100,000 × 1% × ₹50 | 1,000,000 × 2% × ₹60 | 10,000,000 × 2.5% × ₹60 |
| Consumer ARR (computed) | ₹1.00 Cr | ₹15.00 Cr | ₹90.00 Cr |
| B2B ARR (computed) | ₹0.10 Cr | ₹4.00 Cr | ₹10.00 Cr |
| Total ARR, excluding transactional Commerce (computed) | ₹1.10 Cr | ₹19.00 Cr | ₹100.00 Cr |
| Transactional Commerce annualized revenue (computed; **not ARR**) | ₹0.005 Cr | ₹0.12 Cr | ₹1.50 Cr |
| AI annual cost (assumed rate × calls) | ₹0.012 Cr | ₹0.12 Cr | ₹0.48 Cr |
| Infrastructure annual cost (assumed) | ₹0.06 Cr | ₹0.60 Cr | ₹3.00 Cr |
| Support annual cost (assumed) | ₹0.12 Cr | ₹1.20 Cr | ₹8.00 Cr |
| Illustrative gross margin on ARR only (computed) | 82.5% | 89.9% | 88.5% |
| First likely bottleneck to validate | Founder support and Product Truth review capacity | DB and incident coverage after optimization | Evidence governance, regional reliability and staffed support |

The margin row divides `(ARR - AI - infrastructure - support)` by ARR,
rounding to one decimal; it excludes Commerce revenue and its costs. It is
**not** a projection of actual margin. Changing a single assumption requires
recomputing all outputs and documenting the source/date. For a real decision,
add acquisition cost, refunds, payment fees, B2B usage distribution, actual
hosting quotes and cash runway.

## Capacity arithmetic and hard invariants

Current code defaults `POSTGRES_POOL_SIZE=5`, `POSTGRES_MAX_OVERFLOW=5` per
application process. That is an **upper application-side burst of 10 Store B
connections per process**, not a measured provider allowance. If `R` replicas
run `W` server processes each, a conservative planned budget is:

```text
Store_B_max_app_connections = R × W × (pool_size + max_overflow)
Total_planned_connections = Store_B_max_app_connections
                          + scheduler/admin/migration reserve
                          + provider internal/reserved connections
Required: Total_planned_connections < verified provider limit
```

Use a separate verified Store A/OFF budget; never combine Store A into Store B
or assume identical limits. Before replica 2, the in-process B2B/anonymous
limiter must become shared and cross-replica tested. Before any pool increase,
measure active connections, wait time, transaction duration and provider
limit. Connection pooling or a paid database is not justified by ARR alone.

For measured volume, let `D` daily active consumers, `s` scans/consumer/day,
`m` metadata API requests/scan, `a` AI calls/decision, `k` B2B requests/day:

```text
Scans/day = D × s
Approx_API_requests/day = D × s × m + k + other measured traffic
AI_calls/day = completed_eligible_decisions × a
Peak_requests/second = observed peak/mean factor × Approx_API_requests/day / 86400
Required_app_concurrency ≈ peak_requests/second × observed p95 service seconds
```

The peak factor, eligibility and latency must come from privacy-safe load
measurements, not guesses. Test the production-shaped code locally or in an
isolated synthetic environment, never load-test production or call live AI/
push providers. Query budgets, table growth, job duration, image storage,
retention and egress need independent evidence before scaling. Avoid flaky
wall-clock thresholds in CI; record repeatable test conditions and trends.

## Spend gate and refresh cadence

For each proposed paid component or hire record: problem, seven-day or other
representative measurement, customer/safety impact, software and current-tier
alternatives, provider quote in ₹/month and ₹/year, expected capacity gain,
security/privacy implications, migration rehearsal, rollback, owner, founder
approval and post-change verification. Safety/privacy incidents can trigger
immediate mitigation independent of the measurement window. Review the model
quarterly and after a material pricing, provider, workload or contract change.
