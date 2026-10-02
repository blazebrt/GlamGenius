# GlamGenius Product Truth API — V1 integration guide

For pilot integrators. One endpoint, one barcode per request, read-only.

GlamGenius issues pilot keys by hand; there is no sign-up page. Your contact at
GlamGenius gives you a key and your two limits (requests per minute, requests
per day).

## 1. What you get

For one exact barcode, GlamGenius's **current** decision — Buy, Wait or Skip —
with the food grade, the factors that explain it and the published evidence
each factor rests on. It is computed at the moment you ask, from:

- a pack label GlamGenius itself has confirmed (never a third-party catalogue),
  and
- GlamGenius's published grading rules and evidence.

When those are not all in place, you get an honest `not_enough_information`
answer with a reason. That is a normal, successful answer (HTTP 200), not an
error.

What the answer is **not**:

- not about a specific physical pack, lot or batch — you are not holding one,
  so none is assumed;
- not personal or clinical advice, and not about any particular person;
- not a certification, ranking, endorsement or price comparison;
- not something any contract, fee or limit can change. Every client gets the
  same answer for the same label under the same rules.

## 2. Your key

```
ggb_<12 hex characters>_<64 hex characters>
```

- Shown **once**, when it is issued. GlamGenius keeps only a hash and cannot
  show it again. Store it in your secrets manager.
- Send it only in the `Authorization` header, only over HTTPS, only from your
  servers. Never in a URL, never in a mobile app or browser, never in logs.
- The 12 characters after `ggb_` are the key's public prefix. Quote it, not the
  key, when you contact GlamGenius about a key.
- Lost or exposed? Ask for a new key, switch to it, then ask for the old one to
  be revoked. Both work in between, so rotation needs no downtime.

## 3. The request

```
GET /api/b2b/v1/products/{barcode}/truth
Authorization: Bearer ggb_<prefix>_<secret>
```

- `{barcode}` must be an exact GS1 GTIN-8, GTIN-12 (UPC-A), GTIN-13 (EAN-13) or
  GTIN-14, ASCII digits, with a valid check digit. Send it as printed; do not
  pad or trim.
- **No query parameters, no body.** Any query parameter is refused with 422.
  Nothing you send can change the answer.
- `GET` only.

```bash
curl -sS "https://<api-host>/api/b2b/v1/products/8901770000011/truth" \
  -H "Authorization: Bearer $GLAMGENIUS_B2B_KEY"
```

## 4. The response

Top level (always present):

| Field | Type | Meaning |
| --- | --- | --- |
| `contract_version` | string | Always `b2b-product-truth-v1` for this API version. |
| `barcode` | string | The barcode you asked about. |
| `state` | `available` \| `not_enough_information` | Whether verified Product Truth exists right now. |
| `reason` | string or `null` | `null` when available; otherwise one of the reasons in §5. |
| `truth` | object or `null` | The answer when available; `null` otherwise. |
| `truth_fingerprint` | string | SHA-256 identifying this exact answer (§6). |
| `meta.request_id` | string | Quote this when reporting a problem. |
| `meta.generated_at` | ISO 8601 | When this answer was computed. |

`truth` (only when `state` is `available`):

| Field | Meaning |
| --- | --- |
| `product.name`, `product.brand` | As printed on the confirmed label; `null` when the label does not state it. |
| `facts_provenance` | Always `confirmed_label_snapshot`. |
| `label.version_number` | GlamGenius's version counter for this product's confirmed label. It increases when a changed label is confirmed. |
| `label.content_fingerprint` | Identifies the label content the answer was graded from. |
| `label.completeness` | Always `complete_for_grading` when available. |
| `confidence.level` | `verified` (checked by GlamGenius against the pack), `community` (confirmed by more than one person), `unverified` (confirmed once, not yet checked by GlamGenius). |
| `grade.outcome` | `graded`. |
| `grade.grade` | `A`–`E`. |
| `grade.band` | `green` (A, B), `yellow` (C), `red` (D, E). |
| `grade.engine_version` | The grading engine version. |
| `decision.state` | `decided`. |
| `decision.verdict` | `buy` (A, B), `wait` (C) or `skip` (D, E). |
| `decision.reason_key` | Stable key of the main reason: the first factor in `factors.negatives`, or `label_facts` when nothing lowered the grade. |
| `factors.negatives[]` | Every factor that lowered the grade (below). |
| `factors.positives[]` | What the label declares that is worth knowing (protein, fibre). These carry no rule and no evidence: their source is the label itself. |
| `ruleset.rule_version` | The grading rule version. |
| `ruleset.fingerprint` | Changes whenever the published rules or their evidence change. |
| `scope.physical_pack_context` | Always `false`. |
| `scope.official_records` | Always `not_evaluated`. Lot-specific government records need a physical pack. |

Each factor:

| Field | Meaning |
| --- | --- |
| `key` | Stable machine key, e.g. `sugar`, `salt`, `processing`, `additive:322`. |
| `label`, `explanation` | Stable keys for your own wording. Not prose. |
| `status` | e.g. `high`, `moderate`, `flagged`, `not_permitted`, `worth_caution`, `declared`. |
| `band` | `green`, `yellow` or `red`. |
| `quantity` | `{value, unit, basis}` — `basis` is `per_100_g` or `per_100_ml`, never a pack size. Can be `null`. |
| `rule` | The grading rule that applied (negatives only). |
| `evidence` | `status` (always `published`), `rule_version`, `evidence_claim_ids` (never empty), `evidence_claim_version`. `null` on positives. |
| `sources[]` | `name`, `url` (openable), `publisher`, `identifier` of what the rule rests on. |

Field names are stable within `b2b-product-truth-v1`. A new field means a new
contract version, announced in advance. Treat any unknown `reason`,
`verdict` or `band` value as "do not show a verdict".

## 5. `not_enough_information` reasons

| `reason` | What it means for you |
| --- | --- |
| `no_confirmed_label` | GlamGenius has not confirmed a label for this barcode yet. |
| `label_unreadable` | The newest confirmed label cannot be read. |
| `label_incomplete` | The newest confirmed label lacks what grading needs (for example the nutrition panel or ingredient list). |
| `not_graded` | A cooking ingredient such as ghee, oil, salt or sugar. GlamGenius never gives these a letter. |
| `label_facts_insufficient` | The facts on the label are not enough to grade. |
| `evidence_unpublished` | A rule needed to explain the grade has not finished GlamGenius's evidence review. |

Show these as "not enough information", never as a verdict. Ask again later:
answers change when labels are confirmed and evidence is published.

## 6. Verifying `truth_fingerprint`

The fingerprint is SHA-256 over the canonical JSON — keys sorted, no
whitespace, UTF-8, non-ASCII characters unescaped — of exactly these five
fields: `contract_version`, `barcode`, `state`, `reason`, `truth`. It excludes
`meta`, and it contains nothing about you, your key or your limits.

```python
import hashlib, json

def truth_fingerprint(body: dict) -> str:
    material = {key: body[key] for key in ("contract_version", "barcode", "state", "reason", "truth")}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

assert truth_fingerprint(body) == body["truth_fingerprint"]
```

Use it to tell whether an answer changed since you last fetched it: the same
fingerprint means the same answer. Do not store answers as permanent; the
decision is current as of `meta.generated_at` only.

## 7. Errors

All errors are JSON: `{"detail": {"code": "...", "message": "...", "request_id": "..."}}`.

| HTTP | `code` | What to do |
| --- | --- | --- |
| 401 | `B2B_UNAUTHENTICATED` | The key is missing, malformed, unknown, expired, revoked, or your access is suspended. The response deliberately does not say which. Check the header; then contact GlamGenius with your key prefix. |
| 422 | `B2B_BARCODE_INVALID` | Not an exact GS1 barcode with a valid check digit. Fix the barcode; do not retry as is. |
| 422 | `B2B_QUERY_NOT_ACCEPTED` | Remove every query parameter. |
| 429 | `B2B_RATE_LIMITED` | Authenticated client burst protection or platform security/admission protection. Wait `Retry-After` seconds in either case. |
| 429 | `B2B_DAILY_QUOTA_EXHAUSTED` | Daily limit reached. `Retry-After` is the seconds until midnight UTC, when it resets. |
| 500 | `INTERNAL_ERROR` | Retry later with backoff; report persistent failures with the `request_id`. |

`not_enough_information` is **never** an error: it is HTTP 200.

## 8. Limits

- **Per minute:** your agreed requests per minute, counted for your
  organisation across all your keys.
- **Per day:** your agreed requests per UTC day. Each well-formed,
  authenticated request uses one unit, whether the answer is `available` or
  `not_enough_information`. Rejected requests (401, 422, 429) do not use a
  unit.
- `B2B_RATE_LIMITED` can also reflect platform admission protection, not only
  your agreed requests-per-minute limit. Honor `Retry-After` in either case.
  Do not retry 401 or 422 automatically.

Your limits change how many answers you can get, never what any answer says.

## 9. What GlamGenius records about your use

Per day, for your organisation: how many requests used a unit, how many were
answered `available`, how many `not_enough_information`, and how many were
refused for limits. Not which barcodes you asked about, and not the answers.

## 10. Not in V1

No search, list, batch, export, webhook or feed endpoint; no write endpoint of
any kind; no developer portal or self-service sign-up. One request, one barcode.
