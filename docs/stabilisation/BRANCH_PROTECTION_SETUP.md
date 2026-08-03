# Branch protection setup — OWNER ACTION REQUIRED

The GlamGenius stabilisation phase adds a reproducible CI workflow, a
`CODEOWNERS` file and a pull request template. Enforcement of those
files lives in GitHub's branch-protection UI, which cannot be
configured by a coding agent — a repository administrator has to do it
once.

This document is the exact checklist. Nothing in this phase claims that
branch protection was configured; it lists what to click.

## Recommended target

Protect `main` such that:

1. Every merge is via pull request.
2. Every pull request receives an independent human review from
   someone other than the author (see
   [`../engineering/REVIEW_POLICY.md`](../engineering/REVIEW_POLICY.md)).
3. Every required check in the CI workflow is green.
4. `CODEOWNERS` review is required for anything the file marks.
5. Force-push is impossible.
6. Direct commit to `main` is impossible.

## Steps in the GitHub UI (as of Jan 2026)

1. Repository → **Settings** → **Branches**.
2. **Branch protection rules** → **Add rule** (or edit the existing
   rule for `main`).
3. **Branch name pattern:** `main`.
4. Under **Protect matching branches**, enable the following. Every
   one of them is required.

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

5. **Required status checks.** Type the following one at a time into
   the status-check search box. Each one has to have run at least
   once against `main` before it appears in the picker; the first PR
   after this phase merges will cause every check to appear.

   - `Backend unit + integration`
   - `Alembic round-trip`
   - `Frontend Jest + TypeScript + lint`
   - `Expo web export (bundle smoke test)`
   - `Authorization + privacy regression`
   - `Secret scan`
   - `Python dependency audit`
   - `Node dependency audit`

6. **Restrict who can push to matching branches** — leave empty so
   **no one** can push directly. The intent of this phase's branching
   strategy is that every change goes through a PR.

7. **Rules that apply to administrators** — check *"Do not allow
   bypassing the above settings"*. An admin should not be able to
   click through protection during an incident; the correct response
   is a fast-follow PR.

## Steps via the GitHub REST API

Boolean, integer, array, and null fields are not valid `-f`
arguments for `gh api`; `-f` is string-only. The two supported forms
that actually work today are:

- `-F name=value` for typed scalars (`-F enforce_admins=true`,
  `-F required_approving_review_count=1`, `-F strict=true`); or
- `--input body.json` for the full request body, which is the only
  form that supports nested objects and arrays reliably.

The command below uses `--input`. Save the JSON to a file, replace
the placeholders, and run it once. Do not paste the tokens into your
shell history.

```bash
# Save this as branch-protection.json — do not commit it.
cat > branch-protection.json <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Backend unit + integration",
      "Alembic round-trip",
      "Frontend Jest + TypeScript + lint",
      "Expo web export (bundle smoke test)",
      "Authorization + privacy regression",
      "Secret scan",
      "Python dependency audit",
      "Node dependency audit"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/blazebrt/GlamGenius/branches/main/protection \
  --input branch-protection.json
```

To confirm the protection was written correctly, read it back:

```bash
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/blazebrt/GlamGenius/branches/main/protection | jq
```

Expected keys in the response include `enforce_admins.enabled: true`,
`required_status_checks.strict: true`, and the contexts array in the
exact order above.

**Do not** use the `-f 'required_status_checks[strict]=true'` shape
that previously appeared in this document. `gh` treats every `-f`
value as a string, so booleans and arrays land as the literal strings
`"true"` and `"[...]"` on the server, which either fails the request
or writes garbage that reads back correctly but does not enforce.

## Verification

After configuring:

1. Read the protection back with the `gh api` GET call above and
   confirm each key.
2. Follow the throwaway-PR procedure in
   [`../engineering/CI_SELF_TEST.md`](../engineering/CI_SELF_TEST.md).
   That procedure ends with:
   - a deliberately failing PR whose required check is red;
   - the merge box refuses the merge;
   - `gh pr merge --admin` is refused because `enforce_admins=true`.
3. Confirm that a PR touching `backend/migrations/**` cannot merge
   without the repository owner approving.
4. Confirm that `git push --force origin main` is refused, even for
   an admin.

Record the date the protection was activated and the throwaway-PR
verification in
[`../../STABILISATION_REPORT.md`](../../STABILISATION_REPORT.md).

## Status

This step is marked **OWNER ACTION REQUIRED** in the stabilisation
report until the owner completes the steps above and updates
`STABILISATION_REPORT.md` with the date the protection was activated
**and** the CI self-test evidence.
