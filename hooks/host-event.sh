#!/bin/sh
# Prefer the installed `cao` console command. Fall back to this checkout's
# launcher so source-based Agent plugin installs continue to work.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REPO="${CAO_REPO:-$PWD}"

if command -v cao >/dev/null 2>&1; then
  exec cao host-event --repo "$REPO" "$@"
fi
exec "$ROOT/cao" host-event --repo "$REPO" "$@"
