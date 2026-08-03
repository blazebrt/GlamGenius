# External integration checklist

Use this checklist when the PR adds or modifies an outbound call to a
third-party service — a weather provider, calendar OAuth client, push
provider, monitoring backend, object store, or model provider.

Third-party dependencies expand the product's failure surface, its
compliance surface, and its cost surface. The bar for adding one is
higher than the bar for a purely local change.

## 1. Vetting the provider

- [ ] The reviewer read the provider's current official documentation
      (not a cached summary) at a recorded URL and access date.
- [ ] The pricing tier the code targets is documented (free tier,
      paid tier, per-call cost, per-user quota).
- [ ] The data-retention policy is documented. If the provider stores
      user data by default, the PR configures the shortest allowed
      retention.
- [ ] The provider's terms are compatible with the product's use
      (consumer app, Indian primary market, invite-only beta).
- [ ] A registrable business contact for support and incident
      handling is recorded.

## 2. Least privilege

- [ ] The credentials used are the smallest scope that satisfies the
      call (Calendar readonly, not full-access, unless full-access is
      demonstrably needed).
- [ ] The credential is stored via the approved mechanism (repository
      / environment secret; no `.env` file committed).
- [ ] The credential rotation procedure is documented in the same PR
      or in an existing document referenced from the PR.
- [ ] Test-mode credentials are used in CI; production credentials are
      never in a PR.

## 3. Transport hygiene

- [ ] Every outbound call has a timeout. No default "wait forever".
- [ ] TLS certificate verification is on. No `verify=False`.
- [ ] Redirects are bounded and do not forward the Authorization
      header to a host outside a documented allow-list.
- [ ] Retries are bounded and only for idempotent verbs (or use
      idempotency keys explicitly).
- [ ] The client library version is pinned (no floating `>=`).

## 4. Failure modes

- [ ] The PR documents what happens when the provider is unreachable:
      user-facing wording, cache behaviour, silent-degrade vs.
      hard-fail.
- [ ] The PR documents what happens when the provider returns a
      429 / 5xx: retry policy, backoff, circuit-break threshold.
- [ ] The PR documents what happens when the credential is revoked
      or expired.
- [ ] The user can proceed without the provider (integrations are
      optional in this product).

## 5. Consent and privacy

- [ ] The user consented to the data flow before the first outbound
      call. See [`CHECKLIST_PRIVACY.md §4`](CHECKLIST_PRIVACY.md#4-consent).
- [ ] The outbound payload contains only what the provider needs.
      Coarse location, event start/end, and minimal metadata — not the
      full profile.
- [ ] The user's revoke-and-forget flow removes the data on the
      provider side where the provider supports it, and stops sending
      new data where it does not.

## 6. Testing without the provider

- [ ] The provider-independent test suite still passes with the
      provider unavailable / credentials absent.
- [ ] The live path is exercised in an **opt-in, manually dispatched**
      workflow (see the Work Package 3 pattern for the Gemini live
      workflow when it lands).
- [ ] The live path is cost-capped: a runaway loop cannot generate an
      unbounded bill.
- [ ] The live path is not runnable from an untrusted PR
      (`workflow_dispatch` from `main` only).

## 7. Documentation

- [ ] The provider is added (or updated) in
      `docs/stabilisation/INTEGRATIONS.md` once Work Package 5 creates
      that register. Until then, the PR records: provider, scope,
      credential name, retention, contact, docs URL, docs access date.
- [ ] The runbook for the integration (what to do when it is down)
      exists or is opened as a follow-up task in the PR.

## 8. Payment mechanics

- [ ] The integration is not a payment provider. Payment integrations
      are out of scope for Work Packages 1–6. See
      [`CHECKLIST_EVIDENCE.md`](CHECKLIST_EVIDENCE.md#payment-mechanics-untouched)
      for the reviewer's confirmation command.

## 9. Evidence

- [ ] The PR pastes the response of one real call against the
      provider's sandbox / test tier, redacted.
- [ ] The CI live-workflow status is reported (when the integration
      has a live workflow).
- [ ] Failure modes are demonstrated (a screenshot / log of the
      degraded path, or a test that exercises it against a stub).

## 10. Sign-off

- [ ] Independent reviewer per [`REVIEW_POLICY.md`](REVIEW_POLICY.md).
- [ ] Owner approval per CODEOWNERS (`ai.py`, `ai_gateway/**`,
      `media/**`, and equivalents).
