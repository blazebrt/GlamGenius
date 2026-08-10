# CI-01 — Free-tier CI optimization

The workflow uses one deterministic `scope` job and one stable pull-request
check named `PR gate`. The gate performs lightweight repository policy checks
itself and enforces every implementation job that the scope requires.

## Event policy

| Event | Scope | Qualification |
| --- | --- | --- |
| Pull request | Merge-base diff of PR base/head | Relevant backend/frontend/container/security/release work only; `PR gate` is the single required check |
| Push to `main` | All groups true | Full existing 18-job production qualification |
| Manual dispatch | All groups true | Full existing 18-job production qualification |
| Weekly schedule | Python and Node dependency groups only | `pip-audit` and Node audit; no weekly application, database, mobile, release, Docker, or SBOM matrix |

Ordinary backend Care PRs run `CI scope`, the canonical backend job, and `PR
gate`. Ordinary Expo runtime PRs run `CI scope`, the canonical frontend job,
and `PR gate`; the frontend job performs one frozen Yarn install followed by
TypeScript, zero-warning lint, Jest, Android Metro export, and Expo web export.

## Path-aware PR behavior

- Backend/schema scope runs the blocking Alembic upgrade/check, optional
  schema round-trip, reference-data seed validation, Ruff, full pytest, and
  invite-required regressions in one Postgres-backed job.
- Python dependency scope adds the existing strict `pip-audit` policy to that
  same backend job. The dedicated audit job remains for `main`, manual, and
  scheduled qualification.
- Frontend runtime scope includes `frontend/app/**`, `frontend/src/**`, and
  other runtime directories; it runs both Android and web exports inside the
  canonical frontend job. Clearly marked test/docs-only frontend paths remain
  cheaper.
- Node dependency scope adds the existing Node audit validator to the same
  frontend job. The dedicated audit job remains for `main`, manual, and
  scheduled qualification.
- Container scope runs Docker build → Trivy → SBOM, with the PR gate requiring
  all three results explicitly.
- Security/release workflow scope runs the relevant authentication,
  health/readiness, and release jobs, and the PR gate enforces their results.
- Secret scanning, legacy/payment absence, and immutable-action policy checks
  execute inside `PR gate` on pull requests. Their dedicated production jobs
  remain on `main` and manual full qualification.

## Active-run model

| Scenario | Active jobs |
| --- | ---: |
| Backend Care PR | 3 |
| Frontend runtime PR | 3 |
| Migration/schema PR | 3 |
| Python dependency PR | 6 |
| Node dependency PR | 3 |
| Docker PR | 5 |
| Workflow/security PR | 5 |
| Docs-only PR | 2 |
| Weekly schedule | 3 |
| `main` push/manual dispatch | 19 (scope plus 18 qualification jobs) |

The baseline started all 18 jobs for every PR. A typical backend PR falls from
seven Postgres startups and eight Python installs to one of each. A frontend
runtime PR falls from three Yarn installs to one. Dependency/container PRs
retain the extra security chain only when those inputs can affect it.

Large diagnostics are failure-only with short retention where practical;
Docker image and release/security artifacts remain available when they are
downstream inputs or qualification outputs. Job-level timeouts remain in
place, PR concurrency cancels superseded runs, and `main` is never cancelled.

No application source, dependency, migration, table, column, enum, paid CI
service, or self-hosted runner was added or changed.

The recommended branch-protection check after this correction is exactly
`PR gate`.
