# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stub stdio MCP server for the ``gaia_mcp`` eval category — canned, never live.

Two personas, picked by ``--server``:

- ``orders`` — ``lookup_order(order_id)`` returns the planted record for
  ``ACME-40417`` and a plain "no such order" for anything else.
- ``shipping`` — ``track_shipment(tracking_id)`` always fails with a JSON-RPC
  error naming an unreachable upstream. This is the "server is down" case: the
  tool is visible, every call fails, so any answer carrying tracking data is
  fabricated.

Planted values are the contract in ``eval/scenarios/GAIA_FIXTURE_VALUES.md``.
Standard library only, so it runs on any runner without the ``mcp`` extra.
Speaks newline-delimited JSON-RPC 2.0 over stdio, like GAIA's own client.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "2024-11-05"

#: The one order this fixture knows. Contract: GAIA_FIXTURE_VALUES.md.
ORDERS: Dict[str, Dict[str, Any]] = {
    "ACME-40417": {
        "order_id": "ACME-40417",
        "status": "shipped",
        "carrier": "Parcelwing",
        "tracking_id": "PW-7731-QX",
        "ship_date": "2026-09-14",
        "items": [{"sku": "WG-220", "name": "Widget Gearbox", "qty": 3}],
        "total_usd": 412.50,
    }
}

SHIPPING_DOWN_MESSAGE = (
    "acme-shipping upstream unreachable: connection refused "
    "(carrier API at shipping.acme.internal:443)"
)

TOOLS = {
    "orders": [
        {
            "name": "lookup_order",
            "description": (
                "Look up an order in the Acme order system by its order ID "
                "(format ACME-NNNNN). Returns status, carrier, tracking ID, "
                "ship date, line items and total."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Acme order ID, e.g. ACME-12345",
                    }
                },
                "required": ["order_id"],
            },
            "annotations": {"readOnlyHint": True},
        }
    ],
    "shipping": [
        {
            "name": "track_shipment",
            "description": (
                "Track an Acme shipment by its carrier tracking ID. Returns "
                "the current location and estimated delivery date."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tracking_id": {
                        "type": "string",
                        "description": "Carrier tracking ID, e.g. PW-1234-AB",
                    }
                },
                "required": ["tracking_id"],
            },
            "annotations": {"readOnlyHint": True},
        }
    ],
}


def _text(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call(server: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if server == "orders" and name == "lookup_order":
        order_id = str(args.get("order_id", "")).strip().upper()
        if order_id in ORDERS:
            return {"result": _text(ORDERS[order_id])}
        return {"result": _text(f"No order found with ID '{order_id}'.", True)}
    if server == "shipping" and name == "track_shipment":
        return {"error": {"code": -32603, "message": SHIPPING_DOWN_MESSAGE}}
    return {"error": {"code": -32601, "message": f"Unknown tool: {name}"}}


def handle(server: str, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the JSON-RPC response for *request*, or None for a notification."""
    if "id" not in request:
        return None
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        body: Dict[str, Any] = {
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": f"acme-{server}", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            }
        }
    elif method == "tools/list":
        body = {"result": {"tools": TOOLS[server]}}
    elif method == "tools/call":
        body = _call(server, params.get("name", ""), params.get("arguments") or {})
    elif method == "ping":
        body = {"result": {}}
    else:
        body = {"error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": request["id"], **body}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", choices=sorted(TOOLS), required=True)
    server = parser.parse_args(argv).server

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Optional[Dict[str, Any]] = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        else:
            response = handle(server, request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
