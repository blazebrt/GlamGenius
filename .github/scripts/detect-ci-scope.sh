#!/usr/bin/env bash
set -euo pipefail

# Emit a path-aware CI scope for the pull-request workflow.  Non-PR events are
# treated as full qualification runs so pushes to main and manual dispatches
# cannot accidentally bypass production checks.
base_sha="${1:?base SHA is required}"
head_sha="${2:?head SHA is required}"
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

if [[ "$event_name" != "pull_request" ]]; then
  for key in backend schema frontend mobile web python_deps node_deps container security release; do
    emit "$key" true
  done
  exit 0
fi

if [[ -n "${CI_SCOPE_CHANGED_FILES:-}" ]]; then
  # Test hook used by the local scenario harness; CI always uses git diff.
  mapfile -t changed_files < <(printf '%s\n' "$CI_SCOPE_CHANGED_FILES")
else
  mapfile -t changed_files < <(git diff --name-only "$base_sha" "$head_sha")
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
  esac
  case "$path" in
    backend/migrations/*|backend/alembic.ini|backend/app/*/models.py|backend/app/shared/database/*)
      schema=true
      ;;
  esac
  case "$path" in
    frontend/*|shared/*|frontend/package.json|frontend/yarn.lock)
      frontend=true
      ;;
  esac
  case "$path" in
    frontend/app.json|frontend/app.config.*|frontend/android/*|frontend/ios/*|frontend/package.json|frontend/yarn.lock)
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
  case "$path" in
    backend/Dockerfile|Dockerfile|backend/requirements*.txt|docker-compose*.yml|docker-compose*.yaml|.trivyignore|scripts/validate_trivy_exceptions.py)
      container=true
      ;;
  esac
  case "$path" in
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
