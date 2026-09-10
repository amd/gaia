#!/usr/bin/env bash
# Launch the GAIA terminal UI from THIS checkout.
#
# GAIA_GAIA_AGENT_MODE=dev is the load-bearing line: without it the daemon
# starts the last PUBLISHED agent build instead of your working tree, so any
# tool you just added simply will not exist and the agent will say it cannot
# do the thing you are testing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO/src:$REPO/hub/agents/gaia/python:$REPO/hub/agents/chat/python"
export GAIA_GAIA_AGENT_MODE=dev

BIN="$REPO/tui/bin/gaia-tui"
if [ ! -x "$BIN" ]; then
  echo "Terminal UI not built. Run:" >&2
  echo "    cd tui && go build -o bin/gaia-tui ./cmd/gaia" >&2
  exit 1
fi

cd "$REPO"
exec "$BIN" "$@"
