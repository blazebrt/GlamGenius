#!/usr/bin/env bash
# Fix 4 (WP2) — refresh the sha256 digests for service images used in the
# compose files.
#
# Container-image digests are how Docker guarantees a repeatable build: a
# minor-version tag can move under you (`postgres:16-alpine` is a rolling
# target), while `postgres:16-alpine@sha256:<digest>` is bit-for-bit
# immutable. When we bump a service image, that is a code change that goes
# through review like any other.
#
# What this script does:
#
#   1. Pull each image tag we currently use.
#   2. Ask Docker for the RepoDigest of the pulled image.
#   3. Print a set of sed commands the reviewer can apply — this script
#      does NOT edit compose files itself, so the reviewer stays in
#      control of when the digest lands.
#
# The images and their tags live in this file rather than in the compose
# files themselves; keeping the two lists in sync is a review-time task.
#
# Prerequisites:
#   * docker (to pull and inspect)
#
# Usage:
#   scripts/update_service_digests.sh

set -eu

declare -a IMAGES=(
    "mongo:6.0.19"
    "postgres:16.6-alpine"
    "minio/minio:RELEASE.2024-11-07T00-52-20Z"
)

echo "Pulling and inspecting service images..."
echo

for tag in "${IMAGES[@]}"; do
    echo "==> $tag"
    if ! docker pull "$tag" >/dev/null 2>&1; then
        echo "    docker pull FAILED — is docker running and this host online?"
        exit 1
    fi
    # RepoDigests looks like ["postgres@sha256:...", "postgres@sha256:..."].
    # jq is not a hard dependency of the repo, so we grep instead.
    digest="$(docker inspect --format '{{index .RepoDigests 0}}' "$tag" | awk -F@ '{print $2}')"
    if [ -z "$digest" ]; then
        echo "    could not resolve digest for $tag"
        exit 1
    fi
    echo "    $tag@${digest}"
    echo
done

echo "----"
echo "Manual step — reviewer:"
echo
echo "  For each image above, update the corresponding line in"
echo "  docker-compose.yml and docker-compose.test.yml so it reads"
echo "  '<tag>@<digest>' with the value printed above. The tag comment"
echo "  after the digest is guidance for humans and should also be"
echo "  refreshed to match the pulled tag."
echo
echo "  Then run:"
echo "    scripts/verify_clean_environment.sh"
echo "  and confirm both cycles pass before pushing the digest bump."
