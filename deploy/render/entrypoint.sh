#!/bin/sh
#
# Give every production process class its exact deployed commit, then get out
# of the way.
#
# Render sets RENDER_GIT_COMMIT in each service at runtime. Both background
# workers already record COMMIT_SHA as `service_version` on their heartbeat
# row, so copying one into the other means `system_worker_status` reports the
# exact commit a process is running rather than "unknown" — which is what
# makes the exact-commit deployment rule checkable after the fact instead of
# only at deploy time.
#
# It is deliberately a copy and not a hard-coded value. A SHA written into the
# image or into source would be a provenance claim nothing keeps honest.
#
# An explicitly provided COMMIT_SHA always wins, so a local or CI build that
# passes its own value is unaffected.
set -eu

COMMIT_SHA="unknown"
export COMMIT_SHA

# This script starts the process it was given and nothing else. It must never
# grow a branch that runs the Phase B operator, a migration, a seed or any
# other privileged step: production activation is a deliberate human event,
# not something a container start can trigger.
exec "$@"
