#!/bin/sh
# Prefer the installed console command; preserve checkout-based plugin use.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REPO="${CAO_REPO:-$PWD}"

if command -v cao >/dev/null 2>&1; then
  set -- cao host-event --repo "$REPO" "$@"
elif [ -x "$ROOT/cao" ]; then
  set -- "$ROOT/cao" host-event --repo "$REPO" "$@"
else
  printf '%s\n' '[CAO] host-event unavailable; hook failed open' >&2
  exit 0
fi

# Never forward partial/error output as a native hook decision.
OUTPUT="$(mktemp)" || { printf '%s\n' '[CAO] cannot capture hook output; failed open' >&2; exit 0; }
trap 'rm -f "$OUTPUT"' 0
if "$@" >"$OUTPUT"; then
  cat "$OUTPUT"
else
  CODE=$?
  printf '[CAO] host-event failed (exit %s); hook failed open\n' "$CODE" >&2
fi
exit 0
