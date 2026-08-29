# Physical-device sweep protocol (Fix 17, WP6)

Fix 17 requires that the shipped app is walked on real hardware
before the non-payment stabilisation phase can be declared closed.
The coding-agent pod has no device attached, so the walk itself is
an **owner action**. This document is the protocol the owner
follows on each device in the matrix
([`DEVICE_MATRIX.md`](DEVICE_MATRIX.md)); the results template
([`DEVICE_SWEEP_RESULTS_TEMPLATE.md`](DEVICE_SWEEP_RESULTS_TEMPLATE.md))
is what the owner fills in and attaches to the closing PR.

## 1. Preparation

Before the sweep starts, on the device:

- [ ] Sign in with a **fresh** test account (do not use a real
      user's account).
- [ ] Clear app storage so onboarding starts from step 1.
- [ ] Enable TalkBack (Android) / VoiceOver (iOS) for the a11y pass
      and leave it on until told to turn it off.
- [ ] Enable "Reduce motion" for the reduced-motion pass.
- [ ] Set the OS text size to the largest supported value for the
      large-text pass.
- [ ] Note the device model, OS version, and screen size in the
      results file.

## 2. The five-minute onboarding walk (Fix 18 acceptance)

Start a stopwatch when the app icon is tapped. Stop it when the
first-value screen shows a personal result (colours or hints tied
to the user's photo).

- [ ] The app launches in ≤ 3 seconds.
- [ ] "Try a free check" appears without an unskippable tutorial.
- [ ] Invite-code field is one tap away, keyboard type is correct.
- [ ] Camera permission prompt fires **after** the pre-prompt copy
      is shown.
- [ ] Camera opens on tap; no interstitial upsell.
- [ ] First-value screen renders in ≤ 30 seconds under normal
      network.
- [ ] The stopwatch reads ≤ 5 minutes end-to-end.

Cross-reference: [`docs/product/TIME_TO_FIRST_VALUE.md`](TIME_TO_FIRST_VALUE.md).

## 3. Screen matrix (walk each once per device)

Every screen in `frontend/app/` and `frontend/app/(tabs)/`. Read
the copy aloud as if the user is reading it. Take one screenshot
per screen for the results file.

### Tabs (`frontend/app/(tabs)/`)

- [ ] `home.tsx` — hero, tip carousel, primary CTAs.
- [ ] `today.tsx` — today's plan, empty state, error state.
- [ ] `planner.tsx` — calendar view, no-events state.
- [ ] `scan-tab.tsx` — camera entry, permission-denied state.
- [ ] `style-me-tab.tsx` — colour result, palette.
- [ ] `inventory.tsx` — list, empty state, search.
- [ ] `services.tsx` — service catalogue.
- [ ] `history.tsx` — scan / recommendation history.
- [ ] `profile.tsx` — settings, sign-out, delete-account.
- [ ] `_layout.tsx` — tab bar rendering, safe-area padding, tab
      switch animation.

### Top-level (`frontend/app/`)

- [ ] `onboarding.tsx` — first-run flow, skippable steps.
- [ ] `(auth)/*` — sign-in, sign-up, invite entry.
- [ ] `scan.tsx` — full scan flow, consent gating.
- [ ] `look.tsx` — colour look details.
- [ ] `my-appearance.tsx` — appearance memo.
- [ ] `improve.tsx` — improvement suggestions.
- [ ] `progress.tsx` — progress photos + timeline (must not overlay
      any % / score — see
      [`PHOTO_COMPARISON_HONESTY.md`](PHOTO_COMPARISON_HONESTY.md)).
- [ ] `memory.tsx` — memory list.
- [ ] `shelf.tsx` — owned-inventory shelf.
- [ ] `inventory-add.tsx`, `inventory-item.tsx`,
      `inventory-insights.tsx` — inventory flow.
- [ ] `recommendations.tsx` — advice history.
- [ ] `get-advice.tsx` — new advice request.
- [ ] `shopping-check.tsx` — shopping-decision helper.
- [ ] `paywall.tsx` — must be reachable but declared **inactive**
      while `SUBSCRIPTIONS_AVAILABLE=false`; confirm the screen
      does not attempt a Razorpay handshake.
- [ ] `service-details.tsx` — one service catalogue entry.

For every screen, record:

1. Screenshot (portrait; landscape if the screen supports it).
2. Any obscured content (tab bar overlap, keyboard cover, notch
   overlap).
3. Any missing empty / loading / error state.
4. Any string that trips the tone rules (score, "money wasted",
   diagnosis-shaped language — see
   [`CHECKLIST_AI_SAFETY.md`](../engineering/CHECKLIST_AI_SAFETY.md#4-never-allowed-patterns)).

## 4. Accessibility pass

With the screen reader still on:

- [ ] Every interactive element on every walked screen has a
      spoken label that describes what will happen, not what the
      icon looks like.
- [ ] Screen-reader traversal order matches the visual order on
      every walked screen.
- [ ] No purely-colour signal (a red badge also has text; a green
      tick also has an "OK" label).
- [ ] With OS text size at maximum, every walked screen still
      renders without truncated critical text.
- [ ] With "Reduce motion" on, no animation the user cannot turn
      off runs.

## 5. Permission behaviour

- [ ] Deny camera permission on first prompt. Confirm the screen
      shows a first-class "permission denied" state with a way to
      re-open Settings.
- [ ] Deny push permission. Confirm the app still functions and
      later re-prompts only when the user opts in.
- [ ] Deny location permission (if the app asks for it). Confirm
      the weather integration degrades to "no weather" and
      recommendations still generate.

## 6. Network states

- [ ] Turn off Wi-Fi / mobile data mid-scan. Confirm a clear
      error with a retry button, no fabricated result.
- [ ] Throttle to 3G. Confirm loading states are shown; no screen
      is silently blank for > 3 seconds.
- [ ] Restore connectivity. Confirm the app recovers without a
      forced restart.

## 7. Copy sweep

Read every wall of copy aloud. Score against
[`CHECKLIST_AI_SAFETY.md §4`](../engineering/CHECKLIST_AI_SAFETY.md#4-never-allowed-patterns).
Flag every string that:

- Names a condition ("you have rosacea").
- Prescribes a treatment intensity or dosage.
- Uses the phrases "score", "money wasted", "problem area", "flaws".
- Attributes a photo change to a product ("your niacinamide is
  working").
- Claims a percentage improvement or regression between two
  photos.

If a flagged string is found, open a follow-up PR that removes
it. Do not close the sweep until the PR merges.

## 8. Payment sanity check

- [ ] Confirm no screen in the walk offered a subscription purchase
      or opened a Razorpay checkout, per
      `SUBSCRIPTIONS_AVAILABLE=false`.
- [ ] Confirm the `paywall.tsx` screen — if reachable — renders as
      declared-inactive rather than initiating a payment flow.

## 9. Recording the sweep

For each device:

- Copy [`DEVICE_SWEEP_RESULTS_TEMPLATE.md`](DEVICE_SWEEP_RESULTS_TEMPLATE.md).
- Fill in the device metadata, the onboarding stopwatch reading,
  the per-screen checklist ticks / screenshots, the a11y-pass
  outcomes, the permission-denial outcomes, the network-state
  outcomes, the copy sweep findings.
- Attach the completed template + zipped screenshots to the WP6
  PR description.
- Repeat for every device in the matrix.

## 10. Closing WP6

WP6 closes when:

- [ ] At least the two P0 devices in the matrix have completed
      sweeps recorded.
- [ ] Every P0 finding has landed either as a follow-up fix PR or
      as a documented deferred item in the results file.
- [ ] `docs/reports/STABILISATION_REPORT.md` is updated: Fix 17 → PARTIAL or
      DONE with the device list and the results-file path.
- [ ] The final non-payment readiness paragraph in
      `docs/reports/STABILISATION_REPORT.md §"Truthful conclusion"` is
      re-evaluated against the sweep and re-written truthfully.

If the sweep uncovers a class of issue too large to resolve inside
WP6, the honest outcome is Fix 17 = PARTIAL with a bounded fix-up
plan, not a promoted Fix 17 = DONE.
