#!/usr/bin/env bash
# protect_main_branch.sh
# Requires GITHUB_TOKEN environment variable with repo admin access.

set -euo pipefail

REPO="blazebrt/GlamGenius"
BRANCH="main"

VALIDATE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --validate)
      VALIDATE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "Error: GITHUB_TOKEN is not set."
  echo "Usage: GITHUB_TOKEN=your_token ./scripts/protect_main_branch.sh [--dry-run] [--validate]"
  exit 1
fi

PAYLOAD='{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Backend zero-warning lint",
      "Backend release command test",
      "Backend unit + integration",
      "Container vulnerability scan",
      "Legacy and payment absence"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_signatures": false
}'

if [ "$VALIDATE" -eq 1 ]; then
  echo "Validating branch protection for $REPO:$BRANCH..."
  RESPONSE=$(curl -s -L \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$REPO/branches/$BRANCH/protection")
  
  if echo "$RESPONSE" | grep -q '"message": "Not Found"'; then
    echo "FAIL: Branch protection is not enabled."
    exit 1
  fi
  echo "PASS: Branch protection is configured."
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN: Would apply the following branch protection to $REPO:$BRANCH:"
  echo "$PAYLOAD"
  exit 0
fi

echo "Configuring branch protection for $REPO:$BRANCH..."

curl -L \
  -X PUT \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/$REPO/branches/$BRANCH/protection" \
  -d "$PAYLOAD"

echo -e "\n\nBranch protection successfully applied to $BRANCH."
