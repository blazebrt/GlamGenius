# Device matrix (Fix 17, WP6)

The devices the sweep protocol
([`DEVICE_SWEEP_PROTOCOL.md`](DEVICE_SWEEP_PROTOCOL.md)) is walked
against before the non-payment stabilisation phase closes. Priority
is set by the beta's target market (India, Android-first).

Priorities:

- **P0** — must-have. The sweep is not complete without at least
  one clean walk per P0 row.
- **P1** — should-have. A P1 gap is a documented follow-up, not a
  blocker.
- **P2** — nice-to-have.

## Android (primary)

| Priority | Device family | Screen size (in) | Resolution | Android version | Notes |
|---|---|---|---|---|---|
| **P0** | Mid-range 2024 (e.g. Redmi Note 13 / Samsung A25 / Realme 12) | 6.5–6.7 | 1080×2400 | 14 | The single largest cohort in the target market. If the sweep passes here nowhere else is a blocker. |
| **P0** | Compact 2022–2023 (e.g. Pixel 6 / Samsung A34) | 5.8–6.2 | 1080×2340 | 13–14 | Second cohort. Different one-hand reach, different notch. |
| **P1** | Entry 2023 (e.g. Redmi 12 / Realme C55) | 6.6 | 720×1600 | 13 | Lowest-fi screen the sweep tests. Text-scaling and low-res rendering. |
| **P1** | Older flagship 2020–2021 (e.g. Pixel 5 / OnePlus 9) | 6.0 | 1080×2340 | 12–14 | Legacy hardware still in circulation. |
| **P2** | Foldable (Galaxy Z Flip / Fold) | Variable | Variable | 14 | Layout-continuity check; not shipped as a supported target. |

## iOS (secondary)

The beta is Android-first. iOS support is deliberately downstream;
the following devices are walked only if an iOS build ships during
WP6. If iOS is deferred past WP6, this table stays as a plan.

| Priority | Device | OS | Notes |
|---|---|---|---|
| **P1** | iPhone 15 / 15 Pro | iOS 17 | Notch + Dynamic Island. |
| **P2** | iPhone SE (3rd gen) | iOS 17 | Small screen; no notch. |

## Assistive-tech environments

| Priority | Environment | Notes |
|---|---|---|
| **P0** | TalkBack on Android 14 with the app in the primary user flow. | Screen-reader traversal, spoken labels, focus order. |
| **P1** | VoiceOver on iOS 17 (if an iOS build is shipped). | Same three concerns. |
| **P1** | OS text size at maximum on Android. | Truncation / clipping across every walked screen. |
| **P1** | "Reduce motion" on Android. | No animation runs that the user cannot turn off. |
| **P2** | Dark mode toggle. | Only relevant if the app ships an explicit dark theme. |

## Network conditions

| Priority | Condition | Simulated via |
|---|---|---|
| **P0** | Wi-Fi on a normal home / office link. | Real network. |
| **P0** | Airplane mode / no connectivity. | OS toggle. |
| **P1** | Slow 3G. | Device network conditioner. |
| **P2** | High-latency, low-loss. | Optional. |

## What the matrix does not commit to

- The matrix does not promise every screen renders identically on
  every device — brand-specific Android skins introduce minor
  variance. The sweep records the variance, and the copy /
  layout is fixed on the P0 rows first.
- The matrix does not commit to a specific number of physical
  devices the owner must acquire. Two P0 devices are the minimum
  for the phase to close; more is better.
- The matrix does not include Samsung DeX, Chromebook, or any
  desktop-shell rendering path. The web export exists as a smoke
  test (CI's `Expo web export`) and nothing more.

## Cross-references

- [`DEVICE_SWEEP_PROTOCOL.md`](DEVICE_SWEEP_PROTOCOL.md) — the
  step-by-step protocol.
- [`DEVICE_SWEEP_RESULTS_TEMPLATE.md`](DEVICE_SWEEP_RESULTS_TEMPLATE.md)
  — the recording template.
- [`TIME_TO_FIRST_VALUE.md`](TIME_TO_FIRST_VALUE.md) — the
  five-minute promise the sweep validates.
- [`docs/engineering/CHECKLIST_MOBILE_UX.md`](../engineering/CHECKLIST_MOBILE_UX.md)
  — the review-time mobile checklist that lower-cost catches drift
  between sweeps.
