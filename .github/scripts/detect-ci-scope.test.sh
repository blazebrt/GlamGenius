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

# The negative case: scoping must stay narrow. Documentation does not rebuild
# or rescan the image.
expect_scope "docs/OPERATIONS.md" backend false
expect_scope "docs/OPERATIONS.md" container false

if ((failures > 0)); then
  printf '\n%d scope assertion(s) failed.\n' "$failures"
  exit 1
fi

printf '\nAll scope assertions passed.\n'
