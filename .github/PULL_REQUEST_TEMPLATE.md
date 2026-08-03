# Pull request

<!--
Fill every section that applies. "Not applicable" is a valid answer; a blank
one is not. The reviewer will read this before the diff.

Payment mechanics (Razorpay, webhooks, refunds, recurring, migrations 0001–0008
insofar as they touch billing tables) are out of scope for this phase. If your
PR touches any of those, expect it to be blocked. See
docs/engineering/CHECKLIST_EVIDENCE.md § "Payment mechanics untouched".
-->

## Summary

<!-- One paragraph. What changes and why. -->

## Baseline

- Baseline commit (`main` HEAD at branch-off): `<sha>`
- Non-payment stabilisation baseline
  (`89c57e5b1f786de3b631d90f29aa257109feb409`) is an ancestor of this
  head: [ ] yes  [ ] no — if no, stop and rebase.
- Branch name follows [docs/engineering/BRANCHING_STRATEGY.md]
  (../docs/engineering/BRANCHING_STRATEGY.md): [ ] yes.
- Work-package number (if applicable): `01 / 02 / 03 / 04 / 05 / 06 / N/A`.

## Risk

<!--
Low / Medium / High + why. Anything touching auth, privacy, media, safety,
progress, migrations or billing is High by default.
-->

## Rollback plan

<!-- How would we undo this if it goes wrong in production? Include the
exact revert command or migration downgrade command. -->

## Data changes

<!--
Any migration? Any backfill? Any data deletion? Does the change require
follow-up migrations to be safe? Is the migration reversible?
-->

## Tests actually run

<!--
Concrete evidence, not "I think so". The CI output is preferred. If you ran
something locally that CI cannot run (a real device journey, a live provider
call), say which device and paste the timestamped output. See
docs/engineering/CHECKLIST_EVIDENCE.md for the full checklist.
-->

- [ ] `pytest -q tests` — passing (CI run URL: `<url>`)
- [ ] `yarn test --ci --watchAll=false` — passing (no `--passWithNoTests`)
- [ ] `yarn typecheck` — passing
- [ ] `yarn lint --max-warnings=0` — passing
- [ ] `alembic upgrade head` on an empty database — passing
- [ ] `alembic check` — no drift
- [ ] `Alembic round-trip` CI job — passing
- [ ] `Authorization + privacy regression` CI job — passing
- [ ] `Secret scan` CI job — passing
- [ ] `Python dependency audit` CI job — passing (no HIGH/CRITICAL)
- [ ] `Node dependency audit` CI job — passing (no HIGH/CRITICAL)
- [ ] Manual smoke test on a real device (Android / iPhone / web) —
      attach screenshot, or explicitly state that no device was
      available (Work Package 6 owns the full sweep)

## External tests actually run

<!--
Live Gemini? Real Razorpay test webhook? Real weather / calendar / push?
S3-compatible upload against a real bucket? Monitoring test event?

State which of these actually happened, and paste the evidence. "Not run"
is a valid answer.
-->

## UI changes

<!--
Before / after screenshots for every screen that changes. Dark and light
themes if the app supports both. Small screen and large screen.
-->

## Domain checklists

Tick the box on each checklist that applies. If a checklist applies, at
least one line item under it must be checked or explicitly marked N/A
in a review comment.

- [ ] [Security](../docs/engineering/CHECKLIST_SECURITY.md)
- [ ] [Privacy](../docs/engineering/CHECKLIST_PRIVACY.md)
- [ ] [Migration](../docs/engineering/CHECKLIST_MIGRATION.md)
- [ ] [AI safety](../docs/engineering/CHECKLIST_AI_SAFETY.md)
- [ ] [Mobile UX](../docs/engineering/CHECKLIST_MOBILE_UX.md)
- [ ] [External integration](../docs/engineering/CHECKLIST_EXTERNAL_INTEGRATION.md)
- [ ] [Evidence](../docs/engineering/CHECKLIST_EVIDENCE.md) — always

## Payment mechanics untouched

Paste the output of the three commands from
`docs/engineering/CHECKLIST_EVIDENCE.md §5`.

```
$ git diff main -- backend/app/domains/billing backend/app/api/v2/billing.py backend/routes/billing.py
<paste>
$ git diff main -- backend/migrations
<paste>
$ git diff main -- env.example docker-compose.yml docker-compose.test.yml backend/config.py | grep -Ei 'razorpay|subscription|billing|webhook_secret|refund'
<paste>
```

- [ ] `SUBSCRIPTIONS_AVAILABLE=false` unchanged.
- [ ] Migrations 0001–0008 unchanged.
- [ ] Razorpay call surface unchanged.

## AI-assisted authorship

- [ ] Some or all of this PR was produced by an AI coding assistant.
      If checked, name the tool(s): `<tool>`.
- [ ] I confirm the independent reviewer will be human (per
      [`docs/engineering/REVIEW_POLICY.md §7`](../docs/engineering/REVIEW_POLICY.md#7-ai-authored-changes)).
- [ ] The merge commit will carry the trailer
      `AI-assisted-by: <tool>`.

## Known limitations

<!-- What this PR deliberately does not do. Point at the work package
that owns the follow-up. -->

## Owner action required (branch protection etc.)

Only tick items in this section that this PR itself completes. Items
that require the repository owner to click something in GitHub Settings
belong here for tracking; they do not block this PR unless the PR is
the one that documents them.

- [ ] Branch protection is enabled per
      [`docs/stabilisation/BRANCH_PROTECTION_SETUP.md`](../docs/stabilisation/BRANCH_PROTECTION_SETUP.md).
- [ ] CI self-test throwaway-PR procedure has been run at least once
      since the last workflow-file structural change
      ([`docs/engineering/CI_SELF_TEST.md`](../docs/engineering/CI_SELF_TEST.md)).
- [ ] CODEOWNERS enforcement is proven (a PR under an owner rule was
      blocked from merging without owner approval).

## Independent review

Per [`docs/engineering/REVIEW_POLICY.md`](../docs/engineering/REVIEW_POLICY.md),
this PR is not merged without an independent human reviewer.

**Independent reviewer (GitHub handle):** `<@handle>`

The author does not fill in their own handle here. The reviewer fills
in their handle when they approve.

<!--
Every one of these must be checked by the human reviewer other than
the author before merge. Generated code cannot approve itself. See
docs/engineering/REVIEW_POLICY.md.
-->

- [ ] Independent reviewer read the diff.
- [ ] Independent reviewer read the tests that ran (CI output on this
      head commit, not a paraphrase).
- [ ] Independent reviewer read the migrations, if any.
- [ ] Owner approved anything under CODEOWNERS.
- [ ] Reviewer confirmed the three payment-mechanics commands above
      returned no payment-related diff.
