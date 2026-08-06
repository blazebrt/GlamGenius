#!/usr/bin/env bash
# protect_main_branch.sh
# Requires GITHUB_TOKEN environment variable with repo admin access.

set -euo pipefail

REPO="blazebrt/GlamGenius"
BRANCH="main"

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "Error: GITHUB_TOKEN is not set."
  echo "Usage: GITHUB_TOKEN=your_token ./scripts/protect_main_branch.sh"
  exit 1
fi

echo "Configuring branch protection for $REPO:$BRANCH..."

curl -L \
  -X PUT \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/$REPO/branches/$BRANCH/protection \
  -d '{
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

echo -e "\n\nBranch protection successfully applied to $BRANCH."
