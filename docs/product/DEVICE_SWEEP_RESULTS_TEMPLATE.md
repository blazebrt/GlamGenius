# Device sweep results — TEMPLATE (Fix 17, WP6)

Copy this file per device, rename to
`docs/product/device-sweeps/<yyyy-mm-dd>-<device-slug>.md`, fill in
every section, attach screenshots (zipped, referenced by filename
below), and link the completed file from the WP6 PR description.

**Do not commit real user data.** The sweep uses a fresh test
account (see [`DEVICE_SWEEP_PROTOCOL.md §1`](DEVICE_SWEEP_PROTOCOL.md#1-preparation)).

---

## Metadata

- **Device:**            *(brand, model)*
- **OS version:**        *(e.g. Android 14)*
- **Screen:**            *(size in inches, resolution)*
- **App version:**       *(build number from the app's Settings → About)*
- **Backend commit:**    *(`git rev-parse HEAD` at the time of the sweep)*
- **Test account:**      *(email or handle — a synthetic one)*
- **Reviewer:**          *(the human who walked the device)*
- **Started at:**        *(UTC timestamp)*
- **Finished at:**       *(UTC timestamp)*

## §2 — Five-minute onboarding walk

- **Stopwatch reading:** _________ minutes _________ seconds
- **App launched in ≤ 3 s:** [ ] yes  [ ] no
- **"Try a free check" one tap away:** [ ] yes  [ ] no
- **Invite-code field render:** [ ] correct  [ ] issue: _______
- **Camera pre-prompt shown before OS prompt:** [ ] yes  [ ] no
- **First-value screen ≤ 30 s:** [ ] yes  [ ] no
- **Total ≤ 5 min:** [ ] yes  [ ] no

Notes / screenshots:

- `screenshots/01-launcher.png`
- `screenshots/02-welcome-cta.png`
- `screenshots/03-invite.png`
- `screenshots/04-camera-preprompt.png`
- `screenshots/05-first-value.png`

## §3 — Screen sweep

Copy the checklist from
[`DEVICE_SWEEP_PROTOCOL.md §3`](DEVICE_SWEEP_PROTOCOL.md#3-screen-matrix-walk-each-once-per-device)
here and tick per screen. For each screen, list any obscured
content, missing state, or copy issue.

### Tabs

- [ ] `home.tsx` — screenshot: `screenshots/tab-home.png` — notes:
- [ ] `today.tsx` — screenshot: `screenshots/tab-today.png` — notes:
- [ ] `planner.tsx` — screenshot: `screenshots/tab-planner.png` — notes:
- [ ] `scan-tab.tsx` — screenshot: `screenshots/tab-scan.png` — notes:
- [ ] `style-me-tab.tsx` — screenshot: `screenshots/tab-style-me.png` — notes:
- [ ] `inventory.tsx` — screenshot: `screenshots/tab-inventory.png` — notes:
- [ ] `services.tsx` — screenshot: `screenshots/tab-services.png` — notes:
- [ ] `history.tsx` — screenshot: `screenshots/tab-history.png` — notes:
- [ ] `profile.tsx` — screenshot: `screenshots/tab-profile.png` — notes:
- [ ] `_layout.tsx` (tab bar) — screenshot: `screenshots/tab-bar.png` — notes:

### Top-level

- [ ] `onboarding.tsx` — notes:
- [ ] `(auth)/*` — notes:
- [ ] `scan.tsx` — notes:
- [ ] `look.tsx` — notes:
- [ ] `my-appearance.tsx` — notes:
- [ ] `improve.tsx` — notes:
- [ ] `progress.tsx` — notes: (must not overlay % or score)
- [ ] `memory.tsx` — notes:
- [ ] `shelf.tsx` — notes:
- [ ] `inventory-add.tsx` — notes:
- [ ] `inventory-item.tsx` — notes:
- [ ] `inventory-insights.tsx` — notes:
- [ ] `recommendations.tsx` — notes:
- [ ] `get-advice.tsx` — notes:
- [ ] `shopping-check.tsx` — notes:
- [ ] `paywall.tsx` — notes: (SUBSCRIPTIONS_AVAILABLE=false; no Razorpay handshake)
- [ ] `service-details.tsx` — notes:

## §4 — Accessibility pass

- [ ] Every interactive element has a spoken label describing what
      will happen: [ ] yes  [ ] gaps in ______
- [ ] Traversal order matches visual order everywhere: [ ] yes
      [ ] issues in ______
- [ ] No purely-colour signal: [ ] yes  [ ] issues in ______
- [ ] Max text size fits every walked screen: [ ] yes  [ ] issues
      in ______
- [ ] "Reduce motion" respected everywhere: [ ] yes  [ ] issues
      in ______

## §5 — Permission behaviour

- [ ] Camera-deny renders a first-class denied state: [ ] yes
      [ ] issue: ______
- [ ] Push-deny is handled without a crash: [ ] yes  [ ] issue: ______
- [ ] Location-deny degrades weather + recommendations gracefully:
      [ ] yes  [ ] issue: ______

## §6 — Network states

- [ ] Mid-scan Wi-Fi drop shows a retry, no fabricated result:
      [ ] yes  [ ] issue: ______
- [ ] Throttled 3G shows loading indicators (no blank > 3 s):
      [ ] yes  [ ] issue: ______
- [ ] Restore-connectivity recovers without forced restart:
      [ ] yes  [ ] issue: ______

## §7 — Copy sweep (against CHECKLIST_AI_SAFETY.md §4)

List every string that named a condition, prescribed a dose, used
the words "score / money wasted / problem area", attributed a
photo change to a product, or claimed a % improvement between two
photos. If none, write "none".

- (finding 1)
- (finding 2)

## §8 — Payment sanity

- [ ] No screen in the walk offered a subscription purchase:
      [ ] yes
- [ ] No screen opened a Razorpay checkout: [ ] yes
- [ ] `paywall.tsx` (if reachable) rendered as declared-inactive:
      [ ] yes

## Findings summary

Categorise each finding:

- **Blocker** (must fix before phase closes): _______
- **P1 follow-up** (fix in a subsequent PR): _______
- **P2 backlog** (recorded, not urgent): _______

## Follow-up PRs opened

- PR #___ — title:
- PR #___ — title:
