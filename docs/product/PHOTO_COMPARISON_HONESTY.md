# Photo comparison honesty (Fix 15, WP4)

## The claim we do not make

We do not claim that GlamGenius can measure objective skin or hair
progress from photos. Photos are records of what the user chose to
show us, on the day they took them, in the light they were in.
Turning a pair of photos into a "your skin has improved 32 %" score
is exactly the kind of claim the stabilisation brief calls out as
forbidden — a manufactured comparison the user has no way to
verify.

## What the product does

- **Stores photos the user takes.** The `progress_photos` table
  (see `backend/app/domains/progress/models.py`) holds a media
  reference, an area, a taken-on date, and any notes the user
  wrote. No score is stored.
- **Renders a timeline.** The user can see their own photos
  side-by-side over time. That is a memory aid, not a
  measurement.
- **Records user-confirmed observations.** If the user tells the
  app "my skin feels less oily this month", the app records that
  self-report against the timeline. The word "feels" is deliberate:
  a self-report, not a measurement.
- **Never overlays a delta score.** No number appears on any
  progress screen that claims to compare two photos objectively.
  If a user opens two photos next to each other, they see the
  photos and their own labels — nothing else.

## What the product must never do

- Overlay an appearance score (see the safety classifier's
  `safety.body.score` rule; the copy is banned product-wide).
- Report a percentage improvement or regression between two photos.
- Attribute a change in the photo to a product or an ingredient
  ("your niacinamide is working"). Cosmetic changes have too many
  confounders — light, hydration, sleep, camera model, time of day
  — to attribute to a single input from a snapshot.
- Rank users against one another. There is no leaderboard.
- Suggest a user's appearance has degraded ("your acne is worse").

## Copy discipline

Every user-facing string on a progress-photo screen passes through:

1. The banned-word sweep in `backend/app/domains/routines/safety.py`.
2. The structured classifier in
   `backend/app/domains/routines/safety_classifier.py` (Fix 14),
   whose `HARMFUL_BODY_JUDGEMENT` and `GUARANTEED_OUTCOME`
   categories catch appearance judgements and timed promises.

If either layer trips, the string is discarded and the deterministic
result is rendered instead.

## Data honesty

- Progress observations are labelled `self_report`, `photo`, or
  `provider` in the record. A user reading their own history sees
  which was which.
- No aggregate is presented without a **count** alongside it
  ("2 self-reports and 3 photos over 8 weeks"), so the user cannot
  mistake three data points for a trend.

## Owner action for this branch

None. Fix 15 is a policy fix — this document is the source of
truth. Enforcement lives in code already:

- `safety_classifier.HARMFUL_BODY_JUDGEMENT` (Fix 14) blocks the
  banned copy patterns.
- `progress_metrics` in the V2 schema does not store a computed
  photo-diff score.

Future PRs that add a progress-photo surface walk
`docs/engineering/CHECKLIST_AI_SAFETY.md §4` and reference this
document.

## Cross-references

- `backend/app/domains/progress/models.py`
- `backend/app/domains/routines/safety.py`
- `backend/app/domains/routines/safety_classifier.py`
- `docs/engineering/CHECKLIST_AI_SAFETY.md`
- `docs/engineering/CHECKLIST_PRIVACY.md`

## Payment mechanics

Nothing here modifies payment mechanics. Progress photos are not a
paid tier.
