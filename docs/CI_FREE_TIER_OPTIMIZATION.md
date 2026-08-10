# CI-01 — Free-tier CI optimization

The CI workflow keeps the same blocking commands and immutable action pins,
but only starts jobs whose inputs can be affected by a pull request. A small
`scope` job computes the path map from the PR merge base. The three stable
required checks (`PR backend gate`, `PR frontend gate`, and `PR policy gate`)
always complete on pull requests, including docs-only changes; they either
enforce the relevant implementation jobs or report a successful not-applicable
result.

## Trigger policy

| Change | Blocking PR work | Intentionally deferred |
| --- | --- | --- |
| Backend/application or migration | Backend Alembic upgrade/check, seed, full pytest, invite-required suite, and Ruff | Focused auth/health/account/critical reruns (the canonical suite already covers these) |
| Frontend source | Frozen Yarn install, TypeScript, zero-warning lint, Jest | Android/Expo smoke exports unless app/native/config or lockfile paths changed |
| Migration/schema | Backend gate plus Alembic round-trip | — |
| Python requirements | Backend gate, pip-audit, Docker/Trivy/SBOM | — |
| Node dependency files | Frontend gate, npm audit, Android/Expo exports | — |
| Docker/runtime files | Docker build, Trivy, and SBOM | — |
| Docs only | Secret scan, legacy/policy checks, and the three umbrellas | All dependency, database, container, mobile, and application jobs |
| Workflow/security policy | Secret/legacy/policy checks plus relevant release/security validation | — |
| Push to `main`, weekly schedule, or manual dispatch | The complete existing 18-job production qualification matrix | — |

`ci.yml` no longer runs a second heavy matrix on feature-branch pushes; pull
requests are the PR trigger and `main` is the production trigger. A weekly
scheduled run keeps dependency and container audits fresh. Concurrency still
cancels stale PR runs and never cancels a `main` run.

## Cost model

The baseline started all 18 jobs for every PR. The optimized active-run counts
are:

| Scenario | Active jobs | Notes |
| --- | ---: | --- |
| Backend Care/application change | 7 | One Python install and one Postgres service; Ruff is a named step in the canonical backend job |
| Frontend source-only change | 7 | One frozen Yarn install; mobile/web exports are scoped to native/config/lockfile changes |
| Migration/schema change | 8 | Adds the full Alembic round-trip |
| Python requirements | 11 | Adds dependency audit and container security chain |
| Node dependency files | 11 | Adds npm audit and mobile/web exports |
| Dockerfile/runtime image | 9 | Docker build, Trivy, and both SBOM jobs |
| Docs-only change | 6 | Scope, secret, legacy, and three stable umbrellas |
| Workflow/security policy | 9 | Policy checks plus release/auth-health validation |
| `main` push | 19 | Scope marker plus the unchanged 18-job qualification matrix |

Before optimization, a backend PR could start seven separate Postgres
services and eight Python dependency installations (backend tests, lint,
release, round-trip, and four focused suites). A backend PR now starts one of
each. A source-only frontend PR falls from three frozen Yarn installs to one.
Failure diagnostics remain visible: pytest, audit, Trivy, and SBOM artifacts
are uploaded only by the jobs that run (and reports remain available on
failure or as the downstream security input requires).

No application source, dependency, schema, migration, frontend, Android,
Expo, or security policy was weakened. No paid runner, self-hosted runner, or
mutable action reference was introduced.

The container scope is intentionally limited to the Dockerfile, image build
inputs, and container policy files. Application-source changes are validated
by the canonical backend suite; image build and vulnerability qualification
remain mandatory on `main` and whenever an image input changes.
