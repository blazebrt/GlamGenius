# B2B Product Truth API (Step 17 — Verified Product Truth API V1)

> An organisation can **request** a GlamGenius product decision through an API.
> It can never **influence** one.

```text
Evidence / canonical facts (Store B confirmed labels, published rules)
          │
          ▼
Product Truth  ── app/domains/product/truth.py:grade(product, ruleset)   (shared)
          │
          ├──► Consumer Product Result (/api/v2/scan/verdict/{barcode})
          ├──► Purchase OS (step-14-v1) ──► Commerce (commerce-handoff-v1)
          └──► B2B API (/api/b2b/v1/products/{barcode}/truth, b2b-product-truth-v1)

Nothing flows back up. No client, contract, quota, key, partner or payment
reaches Product Truth.
```

## 1. Mission and trust boundary

Step 17 turns GlamGenius from a consumer app into a controlled intelligence
platform, by **distribution**, not by **ownership** of Product Truth. A retailer,
commerce platform, consumer app or enterprise system can ask for the current
GlamGenius decision about one exact barcode, and gets exactly what the
published rules say about GlamGenius's own confirmed label for it.

The **permanent independence law**: no B2B customer can buy, through money,
contract, quota or any parameter, a better grade, BUY instead of WAIT/SKIP,
evidence suppression or promotion, alternative placement, source priority,
official-record suppression, ranking, certification, safety status, a
favourable explanation or a rule exception. A ₹1 pilot and a ₹100 crore
enterprise get the same bytes for the same confirmed label under the same
published ruleset. This is enforced structurally, not by promise (§9).

## 2. Customer types and V1 scope

Who V1 is for: retailers, commerce platforms, consumer applications,
enterprise systems, and other systems GlamGenius approves that need structured
product intelligence for one barcode at a time.

V1 is **one read-only endpoint**:

| | |
| --- | --- |
| Route | `GET /api/b2b/v1/products/{barcode}/truth` |
| Contract | `b2b-product-truth-v1` |
| Input | one exact GS1 barcode (GTIN-8/12/13/14, valid check digit). Nothing else. |
| Credential | `Authorization: Bearer ggb_<prefix>_<secret>` (B2B key only) |
| Issuance | GlamGenius administrators, controlled pilot only |

Not in V1, deliberately: list, search, browse, batch, CSV, dump, GraphQL,
streaming, webhooks, feeds, write routes of any kind, self-service sign-up, a
developer portal, organisation members, invoices or any charging logic.

`/api/v2` remains the consumer API with consumer principals (Supabase JWT,
device token). `/api/b2b/v1` is a separate surface with a separate principal.
Neither accepts the other's credential.

## 3. Audit (before Step 17)

| Area | What existed | What Step 17 did |
| --- | --- | --- |
| Grading | The Product Result route graded inline: `grade_product` → `enforce_published_required_rules` → `presentation.present`. | **Bounded extraction** into `product/truth.py:grade(product, ruleset)`, called by the consumer route and B2B. Same calls, same order, same inputs; no device, account, subject or request reaches it. |
| Device coupling | Only pack context, official records, community batch scope and Purchase OS ownership use the device. | Untouched. B2B never reaches any of them, so no fake device is needed. |
| Confirmed labels | `latest_label_snapshot`, `readable_label_snapshot`, `completeness` (Step 3/12A). | **Reused as-is** for eligibility. |
| Published evidence | `resolve_production_ruleset`; per-row evidence on every factor. | **Reused as-is**; B2B distributes only rows on published rules that cite a published claim. |
| Auth | Supabase JWT (`get_current_supabase_user`), `get_current_admin` (hidden-admin 404). | Admin routes **reuse** `get_current_admin`. B2B has its **own** dependency; no Supabase code path. |
| Rate limiting | `FixedWindowLimiter` (in-process, bounded table). | **Reused**, with an optional per-key `limit=` and `retry_after_seconds()`; existing callers unchanged. |
| Audit | `audit.record` (keyed IP hash, request id). | **Reused** for every admin change. |
| Redaction | Sentry `scrub_event`; log `OAuthRedactionFilter`. | **Extended** to redact anything shaped `ggb_…`, and `key_hash` by name. |
| Privacy | Registry + `EXPORT_COVERAGE` (1.6, 112 INCLUDED). | Three new classifications; consumer export unchanged. |

Consumer regression: the pre-Step-17 consumer Product Result and Purchase OS
answers for nine scenarios were captured on `main` (`261b157c`) **before** any
route code changed, and are replayed byte for byte
(`tests/test_step17_consumer_product_result_golden.py`).

## 4. Store B only, and the ODbL wall

B2B V1 answers **only** from GlamGenius's own independently captured Store B
confirmed-label facts plus GlamGenius's own published rules and evidence.

- No Open Food Facts value of any kind: no name, brand, ingredients,
  nutriments, categories, image, country, taxonomy or alternative.
- No Store A read and no Open Food Facts network call, ever — not to fill a
  gap, not to widen coverage. A barcode Open Food Facts knows richly and we have
  never confirmed is `not_enough_information`; it becomes `available` the
  moment our own complete confirmed label exists, from that label alone.
- The Step 6A comparable alternative and the Step 6B value comparison are not
  in V1, because both read Store A.
- No combined table, no view, no cache: nothing B2B writes contains a barcode
  or an answer (the only write is a per-client daily count).

`backend/tests/test_odbl_data_wall.py` stays green. The Step 17 suite instruments
every Store A path (cache session, network client, join reader, `lookup`) to
fail if touched, and checks every SQL statement a B2B request issues names only
`b2b_*` tables and `product_label_snapshots`.

## 5. Eligibility

```text
exact GS1 barcode                                (route, before any work)
+ a confirmed label snapshot exists              (Store B)
+ its facts are readable                         (readable_label_snapshot)
+ completeness == complete_for_grading           (Step 12A completeness)
+ the shared authority grades it                 (outcome graded, a letter)
+ every lowering factor rests on a published
  rule that cites at least one published claim   (no source → no claim)
= available
```

Anything else is `state: "not_enough_information"` with one closed `reason`:

| `reason` | Meaning |
| --- | --- |
| `no_confirmed_label` | No confirmed label snapshot for this barcode. |
| `label_unreadable` | The newest snapshot's stored facts are not readable. |
| `label_incomplete` | The newest snapshot is not complete enough to grade (identity only, or required panel/ingredients missing). The newest version is never skipped for an older one. |
| `not_graded` | A culinary ingredient (ghee, oil, salt, sugar…): the engine never gives it a letter. |
| `label_facts_insufficient` | The engine could not grade the facts. |
| `evidence_unpublished` | A required rule, or a rule behind any lowering factor, has not finished the evidence lifecycle — or names no published claim. |

No guess, no LLM, no Open Food Facts, no inferred facts, no broadened
evidence, no B2B-only grading rule. The only B2B-specific condition is the
distribution gate in the last line, and it can only withhold.

## 6. Response contract (`b2b-product-truth-v1`)

Fields are frozen; adding one is a contract change made in
`backend/app/api/b2b/schemas.py`, where every model forbids extra fields. A
field outside the contract fails the request closed (HTTP 500) rather than
leaving the server.

```json
{
  "contract_version": "b2b-product-truth-v1",
  "barcode": "8901770000011",
  "state": "available",
  "reason": null,
  "truth": {
    "product": {"name": "Northstar Corn Flakes", "brand": "Northstar"},
    "facts_provenance": "confirmed_label_snapshot",
    "label": {"version_number": 1, "content_fingerprint": "e108…", "completeness": "complete_for_grading"},
    "confidence": {"level": "unverified"},
    "grade": {"outcome": "graded", "grade": "C", "band": "yellow", "engine_version": "food-grade-v1"},
    "decision": {"state": "decided", "verdict": "wait", "reason_key": "processing"},
    "factors": {
      "negatives": [{
        "key": "processing", "label": "processing", "status": "flagged", "band": "red",
        "explanation": "highly_processed", "quantity": null, "rule": "grade.step1.nova_4",
        "evidence": {"status": "published", "rule_version": "v1",
                     "evidence_claim_ids": ["…"], "evidence_claim_version": 1},
        "sources": [{"name": "Monteiro CA … 2019.", "url": "https://doi.org/10.1017/S1368980018003762",
                     "publisher": "Public Health Nutrition (Cambridge University Press)",
                     "identifier": "MONTEIRO-NOVA-2019"}]
      }],
      "positives": [{
        "key": "protein", "label": "protein", "status": "declared", "band": "green",
        "explanation": "declared_on_label", "quantity": {"value": 7.0, "unit": "g", "basis": "per_100_g"},
        "rule": null, "evidence": null, "sources": []
      }]
    },
    "ruleset": {"rule_version": "v1", "fingerprint": "…"},
    "scope": {"physical_pack_context": false, "official_records": "not_evaluated"}
  },
  "truth_fingerprint": "…",
  "meta": {"request_id": "…", "generated_at": "2026-10-02T07:06:00Z"}
}
```

- **Grade, verdict and reason key are the consumer Product Result's own**,
  from the shared authority. B2B re-decides nothing; the suite checks agreement
  with the consumer reference view and with Purchase OS's reference-mode
  decision.
- **Factors.** `negatives` are every factor that lowered the grade, each with
  its published rule, evidence claim ids and openable sources. `positives` are
  label declarations only (protein, fibre), whose source is the confirmed label
  itself; a positive the engine derives without a published rule of its own is
  withheld. The engine's trace, findings and interpretation notes are internal
  reasoning and are never distributed.
- **Identity** comes from the confirmed label. An absent name or brand is
  `null`, never the barcode and never a catalogue value.
- **Scope.** No physical pack is established for a B2B caller, so
  `physical_pack_context` is always `false`, no lot or batch appears, and
  lot-specific official records are `not_evaluated`.
- **`truth_fingerprint`** is SHA-256 of the canonical JSON (sorted keys, no
  whitespace, UTF-8) of `contract_version`, `barcode`, `state`, `reason` and
  `truth`. It excludes `meta` and contains no client, key, quota or usage, so it
  is identical for every client asking about the same label under the same
  rules. `ruleset.fingerprint` changes when the published rules or their claims
  change.

Never in any answer (checked recursively by key): account, user, subject,
household, device, scan, lot, batch, personal lens, FOR YOU, health modes,
pregnancy, breastfeeding, medication, condition, memory, shelf, inventory,
override, community reporter, private media, Commerce partner, affiliate tag or
link, referral, growth, price, MRP, commission, rank, sponsor, prompt, raw AI
output, internal ids, secrets.

## 7. Authentication and key lifecycle

**Format:** `ggb_<prefix>_<secret>` — prefix is 12 lowercase hex characters
(48 random bits, public, uniquely indexed for lookup); secret is 64 lowercase
hex characters (256 bits). Both come from Python's `secrets`.

**At rest:** only `SHA-256(whole credential)`. Hashing the whole credential
binds the prefix to the secret: swapping one client's prefix into another
client's key matches nothing. `key_hash` is constrained to `^[0-9a-f]{64}$`, so
the database itself refuses a raw key. No encryption or KMS dependency.

**Verification:** one indexed read by prefix (expiry judged by the database
clock in the same statement), then one `hmac.compare_digest` — also when the
prefix does not exist, against a fixed absent hash — then revocation, expiry and
client status. A value not exactly the credential shape (a consumer Supabase
JWT, a device token, a truncated key) never reaches the database.

**Refusal:** one identical 401 body for absent, malformed, unknown, wrong
secret, expired, revoked and suspended-client credentials
(`B2B_UNAUTHENTICATED`, `WWW-Authenticate: Bearer`). No oracle for which.

**Lifecycle (admins only):**

| Route (`/api/v2/admin/b2b/…`) | Effect |
| --- | --- |
| `POST /clients` | Create a client: `client_key` slug, operator-only `display_name`, `requests_per_minute` (1–600), `requests_per_day` (1–1,000,000). |
| `GET /clients` | Clients with their keys: id, prefix, timestamps, state. Never a secret or hash. |
| `POST /clients/{client_id}/keys` | Issue a key, optional future `expires_at`. **The raw key appears in this response only.** |
| `POST /keys/{key_id}/revoke` | Revoke at once; idempotent; the row is kept. |
| `POST /clients/{client_id}/suspend` · `/activate` | Suspend or restore access. Never deletes. |
| `GET /clients/{client_id}/usage?days=N` | Operational counts (§8). |

Authority: the existing `get_current_admin` — anonymous 401, signed-in
non-admin the same hidden-admin 404 as every other admin surface. Every change
is written to `audit_events` (actor type `admin`, the admin's account id when
they have one, client key and key prefix; never the secret or its hash).

Rotation: issue a new key, move the integration, revoke the old one. Both keys
work in between.

## 8. Quotas and rate limits

1. **Burst:** the client's own `requests_per_minute`, in-process
   `FixedWindowLimiter` keyed by client id (never the secret), checked right
   after authentication, before any Product Truth work. Refusal: 429
   `B2B_RATE_LIMITED`, `Retry-After` = seconds until the oldest call in the
   window ages out. Per process: honest for one web process; Step 18 decides
   the multi-instance design once measured.
2. **Daily allowance:** the client's `requests_per_day`, per database UTC day,
   taken by one atomic statement:

   ```sql
   INSERT INTO b2b_api_usage_daily AS usage (client_id, usage_date, request_count)
   VALUES (:client_id, timezone('UTC', now())::date, 1)
   ON CONFLICT (client_id, usage_date) DO UPDATE
      SET request_count = usage.request_count + 1, updated_at = now()
    WHERE usage.request_count < (SELECT requests_per_day FROM b2b_api_clients WHERE id = usage.client_id)
   RETURNING usage.usage_date
   ```

   The conflicting row is locked and the `WHERE` re-evaluated against its
   newest committed version, so two concurrent requests can never both take
   the last unit (proved on two sessions with the second blocked on the row,
   and with 12 parallel HTTP requests against an allowance of 4). Committed at
   once, so the lock lasts one statement. Refusal: 429
   `B2B_DAILY_QUOTA_EXHAUSTED`, `Retry-After` = seconds to the next UTC
   midnight, by the database clock.

A malformed request (422) takes no daily unit and reaches no Product Truth
code. Quota changes how many answers a client gets, never what any answer says.

**Usage** (`b2b_api_usage_daily`, aggregate only): `request_count` (took a
unit), `successful_count` (answered `available`), `not_enough_information_count`,
`rate_limited_count` (429s). Database check: `successful + not_enough_information
≤ request`. No barcode, no answer. The admin usage view reports requests,
answers and refusals — never purchases, conversions, revenue or ARR, which an
API request cannot tell anyone.

## 9. Independence proof

| Claim | How it is enforced and tested |
| --- | --- |
| Client, quota, display name, usage count, key and suspension history never change truth | `b2b.truth.product_truth(session, barcode)` and `project(barcode, snapshot, graded)` have no other parameters; their identifiers name no client, quota, usage, key or access; a 10/day pilot and a 100,000/day enterprise get byte-identical bodies (minus `meta`) and fingerprints; quota, display name, usage count, key rotation and suspend/activate are changed mid-test without moving one byte. |
| Revoking a key changes access, not truth | Revoked key → 401; another client's answer unchanged. |
| Commerce has no influence | No B2B module imports Commerce; enabling an Amazon partner mid-test leaves every byte unchanged; Commerce's `active_partner` explodes if B2B touches it. |
| No Growth, no personalisation | No B2B module imports growth, personal_*, family, inventory, purchase, recommendation, profile, identity, consent, analytics, routines, care, ai_gateway, scan or any `/api/v2` module, or Supabase auth. |
| One Product Truth authority | Both readers call `product.truth.grade(product, ruleset)`; neither calls the engine, the publication boundary or the presenter directly; B2B only ever builds from a confirmed label (`build_confirmed_label`), never from a catalogue half. |
| Product Truth never depends on B2B | No module outside the B2B surface (and the model registry) imports `app.domains.b2b` / `app.api.b2b`; no identifier in nutrition, product, evidence, purchase, commerce, alternatives, value or official records, nor in the consumer product or commerce routes, names B2B. |
| No B2B field enters consumer answers | Consumer Product Result and Purchase OS answers (including `decision_fingerprint`) are identical before and after B2B clients exist and call. |

## 10. Privacy

| Table | Classification | Why |
| --- | --- | --- |
| `b2b_api_clients` | `NOT_USER_OWNED` | An organisation GlamGenius chose to admit; no consumer data. |
| `b2b_api_keys` | `SECRET_EXCLUDED` | A credential hash; it authenticates, so it never leaves. |
| `b2b_api_usage_daily` | `OPERATIONAL` | Aggregate counts; no barcode, no answer. |

No B2B table references `accounts` (or devices); every foreign key is between
B2B tables and `RESTRICT`. Consumer account deletion never touches B2B rows (an
admin's own audit rows keep their usual `SET NULL` behaviour). The consumer
export stays at schema **1.6** with **112** INCLUDED tables; no B2B table is in
it. B2B clients are not consumers and there is no organisation-member model.

## 11. Audit and logging

May be logged: client key, key prefix, outcome (`available`,
`not_enough_information`, `unauthenticated`, `rate_limited`), limit kind
(`burst`/`daily`), request id. Never: the key, its secret, its hash, the
`Authorization` header, the barcode, label contents, the response, or evidence
prose. The log filter and the Sentry scrubber both redact anything shaped
`ggb_…` (deliberately looser than the real format, so a truncated or malformed
key is caught), and Sentry redacts `key_hash` and `api_key` by key name.

## 12. Failure semantics

| HTTP | When | Body `detail.code` |
| --- | --- | --- |
| 200 | `available`, or `not_enough_information` with a reason | — (contract body) |
| 401 | Any credential problem | `B2B_UNAUTHENTICATED` |
| 422 | Not an exact GS1 barcode | `B2B_BARCODE_INVALID` |
| 422 | Any query parameter at all | `B2B_QUERY_NOT_ACCEPTED` |
| 429 | Per-minute limit | `B2B_RATE_LIMITED` (+ `Retry-After`) |
| 429 | Daily allowance spent | `B2B_DAILY_QUOTA_EXHAUSTED` (+ `Retry-After`) |
| 405 | Any method but GET on the truth route | — |
| 500 | A genuine server failure only (including a would-be extra field) | `INTERNAL_ERROR` |

404 is never used to say something scientific about a product.

## 13. AI boundary

No AI is called for any B2B request; the AI gateway is instrumented to fail if
reached. No B2B input can become a prompt: the route accepts no query
parameters, no body and no free text — `tone`, `desired_result`,
`customer_policy`, `brand_priority`, health or personal parameters are all
refused with 422, not ignored.

## 14. Rollback

- **Stop all B2B access at once:** suspend every client (or revoke every key)
  from the admin routes. Nothing else changes.
- **Remove the surface:** revert the Step 17 commits; `alembic downgrade
  l0m1n2o3p4` drops exactly the three B2B tables (and their one index) and
  nothing else. The consumer Product Result is unaffected either way: the
  extraction is byte-identical and is covered by its own golden replay.

## 15. ₹0 architecture

Current PostgreSQL and the current web process only. No payment method, paid
Render service, paid database, Redis, worker, cron, disk, domain, Google
billing, API-gateway SaaS, Auth0, Stripe or usage-metering vendor. No new
dependency. Step 18 decides scaling and multi-instance rate limiting once real
B2B behaviour exists.

## 16. Explicit non-goals (V1)

Public sign-up, developer portal, OAuth client registration, organisation
members, customer passwords, invoices, plans, cards, trials, metered charging,
bulk/list/search/browse/batch/CSV/dump/GraphQL/streaming/webhook/feed/S3,
any write route for clients, authenticated pack submissions, lot-specific
official records, personalised or clinical answers, Open Food Facts
redistribution, a stored or cached answer, and seeding any real customer.
