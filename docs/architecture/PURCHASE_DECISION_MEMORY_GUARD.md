# Purchase decision memory and guard

Step 9A adds an append-only, account-owned history beside the existing
`purchase_decisions` current-state rows. The latter remains compatible with
existing purchase clients; each successful current-decision write appends a
`purchase_decision_events` snapshot in the same database transaction.

The event records the candidate, strategy, recommendation snapshot/version and
fingerprint, customer decision, whether it followed the recommendation, and
the event-time identity contract. It is historical authority: it is never
recomputed from today's purchase rules.

## Exact identity

`step-9a-v2` identity is account-independent SHA-256 over a small
strategy-specific allowlist of trusted candidate facts. It excludes price,
timestamps, recommendation values, user decisions, AI identifiers, URLs and
their tracking parameters. Care requires brand/name, product type and a
canonical active-ingredient set; Fragrance requires brand/name, concentration
and family; Style requires a trusted non-draft candidate with brand/name,
subcategory, size, fabric and colour. Anything less is `insufficient`; no
history match is claimed. There is no fuzzy, name-similarity, embedding, or AI
matching.

## Guard

The guard is a current, read-only projection (`step-9a-v2`) of exact matching
events scoped by identity version, category and strategy. It returns
neutral/no-memory, identity-insufficient, historical-context-incomplete, or
exact prior bought/waiting/skipped. It counts distinct candidate considerations,
not every decision transition. A historical event is not current inventory
truth: its snapshot can never produce a current owned/redundancy state. The
guard does not recalculate or override Style ROI, Care verdicts, or Fragrance
coverage rules. Supplements remain prohibited.

The history feed covers Step 9A events only. Existing pre-Step-9 current
decisions are not reconstructed; when one is detected for the current
candidate without a corresponding event, the guard reports
`historical_context_incomplete`.

## Privacy and data boundaries

Events cascade with the account and candidate and are included in account
export. They contain only Store B candidate/decision facts; Step 9A neither
reads nor stores Open Food Facts data, so the ODbL two-store boundary remains
unchanged.
