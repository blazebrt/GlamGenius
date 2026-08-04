# Regression coverage, critical journey and reference seeds

Completion report for the three areas left partially implemented after the
Supabase hardening packages: **backend regression coverage**, the
**deterministic critical journey**, and the **versioned reference-data seed**.

Nothing in the completed architecture was changed: Supabase Auth remains the
sole identity provider, accounts are still keyed on the Supabase UUID, the
invite reservation/finalisation flow, JWT/JWKS validation, PostgreSQL, Supabase
Storage, the privacy-export registry and the durable deletion state machine are
untouched except where a gap is documented below. No MongoDB, V1 route, local
password, custom JWT, payment, subscription or paywall code was reintroduced,
and no browser E2E test was added.

---

## 1. Backend test inventory

**Total: 382 tests, all passing on PostgreSQL** (was 271 collected / 268
passing at the start of this work).

| Test file | Domain (spec §) | Tests | Origin | Risk it protects against |
|---|---|---:|---|---|
| `test_supabase_auth.py` | Identity — token validation (§1.1) | 10 | existing | A forged, anon-role or wrong-issuer token being accepted |
| `test_jwks_asymmetric.py` | Identity — RS256/JWKS (§1.1) | 17 | existing | Key-rotation and asymmetric-verification failures |
| `test_invite_reservation.py` | Identity — invite reservation (§1.1) | 7 | existing | Double-spent, expired or email-mismatched invites |
| `test_invite_bypass_regression.py` | Identity — registration gate (§1.1) | 10 | existing | An authenticated but unregistered caller reaching product routes |
| `test_beta_access.py` | Identity — invite lifecycle (§1.1) | 7 | existing | Exhausted/deactivated invites still redeeming |
| `test_admin_reservation_stats.py` | Identity — admin surface (§1.1) | 3 | existing | Non-admins reaching admin endpoints |
| `test_v2_api.py` | Identity + quiz + scan over HTTP (§1.1) | 14 | existing | Cross-account reads; registration flow regressions |
| `test_domain_consent.py` | Consent (§1.2) | 6 | existing | Analysis running without, or after revoked, consent |
| `test_domain_profile.py` | Profile + onboarding (§1.3) | 6 | existing | Attribute duplication; onboarding state loss |
| `test_domain_inventory.py` | Inventory, all 7 categories (§1.4) | 21 | existing | Lifecycle, ownership and category-validation regressions |
| **`test_domain_media.py`** | **Media (§1.5)** | **29** | **new** | MIME spoofing, oversized uploads, cross-account reads, storage-failure misclassification, row/object drift, prod local-storage, prefix cleanup |
| `test_storage_hardening.py` | Media — Supabase adapter (§1.5) | 13 | existing | Missing object reported as an outage and vice versa |
| `test_no_s3_boto3.py` | Media — removed backends (§1.5) | 5 | existing | S3/boto3 creeping back in |
| `test_domain_ai_gateway.py` | AI gateway + safety (§1.6) | 14 | existing | Fabricated output on provider failure; double usage consumption |
| `test_domain_scan.py` | Scan / photo analysis (§1.7) | 15 | existing | Consent bypass; raw image bytes in payloads |
| `test_domain_quiz_style.py` | Quiz + occasion styling (§1.8) | 12 | existing | Ownership leaks; styling using items not owned |
| **`test_domain_shopping.py`** | **Shopping evaluation (§1.9)** | **19** | **new** | Verdict without its arithmetic; ignoring owned inventory; duplicate-purchase advice; retry double-charging the allowance; payment language |
| **`test_domain_planning.py`** | **Today + planner (§1.10)** | **29** | **new** | Lost completions, recompilation on every read, invented weather, provider outage, cross-account plans/events |
| `test_domain_routines.py` | Routines + ingredient safety (§1.11) | 12 | existing (1 adapted) | Unsafe pairings unflagged; medical language |
| `test_domain_progress.py` | Progress, goals, milestones (§1.12) | 13 | existing (3 fixed) | Silent zeros for missing data; duplicate milestones; engagement rewards |
| `test_domain_memory.py` | Controlled memory (§1.13) | 11 | existing | Deleted facts still influencing output |
| `test_privacy_export.py` | Privacy — registry (§1.14) | 7 | existing | An unclassified table slipping into the schema |
| `test_privacy_api.py` | Privacy — routes (§1.14) | 4 | existing | Cross-account deletion status |
| `test_account_deletion_state_machine.py` | Deletion — state machine (§1.14) | 9 | existing | Lease/retry/terminal-state regressions |
| **`test_domain_privacy_integration.py`** | **Privacy + deletion, real data (§1.14)** | **12** | **new** | Domains missing from an export, secrets in an export, deletion leaving rows/objects, Auth deleted before the data, a failed stage losing the job |
| **`test_monitoring_and_ops.py`** | **Monitoring + ops (§1.15)** | **20** | **new** | Tokens/emails/storage keys/service-role keys reaching Sentry; health lying about the database; production storage misconfiguration; flag drift |
| `test_feature_flag_defaults.py` | Flags (§1.15) | 7 | existing | A new flag shipping with no decided default |
| `test_no_legacy_terms.py` | Vocabulary (§1.15) | 26 | existing | Payment/judgemental terms returning |
| `test_schema_regression.py` | Schema (§1.15) | 5 | existing | Bridge/entitlement tables returning |
| `test_reference_data_seed.py` | Reference data (Part 3) | 16 | existing (extended) | Seed drift, duplicate rows, operator overrides being clobbered |
| `test_critical_journey.py` | Journey — services | 1 | existing | Service-layer end-to-end regressions |
| `test_critical_journey_full.py` | Journey — full domain surface | 1 | existing (extended) | Data-relationship regressions across every domain |
| **`test_critical_journey_api.py`** | **Journey — real HTTP API (Part 2)** | **1** | **new** | The whole product flow breaking at the route layer |

New test files: `test_domain_media.py`, `test_domain_shopping.py`,
`test_domain_planning.py`, `test_domain_privacy_integration.py`,
`test_monitoring_and_ops.py`, `test_critical_journey_api.py`, plus the shared
`tests/journey.py` helper (not a test file).

Restored from Git history: none. The deleted V1-era suites
(`test_privacy.py`, `test_media.py`, `test_v1_regression.py`,
`test_config_flags_and_billing.py`) assume MongoDB, V1 routes, local passwords
and billing — all four are explicitly forbidden by the current architecture, so
restoring them would have meant reintroducing exactly what was removed. Their
*intent* was rewritten against the V2 routes in the new files above.

Tests intentionally not restored, and why:

* **Billing / subscription / paywall suites** — payments are out of the
  architecture; `test_no_legacy_terms.py` asserts they stay gone.
* **V1 route regression suites** — V1 no longer exists.
* **MongoDB fixture suites** — PostgreSQL only.
* **Browser E2E** — GlamGenius is a mobile app; browser automation is not
  release evidence for it.

---

## 2. Product defects found and fixed

Writing the tests surfaced six real gaps. Each is a production fix, not a
test-only accommodation.

1. **Seeded milestone rules could never be awarded.** The seed wrote
   `first_look_completed` (behaviour `one_off`) and `routine_seven_day`
   (behaviour `streak_days`); `progress.service.record_behaviour` rejects both,
   so neither rule could ever fire — and "one-week streak" is an engagement
   reward the progress domain explicitly refuses to ship. Both rows removed;
   the seed now asserts every rule is reachable and in lockstep with
   `progress.milestones.RULES`.
2. **The seeded progress-metric catalogue held 2 of 13 metrics**, one of which
   (`routine_adherence`) does not exist in the registry at all. The catalogue is
   now derived from `progress.registry.METRICS`, so every metric ships with its
   formula version, inputs, missing-data behaviour and explanation.
3. **The seeded ingredient catalogue was disconnected from the engine.** The
   seed wrote its own 10-ingredient list with its own alias spellings, while the
   label parser and rules engine read `routines.ontology` (44 ingredients). The
   seed now mirrors the ontology, including every alias spelling and every
   compatibility rule.
4. **Feature-flag seeds had drifted** — the seed wrote `v2_virtual_try_on`
   (a key nothing reads) and omitted `v2_inventory_batch`, `v2_beta_access` and
   `v2_onboarding` entirely. Flags are now derived from
   `flags.KNOWN_FLAGS` / `STABLE_BETA_DEFAULTS`.
5. **The privacy export omitted memory revisions, memory sources, feedback
   events and behaviour events**, all four classified `INCLUDED` in the
   registry. An export therefore showed the current wording of a memory fact but
   not the correction history or tombstones. All four are now exported.
6. **Shopping evaluation ignored `client_mutation_id`.** The request schema
   accepted a retry key that nothing stored, so a retry on a flaky connection
   created a second candidate and consumed a second run from the beta
   allowance. Added `shopping_candidates.client_mutation_id` (migration
   `0005_shopping_idempotency`, unique per account) and a replay path that
   returns the original verdict.

Two smaller behavioural fixes:

* **Regenerating Today un-ticked completed actions.** The rebuild replaces the
  day's action rows, so completions were dropped. Completions are now carried
  across a rebuild, keyed on what the action *is* (module, type, title).
* **The Sentry scrubber did not redact `SUPABASE_SERVICE_ROLE_KEY`,
  `storage_key` or other credential-shaped key names** — none matched its
  key-name filter. Added `service_role`, `credential`, `private_key`,
  `access_key`, `anon_key`, `dsn`, `storage_key`, `storage_path`, `object_key`.

---

## 3. Critical journey

Three journeys now exist, and all three pass:

| File | Level | What it proves |
|---|---|---|
| `test_critical_journey.py` | services | The original seed → register → inventory → media → consent → export → deletion walk |
| `test_critical_journey_full.py` | services + DB | Data relationships across every domain, including milestone awards through the real behaviour path |
| `test_critical_journey_api.py` | **real V2 HTTP routes** | The full product flow as a phone drives it |

The API journey performs, in order: reference-data seed on an empty database →
admin invite → reserve → simulated Supabase sign-up → register → `/me` →
invite-reuse refusal → profile create/update → onboarding (partial save, resume,
complete) → photo-analysis consent → an item in each of the seven inventory
categories → media upload and association → usage event → face scan → scan
history → quiz questions/submit/read → occasion → styling → look feedback →
shopping evaluation → decision → Today (weather, calendar event, plan, action
completion, regenerate, re-read) → weekly plan → planner days → skincare and
hair-care routine generation → routine adherence → ingredient alias resolution →
progress metrics with formula versions → goal create/update → controlled memory
learn/correct/delete → privacy export → account deletion (202) → deletion worker
stages → storage prefix empty → application rows gone → Supabase Auth deleted
last → deleted identity refused on eleven product routes.

It asserts relationships, not status codes: the look references the created
occasion and uses only owned items; the shopping evaluation names owned items it
compared against; a completed Today action survives a rebuild and is not
duplicated on re-read; the corrected memory fact supersedes the original and the
deleted one disappears; the export carries all twelve domains and all seven
inventory categories with no secrets; and the Supabase Auth spy records zero
objects remaining at the moment it is called.

No payment, subscription, checkout, paywall, upgrade or paid-entitlement step
appears in any journey, and no browser automation is used.

---

## 4. Reference-data seed

Command (unchanged): `python -m app.bootstrap.reference_data`

Seed version: **2026.02.16**

Counts on an empty database (first run):

| Domain | Rows |
|---|---:|
| inventory_categories | 7 |
| inventory_subtypes | 22 |
| ingredients + aliases + compatibility rules | 180 |
| ingredient_contraindications | 5 |
| ingredient_sensitivities | 4 |
| routine_templates (+ steps) | 70 |
| perfume_context | 13 |
| supplement_context | 7 |
| progress (metric definitions + milestone rules) | 25 |
| feature_flags | 19 |

Second identical run: **0 new rows** in every domain (inventory categories are
an upsert and report 7 either way; the tests assert the row count does not
grow). Operator-edited feature-flag `enabled` values survive a reseed; only the
description is refreshed. The seed-version record is written per domain, and
version upgrades rewrite versioned rows in place rather than duplicating them.

---

## 5. Commands run

```
cd backend
alembic upgrade head                        # 0001 → 0005, from an empty database
python -m app.bootstrap.reference_data      # first run, counts above
python -m app.bootstrap.reference_data      # second run, 0 new rows
alembic check                               # No new upgrade operations detected
pytest -q tests/test_reference_data_seed.py # 16 passed
pytest -q tests/test_critical_journey_api.py# 1 passed
pytest -q tests                             # 382 passed
```

The full suite was run twice: against the working database, and against a
freshly created database that had only been migrated and seeded. Both runs:
**382 passed, 0 failed, 0 skipped.**

Formatting, lint and type checking: the project configures none for the backend
(`.github/workflows/ci.yml` runs pytest, Alembic, gitleaks and pip-audit; there
is no ruff/black/flake8/mypy configuration in the repository). `python -m
compileall` over `app/` and `tests/` is clean. The frontend's own
typecheck/lint/Jest gates are unchanged by this work.

Warnings: 37, all pre-existing — Starlette/FastAPI `on_event` deprecations, a
`python_multipart` import notice, and PyJWT `InsecureKeyLengthWarning` from the
deliberately short test signing key.

`backend_test.py` at the repository root was **not** run: it targets the V1 API
(`/api/users/me`, `/api/subscription/create-order`) against a live server with
MongoDB-era assumptions. Those routes no longer exist and payments are out of
the architecture, so the file is superseded by `backend/tests`. It is left in
place rather than deleted, since removing it is outside this task's scope.

---

## 6. Known limitations

* A credential failure from Supabase Storage (`StorageUnauthorized`) is
  surfaced to the client as the same retryable 503 as an outage, although the
  service comment describes a non-retryable 502. It is logged distinctly
  (`storage_unauthorized`), so an operator can tell them apart. The test records
  the behaviour as it is; changing the status code is a client-visible contract
  change and was left alone.
* Style-request idempotency (`/api/v2/style/occasion`) still reuses the request
  row but re-runs the pipeline and consumes the allowance again. Only the
  shopping path was made properly idempotent here, because that is the one
  §1.9 names.
* Native Android/iOS journeys remain unverified from this environment; the CI
  Metro-bundle job is the only mobile evidence and is unchanged.
