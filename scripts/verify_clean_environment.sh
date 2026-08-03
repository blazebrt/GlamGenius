#!/usr/bin/env bash
# Fix 4 (WP2) — verify the reproducible-Docker acceptance criteria.
#
# Runs the exact commands the stabilisation brief lists. Two consecutive
# clean runs are performed so a "works once because of a warm cache"
# failure mode is caught. All logs are preserved under
# /tmp/glamgenius-docker-verify-<timestamp>/ for post-mortem review.
#
# Prerequisites on the operator's machine:
#   * git   (any recent version)
#   * docker + docker compose plugin  (v2.20 or newer)
# NO Python. NO Node. NO Yarn. That is the point of the fix.
#
# Usage:
#   scripts/verify_clean_environment.sh
#
# Exit status:
#   0  every step succeeded (twice)
#   1  a step failed — see the log directory for details

set -u
set -o pipefail

# ---------------------------------------------------------------------------
# Preamble: sanity check the operator's environment.
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/tmp/glamgenius-docker-verify-${STAMP}"
mkdir -p "$LOG_DIR"

echo "[verify] repo root: $REPO_ROOT"
echo "[verify] log dir:   $LOG_DIR"
echo

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[verify] MISSING: $1" >&2
        echo "[verify] this script needs only git and docker; nothing else." >&2
        exit 1
    fi
}
require git
require docker

if ! docker compose version >/dev/null 2>&1; then
    echo "[verify] MISSING: docker compose plugin (v2)." >&2
    echo "[verify] install the Docker Compose plugin, then rerun." >&2
    exit 1
fi

# We deliberately DO NOT require python or node. If they are on the host it
# is fine, but nothing in the verification uses them.
for forbidden in python python3 node yarn npm; do
    if command -v "$forbidden" >/dev/null 2>&1; then
        echo "[verify] note: $forbidden is on \$PATH, but the verification does not use it."
    fi
done

# ---------------------------------------------------------------------------
# The one function that runs a compose command, logs it, and reports.
# ---------------------------------------------------------------------------
FAILURES=0

step() {
    local label="$1"; shift
    local logfile="${LOG_DIR}/${label}.log"
    echo "[verify] ==> ${label}"
    echo "[verify]     command: $*"
    if ( cd "$REPO_ROOT" && "$@" ) >"$logfile" 2>&1; then
        echo "[verify]     PASS  -> ${logfile}"
    else
        echo "[verify]     FAIL  -> ${logfile}"
        tail -n 30 "$logfile" | sed 's/^/[verify]         /'
        FAILURES=$((FAILURES + 1))
    fi
}

# ---------------------------------------------------------------------------
# Two consecutive clean cycles.
# ---------------------------------------------------------------------------
cycle() {
    local n="$1"
    echo
    echo "[verify] === Cycle ${n} of 2 ==="

    step "${n}-01-build-dev"        docker compose build --no-cache
    step "${n}-02-up-dev"           docker compose up -d
    step "${n}-03-ps-dev"           docker compose ps
    step "${n}-04-build-test"       docker compose -f docker-compose.test.yml build --no-cache
    step "${n}-05-backend-tests"    docker compose -f docker-compose.test.yml run --rm backend-tests
    step "${n}-06-frontend-tests"   docker compose -f docker-compose.test.yml run --rm frontend-tests
    step "${n}-07-down-test"        docker compose -f docker-compose.test.yml down -v
    step "${n}-08-down-dev"         docker compose down -v
}

cycle 1
cycle 2

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------
echo
if [ "$FAILURES" -eq 0 ]; then
    echo "[verify] SUCCESS — every step passed twice."
    echo "[verify] logs at ${LOG_DIR}"
    exit 0
else
    echo "[verify] FAILURE — ${FAILURES} step(s) failed."
    echo "[verify] logs at ${LOG_DIR}"
    echo "[verify] Do not paste this report claiming success; the last-run log directory is the source of truth."
    exit 1
fi
