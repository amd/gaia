#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A strict toy MCP server that refuses any handshake the spec doesn't allow.

Real servers are often tolerant, which is how a client that sends an invalid
``protocolVersion`` or skips ``notifications/initialized`` goes unnoticed. This
one is not:

- ``initialize`` with a ``protocolVersion`` outside :data:`SUPPORTED` is an error.
- Any request other than ``initialize`` or ``ping`` before
  ``notifications/initialized`` is an error.
- A ``notifications/message`` line precedes every reply, so a client that
  takes the first line it reads as its answer gets the wrong message.

Every message received is appended to ``--log`` (JSON lines) so a test can
assert the exact shape of what the client sent.

Stdlib only, never imports ``gaia``. Run over stdio with
``toy_mcp_server.py --log PATH [--answer-version V]``, or build an HTTP server
in-process with :func:`make_http_server`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
_OMIT = "<omit>"

TOOL = {
    "name": "echo",
    "description": "Return the text it was given.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


class StrictSession:
    """One client's handshake state; ``handle`` returns the lines to send back."""

    def __init__(self, log_path: Optional[str], answer_version: Optional[str]):
        self.log_path = log_path
        self.answer_version = answer_version
        self.initialized = False

    def _log(self, message: Any) -> None:
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(message) + "\n")

    def handle(self, message: Any) -> List[Dict[str, Any]]:
        self._log(message)
        if not isinstance(message, dict) or "method" not in message:
            return []
        method = message["method"]
        if "id" not in message:
            if method == "notifications/initialized":
                self.initialized = True
            return []

        params = message.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested not in SUPPORTED:
                return [
                    _error(
                        message, -32602, f"Unsupported protocolVersion {requested!r}"
                    )
                ]
            result: Dict[str, Any] = {
                "serverInfo": {"name": "toy-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            }
            answer = self.answer_version or requested
            if answer != _OMIT:
                result["protocolVersion"] = answer
            return _with_noise(message, {"result": result})
        if method == "ping":
            return _with_noise(message, {"result": {}})
        if not self.initialized:
            return [
                _error(message, -32002, f"'{method}' before notifications/initialized")
            ]
        if method == "tools/list":
            return _with_noise(message, {"result": {"tools": [TOOL]}})
        if method == "tools/call":
            text = (params.get("arguments") or {}).get("text", "")
            content = [{"type": "text", "text": text}]
            return _with_noise(message, {"result": {"content": content}})
        return [_error(message, -32601, f"Method not found: {method}")]


def _error(request: Dict[str, Any], code: int, text: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": code, "message": text},
    }


def _with_noise(request: Dict[str, Any], body: Dict[str, Any]) -> List[Dict[str, Any]]:
    noise = {
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {"level": "info", "data": f"handling {request['method']}"},
    }
    return [noise, {"jsonrpc": "2.0", "id": request["id"], **body}]


def serve_stdio(log_path: Optional[str], answer_version: Optional[str]) -> int:
    session = StrictSession(log_path, answer_version)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        for reply in session.handle(json.loads(line)):
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


def make_http_server(
    log_path: Optional[str] = None,
    answer_version: Optional[str] = None,
    notification_body: str = "",
) -> ThreadingHTTPServer:
    """A loopback HTTP server on a free port, answering each POST with its reply.

    Notifications get ``202`` and *notification_body* (empty, per the spec).
    """
    session = StrictSession(log_path, answer_version)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server's naming
            length = int(self.headers.get("Content-Length", 0))
            replies = session.handle(json.loads(self.rfile.read(length)))
            answers = [r for r in replies if "id" in r]
            if answers:
                status, body = 200, json.dumps(answers[-1]).encode("utf-8")
            else:
                status, body = 202, notification_body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log")
    parser.add_argument(
        "--answer-version",
        help="protocolVersion to answer with; '<omit>' drops it; "
        "'env:NAME' reads it from an environment variable",
    )
    args = parser.parse_args(argv)
    answer = args.answer_version
    if answer and answer.startswith("env:"):
        answer = os.environ[answer[4:]]
    return serve_stdio(args.log, answer)


if __name__ == "__main__":
    sys.exit(main())
