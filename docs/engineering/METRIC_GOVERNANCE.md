# Metric governance (Fix 16, WP4)

Every analytics event, every dashboard chart, every "north-star"
metric in this repository has to satisfy the same short list of
questions before it lands:

1. **What decision does this metric inform?** If the answer is
   "none", the metric is not worth collecting.
2. **What is the hypothesis?** A metric without a hypothesis is a
   number without a job.
3. **Who owns it?** A named human (a GitHub handle, not a team).
4. **When is it retired?** Every metric has a retirement condition
   — a date, an event, or a threshold — recorded when it lands.
5. **What is the privacy cost?** Any event carrying user text,
   free-form fields, or media references must be justified against
   `docs/engineering/CHECKLIST_PRIVACY.md`. Image URLs, personal
   identifiers, and full request bodies are never logged.

## The register

`docs/engineering/METRICS.md` is the register. Every event and
metric the product tracks lives there, with the five answers
above. A PR that adds a new event edits `METRICS.md` in the same
diff as the code that emits the event; a review that lands one
without the other is a policy violation.

## Categories

- **Product usage** — count-of-users, count-of-actions. Aggregate
  only. No individual-user timelines are shipped from these.
- **AI outcomes** — success vs. specific-failure counters from the
  AI gateway. Never carry the prompt or the response body.
- **Safety** — the count of `safety_classifier` blocks by category
  (Fix 14). No text; only the category.
- **Reliability** — 4xx / 5xx counts, provider timeout counts,
  Alembic drift.

## What we do not measure

- Individual users' scan history as a public leaderboard.
- Any appearance-score derivative (banned by the safety layer).
- The literal contents of any user upload.
- The literal contents of any user-typed field beyond an
  aggregate-length count.

## Retirement

If the hypothesis has been answered — or if six months have passed
without anyone looking at the metric — the event is removed in the
same style it was added: a PR that deletes the code and updates
`METRICS.md`.

## Cross-references

- `docs/engineering/METRICS.md`
- `docs/engineering/CHECKLIST_PRIVACY.md`
- `docs/engineering/CHECKLIST_MOBILE_UX.md §9`
