#!/usr/bin/env bash
#
# Regression test for governance scopes in detect-ci-scope.sh.
#
# The bug this pins: a pull request that changed only `.trivy-exceptions.yaml`
# -- the registry deciding which CVEs the container scan may ignore -- did not
# set `container`, so the image was never built and never scanned, and the PR
# reported green. The scan first ran after merge. Governance changes must be
# qualified by the gate they govern, so every input to container qualification
# now sets `container`, and this test says so out loud.
#
# Deliberately small: it asserts the container flag on the paths that matter,
# backend self-qualification, and narrow positive and negative controls.
#
#     bash .github/scripts/detect-ci-scope.test.sh

set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
subject="$script_dir/detect-ci-scope.sh"

failures=0

# Run the subject over one changed path and echo the requested scope value.
# GITHUB_OUTPUT is unset so the script prints key=value to stdout; the SHAs are
# placeholders the script requires but never reads once the test hook is set.
scope_for() {
  local path="$1" key="$2"
  env -u GITHUB_OUTPUT CI_SCOPE_CHANGED_FILES="$path" \
    bash "$subject" base head pull_request |
    sed -n "s/^${key}=//p"
}

expect_scope() {
  local path="$1" key="$2" want="$3" got
  got="$(scope_for "$path" "$key")"
  if [[ "$got" == "$want" ]]; then
    printf 'ok    %-40s %s=%s\n' "$path" "$key" "$got"
  else
    printf 'FAIL  %-40s %s=%s (expected %s)\n' "$path" "$key" "${got:-<none>}" "$want"
    failures=$((failures + 1))
  fi
}

# The image's own contents.
expect_scope "backend/Dockerfile" container true

# The container security qualification logic itself. A change to any of these
# alters what the scan will accept, so the scan has to run against it.
expect_scope ".trivy-exceptions.yaml" container true
expect_scope ".trivyignore" container true
expect_scope "scripts/validate_trivy_exceptions.py" container true
expect_scope ".github/scripts/detect-ci-scope.sh" container true
expect_scope ".github/workflows/ci.yml" container true

# Changes to the canonical backend workflow or its scope authority must run the
# backend suite they govern. An ordinary backend file remains a positive control.
expect_scope ".github/scripts/detect-ci-scope.sh" backend true
expect_scope ".github/workflows/ci.yml" backend true
expect_scope "backend/app/config.py" backend true

# --- Production runtime deployment artefacts --------------------------------
#
# The second bug this file pins. Before the production runtime milestone,
# `render.yaml` matched no case at all, so a pull request that rewrote the
# entire production topology -- switched automatic deploys on, deleted the
# hourly notification job, pointed the readiness probe somewhere harmless,
# added a Render PostgreSQL beside the two the architecture already has --
# set every scope false and reported green with nothing run.
#
# Each of the four scopes below is asserted because each answers a different
# question about these files, and a single "run everything" flag would not
# survive review:
#
#   backend    the assertions live in a backend test suite
#   container  these files decide what the production image is and contains
#   security   this is where a credential would most plausibly slip in
#   release    the Blueprint names `python -m app.release` as its deploy gate
for scope in backend container security release; do
  expect_scope "render.yaml" "$scope" true
  expect_scope "deploy/render/Dockerfile" "$scope" true
  expect_scope "deploy/render/entrypoint.sh" "$scope" true
done

# The production runtime suite itself is an ordinary backend test, and must
# run when it changes.
expect_scope "backend/tests/test_production_runtime_foundation.py" backend true

# The workflow governs its own gates -- including, since the production runtime
# milestone, whether the image Render actually deploys gets built and scanned at
# all. A change to it must run every gate it governs.
for scope in backend container security release; do
  expect_scope ".github/workflows/ci.yml" "$scope" true
done

# What the production image contains. Not a release question: `.dockerignore`
# cannot change what `python -m app.release` does to a database.
expect_scope ".dockerignore" container true
expect_scope ".dockerignore" security true
expect_scope ".dockerignore" release false

# The public readiness endpoint is an explicit redaction boundary now, so it
# has to run the health-and-readiness job. That job is gated on `security`;
# `backend/*` alone would have run only the backend suite and let a change to
# the most exposed surface in the service skip its own gate.
expect_scope "backend/app/api/v2/config.py" security true
expect_scope "backend/app/api/v2/config.py" backend true

# The release entrypoint keeps its existing scopes and gains nothing it does
# not need: editing it cannot change what is inside the image.
expect_scope "backend/app/release.py" release true
expect_scope "backend/app/release.py" backend true
expect_scope "backend/app/release.py" container false

# The negative case: scoping must stay narrow. Documentation does not rebuild
# or rescan the image, and the deployment paths above must not have widened
# it into a run-everything switch.
expect_scope "docs/OPERATIONS.md" backend false
expect_scope "docs/OPERATIONS.md" container false
expect_scope "docs/OPERATIONS.md" release false
expect_scope "docs/OPERATIONS.md" security false
expect_scope "render.yaml" frontend false
expect_scope "render.yaml" schema false
expect_scope "render.yaml" mobile false
expect_scope "deploy/render/Dockerfile" schema false

# A deployment change can never produce an all-skipped green pull request.
# This is the property the scenarios above add up to, asserted directly so it
# cannot be lost by editing one of them.
for path in "render.yaml" "deploy/render/Dockerfile" "deploy/render/entrypoint.sh" ".dockerignore"; do
  any_true=false
  for scope in backend schema frontend mobile web python_deps node_deps container security release; do
    if [[ "$(scope_for "$path" "$scope")" == "true" ]]; then
      any_true=true
      break
    fi
  done
  if [[ "$any_true" == "true" ]]; then
    printf 'ok    %-40s qualifies something\n' "$path"
  else
    printf 'FAIL  %-40s produced an all-skipped green PR\n' "$path"
    failures=$((failures + 1))
  fi
done

if ((failures > 0)); then
  printf '\n%d scope assertion(s) failed.\n' "$failures"
  exit 1
fi

printf '\nAll scope assertions passed.\n'
