# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stand-in for GAIA desktop's browser-pane bridge, over real loopback HTTP.

It records each request (path, headers, JSON body) and answers from the
shared fixtures in ``tests/fixtures/browser_pane/bridge/``, so the tests check
the request PaneBridge actually puts on the wire, not that a method was called.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Union

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "browser_pane" / "bridge"
TOKEN = "ab" * 32

Reply = Union[Dict[str, Any], Tuple[int, Dict[str, Any]]]


def fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeBridge:
    """Answers each op from ``replies[op]``: a reply, a (status, body) pair, or a callable."""

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.replies: Dict[str, Union[Reply, Callable[[Dict[str, Any]], Reply]]] = {
            "hello": fixture("hello.json"),
            "attach": fixture("attach_attached.json"),
            "tabs": fixture("tabs_user_page.json"),
            "navigate": fixture("navigate.json"),
            "evaluate": self._evaluate,
            "origin": fixture("origin_loopback.json"),
            "screenshot": fixture("screenshot.json"),
            "logs": fixture("logs_console.json"),
            "act": fixture("act.json"),
            "viewport": fixture("viewport.json"),
            "devServers": self._dev_servers,
        }
        #: What ``evaluate`` answers with for a page read; tests replace it.
        self.snapshot: Dict[str, Any] = fixture("evaluate_snapshot.json")
        #: A live label read: selector -> the label the page shows now (None: gone).
        self.labels: Dict[str, Any] = {}
        #: The dev servers this chat's ``devServers`` list op reports.
        self.servers: List[Dict[str, Any]] = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server's name
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                op = self.path.rsplit("/", 1)[-1]
                bridge.requests.append(
                    {
                        "path": self.path,
                        "op": op,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )
                reply = bridge.replies.get(
                    op,
                    (404, {"error": {"code": "unknown_op", "message": op, "data": {}}}),
                )
                if callable(reply):
                    reply = reply(body)
                status, payload = reply if isinstance(reply, tuple) else (200, reply)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        self._thread.start()

    def serve(self, port: int, status: str = "ready") -> None:
        """This chat started a dev server on ``port``."""
        self.servers.append(
            {
                "key": f"{port:016x}",
                "name": "web",
                "status": status,
                "port": port,
                "url": f"http://localhost:{port}/",
            }
        )

    def _dev_servers(self, body: Dict[str, Any]) -> Reply:
        if body.get("op") == "list":
            return {"servers": list(self.servers)}
        return (
            404,
            {"error": {"code": "unknown_op", "message": "devServers", "data": {}}},
        )

    def _evaluate(self, body: Dict[str, Any]) -> Reply:
        from gaia.browser.pane_driver import _LIVE_LABEL_JS

        if body.get("script") != _LIVE_LABEL_JS:
            return self.snapshot
        selector = body["args"]
        if selector in self.labels:
            label = self.labels[selector]
        else:
            label = next(
                (
                    el.get("name") or ""
                    for f in self.snapshot["frames"]
                    if f.get("ok")
                    for el in (f.get("value") or {}).get("elements") or []
                    if f'"{el["ref"]}"' in selector
                ),
                None,
            )
        return {
            "tabId": "tab-1",
            "url": self.snapshot.get("url"),
            "frames": [{"frameId": None, "url": "", "ok": True, "value": label}],
        }

    def ops(self) -> List[str]:
        return [r["op"] for r in self.requests]

    def page_ops(self) -> List[str]:
        """The ops sent, leaving out the dev server lookups that check a page's scope."""
        return [op for op in self.ops() if op != "devServers"]

    def last(self, op: str) -> Dict[str, Any]:
        return [r for r in self.requests if r["op"] == op][-1]

    def write_discovery(self, path: Path, *, pid: int = 0, protocol: int = 1) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "port": self.port,
                    "token": TOKEN,
                    "pid": pid or os.getpid(),
                    "protocol": protocol,
                }
            ),
            encoding="utf-8",
        )
        return path

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
