#!/usr/bin/env bash
set -euo pipefail

event_name="${3:-pull_request}"

emit() {
  local key="$1"
  local value="$2"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
  else
    printf '%s=%s\n' "$key" "$value"
  fi
}

if [[ "$event_name" == "schedule" ]]; then
  # Scheduled qualification is intentionally limited to dependency refreshes.
  for key in backend schema frontend mobile web container security release; do
    emit "$key" false
  done
  emit python_deps true
  emit node_deps true
  exit 0
fi

if [[ "$event_name" != "pull_request" ]]; then
  # Push-to-main and manual dispatch are full production qualification events.
  for key in backend schema frontend mobile web python_deps node_deps container security release; do
    emit "$key" true
  done
  exit 0
fi

base_sha="${1:?base SHA is required for pull_request}"
head_sha="${2:?head SHA is required for pull_request}"

if [[ -n "${CI_SCOPE_CHANGED_FILES:-}" ]]; then
  # Test hook used by the local scenario harness; CI always uses git diff.
  mapfile -t changed_files < <(printf '%s\n' "$CI_SCOPE_CHANGED_FILES")
else
  # Compare the PR head to its merge-base with the current base tip. This
  # excludes unrelated changes that landed on main after the feature branch.
  merge_base="$(git merge-base "$base_sha" "$head_sha")"
  mapfile -t changed_files < <(git diff --name-only "$merge_base" "$head_sha")
fi

backend=false
schema=false
frontend=false
mobile=false
web=false
python_deps=false
node_deps=false
container=false
security=false
release=false

for path in "${changed_files[@]}"; do
  case "$path" in
    backend/*|tests/*|backend/pyproject.toml|pyproject.toml)
      backend=true
      ;;
    .github/scripts/detect-ci-scope.sh|.github/workflows/ci.yml)
      backend=true
      ;;
  esac
  case "$path" in
    backend/migrations/*|backend/alembic.ini|backend/app/*/models.py|backend/app/shared/database/*)
      schema=true
      ;;
  esac
  case "$path" in
    frontend/*test*|frontend/*spec*|frontend/__tests__/*|frontend/tests/*|frontend/jest.setup.js|frontend/docs/*)
      frontend=true
      ;;
    frontend/app/*|frontend/src/*|frontend/components/*|frontend/hooks/*|frontend/lib/*|frontend/screens/*|frontend/*.tsx|frontend/*.ts|frontend/*.jsx|frontend/*.js|frontend/app.json|frontend/app.config.*|frontend/android/*|frontend/ios/*|frontend/package.json|frontend/yarn.lock)
      frontend=true
      mobile=true
      web=true
      ;;
    frontend/*)
      frontend=true
      ;;
    shared/*)
      backend=true
      frontend=true
      mobile=true
      web=true
      ;;
  esac
  case "$path" in
    backend/requirements*.txt|pyproject.toml|.python-version|security/pip-audit*|security/requirements*)
      python_deps=true
      ;;
  esac
  case "$path" in
    frontend/package.json|frontend/yarn.lock|security/node-audit*|security/*audit*)
      node_deps=true
      ;;
  esac
  # Anything that changes what the image contains, or that changes how the
  # container is qualified, must rebuild and rescan the image.
  #
  # The second half of that sentence is the part that was missing. A PR
  # editing only `.trivy-exceptions.yaml` -- the registry that decides which
  # CVEs the scan is allowed to ignore -- did not set `container`, so the
  # build and the Trivy scan were both skipped and the PR reported green.
  # The scan then ran for the first time after merge, during push-to-main
  # qualification, which is exactly the wrong moment to discover that an
  # exception was wrong. The same hole applied to the validator's own inputs
  # and to this script: a change to the qualification logic could ship
  # without the qualification ever running against it.
  case "$path" in
    backend/Dockerfile|Dockerfile|backend/requirements*.txt|docker-compose*.yml|docker-compose*.yaml)
      container=true
      ;;
    .trivyignore|.trivy-exceptions.yaml|scripts/validate_trivy_exceptions.py|.github/scripts/detect-ci-scope.sh|.github/workflows/ci.yml)
      container=true
      ;;
  esac
  case "$path" in
    security/node-audit*|security/*audit*|security/pip-audit*|security/requirements*)
      ;;
    backend/app/config.py|backend/app/auth/*|backend/app/shared/auth*|backend/tests/test_supabase*|backend/tests/test_jwks*|security/*|.github/*)
      security=true
      ;;
  esac
  case "$path" in
    backend/app/release.py|scripts/deploy*|.github/workflows/*)
      release=true
      ;;
  esac
done

for key in backend schema frontend mobile web python_deps node_deps container security release; do
  emit "$key" "${!key}"
done
