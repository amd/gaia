#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Toy sidecar agent binary for the daemon supervision integration contract
(issue #2142, T4).

Stdlib only — never imports ``gaia`` — so it can be copied into a fake
``~/.gaia/agents/<id>/`` hub install dir and executed directly as a "verified"
binary the way a real published sidecar would be. It answers the same
``/health``/``/version`` contract :class:`AgentSidecarManager` probes, and
spawns one long-lived child process in its own process group so a test can
prove a tree-kill takes the child down too, not just the leader.

Run directly: ``toy_sidecar.py --port <port> [--host HOST] [--service ID]``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_child_pid: "int | None" = None


def _spawn_child() -> int:
    """Spawn one long-lived helper process in this leader's process group."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3600)"],
    )
    return proc.pid


class _Handler(BaseHTTPRequestHandler):
    service = "gaia-agent-toy"

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - required stdlib handler name
        if self.path == "/health":
            self._reply(
                200,
                {"status": "ok", "service": self.service, "childPid": _child_pid},
            )
        elif self.path == "/version":
            self._reply(200, {"apiVersion": "1.0", "agentVersion": "0.0.1-toy"})
        else:
            self._reply(404, {"detail": f"not found: {self.path}"})

    def log_message(self, fmt, *args) -> None:  # noqa: A002 - silence access log
        pass


def main() -> None:
    global _child_pid
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--service", default="gaia-agent-toy")
    args = parser.parse_args()

    _Handler.service = args.service
    _child_pid = _spawn_child()

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
