#!/usr/bin/env python
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Launch the Agent UI MCP server with its stderr preserved.

The eval spawns this server once per scenario, as a grandchild: the runner
starts ``claude -p``, and ``claude -p`` starts the server. When the server dies
the client reports only ``CONNECTION_CLOSED``, and the server's own stderr goes
nowhere — it is not the scenario subprocess, so capturing *that* process's
output (#3375) does not reach it either.

Three runs of the eval gate were spent inferring a cause from timings that one
line of this log would have stated outright. So: exec the real server in-process
with stderr tee'd to a file the workflow uploads.

stdout is untouched and unbuffered. It carries the MCP protocol, and a single
stray byte on it desynchronises the client — which is why the diagnostics go to
a file rather than to stderr-as-console or, worse, to stdout.
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

#: Where to tee stderr. The workflow uploads this directory as an artifact.
_LOG_DIR = Path(os.environ.get("GAIA_MCP_LOG_DIR", "eval-out"))
_LOG_PATH = _LOG_DIR / "mcp-server.err.log"


class _Tee:
    """Write to both the real stderr and the log, so neither is lost."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:  # noqa: BLE001 - a broken tee must not kill the server
                pass
        return len(data)

    def flush(self):
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(_LOG_PATH, "a", encoding="utf-8", errors="replace")
    except OSError:
        # A log we cannot open must not stop the server from starting.
        log = None

    if log is not None:
        sys.stderr = _Tee(sys.stderr, log)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(
            f"\n=== MCP server launch {stamp} pid={os.getpid()} "
            f"python={sys.executable} ===",
            file=sys.stderr,
        )

    # argv[0] must look like the module's own invocation, and the --stdio flag
    # the config passes has to survive.
    sys.argv = ["gaia.mcp.servers.agent_ui_mcp", *sys.argv[1:]]
    try:
        runpy.run_module("gaia.mcp.servers.agent_ui_mcp", run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        print(f"=== MCP server exited with {code} ===", file=sys.stderr)
        return code
    except BaseException:  # noqa: BLE001 - the whole point is to record it
        print("=== MCP server raised ===", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
