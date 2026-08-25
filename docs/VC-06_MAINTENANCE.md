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

## The catalogue is a suggestion, never a schedule

Seven kinds live in `app/domains/care/maintenance_rules.py`, in code so the
engine and any future seed cannot drift apart: haircut, hair trim, hair colour
upkeep, beard upkeep, brow upkeep, nail care, body hair upkeep.

Each carries a `suggested_interval_days`. That number is **offered**, never
applied: the customer sees it as a preset with a "Use it" control, and until
they choose a rhythm the kind has no schedule at all. The rhythm they choose is
the only authority.

The lead window — how far ahead something reads as coming up — is derived from
the rhythm they actually chose (`lead_days_for`: a quarter of it, floored at a
day, capped at a week). Deriving it from the catalogue preset instead would let
a 3-day rhythm inherit a 7-day window and read as coming up the moment it was
recorded done, so it could never read `not_due` at all.

## The decision

`app/domains/care/maintenance.py` is pure: plain values in, a decision out, no
database and no clock read — the plan date is always passed in, so a plan is
reproducible. Six states, each meaning exactly one thing:

- `not_tracked` — no choice made, so nothing is scheduled
- `needs_cadence` — tracked, but no rhythm chosen. Tracking says the kind
  matters, not how often they do it
- `needs_anchor` — rhythm chosen, but no recorded date. **We say so rather than
  anchoring to today**; guessing would manufacture a starting point nobody set
- `not_due`, `coming_up`, `due` — computed from the last recorded date plus the
  chosen rhythm

An out-of-range interval stored by an older row reads as unset rather than
becoming a real schedule; the API refuses such a value outright rather than
silently squashing it.

## Where it surfaces

**Care** — the screen at `/services`, reachable from the Care hub at
`/improve`, lists what is tracked and what can be added. Behind a per-kind setup
panel the customer can choose or change their rhythm, accept the preset, clear
it, record or correct a historical last date, and turn reminders on or off.
Controls sit behind that panel rather than permanently on screen, so the list
stays readable on a phone.

**Today** — at most **one** card, and only when something is genuinely `due`.
`coming_up` deliberately stays on the Care screen, and `needs_cadence` and
`needs_anchor` are questions for Care rather than tasks for the day: incomplete
configuration never becomes an invented task. Maintenance is part of the
canonical Care material: it contributes to the Care fingerprint, the Today
cache key, and the audited `DailyPlanInput` rows, and the locked-day Care
refresh owns the maintenance module so a locked plan updates correctly.

**Event Ready** — one `preparation:maintenance_timing` action when tracked
upkeep falls due on or before the event. The event's own local date is passed in
explicitly rather than read off the material, so the two can never drift into
being accidentally equal. Timing comes from `due_by_event_date`, the same
authority; a test asserts Event Ready contains no interval arithmetic of its
own. The canonical maintenance fingerprint is part of the Care payload the plan
fingerprint hashes, so a maintenance-only change cannot alter the timeline while
the stored provenance still describes the previous state.

## Notifications

Maintenance reminders are opt-in **per kind** and default to off. That switch,
not the generic notification module flag, is the gate: excluding maintenance
from the module defaults would strand the opt-in, because nothing in the product
turns that flag back on, so an enabled reminder would be suppressed as
`module_disabled` and never send.

`queue_for_plan` asks canonical maintenance state which due kinds the customer
opted into. With none, it moves on to the next action rather than sending
maintenance text or dropping the day's notification. With some, it rebuilds the
notification copy from **only those kinds** — the Today card names every due
kind, but the reminder must never name one the customer switched off. Both use
the same `maintenance_headline` builder so they cannot drift into describing
different things.

Untracking removes eligibility, turning the module off explicitly still
suppresses, and quiet hours, the daily cap and deduplication all still apply.

## Planner provenance

Three versions identify rule sets that VC-06 changed, and all three advance:

- `PLANNER_VERSION` → `vc06-v1`, with a migration moving the `engine_version`
  column defaults. A weekly plan updates its stored version when regeneration
  rebuilds its days, so it cannot report rules that did not produce its content.
- `EVENT_READY_VERSION` → `vc-06-v1`, because the Care material and the timeline
  action rule changed. A plan regenerated under the new rules updates its stored
  version alongside its fingerprint.
- `MAINTENANCE_VERSION` → `vc-06.1`, because `needs_cadence` and the lead-window
  formula are a materially different decision contract from the first cut.

Existing rows keep the version they were written with, which is the point of
storing it.

## Data and privacy

Two account-scoped tables, both `ON DELETE CASCADE`:
`maintenance_preferences` (one row per explicit choice) and
`maintenance_events` (dates the customer recorded, always `user_declared` —
the engine never writes one). Both are classified `INCLUDED` in the privacy
registry and appear in the export under `routines`.

A date after the planning day cannot anchor that day. Because the anchor is the
latest recorded date on or before the planning day, saving a *corrected* earlier
date removes the previous one first — otherwise the correction would sit behind
the old anchor and appear to do nothing. Recording the same day twice is one
fact, not two, and the database resolves that with an upsert rather
than a check-then-insert, so two concurrent retries cannot collide on the unique
constraint. A retry that carries no note leaves any existing note alone.
