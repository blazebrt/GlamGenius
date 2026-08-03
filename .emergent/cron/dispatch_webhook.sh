#!/bin/sh
# Pod-local webhook-cron dispatcher: one crontab line per enabled cron, run by
# crond inside the preview/env pod. The full endpoint URL is substituted at
# render time; this fires a single request with the .env secret and exits 0.
#
# Redirect handling (Work Package 1 hardening):
#   The baseline used `curl --location-trusted --max-redirs 2`, which forwards
#   the Authorization header on redirect even to an unrelated host. That is a
#   Bearer-leak waiting for a mis-configured ingress.
#
#   This script now performs redirects manually with an allow-list. The first
#   request is issued with --max-redirs 0. If the response is a 3xx, the
#   Location header is parsed and the target host suffix is compared to
#   WEBHOOK_ALLOWED_REDIRECT_SUFFIXES. Same-platform redirects
#   (e.g. `preview.<host>` -> `internal.<host>`) still work; cross-host
#   redirects are refused and the Bearer is never sent to them.
#
#   The dispatcher fails closed — refusing a redirect logs a message and
#   exits 0. It never retries against an unapproved host.
#
# See docs/stabilisation/EMERGENT_HOSTING_AUDIT.md for the full rationale.
set -eu

: "${CRON_NAME:?}" "${METHOD:?}" "${ENDPOINT_URL_B64:?}"
JOB_ID="${JOB_ID:-}"
WEBHOOK_ENV_FILE="${WEBHOOK_ENV_FILE:-/app/backend/.env}"
AT_DATE="${AT_DATE:-}"
END_DATE="${END_DATE:-}"

# Hostname suffixes we are willing to follow a redirect to WITH the Bearer.
# Default list covers same-platform (Emergent preview + internal) hosts;
# override with WEBHOOK_ALLOWED_REDIRECT_SUFFIXES to extend or replace.
WEBHOOK_ALLOWED_REDIRECT_SUFFIXES="${WEBHOOK_ALLOWED_REDIRECT_SUFFIXES:-emergentagent.com preview.emergentagent.com internal.preview.emergentagent.com ea.int.apis.emergentagent.com}"

# AT_DATE (one-time trigger): crond can't express the year, so the "M H D Mo *"
# line re-fires this minute every year. Fire only when the current UTC minute
# (first 16 chars of RFC3339) matches AT_DATE's minute.
if [ -n "$AT_DATE" ]; then
        now_min="$(date -u +%Y-%m-%dT%H:%M)"
        at_min="$(printf '%s' "$AT_DATE" | cut -c1-16)"
        [ "$now_min" = "$at_min" ] || exit 0
fi

# END_DATE (recurring cutoff): both sides use the same fixed %Y-%m-%dT%H:%M:%SZ
# layout, so comparing their digit-only forms numerically preserves chronological
# order. Stop firing once now is strictly past END_DATE.
if [ -n "$END_DATE" ]; then
        now_num="$(date -u +%Y%m%d%H%M%S)"
        end_num="$(printf '%s' "$END_DATE" | tr -cd '0-9')"
        [ "$now_num" -le "$end_num" ] || exit 0
fi

ENDPOINT="$(printf '%s' "$ENDPOINT_URL_B64" | base64 -d)"

strip_quotes() {
        # Strip a single matching pair of surrounding quotes.
        v="$1"
        case "$v" in
                \"*\") v="${v#\"}"; v="${v%\"}" ;;
                \'*\') v="${v#\'}"; v="${v%\'}" ;;
        esac
        printf '%s' "$v"
}

# Read the per-app secret from the dotenv at dispatch time (never from cron env).
read_secret() {
        [ -f "$WEBHOOK_ENV_FILE" ] || return 0
        line="$(grep -E '^WEBHOOK_CRON_SECRET=' "$WEBHOOK_ENV_FILE" | tail -n 1 || true)"
        value="$(strip_quotes "${line#WEBHOOK_CRON_SECRET=}")"
        printf '%s' "$value"
}
WEBHOOK_CRON_SECRET="$(read_secret)"

# Extract the hostname (without port) from a URL. Robust enough for the
# Location headers curl emits: scheme://host[:port]/path.
url_host() {
        printf '%s' "$1" | awk '{
                sub(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//, "", $0);
                # strip everything from the first / or ? onwards
                sub(/[\/\?#].*$/, "", $0);
                # strip userinfo
                sub(/^.*@/, "", $0);
                # strip port
                sub(/:.*$/, "", $0);
                print tolower($0);
        }'
}

# Is $1 a hostname whose suffix appears in the allow-list ($WEBHOOK_ALLOWED_REDIRECT_SUFFIXES)?
# The allow-list is a whitespace-separated list of suffixes; a suffix matches
# when it equals the host or the host ends with `.` + suffix.
host_allowed() {
        _host="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
        [ -n "$_host" ] || return 1
        for _sfx in $WEBHOOK_ALLOWED_REDIRECT_SUFFIXES; do
                _sfx_lc="$(printf '%s' "$_sfx" | tr '[:upper:]' '[:lower:]')"
                [ -n "$_sfx_lc" ] || continue
                if [ "$_host" = "$_sfx_lc" ]; then
                        return 0
                fi
                # `.suffix` match
                case "$_host" in
                        *.$_sfx_lc) return 0 ;;
                esac
        done
        return 1
}

# RUN_ID is the idempotency key: cron name + fire time (minute granularity
# matches the schedule floor).
RUN_ID="${CRON_NAME}-$(date -u +%Y%m%dT%H%M)"
DISPATCH_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ENVELOPE="{\"event\":\"schedule.triggered\",\"schedule_id\":\"$CRON_NAME\",\"run_id\":\"$RUN_ID\",\"dispatch_time\":\"$DISPATCH_TIME\",\"job_id\":\"$JOB_ID\",\"data\":null}"

# --- Step 1: request WITHOUT following redirects. --------------------------
# We keep --location-trusted OFF so the Bearer never leaks on an untrusted
# 3xx. Capture headers in a temp file so we can extract Location manually.
HEADERS_FILE="$(mktemp -t webhook-cron-headers.XXXXXX 2>/dev/null || printf '/tmp/webhook-cron-headers.%s' "$$")"
trap 'rm -f "$HEADERS_FILE"' EXIT

HTTP_STATUS="$(curl -sS -o /dev/null -D "$HEADERS_FILE" -w '%{http_code}' \
        --max-time 10 \
        --max-redirs 0 \
        -X "$METHOD" \
        -H "Authorization: Bearer $WEBHOOK_CRON_SECRET" \
        -H "Content-Type: application/json" \
        -H "X-Webhook-Id: $RUN_ID" \
        -H "X-Webhook-Timestamp: $DISPATCH_TIME" \
        -d "$ENVELOPE" \
        "$ENDPOINT" 2>/dev/null || true)"

# --- Step 2: if the server 3xx-redirects, follow it only when allow-listed.
case "${HTTP_STATUS:-000}" in
        30[1-9])
                LOCATION="$(awk 'BEGIN{IGNORECASE=1} /^[Ll]ocation:/ {sub(/^[^:]*:[[:space:]]*/,"",$0); sub(/\r$/,"",$0); print; exit}' "$HEADERS_FILE" 2>/dev/null || true)"
                if [ -z "$LOCATION" ]; then
                        echo "dispatch complete (cron=$CRON_NAME http=$HTTP_STATUS redirect=missing-location)"
                        exit 0
                fi
                # Resolve relative Location against the original endpoint.
                case "$LOCATION" in
                        http://*|https://*) NEXT_URL="$LOCATION" ;;
                        /*)
                                # scheme + host of ENDPOINT, then Location as path.
                                _prefix="$(printf '%s' "$ENDPOINT" | awk '{
                                        match($0, /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\/]+/);
                                        print substr($0, RSTART, RLENGTH);
                                }')"
                                NEXT_URL="${_prefix}${LOCATION}"
                                ;;
                        *) NEXT_URL="$LOCATION" ;;
                esac
                NEXT_HOST="$(url_host "$NEXT_URL")"
                if host_allowed "$NEXT_HOST"; then
                        HTTP_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' \
                                --max-time 10 \
                                --max-redirs 0 \
                                -X "$METHOD" \
                                -H "Authorization: Bearer $WEBHOOK_CRON_SECRET" \
                                -H "Content-Type: application/json" \
                                -H "X-Webhook-Id: $RUN_ID" \
                                -H "X-Webhook-Timestamp: $DISPATCH_TIME" \
                                -d "$ENVELOPE" \
                                "$NEXT_URL" 2>/dev/null || true)"
                        echo "dispatch complete (cron=$CRON_NAME http=${HTTP_STATUS:-000} followed=$NEXT_HOST)"
                        exit 0
                else
                        # Fail closed. Do NOT resend the Bearer to an unapproved host.
                        echo "dispatch refused: redirect to unapproved host (cron=$CRON_NAME host=$NEXT_HOST)"
                        exit 0
                fi
                ;;
        *)
                echo "dispatch complete (cron=$CRON_NAME http=${HTTP_STATUS:-000})"
                exit 0
                ;;
esac
