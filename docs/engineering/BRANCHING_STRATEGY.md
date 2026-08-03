# GlamGenius branching strategy

Fixing the branching model is Fix 20 of the non-payment stabilisation
plan. The previous long-lived agent branch merged partial work as a
single large PR under an "Interim" report. This document describes how
we will not do that again.

## 1. Trunk

- The single trunk is `main`.
- `main` is protected. See
  [`../stabilisation/BRANCH_PROTECTION_SETUP.md`](../stabilisation/BRANCH_PROTECTION_SETUP.md).
- Nobody pushes to `main` directly, including administrators.
- `main` always has passing CI and always has an evidence report that
  matches its state (see [`../../STABILISATION_REPORT.md`](../../STABILISATION_REPORT.md)).

## 2. Feature branches

- Every change is made on a branch off current `main`.
- Branches are short-lived. The intent is measured in days, not weeks.
- One PR merges one focused change. Scope creep is a review-blocker,
  not a nice-to-have.

### Naming

Branches use the form `<type>/<short-scope>`.

Types:

| Type | Meaning |
|---|---|
| `stabilisation/NN-scope` | A numbered work package from the non-payment stabilisation plan (this is what Work Package 1 uses). |
| `feat/scope` | A new user-facing capability. |
| `fix/scope` | A bug fix that does not add capability. |
| `chore/scope` | Housekeeping: dependency bumps, CI edits, doc edits with no product effect. |
| `docs/scope` | Documentation-only. |
| `refactor/scope` | Internal restructuring with no user-visible change. |
| `test/scope` | Adds or repairs tests without a product change. |

Scope tokens are lowercase, hyphen-separated, and describe **the change**,
not the person making it:

- ✅ `stabilisation/01-governance-ci-cleanup`
- ✅ `fix/planning-timezone`
- ✅ `feat/inventory-progressive-onboarding`
- ❌ `alex/wip`, `agent-branch`, `try-again`

Agent-generated branches use a prefix that identifies the agent runner,
e.g. `stabilisation/01-governance-ci-cleanup`. They still go through the
same PR process; the prefix does not skip review.

## 3. Non-payment stabilisation work packages

The stabilisation plan uses a fixed sequence of numbered branches. Each
branch closes exactly the scope for that work package and stops. The
work-package branch is not reused for follow-ups; each subsequent
package starts from fresh `main`.

| Package | Branch |
|---|---|
| 1 — Governance, CI hardening, repo cleanup | `stabilisation/01-governance-ci-cleanup` |
| 2 — Reproducible containers, media, image privacy | `stabilisation/02-containers-media-privacy` |
| 3 — AI reliability, ingredient evidence, safety | `stabilisation/03-ai-safety-evidence` |
| 4 — Product truth, architecture, V1, photo, metrics, onboarding | `stabilisation/04-product-truth-simplification` |
| 5 — Real weather, calendar, push, and monitoring proof | `stabilisation/05-live-integrations-observability` |
| 6 — Physical-device UX, accessibility, and release evidence | `stabilisation/06-device-ux-release-evidence` |

Work Package `N+1` does not open before Work Package `N` is reviewed
and its PR is resolved.

## 4. The forbidden patterns

The following patterns are the ones that got the previous phase into
its current state. They are not permitted.

1. **A long-lived stabilisation branch that accumulates fixes.**
   Package branches are per-package. Do not reuse
   `stabilisation/non-payment-production-readiness` or an equivalent
   super-branch.
2. **Merging under an "Interim" report.** A PR does not merge until its
   evidence report describes the actual state of the diff, not the plan
   the diff started from.
3. **Rewriting a merged migration.** Migrations 0001–0008 are frozen.
   A follow-up correction is a new numbered migration.
4. **Force-push to `main`.** Prohibited by branch protection and this
   document. Force-push to a feature branch is fine before review has
   started; once review has started, use additional commits so the
   diff is stable.
5. **Auto-merge during the stabilisation phase.** GitHub's auto-merge is
   disabled at the repository level. It may be re-enabled after Work
   Package 6 lands, with a documented rationale.
6. **Bypass by an admin during an incident.** If a fix is needed
   urgently, the fix goes through a PR marked `type=fix/incident-N` with
   the same review rules. The review can be fast; it cannot be skipped.
7. **Combining a payment change with a non-payment change.** During the
   stabilisation phase (Work Packages 1–6) no PR modifies payment
   mechanics. A payment change would need its own reserved series
   (Work Package 7 and later, opened only after WP6 closes).

## 5. Merging

- **Merge strategy.** Squash-and-merge only. The rebased-history tab of
  the PR should be one meaningful commit at merge time.
- **Merge commit message.** The subject is the PR title in imperative
  mood. The body is the PR description condensed to the "what changed
  and why" paragraph, plus a `Refs #NN` line pointing at the PR.
- **AI-assisted commits.** Add the trailer `AI-assisted-by: <tool
  name>` to the merge commit if any part of the PR was produced by an
  automated coding assistant. See
  [`REVIEW_POLICY.md §7`](REVIEW_POLICY.md#7-ai-authored-changes).
- **Branch deletion.** GitHub deletes the head branch on merge.
  Preserving history on the branch is not required — history lives in
  the merge commit and in the PR record.

## 6. Backports and hotfixes

- The stabilisation phase does not have long-lived release branches. If
  the situation changes after Work Package 6, this section is updated
  in the same PR that creates the release branch.
- Until then, a hotfix is just a `fix/` branch with a fast review.

## 7. Tags and releases

- Tags are annotated and signed by the owner.
- Tag names follow `vMAJOR.MINOR.PATCH` for the app and
  `stabilisation/NN` for closed non-payment stabilisation work packages.
- A tag is created after the corresponding merge lands on `main` and
  its evidence report has been updated.

## 8. What this document is not

- It is not the PR template. See
  [`../../.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md).
- It is not the review policy. See
  [`REVIEW_POLICY.md`](REVIEW_POLICY.md).
- It is not the deployment or release runbook. That lives in
  [`../OPERATIONS.md`](../OPERATIONS.md).

## 9. Change control

Amendments require the same PR process as any other change under
`docs/engineering/**`: one independent reviewer plus the owner
(CODEOWNERS).
