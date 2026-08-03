# CI self-test — proving that the merge gate actually blocks

The CI workflow (`.github/workflows/ci.yml`) has no `continue-on-error`
on any required check. That is the intent. This document is the manual
proof procedure the owner runs once, after branch protection is
enabled, so that "CI is strict" is not a claim on paper.

The proof must happen at least once per year, and again after any PR
that changes the workflow file structurally (adding or removing a job,
adding or removing a step whose failure could bypass the gate).

## When to run it

Run the throwaway-PR procedure below:

1. Immediately after the owner enables branch protection with the
   status-check list from
   [`../stabilisation/BRANCH_PROTECTION_SETUP.md`](../stabilisation/BRANCH_PROTECTION_SETUP.md).
2. After any PR that edits `.github/workflows/ci.yml`.
3. Once a year, unprompted, as a scheduled compliance check.

Record the run in
[`../../STABILISATION_REPORT.md`](../../STABILISATION_REPORT.md) with
the date, the PR URL, and the CI run URL that failed.

## What "proved" means

At the end of the procedure, the following must all be true:

- The throwaway PR has at least one **failed** required check on its
  head commit.
- GitHub's merge box shows the PR as **not mergeable** (either the
  "Merge pull request" button is disabled, or a red "Required statuses
  must pass before merging" line is shown).
- Attempting to force-merge as an administrator is refused
  (`enforce_admins=true`).
- Attempting to bypass with `gh pr merge --admin` is refused.
- Closing the throwaway PR without merging returns the branch to a
  clean state.

## Procedure — throwaway PR

The safest form of proof is a throwaway PR that fails one check
deliberately. This is preferred over asserting the property by
reading the config.

### Step 1 — branch

```bash
git checkout main
git pull --ff-only origin main
git checkout -b chore/ci-self-test-YYYYMMDD
```

### Step 2 — introduce a deterministic failure

Pick **one** of the following. Do not use more than one; the point is
to prove the specific gate.

**Frontend Jest gate**

Add a failing Jest test at `frontend/src/__tests__/self_test.test.ts`:

```ts
test('CI self-test: this test must fail so we can prove the gate blocks', () => {
  expect(1).toBe(2);
});
```

Expected: the `Frontend Jest + TypeScript + lint` check fails and blocks
merge.

**Backend pytest gate**

Add a failing pytest at `backend/tests/test_self_test.py`:

```python
def test_ci_self_test_must_fail():
    assert 1 == 2, "CI self-test — deliberate failure to prove merge gate."
```

Expected: the `Backend unit + integration` check fails and blocks merge.

**Lint gate (zero-warning policy)**

Introduce one deliberate ESLint warning in an untouched file (e.g. a
top-level unused variable). Do not fix it.

Expected: the `Frontend Jest + TypeScript + lint` check fails (because
`--max-warnings=0` promotes warnings to errors) and blocks merge.

**pip-audit gate**

Temporarily add a known-vulnerable pin in
`backend/requirements.txt` (e.g. an old `urllib3` version with a
published HIGH advisory).

Expected: the `Python dependency audit` check fails and blocks merge.

**Secret-scan gate**

Add a plausible-looking dummy token to a file (e.g. a fake `sk_test_…`
in an unrelated markdown file).

Expected: the `Secret scan` check fails and blocks merge.

### Step 3 — push and open PR

```bash
git add -A
git commit -m "chore(ci): self-test — deliberate failure, DO NOT MERGE"
git push -u origin chore/ci-self-test-YYYYMMDD
gh pr create --draft --title "chore(ci): self-test — deliberate failure, DO NOT MERGE" \
             --body "Do not merge. See docs/engineering/CI_SELF_TEST.md."
```

### Step 4 — observe

- CI runs.
- The intended job fails.
- GitHub's merge box shows the PR as blocked.
- Attempt to merge with the UI: refused.
- Attempt to merge as admin (`gh pr merge --admin` if the CLI is
  configured): refused if `enforce_admins=true` and
  `required_status_checks` includes the failing check.

### Step 5 — record

Update `STABILISATION_REPORT.md` §"CI self-test evidence" with:

- The PR URL.
- The CI run URL that failed.
- The date.
- The name of the check that blocked.
- Confirmation that `gh pr merge --admin` was refused (screenshot or
  paste).

### Step 6 — close and delete

Close the PR without merging. Delete the branch (`gh pr close
--delete-branch <PR_NUMBER>` or via the GitHub UI). Do not squash-merge
"just to clean up"; the whole point is that the branch never merged.

## Service image pinning — Work Package 1 stance

The CI workflow uses minor-version image tags for the service
containers (`postgres:16-alpine`, `mongo:6`) rather than sha256 digests.

Rationale, temporary:

- The primary risk of a moving tag is silently upgrading a runtime the
  test suite trusts. `postgres:16` moves within Postgres 16 patches
  (16.x); it does not move to 17. `mongo:6` moves within Mongo 6.
  Behaviour-breaking upgrades within a patch line are historically
  rare for the query shapes the app uses.
- The primary risk of a pinned digest is a review overhead that
  falls out of sync with security patches. During Work Package 1
  there is no reproducible-Docker workflow yet (that is Work Package
  2 / Fix 4), so a pinned digest would drift without a corresponding
  test that would catch drift.

Work Package 2 replaces the minor tags with sha256 digests, updated
by a reproducible script (`scripts/update_service_digests.sh`),
verified against the Docker container-image manifests. The rationale
above is deleted from this document when Work Package 2 lands.

## References

- [`REVIEW_POLICY.md`](REVIEW_POLICY.md)
- [`BRANCHING_STRATEGY.md`](BRANCHING_STRATEGY.md)
- [`CHECKLIST_EVIDENCE.md`](CHECKLIST_EVIDENCE.md)
- [`../stabilisation/BRANCH_PROTECTION_SETUP.md`](../stabilisation/BRANCH_PROTECTION_SETUP.md)
