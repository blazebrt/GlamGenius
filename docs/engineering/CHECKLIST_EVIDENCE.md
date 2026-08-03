# Evidence checklist

Use this checklist on every PR, before requesting review. It is the
mechanical part of the "tests actually run" section of the pull request
template — the review policy delegates the details to this file.

The rule: **never report "passed" for something that did not run**. A
CI green tick is evidence. A copy-pasted test name is not.

## 1. Backend evidence

- [ ] `pytest -q tests` on the head commit — green. Paste the run
      URL from the `Backend unit + integration` CI job, or the local
      output if the PR is a hotfix in progress.
- [ ] `alembic upgrade head` on an empty database — green. The
      `backend-tests` and `alembic-round-trip` CI jobs cover this.
- [ ] `alembic check` — no drift.
- [ ] `pytest -q tests/test_privacy.py tests/test_media.py
      tests/test_v1_regression.py tests/test_config_flags_and_billing.py
      tests/test_critical_journey.py` — green (the
      `Authorization + privacy regression` CI job).

## 2. Frontend evidence

- [ ] `yarn typecheck` — green.
- [ ] `yarn lint --max-warnings=0` — green. Any warning is treated as
      a merge-blocker.
- [ ] `yarn test --ci --watchAll=false` — green. `--passWithNoTests`
      is **not** set; a zero-test run fails.
- [ ] `npx expo export --platform web` — green. The web build is the
      current bundle smoke test.

## 3. Dependency evidence

- [ ] `Python dependency audit` — green (no HIGH or CRITICAL
      advisory).
- [ ] `Node dependency audit` — green (yarn audit exit code below 8).
- [ ] The full JSON report (uploaded as an artifact by the two audit
      jobs) is attached to the PR when the reviewer wants to look at
      the lower-severity findings.

## 4. Secret evidence

- [ ] `Secret scan` (Gitleaks) — green.
- [ ] Manual grep of the diff for `sk_`, `pk_`, `ghp_`, `AKIA`,
      `AIzaSy`, `-----BEGIN`, `client_secret`, `api_key`.

## 5. Payment mechanics untouched

Every PR in Work Packages 1–6 must include the output of the following
commands. The reviewer runs them again on the head commit and confirms
the output matches.

```bash
# Payment application code
git diff main -- backend/app/domains/billing backend/app/api/v2/billing.py \
                 backend/routes/billing.py 2>/dev/null

# Payment migrations (0001–0008 are frozen; only new migrations are permitted)
git diff main -- backend/migrations

# Payment configuration
git diff main -- env.example docker-compose.yml docker-compose.test.yml \
                 backend/config.py 2>/dev/null | \
  grep -Ei 'razorpay|subscription|billing|webhook_secret|refund'
```

- [ ] The first command returned no output.
- [ ] The second command shows no edits to any file numbered
      `0001_` through `0008_` (only additions of higher-numbered
      files, if any).
- [ ] The third command returned no output. If it did, the reviewer
      inspected each hit and confirmed it is unrelated to payment
      mechanics (e.g. a copy correction outside the billing surface,
      approved by the review policy).
- [ ] `SUBSCRIPTIONS_AVAILABLE` remains `false` in every environment
      configuration touched by the PR.

## 6. External evidence

- [ ] If the PR runs a live provider call (Gemini live workflow, real
      weather / calendar / push endpoint), the corresponding
      `live-*` workflow was manually dispatched from `main` and the
      result URL is pasted.
- [ ] If the PR changes a Docker configuration, the container build
      was run and the log is attached (Work Package 2 will formalise
      the exact commands).
- [ ] If the PR changes a physical-device surface, at least one
      device was used or the PR records that no device was available
      (Work Package 6 owns the full sweep).

## 7. Reproducibility evidence

- [ ] The commit SHA of the head is pasted in the PR description
      (`git rev-parse HEAD`).
- [ ] The commit SHA that the CI run was against matches the head
      SHA.
- [ ] The PR description states the baseline commit
      (`89c57e5b1f786de3b631d90f29aa257109feb409` for the
      stabilisation phase) and confirms it is an ancestor of the
      head.
- [ ] `git status` on the head is clean (no uncommitted files).

## 8. What NOT to write

- Do **not** write "all tests pass" without a link or a paste.
- Do **not** copy a green tick from a previous run of a different
      commit.
- Do **not** describe a test as green when the CI job is still
      queued or was cancelled.
- Do **not** paste output from a local environment that has different
      dependencies from CI. If the difference matters, the PR fixes
      the dependency skew first.

## 9. Sign-off

- [ ] Author filled every relevant section of the PR template.
- [ ] Reviewer confirmed the evidence on the head commit before
      approving.
