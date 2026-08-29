# Phase 4 — Paid Decision MVP

Occasion styling and shopping decisions, built on the Appearance Digital Twin (Phase 2) and
the Complete Inventory (Phase 3).

Two workflows, and only two:

1. **Style me for an occasion**
2. **Should I buy this?**

The Today engine and the weekly planner are Phase 5 and are not implemented here.

---

## 1. Baseline

Verified before any Phase 4 code was written.

| | |
|---|---|
| Baseline commit | `5194d9a321e297d3a0a33dd92d5e9173d4d4ee33` |
| Branch created from | that commit, working tree clean |
| Development branch | `claude/code-handover-prep-8uhjje` |
| Merged predecessors | PR #11 (Phase 1), #12 (Phase 2), #13 (Phase 3) |
| Migrations present | `0001_v2_foundation`, `0002_appearance_digital_twin`, `0003_complete_inventory` |

### Baseline results

| Check | Result |
|---|---|
| `alembic upgrade head` | clean |
| `alembic check` | no drift |
| Backend pytest | **118 passed** |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings |
| Jest | **56 passed** |

No stale `wellness_scores` assertion was found in the provider-independent suite; the whole
baseline was green, so the stop rule was not triggered.

### One environment deviation, stated plainly

`docker compose -f docker-compose.test.yml run --rm backend-tests` **could not be used** in
this environment. Two hosts are blocked by the egress policy:

* `production.cloudfront.docker.com` — Docker Hub's blob CDN (403 on CONNECT)
* `deb.debian.org` — so the backend image's `apt-get` layer cannot build

Docker Hub images were pulled through the permitted `mirror.gcr.io`, so the **databases are
the same containers the compose file specifies** (`mongo:6`, `postgres:16-alpine`). The test
process itself was run on the host with the identical environment block from
`docker-compose.test.yml`, and it runs the same three commands in the same order:
`alembic upgrade head && alembic check && pytest -q tests`. The frontend gate was run with
the same three commands as the `frontend-tests` service.

On a machine with normal registry access, the documented containerised commands run this
work unchanged. Nothing about the code depends on the workaround.

---

## 2. What was built

### A new domain, not an extension of the V1 coach

`backend/ai.py` is untouched and unused by Phase 4. Its duplicated fashion blocks, salon
suggestions, weight references and "current trends" wording are all V1 concepts, and none of
them are reachable from the new code. Phase 4 lives in `backend/app/domains/recommendation/`
and depends on the Phase 2 profile and the Phase 3 inventory, never on the V1 output schema.

| File | Stage | What it does |
|---|---|---|
| `occasions.py` | — | The closed 16-occasion vocabulary, dress codes, per-occasion questions |
| `context.py` | 1, 2 | Gather confirmed facts, resolve them into constraints |
| `compatibility.py` | 4 | Deterministic colour, formality, weather, fit and comfort scoring |
| `candidates.py` | 3, 5 | Filter owned items, assemble complete outfits |
| `ranking.py` | 4, 6 | Score under three different priorities, enforce distinctness |
| `explanation.py` | 7, 8 | The only file that calls a model, and validates everything it says |
| `roi.py` | — | The Appearance ROI model for purchase decisions |
| `service.py` | 9, 10 | Ownership, persistence, serialisation, feedback, entitlements |
| `orchestrator.py` | all | Wires the stages together and records the run |

Candidate generation, ranking, explanation and presentation are separate modules with
separate tests, as required.

### The core guarantee, and why it is structural

**Every owned item in a look is a real inventory row belonging to the caller.**

This is not something the prompt is asked to respect. Items enter a look as
`look_items.inventory_item_id` foreign keys, chosen by `candidates.filter_candidates` from
rows already loaded out of the caller's inventory. The model is never given the opportunity
to name an item: it receives a fixed list and returns prose keyed by variant. On the way out,
`service.serialize_look` re-resolves every id against the account's active inventory and
drops anything that no longer resolves, so an item archived after the look was built stops
appearing as owned and is reported in `unavailable_items` instead.

Only **confirmed** inventory is used. A photo-extracted draft is counted, reported back and
excluded — "you own this" has to be true, and a draft is not yet a fact the user has agreed
to.

### AI failure is survivable by construction

Stages 1–6 never call a model. By the time `explanation.py` runs, the looks exist, the items
are chosen and the verdict is computed. The model can only improve the wording, and four
gates stand between its answer and the user:

1. **Schema** — the AI gateway rejects anything malformed (Phase 1 machinery, unchanged).
2. **Addressing** — a narrative naming a variant we did not ask about is dropped.
3. **Language** — anything matching `BANNED_NARRATIVE_TERMS` is dropped.
4. **Consistency** — a purchase summary that argues with its own verdict is dropped.

If the provider is missing, times out, returns nonsense, or is rejected by any gate, the
deterministic text stands and `explanation_source` says `deterministic`. There is no
fabricated fallback anywhere, because there is nothing to fabricate: the recommendation was
never the model's to make. The test suite proves this — the whole Phase 4 suite runs with
**no Gemini key configured at all**.

### Three meaningfully different options

Top-three-by-score returns the same shirt with three different trousers. Instead each variant
is re-scored under its own priority — balanced, comfort-weighted, contrast-and-freshness
weighted — and a variant is only offered if its core (clothing + shoes) differs from every
variant already chosen and overlaps it by no more than 60%. Fewer than three is a legitimate
outcome; padding the list would be pretending a choice exists.

### The Appearance ROI formula

```
roi = sum(factor value x factor weight) / sum(weight of the factors that could be scored)
```

| Factor | Weight |
|---|---|
| New outfit combinations | 0.22 |
| Fills a gap | 0.16 |
| How different it is from what you own | 0.16 |
| Occasions it covers | 0.12 |
| Colour fit with your wardrobe | 0.12 |
| Price against expected wears | 0.10 |
| Suits your climate | 0.08 |
| How often you would wear it | 0.08 |
| Versatility | 0.06 |

Thresholds: **Buy** at 0.65 and above, **Wait** from 0.45, **Skip** below.

Two overrides the arithmetic cannot outvote:

* something scoring at or above 0.82 similarity to an owned item can never be a Buy;
* something creating zero new outfit combinations can never be a Buy.

Renormalising over *scored* factors is the important detail: a missing price drops the price
factor rather than guessing it, and the remaining factors are reweighted. Missing information
lowers **confidence**, never the score. Every factor's value, weight, contribution and
explanation is persisted to `purchase_evaluation_factors` and returned, and the whole model
is published at `GET /api/v2/shopping/roi-model` so a user told to Skip can check the working.

New combinations are counted from real pairings only — colours must score at least 0.6 and
formality must be within one level — rather than every theoretical permutation, which would
produce a large, flattering and meaningless number.

### One deliberate correction to the colour model

The first implementation put colour names in twelve evenly spaced slots. That made
red-and-teal — a pairing this product has recommended since V1 — score as a clash, because
even spacing over-weights the green-to-blue region. It is now real hue angles in degrees,
with the classic relationships falling where they actually fall. A test asserts that
complementary pairs outscore awkward intervals.

---

## 3. Database

One forward-only migration: **`0004_phase_4_decision_mvp`**, revising `0003_complete_inventory`.

It is purely additive. Migrations 0001–0003 are untouched; no existing table is renamed,
altered or dropped, so an existing deployment upgrades with no data movement.

Fourteen tables:

`occasions`, `style_requests`, `recommendation_runs`, `recommendation_inputs`, `looks`,
`look_items`, `look_adjustments`, `look_feedback`, `shopping_candidates`,
`purchase_evaluations`, `purchase_evaluation_factors`, `purchase_decisions`,
`compatibility_edges`, `recommendation_entitlements`

Every one hangs off `account_links.id`. No table stores user identity, and V1 authentication
was not touched.

`recommendation_inputs` is what makes a run auditable: every fact that fed a recommendation
is written down with its source, so "why did it suggest this" is answerable from the database
months later.

---

## 4. API

```
GET    /api/v2/style/occasion-types
POST   /api/v2/occasions
GET    /api/v2/occasions
GET    /api/v2/occasions/{id}
PATCH  /api/v2/occasions/{id}
POST   /api/v2/style/occasion
GET    /api/v2/looks/{id}
POST   /api/v2/looks/{id}/revise
POST   /api/v2/looks/{id}/swap-item
POST   /api/v2/looks/{id}/feedback
GET    /api/v2/shopping/roi-model
POST   /api/v2/shopping/evaluate
GET    /api/v2/shopping/evaluations/{id}
POST   /api/v2/shopping/evaluations/{id}/decision
```

Behind two fail-closed flags: `v2_recommendations` and `v2_shopping_decisions`. A disabled
flag returns 404, so a switched-off feature looks absent rather than forbidden.

---

## 5. Frontend

| File | What |
|---|---|
| `app/(tabs)/style-me-tab.tsx` | The centre tab: both decisions, side by side |
| `app/style-me.tsx` | Occasion selection, follow-ups, processing, three lookboards |
| `app/look.tsx` | One look, with revise and swap |
| `app/shopping-check.tsx` | Upload, review, verdict, ROI, decision |
| `src/components/style/StylePieces.tsx` | Lookboard and its parts |
| `src/components/shopping/ShoppingPieces.tsx` | Verdict, ROI breakdown, comparisons |
| `src/services/apiV2.ts` | Typed client for all fourteen routes |

Style Me is now the raised centre button in the tab bar, where the skin check used to be; the
check keeps its own tab beside it, and Ideas moved to a pushed route (still reachable from
Home, which already linked to it).

Owned and optional pieces are rendered by **different components** — different colour, icon
and words — so the distinction survives a glance rather than depending on someone reading a
label. Confidence is described in words, not just a number, because it is a statement about
how much we know, not about how good the look is.

---

## 6. Tests

### Backend — 52 new, `backend/tests/test_recommendations.py`

Every listed area is covered: no hallucinated inventory ids, invalid occasion, dress-code
rules, weather rules, colour compatibility, duplicate penalty, outfit-combination
calculation, ROI calculation, screenshot extraction, missing price, missing size,
recommendation differences, revision, item swap, authorization, feedback storage, invalid AI
output, owned-versus-optional labels, inclusive language, no body-shaming, and shareable
result generation.

Worth calling out specifically:

* every route rejects an unauthenticated call, and returns 404 for another account's data;
* an `account_id` in a request body is a 422, never trusted;
* a swap cannot smuggle in another user's item, an item in the wrong category, or an
  unconfirmed draft;
* four separate bad AI payloads each leave the looks intact and deterministic;
* AI wording containing "slimming" / "hides your problem areas" / "body type" is discarded;
* a summary arguing with its own verdict is discarded;
* no response anywhere contains "money wasted", body-shaming wording, or an appearance score;
* the shareable payload contains garment names but no ids;
* an item archived after a look was built stops appearing as owned.

### Frontend — 27 new, across two suites

`styleMe.test.tsx` and `shoppingDecision.test.tsx` cover all sixteen occasions, follow-up
questions, the processing state, owned-versus-optional rendering, save / revise / swap /
reject / share, "why this works", confidence and missing information, the not-enough-inventory
state, the allowance, all three verdicts, the ROI breakdown, owned comparisons, risk notes,
decision recording, and a banned-language sweep over the rendered output of both screens.

### Exact results

| Check | Baseline | After Phase 4 |
|---|---|---|
| `alembic upgrade head` | clean | clean |
| `alembic check` | no drift | no drift |
| Backend pytest | 118 passed | **170 passed** |
| `tsc --noEmit` | clean | clean |
| `expo lint` | 0 errors, 3 warnings | 0 errors, **3 warnings (the same three)** |
| Jest | 56 passed | **83 passed** |
| `expo export` (production, web) | — | succeeded, all new routes emitted |
| Fresh-stack smoke | — | **31/31 checks passed** |

The fresh-stack smoke ran migrations from an empty database up to `0004`, started a real
uvicorn server, registered a user over HTTP, created seven items, produced three distinct
looks, revised one, swapped a shoe, rejected a fabricated item id, stored feedback, ran three
shopping evaluations, recorded a decision, and confirmed V1 still works.

### `backend_test.py` — reported honestly

CLAUDE.md requires this suite to pass. It is the root end-to-end suite that needs a live
Gemini key, which this environment does not have.

**9/18 passed on this branch. 9/18 passed on baseline `main` (`5194d9a`), with the identical
nine failures.** Both were run against a real server on fresh databases with the same
configuration, so this is a like-for-like comparison.

The nine failures are all AI-provider tests — Face Scan, Hair Scan, Recommendations Advice,
Quiz Submit, the signed-out preview, the free-check quota, no-anonymous-scanning, AI rate
limiting, and the Plus ceiling. All of them call Gemini and fail with `ANALYSIS_UNAVAILABLE`
because no key is configured. **This is pre-existing and unrelated to Phase 4.** It is not
being called passing.

The nine that do pass — profile CRUD, services, all four security tests, brute-force
protection, anonymous-signup removal, invite gating, and CORS — pass on both.

The live Gemini suite was not run and received no quota errors, because it was never called.

---

## 7. Security review

| Concern | How it is handled |
|---|---|
| Identity | Always from the signed token via `get_current_account`. Never from a path, query or body. |
| Ownership | Every read and write filtered by `account_id`; another account's id returns 404, not 403, so ids are not enumerable. |
| `user_id` in a body | All request schemas are `extra="forbid"` — an injected id is a 422. |
| Media ownership | Shopping screenshots go through `media_service.get_owned_asset` and must have the inventory purpose. |
| Swap injection | The replacement item is re-fetched scoped to the account, and must be active, confirmed and in the right category. |
| Preferred items | Ids that the caller does not own are dropped during context gathering, not honoured. |
| AI-extracted data | Shopping candidates are `draft` and never become inventory. Nothing auto-verifies. |
| Profile writes | Phase 4 only reads confirmed attributes. It writes nothing to the profile. |
| Secrets | None added. No new environment variable holds a secret; nothing is logged or returned. |
| Logging | Failures log the failure type and variant, never payloads or user content. |
| Billing | `SUBSCRIPTIONS_AVAILABLE` untouched and still false. Entitlements are backend-granted `beta_grant` rows; no payment path can create one. |
| Flags | Fail-closed. Both new flags default off and return 404 when disabled. |

## 8. Cost review

* **One AI call per styling run**, covering all three looks in a single request rather than
  one per look.
* **Zero AI calls** for filtering, scoring, assembly, ranking, revision or item swap.
* **Revision and swap consume no allowance** — a revision is a correction to our first
  answer, and charging for it would penalise the user for it not landing.
* **A run that produces no looks consumes no allowance**, and the empty response says so.
* Shopping: one extraction call only when a screenshot is sent, plus one explanation call.
  Typing details in costs one call.
* Every call goes through the Phase 1 gateway, so it lands in `ai_runs` with latency, tokens
  and estimated cost.
* Deterministic pair scores are cached in `compatibility_edges` and deduplicated per batch.
* Beta allowance: 60 style requests and 60 shopping evaluations per account per month.

---

## 9. Verification steps

On a machine with normal registry access:

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests
docker compose -f docker-compose.test.yml run --rm frontend-tests
docker compose -f docker-compose.test.yml down -v
```

Expect 170 backend tests and 83 frontend tests to pass, with `alembic check` reporting no
drift.

To see it running:

```bash
cp env.example .env          # V2_FEATURES now includes the two new flags
docker compose up --build
```

Then: sign in, add a few wardrobe items and confirm them, open the Style Me tab, pick an
occasion. To check the shopping side, use "Should I buy this?" and enter an item by hand —
it works with no Gemini key, which is the point.

---

## 10. Limitations

Stated rather than hidden.

* **Garment shape is inferred from words** in the subcategory and display name. An item named
  only "Blue thing" classifies as `unknown` and is treated as a top. Adding a subcategory
  fixes it; the look still only contains items the user owns either way.
* **Clothing is one or two pieces.** A one-piece stands alone; otherwise a top is paired with
  a bottom and a single layer may be added. Three-piece traditional sets are recorded as one
  item, not composed.
* **Weather is what the user types**, not fetched. No weather service is integrated, and none
  was in scope. With no weather given, weather scoring is neutral and the response says so.
* **Colour comes from the `colour` detail field.** Items with no colour recorded score
  neutrally on colour rather than being excluded, and the look reports it as missing
  information.
* **Occasion tags are matched by substring**, so an item tagged "office wear" matches the
  office occasion but "corporate" does not.
* **Cost per wear uses a heuristic wear estimate** (`combinations x 3`, clamped to 4–60).
  It is transparent in the factor explanation but it is an estimate, not a measurement.
* **Feedback is a soft signal.** A rejected item is scored down, not banned, because
  something wrong for a wedding may be right for the office.
* **No visual rendering of garments.** Lookboards are typographic; there is no image
  composition, and inventory photos are not laid out into an outfit picture.
* **The dedicated `look_adjustments` history is capped** at the 50 most recent per look in
  the API response.

---

## 11. Acceptance checklist

| Criterion | Status |
|---|---|
| Every owned item in a look exists in inventory | ✅ structural — foreign key, plus re-resolution on read |
| Optional additions are clearly labelled | ✅ separate component, `owned: false`, no id, explicit label |
| The system creates up to three genuinely different options | ✅ distinctness enforced and tested |
| The user can revise or swap an item | ✅ both, with history recorded |
| Shopping evaluation returns Buy, Wait or Skip | ✅ |
| Appearance ROI has a documented formula | ✅ documented here, published at `/shopping/roi-model` |
| Existing owned alternatives are shown where relevant | ✅ |
| Recommendations use profile and inventory context | ✅ recorded in `recommendation_inputs` |
| No recommendation depends on a fabricated fallback | ✅ whole suite runs with no provider configured |
| All relevant tests pass | ✅ 170 backend, 83 frontend; `backend_test.py` matches baseline exactly |
| `PHASE_4_REPORT.md` exists | ✅ |
| The phase is committed | ✅ |

## 12. Preservation

Not done, as required: migrations 0001–0003 unchanged; no V2 table or route renamed; no
history rewritten; PostgreSQL still owns V2; V1 auth not migrated; no duplicated identity; no
ownership check bypassed; no `user_id` from a body; media ownership unchanged; AI cannot
overwrite confirmed attributes; AI-extracted inventory does not auto-verify; no fabricated
fallbacks; no attractiveness or appearance scores; billing still unavailable; still seven
inventory categories; the term "Money Wasted" appears nowhere and is in the banned-language
list; no Phase 5 work; the pull request is not merged.

Preserved: V1 under `/api`, V2 under `/api/v2`, `account_links` as the identity bridge, the
AI gateway and its schema validation, owner-scoped media, explicit profile confirmation,
draft-only inventory extraction, supplement inventory safety, transparent estimated Value to
Recover, backend-controlled subscriptions, fail-closed feature flags.

**Stop after Phase 4.**
