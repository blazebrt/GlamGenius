# Mobile UX and accessibility checklist

Use this checklist when the PR touches any Expo / React Native screen,
component, navigation, permission prompt, empty state, or visual
element that a user sees.

Physical-device evidence is the responsibility of Work Package 6
(`stabilisation/06-device-ux-release-evidence`). This checklist covers
the review bar that applies **now**, on every PR that changes a
user-facing surface, whether or not a device is available for that PR.

## 1. Safe areas and layout

- [ ] Every new screen or modal respects the device safe area
      (top notch, bottom home indicator, side rounded corners).
- [ ] Content is not obscured by the tab bar or the keyboard.
- [ ] Long text does not clip. The largest supported font size does
      not break the layout of any modified screen.
- [ ] Layouts render on a small screen (≤ 5.5") and a large screen
      (≥ 6.7") — either a screenshot at both sizes is attached or the
      component uses percentage / flex layout that is size-safe by
      construction.

## 2. Keyboard

- [ ] Every screen that has a text input handles keyboard avoidance
      (the input is not covered by the keyboard on any modified
      screen).
- [ ] Return-key behaviour is set (`returnKeyType`) where the
      workflow implies "next" or "done".
- [ ] Dismissing the keyboard does not lose in-progress input.

## 3. Touch targets

- [ ] Every interactive element on a modified screen has a hit area
      ≥ 44 pt on iOS / 48 dp on Android. Small icons use
      `hitSlop`.
- [ ] Nothing critical relies on a long-press without an equivalent
      tap affordance.
- [ ] Destructive actions (delete, revoke, disconnect) confirm before
      taking effect.

## 4. Accessibility

- [ ] Every interactive element on a modified screen has an
      `accessibilityLabel` that describes what will happen, not what
      the icon looks like.
- [ ] `accessibilityRole` is set (`button`, `link`, `switch`, ...).
- [ ] The screen-reader traversal order matches the visual order.
- [ ] Colour is not the only carrier of meaning (a red badge also has
      text; a green tick also has an "OK" label).
- [ ] Contrast on any new text meets WCAG AA (4.5:1 for body, 3:1
      for large text).
- [ ] Reduced-motion setting is respected: no animation the user
      cannot turn off.
- [ ] Screen-reader announcements do not repeat every render tick;
      `accessibilityLiveRegion` (Android) / `AccessibilityInfo.announceForAccessibility`
      (iOS) is used deliberately.

## 5. Loading, stale, empty, error states

- [ ] Every network-backed screen has a loading state that is not a
      full-screen blocker for content already available.
- [ ] Every network-backed screen has an error state that offers a
      retry.
- [ ] Every network-backed screen has an empty state that explains
      what the user can do next (not just "No data").
- [ ] Every network-backed screen has a stale state when it is
      showing cached data (weather, calendar, media) — the source of
      the data is visible.

## 6. Permissions

- [ ] Every new permission request has a pre-prompt that explains why
      the permission is being asked for.
- [ ] Permission denial is a supported first-class state, not an
      error dialog.
- [ ] The app degrades gracefully when a permission is revoked in
      Settings after being granted once.

## 7. Copy and tone

- [ ] User-facing wording follows the product's non-medical tone (see
      [`CHECKLIST_AI_SAFETY.md`](CHECKLIST_AI_SAFETY.md#4-never-allowed-patterns)).
- [ ] No copy says "score", "grade", "money wasted", or an
      appearance-judgement equivalent.
- [ ] Placeholders are grammatical in every supported locale.
- [ ] Every string user-facing string is available for future
      localisation (no hard-coded English constants inside a
      component's JSX when the codebase uses a strings file).

## 8. Performance

- [ ] Images use `expo-image` or an equivalent with placeholder /
      caching, not the bare React Native `<Image>` for
      unbounded-size photos.
- [ ] Long lists use `@shopify/flash-list` or `FlatList` with
      `keyExtractor` and `getItemLayout` where practical.
- [ ] The screen does not fetch on every focus if the fetch is
      expensive; a stale-while-revalidate or debounce is applied.

## 9. Analytics

- [ ] Any new analytics event is documented in the metric-governance
      table (once Work Package 4 lands that table). Until then, the
      reviewer records the event in the PR description with:
      intended decision, hypothesis, owner, retirement criteria.
- [ ] No analytics event carries an unredacted image URL, personal
      identifier, or free-text user content.

## 10. Physical device

- [ ] If a device is available to the author, at least one physical
      device test was run and the result is described in the PR.
- [ ] If no device is available, the PR states this. It is not a
      block for this PR (Work Package 6 owns the sweep) but it is
      recorded so Work Package 6 knows what has not been walked yet.

## 11. Evidence

- [ ] `yarn typecheck && yarn lint && yarn test --ci` are green.
- [ ] Screenshots / short screen-capture attached for every screen
      that changes, in both the light and dark theme when both are
      supported.

## 12. Sign-off

- [ ] Independent reviewer per [`REVIEW_POLICY.md`](REVIEW_POLICY.md).
