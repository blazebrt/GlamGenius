# Pull request

<!--
Fill every section that applies. "Not applicable" is a valid answer; a blank
one is not. The reviewer will read this before the diff.

Payment mechanics (Razorpay, webhooks, refunds, recurring, migrations 0001–0008
insofar as they touch billing tables) are out of scope for this phase. If your
PR touches any of those, expect it to be blocked.
-->

## Summary

<!-- One paragraph. What changes and why. -->

## Risk

<!--
Low / Medium / High + why. Anything touching auth, privacy, media, safety,
progress, migrations or billing is High by default.
-->

## Rollback plan

<!-- How would we undo this if it goes wrong in production? -->

## Data changes

<!--
Any migration? Any backfill? Any data deletion?  Does the change require
followup migrations to be safe? Is the migration reversible?
-->

## Tests actually run

<!--
Concrete evidence, not "I think so". The CI output is preferred. If you ran
something locally that CI cannot run (a real device journey, a live provider
call), say which device and paste the timestamped output.
-->

- [ ] `pytest -q tests` — passing
- [ ] `yarn test --ci` — passing
- [ ] `yarn typecheck` — passing
- [ ] `yarn lint` — passing
- [ ] `alembic upgrade head` on an empty database — passing
- [ ] `alembic check` — no drift
- [ ] Manual smoke test on a real device (Android / iPhone / web) — attach screenshot

## External tests actually run

<!--
Live Gemini? Real Razorpay test webhook? Real weather / calendar / push?
S3-compatible upload against a real bucket? Monitoring test event?

State which of these actually happened, and paste the evidence.
-->

## UI changes

<!--
Before / after screenshots for every screen that changes. Dark and light
themes if the app supports both. Small screen and large screen.
-->

## Checklists (leave any that do not apply, check the ones that do)

**Authorization**
- [ ] No route accepts `account_id` or `user_id` from a request body
- [ ] Every write path is scoped to `current.account_id`
- [ ] A cross-account regression test covers this change

**Privacy**
- [ ] No new field stores image bytes, base64 fragments or model output
      outside the media service
- [ ] Personal data added to logs or monitoring is redacted by a tested
      scrubber
- [ ] Account deletion is still complete for the data introduced here

**Migration**
- [ ] No existing migration file was edited
- [ ] Down migration is reversible
- [ ] Data backfill is idempotent and dry-runnable

**AI safety**
- [ ] Any prompt change bumps `PROMPT_VERSION_*`
- [ ] Any schema change bumps the schema version constant
- [ ] `narrative_is_safe` is still applied to any new AI-written string
- [ ] No user-facing wording introduces an appearance score, a diagnosis or
      a dosage

**Mobile UX**
- [ ] Keyboard avoidance
- [ ] Safe areas on every screen touched
- [ ] Touch targets ≥ 44 pt
- [ ] Screen-reader labels for every interactive element
- [ ] Reduced motion respected
- [ ] Largest supported font size does not break the layout

**External integration**
- [ ] Provider documentation link + access date recorded in
      `docs/stabilisation/INTEGRATIONS.md`
- [ ] Secret storage uses the approved mechanism
- [ ] Failure modes documented
- [ ] Test-mode / sandbox call recorded

## Known limitations

<!-- What this PR deliberately does not do. -->

## Reviewer sign-off

<!--
Every one of these must be checked by a human reviewer other than the author
before merge. Generated code cannot approve itself. See
docs/engineering/REVIEW_POLICY.md.
-->

- [ ] Independent reviewer read the diff
- [ ] Independent reviewer read the tests that ran
- [ ] Independent reviewer read the migrations, if any
- [ ] Owner approved anything under CODEOWNERS
