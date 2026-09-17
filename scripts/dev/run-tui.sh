#!/usr/bin/env bash
# Launch the GAIA terminal UI against the agent in THIS checkout.
#
# The TUI spawns the flagship as a child process and finds it by looking for
# `gaia-agent` on PATH FIRST, then ~/.gaia/agents/gaia/. So the only way to run
# your working tree instead of the last installed build is to put a `gaia-agent`
# earlier on PATH — which is what scripts/dev/bin does. Without it you are
# testing the installed binary and any tool you just added simply will not
# exist, no matter what PYTHONPATH says (a frozen binary ignores it).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO/src:$REPO/hub/agents/gaia/python:$REPO/hub/agents/chat/python"
export PATH="$REPO/scripts/dev/bin:$PATH"

BIN="$REPO/tui/bin/gaia-tui"
if [ ! -x "$BIN" ]; then
  echo "Terminal UI not built. Run:" >&2
  echo "    cd tui && go build -o bin/gaia-tui ./cmd/gaia" >&2
  exit 1
fi

cd "$REPO"
exec "$BIN" "$@"
