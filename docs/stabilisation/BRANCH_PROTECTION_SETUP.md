# Branch protection setup — OWNER ACTION REQUIRED

The GlamGenius stabilisation phase adds a reproducible CI workflow, a
`CODEOWNERS` file and a pull request template.  Enforcement of those files
lives in GitHub's branch-protection UI, which cannot be configured by a
coding agent — a repository administrator has to do it once.

This document is the exact checklist.  Nothing in this phase claims that
branch protection was configured; it lists what to click.

## Recommended target

Protect `main` such that:

1. Every merge is via pull request.
2. Every pull request receives an independent human review from someone
   other than the author.
3. Every required check in the CI workflow is green.
4. `CODEOWNERS` review is required for anything the file marks.
5. Force-push is impossible.
6. Direct commit to `main` is impossible.

## Steps in the GitHub UI (as of Jan 2026)

1. Repository → **Settings** → **Branches**.
2. **Branch protection rules** → **Add rule** (or edit the existing rule
   for `main`).
3. **Branch name pattern:** `main`.
4. Under **Protect matching branches**, enable the following.  Every one of
   them is required.

   | Setting | Value |
   |---|---|
   | Require a pull request before merging | ✅ |
   | Require approvals | ✅ — minimum 1 (the review-policy document requires 2 for sensitive areas; enforcement is via CODEOWNERS below) |
   | Dismiss stale pull request approvals when new commits are pushed | ✅ |
   | Require review from Code Owners | ✅ |
   | Restrict who can dismiss pull request reviews | Repository owner |
   | Require status checks to pass before merging | ✅ |
   | Require branches to be up to date before merging | ✅ |
   | Required status checks (search-and-add exactly these names) | see below |
   | Require conversation resolution before merging | ✅ |
   | Require signed commits | Recommended, not enforced by this document |
   | Require linear history | ✅ (no merge commits on `main`, PRs squash-merge) |
   | Do not allow bypassing the above settings | ✅ (nobody, including admins) |
   | Allow force pushes | ❌ |
   | Allow deletions | ❌ |

5. **Required status checks.** Type the following one at a time into the
   status-check search box.  Each one has to have run at least once
   against `main` before it appears in the picker; the first PR after this
   phase merges will cause every check to appear.

   - `Backend unit + integration`
   - `Alembic round-trip`
   - `Frontend Jest + TypeScript + lint`
   - `Expo production web export`
   - `Authorization + privacy regression`
   - `Secret scan`
   - `Python dependency audit`
   - `Node dependency audit`

6. **Restrict who can push to matching branches** — leave empty so **no
   one** can push directly.  The intent of this phase's branching strategy
   is that every change goes through a PR.

7. **Rules that apply to administrators** — check *"Do not allow bypassing
   the above settings"*.  An admin should not be able to click through
   protection during an incident; the correct response is a fast-follow
   PR.

## Steps via the GitHub REST API

If you prefer scripting, this is the equivalent single call.  Replace the
placeholders and either export `GITHUB_TOKEN` as a repo-admin PAT or run
via `gh api`.

```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/blazebrt/GlamGenius/branches/main/protection \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=Backend unit + integration' \
  -f 'required_status_checks[contexts][]=Alembic round-trip' \
  -f 'required_status_checks[contexts][]=Frontend Jest + TypeScript + lint' \
  -f 'required_status_checks[contexts][]=Expo production web export' \
  -f 'required_status_checks[contexts][]=Authorization + privacy regression' \
  -f 'required_status_checks[contexts][]=Secret scan' \
  -f 'required_status_checks[contexts][]=Python dependency audit' \
  -f 'required_status_checks[contexts][]=Node dependency audit' \
  -f 'enforce_admins=true' \
  -f 'required_pull_request_reviews[required_approving_review_count]=1' \
  -f 'required_pull_request_reviews[dismiss_stale_reviews]=true' \
  -f 'required_pull_request_reviews[require_code_owner_reviews]=true' \
  -f 'restrictions=null' \
  -f 'allow_force_pushes=false' \
  -f 'allow_deletions=false' \
  -f 'required_linear_history=true' \
  -f 'required_conversation_resolution=true'
```

## Verification

After configuring:

- Open a throwaway PR against `main` and confirm every required check
  appears in the merge box.
- Confirm that a review from someone other than the author is required.
- Confirm that a PR touching `backend/migrations/**` cannot merge without
  the repository owner approving.
- Confirm that `git push --force origin main` is refused, even for an
  admin.

## Status

This step is marked **OWNER ACTION REQUIRED** in the stabilisation report
until the owner completes the steps above and updates
`STABILISATION_REPORT.md` with the date the protection was activated.
