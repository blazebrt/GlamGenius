#!/bin/sh
# Best-effort tests for the redirect allow-list in dispatch_webhook.sh.
# Run manually — the .emergent directory is a platform artefact and is not
# covered by the product CI.
#
# Usage:  sh .emergent/cron/tests/test_dispatch_allowlist.sh
# Exit:   0 on all-pass, non-zero on any failure.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DISPATCH="$SCRIPT_DIR/dispatch_webhook.sh"

if [ ! -f "$DISPATCH" ]; then
        echo "not found: $DISPATCH"
        exit 1
fi

# Source the dispatcher's helpers by extracting them into a temp shell we
# can exec. The functions we exercise are `url_host` and `host_allowed`,
# both of which are pure text transforms and safe to run in isolation.
HELPERS="$(mktemp -t dispatch-helpers.XXXXXX 2>/dev/null || printf '/tmp/dispatch-helpers.%s' "$$")"
trap 'rm -f "$HELPERS"' EXIT

awk '
BEGIN { in_fn=0 }
/^url_host\(\) \{/    { in_fn=1 }
/^host_allowed\(\) \{/{ in_fn=1 }
in_fn { print }
in_fn && /^\}$/ { in_fn=0 }
' "$DISPATCH" > "$HELPERS"

pass=0
fail=0

check() {
        _desc="$1"; _expected="$2"; _actual="$3"
        if [ "$_expected" = "$_actual" ]; then
                pass=$((pass + 1))
                printf '  OK   %s\n' "$_desc"
        else
                fail=$((fail + 1))
                printf '  FAIL %s  (expected=%s got=%s)\n' "$_desc" "$_expected" "$_actual"
        fi
}

WEBHOOK_ALLOWED_REDIRECT_SUFFIXES="emergentagent.com preview.emergentagent.com internal.preview.emergentagent.com"

echo "url_host():"
check "https url"        "example.com"                    "$( . "$HELPERS"; url_host "https://example.com/path?x=1" )"
check "with port"        "example.com"                    "$( . "$HELPERS"; url_host "https://example.com:8443/path" )"
check "with userinfo"    "example.com"                    "$( . "$HELPERS"; url_host "https://user:pass@example.com/path" )"
check "no scheme"        "example.com"                    "$( . "$HELPERS"; url_host "example.com/path" )"
check "uppercase"        "example.com"                    "$( . "$HELPERS"; url_host "HTTPS://EXAMPLE.COM/PATH" )"

echo "host_allowed():"
check "exact top suffix"    "0" "$( . "$HELPERS"; if host_allowed "emergentagent.com"; then echo 0; else echo 1; fi )"
check "subdomain of top"    "0" "$( . "$HELPERS"; if host_allowed "preview.emergentagent.com"; then echo 0; else echo 1; fi )"
check "deep subdomain"      "0" "$( . "$HELPERS"; if host_allowed "app.preview.emergentagent.com"; then echo 0; else echo 1; fi )"
check "unapproved host"     "1" "$( . "$HELPERS"; if host_allowed "attacker.example"; then echo 0; else echo 1; fi )"
check "empty host"          "1" "$( . "$HELPERS"; if host_allowed ""; then echo 0; else echo 1; fi )"
check "prefix-not-suffix"   "1" "$( . "$HELPERS"; if host_allowed "emergentagent.com.attacker.example"; then echo 0; else echo 1; fi )"
check "uppercase input"     "0" "$( . "$HELPERS"; if host_allowed "PREVIEW.EMERGENTAGENT.COM"; then echo 0; else echo 1; fi )"

printf '\n%d pass, %d fail\n' "$pass" "$fail"

if [ "$fail" -ne 0 ]; then
        exit 1
fi
exit 0
