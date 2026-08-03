# GlamGenius review policy

Every change to `main` must be reviewed by a human who did not write it.
Nothing in this document is optional. If a reviewer is missing, the pull
request waits.

## 1. Principles

1. **No self-approval.** A pull request cannot be merged by its author.
   The author's approval never counts as the "independent" approval.
2. **No agent-approves-agent.** A pull request opened by an automated
   author (an AI assistant, a bot, a scheduled job) must be reviewed by a
   named human. A second automated actor commenting "LGTM" does not count.
3. **The reviewer reads the diff.** The reviewer's approval is a claim
   that the reviewer has read the diff, understood it, and believes it
   accomplishes the summary in the PR description. Rubber stamps are a
   policy violation.
4. **The reviewer reads the tests that actually ran.** Not the tests the
   PR description says should have run — the actual green CI output on
   the head commit, and the extra manual output the PR pastes.
5. **Every merge is squash-merged.** `main` history is linear. Any local
   review comments that led to fixups collapse into one meaningful commit.
6. **Nobody bypasses the checks.** Not the repository owner, not an
   incident-response admin, not a coding agent. Branch protection is
   configured with `enforce_admins=true` (see
   [Branch protection setup](../stabilisation/BRANCH_PROTECTION_SETUP.md)).

## 2. Required approvals

| Change scope | Minimum independent approvals |
|---|---|
| Any change outside the tables below | 1 |
| Authentication, authorization, session, invite (`backend/security.py`, `backend/invites.py`, `backend/routes/users.py`, `backend/app/api/v2/consent.py`) | 1 owner approval (CODEOWNERS) |
| Privacy scrubbers, media validators, media adapters (`backend/app/domains/media/**`, `backend/app/shared/validation/media.py`, `backend/app/api/v2/privacy.py`) | 1 owner approval |
| Migrations (`backend/migrations/**`) and any file that stores derived facts on top of `progress_metrics`, `photo_events`, `preview_attempts` | 1 owner approval |
| Safety rules and ontology (`backend/app/domains/routines/safety.py`, `backend/app/domains/routines/rules.py`, `backend/app/domains/routines/ontology.py`) | 1 owner approval |
| Billing (`backend/app/domains/billing/**`, `backend/app/api/v2/billing.py`) | 1 owner approval, **and** the PR title must not include the word "billing" during the non-payment stabilisation phase |
| Workflow files, CODEOWNERS, this policy | 1 owner approval |

CODEOWNERS enforcement is configured through branch protection, not this
document. This document is what a reviewer reads before they click
approve.

## 3. What counts as an independent reviewer

An **independent reviewer** is a human who:

1. did not write any commit in the pull request,
2. did not co-author any commit in the pull request,
3. is not the automated actor that opened the pull request,
4. has a named GitHub identity (not an unauthenticated bot),
5. has repository write access (so their approval participates in branch
   protection),
6. read the diff.

If no such person is available, the PR waits. The stabilisation phase
grew out of the exact failure mode this rule prevents: work merged with
an "Interim" label on top of it, no independent human between the
generator and `main`.

## 4. What the reviewer checks

Every reviewer, on every PR:

1. **Scope** — the PR does what the description says and only that.
2. **Tests** — the PR fills the "Tests actually run" section of the PR
   template with output that came from this branch, not a paraphrase.
3. **Migrations** — no committed migration file was edited (migrations
   0001–0008 are frozen).
4. **Payment surface** — the diff does not touch payment mechanics. See
   [`docs/engineering/CHECKLIST_EVIDENCE.md`](CHECKLIST_EVIDENCE.md#payment-mechanics-untouched)
   for the exact command the reviewer runs.
5. **Secrets** — no secret material is committed. The CI secret-scan job
   is not a substitute; it is the second line.
6. **CI signal** — every required check on the head commit is green. A
   failing check is not "flaky" until someone re-runs it and it stays
   failing.

Depending on the diff, reviewers use one or more of the domain
checklists:

- [Security](CHECKLIST_SECURITY.md)
- [Privacy](CHECKLIST_PRIVACY.md)
- [Migration](CHECKLIST_MIGRATION.md)
- [AI safety](CHECKLIST_AI_SAFETY.md)
- [Mobile UX](CHECKLIST_MOBILE_UX.md)
- [External integration](CHECKLIST_EXTERNAL_INTEGRATION.md)
- [Evidence](CHECKLIST_EVIDENCE.md)

## 5. Reviewer time budget

There is no minimum time budget, but the reviewer's own memory of the
diff is the source of truth for their approval. If the reviewer cannot,
by memory, describe what the PR changes and why an hour later, they read
it again.

## 6. Escalation

If a reviewer sees a change that they believe is unsafe:

1. Request changes and describe the concern in a review comment.
2. Do not merge. Do not dismiss another reviewer's request-for-changes.
3. If the concern is disputed by the author, tag the repository owner.
4. The owner resolves. No timer forces a merge.

If a reviewer sees a change that touches an area outside their
competence, they mark themselves as "reviewed for the parts I read" and
request another named reviewer for the rest. Partial reviews do not
count as approvals.

## 7. AI-authored changes

An AI-authored change is any commit produced by an automated coding
assistant, whether the human is directing it interactively or the
assistant is running as a scheduled agent.

1. AI-authored commits are marked in the commit message trailer:
   `AI-assisted-by: <tool name>`.
2. The independent reviewer must be human (rule §3, item 4).
3. The PR description must state which parts of the diff were
   AI-generated and which were hand-written by the author. Nothing about
   the diff itself is treated differently, but a reviewer knows to read
   patterns of AI output that are known to look right and be wrong (see
   [CHECKLIST_AI_SAFETY.md §"AI-generated evidence"](CHECKLIST_AI_SAFETY.md#ai-generated-evidence)).
4. AI cannot approve. Even a very good AI review comment is not a
   reviewer sign-off.

## 8. Dependabot and other bot PRs

A dependabot PR:

1. Runs CI like any other PR.
2. Requires one independent human approval.
3. Is squash-merged (never auto-merged during the stabilisation phase).
4. Cannot be approved by the bot that opened it.

Auto-merge is disabled at the repository level during this phase. It may
be re-enabled after Work Package 6 with a documented rationale.

## 9. What this policy is not

- It is not a replacement for the [Branching strategy](BRANCHING_STRATEGY.md).
- It is not a replacement for CODEOWNERS enforcement. Both are required.
- It is not a code style guide.
- It is not a promise that the review will be fast. It is a promise that
  it will happen.

## 10. Change control

This document lives in `docs/engineering/REVIEW_POLICY.md`. Amendments
require the same review process: a PR, an independent reviewer, and an
owner approval (because CODEOWNERS matches `docs/engineering/**`).
