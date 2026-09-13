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

`step-9a-v1` identity is account-independent SHA-256 over canonical stored
candidate facts. It excludes price, timestamps, recommendation values, user
decisions, AI identifiers, and URL tracking/fragment components. A stable
merchant product URL is sufficient. Without one, the stored category, brand,
display name, and at least two structured candidate facts are required.
Anything less is `insufficient`; no history match is claimed. There is no
fuzzy, name-similarity, embedding, or AI matching.

## Guard

The guard is a current, read-only projection (`step-9a-v1`) of exact matching
events. It returns neutral/no-memory, identity-insufficient, exact prior
bought/waiting/skipped, or existing-strategy-proven exact-owned context. It
does not recalculate or override Style ROI, Care verdicts, or Fragrance
coverage rules. Supplements remain prohibited.

## Privacy and data boundaries

Events cascade with the account and candidate and are included in account
export. They contain only Store B candidate/decision facts; Step 9A neither
reads nor stores Open Food Facts data, so the ODbL two-store boundary remains
unchanged.
