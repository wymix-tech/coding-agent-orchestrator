#!/bin/sh
# Prefer the installed `cao` console command. Fall back to this checkout's
# launcher so source-based Agent plugin installs continue to work.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
REPO="${CAO_REPO:-$PWD}"

if command -v cao >/dev/null 2>&1; then
  exec cao host-event --repo "$REPO" "$@"
fi
if [ -x "$ROOT/cao" ]; then
  exec "$ROOT/cao" host-event --repo "$REPO" "$@"
fi
# CAO is unavailable here. Fail open: a missing or broken CAO binary must never
# block the Agent's tool calls, including the `cao bootstrap` it needs to run.
exit 0
