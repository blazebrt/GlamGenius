#!/usr/bin/env bash
#
# Regression test for the container scope of detect-ci-scope.sh.
#
# The bug this pins: a pull request that changed only `.trivy-exceptions.yaml`
# -- the registry deciding which CVEs the container scan may ignore -- did not
# set `container`, so the image was never built and never scanned, and the PR
# reported green. The scan first ran after merge. Governance changes must be
# qualified by the gate they govern, so every input to container qualification
# now sets `container`, and this test says so out loud.
#
# Deliberately small: it asserts the container flag on the paths that matter
# and one negative case. It is not a general test of every scope output.
#
#     bash .github/scripts/detect-ci-scope.test.sh

set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
subject="$script_dir/detect-ci-scope.sh"

failures=0

# Run the subject over one changed path and echo the value of `container`.
# GITHUB_OUTPUT is unset so the script prints key=value to stdout; the SHAs are
# placeholders the script requires but never reads once the test hook is set.
container_for() {
  local path="$1"
  env -u GITHUB_OUTPUT CI_SCOPE_CHANGED_FILES="$path" \
    bash "$subject" base head pull_request |
    sed -n 's/^container=//p'
}

expect() {
  local path="$1" want="$2" got
  got="$(container_for "$path")"
  if [[ "$got" == "$want" ]]; then
    printf 'ok    %-40s container=%s\n' "$path" "$got"
  else
    printf 'FAIL  %-40s container=%s (expected %s)\n' "$path" "${got:-<none>}" "$want"
    failures=$((failures + 1))
  fi
}

# The image's own contents.
expect "backend/Dockerfile" true

# The container security qualification logic itself. A change to any of these
# alters what the scan will accept, so the scan has to run against it.
expect ".trivy-exceptions.yaml" true
expect ".trivyignore" true
expect "scripts/validate_trivy_exceptions.py" true
expect ".github/scripts/detect-ci-scope.sh" true
expect ".github/workflows/ci.yml" true

# The negative case: scoping must stay narrow. Documentation does not rebuild
# or rescan the image.
expect "docs/OPERATIONS.md" false

if ((failures > 0)); then
  printf '\n%d container-scope assertion(s) failed.\n' "$failures"
  exit 1
fi

printf '\nAll container-scope assertions passed.\n'
