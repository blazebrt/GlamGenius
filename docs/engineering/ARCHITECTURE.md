# Architecture inventory (Fix 10, WP4)

This document is the single-page map of what GlamGenius actually is,
today. It is deliberately short. The ADRs (Architecture Decision
Records) under `docs/engineering/adrs/` carry the rationale for the
non-obvious choices.

## 1. What the app does

GlamGenius is a private-beta styling / skin / hair companion for
Indian users. The app records a user's owned products, reads
labels, offers routine ordering and colour/style suggestions, and
gates AI-powered analysis behind consent and monthly allowances.
Payment mechanics exist in code (Razorpay integration, subscription
catalogue) but are behind `SUBSCRIPTIONS_AVAILABLE=false` and are
out of scope for the current stabilisation phase.

## 2. High-level topology

```
+---------------------+           +--------------------------------+
|                     |  HTTPS    |                                |
|  Expo / RN client   +---------->+  FastAPI  (backend/)           |
|  (frontend/)        |           |  * /api/*    — V1 (Mongo)       |
|                     |           |  * /api/v2/* — V2 (Postgres)    |
+---------------------+           |                                |
                                  |  Alembic migrations 0001–0008  |
                                  |  frozen                        |
                                  +----+-----+------+--------------+
                                       |     |      |
                                       |     |      |
                                +------v-+  +v----+ +v----------+
                                |        |  |     | |           |
                                | Mongo  |  |Pg16 | |  Media    |
                                |  6.x   |  |     | | local /   |
                                |  (V1)  |  |(V2) | |  S3 (Fix 9)|
                                +--------+  +-----+ +-----------+

                    +--------------------------------+
                    |  Gemini (Google AI) — outbound |
                    |  gated by consent, allowance,  |
                    |  and the AI gateway layer      |
                    +--------------------------------+
```

## 3. Component ownership

| Component | Path | Owner surface | Notes |
|---|---|---|---|
| FastAPI backend | `backend/` | `@blazebrt` | Single process serves both V1 and V2. |
| V1 routes (Mongo) | `backend/routes/*.py`, `backend/server.py` | `@blazebrt` | See `V1_DEPRECATION_PLAN.md`. |
| V2 domains (Postgres) | `backend/app/domains/*` | `@blazebrt` | Written for the V2 rewrite; feature-flagged via `V2_FEATURES`. |
| AI gateway | `backend/app/domains/ai_gateway/` | `@blazebrt` | Every provider call passes through here; scrubbing happens above. |
| Safety layer | `backend/app/domains/routines/safety*.py` | `@blazebrt` | Deterministic (Fix 14) plus banned-word sweep. |
| Ingredient rules | `backend/app/domains/routines/ontology.py`, `rules.py` | `@blazebrt` | Every rule documented in `INGREDIENT_COVERAGE.md`. |
| Media | `backend/app/domains/media/` | `@blazebrt` | Local + S3 adapters (Fix 9). |
| Migrations | `backend/migrations/` | `@blazebrt` | Frozen: 0001–0008 not editable. |
| Frontend | `frontend/` | `@blazebrt` | Expo / RN, ships to Android first. |
| Emergent hosting | `.emergent/` | Platform | Audit in `EMERGENT_HOSTING_AUDIT.md`. |

## 4. Data stores

- **MongoDB 6.x** — V1 user records, scans, invites, quiz answers,
  recommendation history. Being deprecated (see
  `V1_DEPRECATION_PLAN.md`); no new V1 tables are added.
- **PostgreSQL 16** — V2 authoritative store for account,
  preferences, inventory, routines, progress, media assets, audit
  log, entitlements, outbox events. Migrations 0001–0008 are
  frozen; corrections land as new numbered migrations.
- **Filesystem or S3** — Media adapter chosen by
  `MEDIA_STORAGE_BACKEND`; production refuses local
  (Fix 9).

## 5. Outbound integrations

| Provider | Purpose | Credential | Live workflow |
|---|---|---|---|
| Gemini | Appearance/ingredient/label analysis | `GEMINI_API_KEY` | `.github/workflows/live-gemini.yml` (opt-in, WP3). |
| S3-compatible | Media storage | `S3_*` env vars | Tested via MinIO in CI (WP2). |
| Razorpay | Billing (dormant) | `RAZORPAY_*` env vars | **Out of scope this phase.** |
| Sentry | Crash reporting | `SENTRY_BACKEND_DSN`, `EXPO_PUBLIC_SENTRY_DSN` | Optional; both scrub PII before sending. |

## 6. Architecture Decision Records (ADRs)

The ADR series records decisions with a real cost of reversal. New
ADRs are added under `docs/engineering/adrs/NNNN-<slug>.md`.

- [ADR 0001 — MongoDB (V1) alongside Postgres (V2)](adrs/0001-dual-datastore.md)
- [ADR 0002 — Feature flags via `V2_FEATURES` env variable + Postgres override](adrs/0002-feature-flags.md)
- [ADR 0003 — Deterministic safety layer + additive model second-opinion](adrs/0003-deterministic-safety.md)
- [ADR 0004 — Media storage adapter pattern with production-refusal guard](adrs/0004-media-storage-adapter.md)
- [ADR 0005 — Migrations 0001–0008 frozen; corrections are new migrations](adrs/0005-migrations-frozen.md)

## 7. What this document is not

- It is not the deployment runbook (that is `docs/OPERATIONS.md`).
- It is not the review policy (`docs/engineering/REVIEW_POLICY.md`).
- It is not the branching strategy (`docs/engineering/BRANCHING_STRATEGY.md`).

## 8. Change control

Amendments to this document require the same review process as any
other file under `docs/engineering/**`: one independent reviewer
plus the owner (CODEOWNERS).
