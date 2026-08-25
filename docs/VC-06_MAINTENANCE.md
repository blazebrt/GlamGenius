# VC-06 — Skin & Hair maintenance timing

The hidden V2 placeholder at `app/(tabs)/services.tsx` is replaced by a real
domain. Maintenance answers exactly one question — **when is this upkeep next
worth doing** — for the kinds a customer has explicitly chosen to track.

## What it is not

There is no salon discovery, no booking, no pricing, no marketplace, and no
comment on how anyone looks. Nothing here is a procedure, a treatment or a
health instruction. A regression test asserts the catalogue and the Today copy
contain none of that vocabulary.

Maintenance also holds no product authority. `CareRoutinePlan` remains the only
thing that chooses a Care product; a kind that would need one (a conditioning
treatment, say) is deliberately absent from the catalogue for that reason.

## The catalogue

Seven kinds live in `app/domains/care/maintenance_rules.py`, in code so the
engine and any future seed cannot drift apart: haircut, hair trim, hair colour
upkeep, beard upkeep, brow upkeep, nail care, body hair upkeep. Each carries a
default rhythm and a lead time (a quarter of the interval, capped at a week).

## The decision

`app/domains/care/maintenance.py` is pure: plain values in, a decision out, no
database and no clock read — the plan date is always passed in, so a plan is
reproducible. Five states:

- `not_tracked` — no choice made, so nothing is scheduled
- `needs_anchor` — tracked, but no recorded date. **We say so rather than
  anchoring to today**; guessing would manufacture a rhythm nobody set
- `not_due`, `coming_up`, `due` — computed from the last recorded date plus the
  effective interval

A customer's own interval overrides the catalogue. An out-of-range interval
stored by an older row falls back to the catalogue rather than becoming a real
schedule; the API refuses such a value outright rather than silently squashing
it.

## Where it surfaces

**Care** — the screen at `/services` lists what is tracked and what can be
added, with a "Done today" control and a way to stop tracking.

**Today** — at most **one** card, and only when something is genuinely `due`.
`coming_up` deliberately stays on the Care screen, and `needs_anchor` is a
question for Care rather than a task for the day. Maintenance is part of the
canonical Care material: it contributes to the Care fingerprint, the Today
cache key, and the audited `DailyPlanInput` rows, and the locked-day Care
refresh owns the maintenance module so a locked plan updates correctly.

**Event Ready** — one `preparation:maintenance_timing` action when tracked
upkeep falls due on or before the event. Timing comes from
`due_by_event_date`, the same authority; Event Ready never computes a second
schedule.

## Data and privacy

Two account-scoped tables, both `ON DELETE CASCADE`:
`maintenance_preferences` (one row per explicit choice) and
`maintenance_events` (dates the customer recorded, always `user_declared` —
the engine never writes one). Both are classified `INCLUDED` in the privacy
registry and appear in the export under `routines`.

A date after the planning day cannot anchor that day, and recording the same
day twice is one fact, not two.
