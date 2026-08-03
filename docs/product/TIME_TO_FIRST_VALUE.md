# Time to first value ≤ 5 minutes (Fix 18, WP4)

## The promise

A new user arrives, follows the shipped onboarding path, and sees
their first useful, non-generic result within **five minutes**.
That result is theirs — colours from their photo, or care hints
from a product they own — and it is delivered without a payment
prompt, without a required email verification loop, and without a
sign-up gate for the preview.

## The path

1. **Open the app.** The launcher renders in < 3 seconds on the
   tested Android reference device (see WP6).
2. **See "Try a free check"** — the welcome CTA is one tap. No
   sign-up before the preview.
3. **Enter an invite code.** During the private beta the invite is
   the only gate. This is one field, one tap.
4. **Grant camera permission** — a single OS-level prompt. The
   pre-prompt (`docs/engineering/CHECKLIST_MOBILE_UX.md §6`) is
   what makes the OS prompt make sense.
5. **Take a photo, or pick one from the gallery.** The camera
   opens directly on the preview screen — no interstitial "learn
   more" panel between tap and capture.
6. **See the first-value screen.** The preview returns skin tone,
   undertone, and 3 clothing colours in ≤ 30 seconds under normal
   network conditions.

At step 6 the timer stops. Everything after that — full analysis,
routine building, inventory, save-to-account — is elective.

## What blocks a five-minute claim

- Any surface that asks for an email or phone number **before** the
  preview.
- Any modal that says "read our terms" and blocks the CTA.
- Any onboarding tutorial the user cannot skip.
- A camera permission prompt without a pre-prompt.
- An interstitial upsell before the preview shows a result.
- A network round-trip loop that retries silently for more than
  10 seconds without a visible "still working" state.

The list is not exhaustive; a change that adds any of the above
is caught in review by
`docs/engineering/CHECKLIST_MOBILE_UX.md §5` (loading / stale /
empty / error states).

## Measurement (light-touch)

The event register (`docs/engineering/METRICS.md`) records
`event.scan_analyze.completed` and `event.inventory.item_added`.
Time-to-first-value is derived at analysis time by comparing the
first `event.scan_analyze.completed` timestamp against
`user.created_at` (V1) or `account.created_at` (V2). No
per-user timeline is shipped from this — the aggregate is what
informs the product decision.

## Instrumentation constraints

- Never persist a per-user first-value duration.
- Report the aggregate median and 90th percentile only.
- Retire the metric once the product decision about onboarding
  simplification is made (see `METRICS.md` retirement policy).

## Owner action for this branch

None. Fix 18 is a policy document — no code change on this branch
adds or removes an onboarding step. The Work Package 6
device-and-a11y sweep is where the shipped onboarding is walked
against the promise; if a step blocks the five-minute claim on a
real device, that PR fixes it.

## Cross-references

- `docs/engineering/CHECKLIST_MOBILE_UX.md`
- `docs/engineering/METRICS.md`
- `docs/product/PHOTO_COMPARISON_HONESTY.md`

## Payment mechanics

The five-minute path never crosses a payment screen. The billing
surface stays behind `SUBSCRIPTIONS_AVAILABLE=false` and is not on
the onboarding path.
