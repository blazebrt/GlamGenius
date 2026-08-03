# Metrics register (Fix 16, WP4)

Every analytics event and every dashboard metric this product
tracks appears here. Format per row:

- **Name** — the event or metric identifier as it appears in code.
- **Decision informed** — what decision this number changes.
- **Hypothesis** — the belief we are testing.
- **Owner** — GitHub handle.
- **Retirement** — the condition under which we stop collecting it.
- **Privacy cost** — any personal data touched, and the redaction
  path.

Adding a row here without a code change, or a code change without
a row here, is a policy violation (`METRIC_GOVERNANCE.md`).

## Product usage

### `event.scan_analyze.completed`
- **Decision informed:** whether the AI quota per user is right.
- **Hypothesis:** most users complete ≤ 2 scans per month.
- **Owner:** @blazebrt
- **Retirement:** when the monthly quota is decided by product;
  or 6 months from the WP4 merge, whichever is sooner.
- **Privacy cost:** counter only. No user id in the event
  payload; aggregation happens in Postgres via
  `entitlements.record_consumption`.

### `event.inventory.item_added`
- **Decision informed:** whether ingredient extraction is worth
  keeping.
- **Hypothesis:** ≥ 60 % of users record at least 3 owned products
  in their first week.
- **Owner:** @blazebrt
- **Retirement:** when hypothesis is confirmed or disproved;
  6 months maximum.
- **Privacy cost:** account id + item id. No product name,
  ingredient list, or free text.

## AI outcomes

### `ai.run.<feature>.status`
- **Decision informed:** which provider / prompt combination is
  reliable enough to ship.
- **Hypothesis:** the success rate per feature is ≥ 95 %.
- **Owner:** @blazebrt
- **Retirement:** 6 months, or when the prompt versions stabilise.
- **Privacy cost:** provider name, feature, `ai_run_id`, status
  bucket (ok/timeout/quota_429/invalid_json/schema_fail/provider_error).
  Never the prompt, never the response body.

### `ai.run.<feature>.cost_estimate_usd`
- **Decision informed:** whether Gemini quota is affordable at
  scale.
- **Hypothesis:** cost per active user per month < $0.10.
- **Owner:** @blazebrt
- **Retirement:** when pricing model changes or 12 months.
- **Privacy cost:** aggregate spend only; no per-user column.

## Safety

### `safety.classifier.block.<category>`
- **Decision informed:** which safety categories fire in practice,
  and whether a new pattern is needed.
- **Hypothesis:** blocks are dominated by DIAGNOSIS and TREATMENT.
- **Owner:** @blazebrt
- **Retirement:** open-ended — this is a safety metric and stays.
- **Privacy cost:** category name only. Never the offending text.

## Reliability

### `http.5xx.count`
- **Decision informed:** whether a rollback is warranted.
- **Hypothesis:** 5xx rate < 0.1 %.
- **Owner:** @blazebrt
- **Retirement:** open-ended.
- **Privacy cost:** path, status, and count. No request body,
  no user id, no token.

### `alembic.check.drift`
- **Decision informed:** whether a migration has drifted from the
  ORM. The CI's `alembic-check` job is the enforcement layer; the
  metric is a friendly view for the operator.
- **Hypothesis:** drift is zero in production.
- **Owner:** @blazebrt
- **Retirement:** open-ended.
- **Privacy cost:** table name only.

## What is deliberately not measured

- Any per-user photo history as a timeline.
- Any appearance-derived score.
- Any free-text field the user typed.
- Any Razorpay identifier, order id, or webhook body.

## Change log

- 2026-08-03 — Initial register, Fix 16 (WP4).
