# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A fake Home Assistant MCP server, served over real HTTP on loopback.

It mirrors what Home Assistant 2026.x answers at ``POST /api/mcp/assist``:
bearer-token auth, stateless streamable HTTP (plain JSON replies), the Assist
tool names, titles, schemas and annotations, and ``GetLiveContext`` over a
small house. Control tools change that house, so a test can check what the
agent actually did, not only what it said.

``FakeHomeAssistant(token).start()`` returns the base URL. Every request is
recorded in ``requests`` (path, method, headers) for header-shape assertions.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_CONTROL = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}

_TARGET = {
    "name": {"type": "string"},
    "area": {"type": "string"},
    "domain": {
        "type": "array",
        "items": {"type": "string", "enum": ["light", "fan", "switch", "cover"]},
    },
}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "homeassistant__GetLiveContext",
        "title": "Get live context",
        "description": "Provides real-time information about the CURRENT state of devices.",
        "inputSchema": {"type": "object", "properties": {"domain": {"type": "string"}}},
        "annotations": _READ_ONLY,
    },
    {
        "name": "intent__HassTurnOn",
        "title": "Turn on",
        "description": "Turns on/opens a device or entity.",
        "inputSchema": {"type": "object", "properties": dict(_TARGET)},
        "annotations": _CONTROL,
    },
    {
        "name": "intent__HassTurnOff",
        "title": "Turn off",
        "description": "Turns off/closes a device or entity.",
        "inputSchema": {"type": "object", "properties": dict(_TARGET)},
        "annotations": _CONTROL,
    },
    {
        "name": "light__HassLightSet",
        "title": "Set light",
        "description": "Sets the brightness percentage or color of a light.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_TARGET,
                "brightness": {"type": "integer", "description": "0-100"},
            },
        },
        "annotations": _CONTROL,
    },
]


def default_house() -> Dict[str, Dict[str, Any]]:
    return {
        "Kitchen Lights": {
            "domain": "light",
            "area": "Kitchen",
            "state": "on",
            "brightness": 100,
        },
        "Porch Light": {
            "domain": "light",
            "area": "Outside",
            "state": "on",
            "brightness": 100,
        },
        "Bed Light": {
            "domain": "light",
            "area": "Bedroom",
            "state": "off",
            "brightness": 0,
        },
        "Ceiling Fan": {"domain": "fan", "area": "Living Room", "state": "off"},
    }


class FakeHomeAssistant:
    def __init__(
        self, token: str, *, mcp_enabled: bool = True, redirect_to: Optional[str] = None
    ):
        self.token = token
        self.mcp_enabled = mcp_enabled
        self.redirect_to = redirect_to
        self.house = default_house()
        self.requests: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []
        self._server: Optional[ThreadingHTTPServer] = None

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(
                self,
                status: int,
                body: Any = None,
                headers: Optional[Dict[str, str]] = None,
            ):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self):
                fake.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                    }
                )

            def _authorized(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {fake.token}"

            def do_GET(self):  # noqa: N802
                self._record()
                if self.path == "/manifest.json":
                    return self._send(
                        200, {"name": "Home Assistant", "short_name": "Home Assistant"}
                    )
                if self.path == "/api/":
                    if not self._authorized():
                        return self._send(401, {"message": "401: Unauthorized"})
                    return self._send(200, {"message": "API running."})
                return self._send(404, {"message": "Not Found"})

            def do_DELETE(self):  # noqa: N802
                self._record()
                return self._send(405)

            def do_POST(self):  # noqa: N802
                self._record()
                length = int(self.headers.get("Content-Length") or 0)
                message = json.loads(self.rfile.read(length) or b"{}")
                if fake.redirect_to:
                    return self._send(
                        307, None, {"Location": fake.redirect_to + self.path}
                    )
                if self.path != "/api/mcp/assist":
                    return self._send(404, {"message": "Not Found"})
                if not self._authorized():
                    return self._send(401, {"message": "401: Unauthorized"})
                if not fake.mcp_enabled:
                    return self._send(
                        404,
                        {"message": "Model Context Protocol server is not configured"},
                    )
                if "id" not in message:
                    return self._send(202)
                return self._send(200, fake.answer(message))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # --- MCP --------------------------------------------------------------

    def live_context(self, domain: Optional[str] = None) -> str:
        lines = [
            "Live Context: An overview of the areas and the devices in this smart home:"
        ]
        for name, device in self.house.items():
            if domain and device["domain"] != domain:
                continue
            lines.append(
                f"- names: {name}\n  domain: {device['domain']}\n  state: '{device['state']}'\n  areas: {device['area']}"
            )
            if device["domain"] == "light":
                lines.append(
                    f"  attributes:\n    brightness: '{round(device['brightness'] * 2.55)}'"
                )
        return "\n".join(lines)

    def _resolve(self, args: Dict[str, Any]) -> List[str]:
        if args.get("name"):
            return [n for n in self.house if n.lower() == str(args["name"]).lower()]
        hits = list(self.house)
        if args.get("area"):
            hits = [
                n
                for n in hits
                if self.house[n]["area"].lower() == str(args["area"]).lower()
            ]
        if args.get("domain"):
            hits = [n for n in hits if self.house[n]["domain"] in args["domain"]]
        return hits if (args.get("area") or args.get("domain")) else []

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"name": name, "args": args})
        if name == "homeassistant__GetLiveContext":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "result": self.live_context(args.get("domain")),
                            }
                        ),
                    }
                ],
                "isError": False,
            }
        targets = self._resolve(args)
        if not targets:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: no device matched that name, area or domain",
                    }
                ],
                "isError": True,
            }
        for target in targets:
            device = self.house[target]
            if name == "intent__HassTurnOn":
                device["state"] = "on"
                if device["domain"] == "light" and not device.get("brightness"):
                    device["brightness"] = 100
            elif name == "intent__HassTurnOff":
                device["state"] = "off"
            elif name == "light__HassLightSet":
                device["state"] = "on"
                device["brightness"] = int(
                    args.get("brightness", device.get("brightness") or 100)
                )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"success": True, "targets": targets}),
                }
            ],
            "isError": False,
        }

    def answer(self, message: Dict[str, Any]) -> Dict[str, Any]:
        method, rid = message.get("method"), message.get("id")
        if method == "initialize":
            result: Dict[str, Any] = {
                "protocolVersion": message.get("params", {}).get(
                    "protocolVersion", "2025-06-18"
                ),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "home-assistant", "version": ""},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params", {})
            result = self.call_tool(params.get("name"), params.get("arguments") or {})
        else:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": rid, "result": result}


def main(argv=None) -> int:
    """Serve a fake Home Assistant until killed: ``fake_ha.py --port P --token T``."""
    import argparse
    import time

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--token", required=True)
    parser.add_argument("--house", help="JSON file with a house dict to serve instead")
    parser.add_argument("--ready-file", help="written with the base URL once serving")
    args = parser.parse_args(argv)
    server = FakeHomeAssistant(args.token)
    if args.house:
        with open(args.house, encoding="utf-8") as fh:
            server.house = json.load(fh)
    base = server.start()
    if args.ready_file:
        with open(args.ready_file, "w", encoding="utf-8") as fh:
            fh.write(base)
    print(base, flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
