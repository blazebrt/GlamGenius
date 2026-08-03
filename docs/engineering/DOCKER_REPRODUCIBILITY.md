# Docker reproducibility runbook

Fix 4 (Work Package 2) — everything an operator needs to prove the
GlamGenius stack builds and boots the same way today as it did
yesterday, from a machine with **only Git and Docker installed**.

## 1. Constraints the fix delivers on

- The host needs only Git and Docker (+ the Docker Compose v2
  plugin). No Python, no Node, no Yarn, no MinIO, no local database.
- Python and Node versions used at runtime are pinned in the
  Dockerfiles (`python:3.11.10-slim`, `node:20.18.1-alpine`).
- Service-image tags are pinned to a specific minor version
  (`mongo:6.0.19`, `postgres:16.6-alpine`,
  `minio/minio:RELEASE.2024-11-07T00-52-20Z`). Adding the `sha256:`
  digest to each `image:` line is an **owner action on a live
  Docker host** — see §4. This branch ships the infrastructure
  (the `update_service_digests.sh` refresher, the `verify_clean_environment.sh`
  runner, the compose files that host the digest) without
  guessing at digests the agent environment cannot verify.
- The backend image runs as uid `10001`, not root. Compilers are
  present only in the builder stage and never in the final layer.
- Health checks are declared for every service the compose files
  start (`mongo`, `postgres`, `backend`, `minio`); `depends_on`
  reads `service_healthy` so nothing races to accept traffic
  before its dependencies are alive.
- The test stack uses `tmpfs` for both databases so a run leaves
  nothing on disk. Two consecutive `up && down -v` cycles observe
  the same bytes.
- MinIO is started alongside the databases so the S3 integration
  test at `backend/tests/test_media_s3.py` runs in CI without a
  paid provider.

## 2. The exact commands

Development stack (production-shaped, persistent volumes):

```bash
docker compose build --no-cache
docker compose up -d
docker compose ps
```

Test stack (throwaway):

```bash
docker compose -f docker-compose.test.yml build --no-cache
docker compose -f docker-compose.test.yml run --rm backend-tests
docker compose -f docker-compose.test.yml run --rm frontend-tests
docker compose -f docker-compose.test.yml down -v
```

Both the backend and the frontend job in the test stack are
self-contained: `backend-tests` runs Alembic + `pytest -q tests`
(including the MinIO-backed S3 integration when
`S3_INTEGRATION_ENABLED=true`, which is set inside the compose
file); `frontend-tests` runs `yarn install --frozen-lockfile`,
`yarn typecheck`, `yarn lint --max-warnings=0`, and
`yarn test --ci --watchAll=false`.

## 3. Automated verification

`scripts/verify_clean_environment.sh` performs the eight steps above
**twice**, in sequence, and reports pass/fail per step. Logs land
in `/tmp/glamgenius-docker-verify-<utc-timestamp>/`.

```bash
scripts/verify_clean_environment.sh
```

Exit status:

- `0` — every step succeeded on both cycles.
- `1` — one or more steps failed; the log directory holds the
  captured output.

The script deliberately does not require `python`, `node`, `yarn`,
`npm`, or `pytest` on the host. If they happen to be installed the
script notes that but does not use them.

## 4. Refreshing service-image digests

Digests are bit-for-bit immutable; the human-facing tag alongside
them (`postgres:16.6-alpine@sha256:...`) is a comment for reviewers.
When a security update lands upstream, the digest changes and we
have to update both compose files.

The refresh flow is deliberately two-step so the digest is inspected
by a human before landing:

```bash
scripts/update_service_digests.sh
```

The script pulls each image tag, prints the resolved
`<tag>@sha256:<digest>` string, and stops. The reviewer edits the
digest in both compose files (`docker-compose.yml` and
`docker-compose.test.yml`), then runs the verification script
before pushing the bump.

Rationale for not letting the script edit the compose files:
`update_service_digests.sh` can be run by anyone, but a merge to
`main` still needs an independent review of the digest bump. Keeping
the sed/edit step as a manual gate stops "the script updated 4
digests silently" from turning into "we shipped an untested image".

## 5. What "reproducible" does not mean

- **It does not mean bit-for-bit-identical Python or Node
  packages.** The `yarn.lock` and `requirements.txt` pin exact
  versions, but the resolved dependency tree can shift when a
  registry serves a new metadata revision. That is why the CI
  workflow runs `--frozen-lockfile` and `--strict` on every job:
  drift is caught in the same run that would ship it.
- **It does not mean the backend image itself is bit-for-bit
  reproducible.** `python:3.11.10-slim` moves under its own tag
  when Debian security patches land; that is the whole point of
  using it. The digest we pin is the digest at the time of the
  compose commit; a subsequent rebuild without a digest change
  pulls the same bytes, which is what we test.

## 6. Ownership

- `backend/Dockerfile` — backend runtime image.
- `frontend/Dockerfile.test` — frontend test runner image.
- `docker-compose.yml` — production-shaped dev/preview stack.
- `docker-compose.test.yml` — CI-parity test stack (Mongo +
  Postgres + MinIO + backend-tests + frontend-tests).
- `scripts/verify_clean_environment.sh` — automated verifier.
- `scripts/update_service_digests.sh` — digest refresher.

All of the above are covered by `docs/engineering/**` in
`CODEOWNERS`; a bump to any of them requires the repository owner
to sign off on the PR (see
[`docs/engineering/REVIEW_POLICY.md §2`](../engineering/REVIEW_POLICY.md#2-required-approvals)).

## 7. Payment mechanics

Nothing in the reproducibility fix touches payment mechanics.

- `docker-compose.yml` and `docker-compose.test.yml` do not
  configure a Razorpay key or a webhook secret.
- `backend/Dockerfile` and `frontend/Dockerfile.test` do not bake
  in any payment-related credential.
- `scripts/verify_clean_environment.sh` and
  `scripts/update_service_digests.sh` do not touch billing files.
- `SUBSCRIPTIONS_AVAILABLE=false` remains, and no billing schema
  migration is introduced.

## 8. Owner action

Two items on this branch require the operator's Docker host — they
cannot be performed inside the coding-agent pod:

1. **Resolve sha256 digests and paste them into the compose files.**
   Run `scripts/update_service_digests.sh`, then edit
   `docker-compose.yml` and `docker-compose.test.yml` so each
   `image:` line reads `<tag>@sha256:<digest>` with the value the
   script printed. Commit that as a separate follow-up PR.
2. **Run `scripts/verify_clean_environment.sh` once on a clean host
   and paste the exit status + last log line into the WP2 PR
   description.** The stabilisation brief calls this out as the
   acceptance evidence for Fix 4. Two consecutive clean cycles
   must pass.

Cross-reference:
[`docs/engineering/CHECKLIST_EVIDENCE.md §6`](CHECKLIST_EVIDENCE.md#6-external-evidence).
