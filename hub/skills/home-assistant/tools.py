# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``home-assistant`` skill.

Device control itself comes from Home Assistant's own MCP server through the
``mcp-home-assistant`` connector. These two tools cover what that server
cannot answer: whether GAIA is connected to it at all, and where on the
network it might be. Neither sends the access token anywhere new.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List

from gaia.agents.base.tools import tool

#: Where a default Home Assistant install answers on a home network.
_CANDIDATES = (
    "http://homeassistant.local:8123",
    "http://homeassistant:8123",
    "http://localhost:8123",
)

#: Registry prefix of the connector's tools (``mcp_<server prefix>_``).
_TOOL_PREFIX = "mcp_home_assistant_"

_WHERE_TO_CONNECT = (
    "Connect it in the Agent UI under Settings › Smart home, or with /home in "
    "the GAIA terminal UI."
)


def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside a running loop: run on a private one.
    result: Dict[str, Any] = {}

    def runner() -> None:
        result["value"] = asyncio.run(coro)

    worker = threading.Thread(target=runner)
    worker.start()
    worker.join()
    return result["value"]


@tool
def home_assistant_status() -> dict:
    """Check whether GAIA is connected to Home Assistant, and test the link.

    Use when Home Assistant's own tools are missing, a device call failed with
    a connection or token error, or the user asks whether Home Assistant is
    connected.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.connectors import home_assistant as ha

    if any(name.startswith(_TOOL_PREFIX) for name in _TOOL_REGISTRY):
        return {
            "connected": True,
            "working": True,
            "message": (
                "Home Assistant is connected and its tools are available in this "
                "chat. A device missing from its live context isn't shared with "
                "GAIA: the user can share it in Home Assistant under Settings › "
                "Voice assistants › Expose."
            ),
        }
    state = ha.status()
    if not state["connected"]:
        return {
            "connected": False,
            "message": f"Home Assistant is not connected to GAIA. {_WHERE_TO_CONNECT}",
        }
    check = _run(ha.check())
    message = check.get("detail", "")
    if check.get("ok") and not state.get("active"):
        message += (
            " It is connected but turned off for GAIA; turn it on again in the "
            "same place."
        )
    elif check.get("ok"):
        message += " Start a new chat if its tools are missing from this one."
    return {
        "connected": True,
        "working": bool(check.get("ok")),
        "address": state.get("url"),
        "message": message,
    }


def _looks_like_home_assistant(base: str) -> bool:
    """True when *base* serves Home Assistant's web manifest. No token is sent."""
    try:
        with urllib.request.urlopen(f"{base}/manifest.json", timeout=2) as response:
            manifest = json.loads(response.read(65536).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return isinstance(manifest, dict) and "Home Assistant" in str(
        manifest.get("name", "")
    )


@tool
def find_home_assistant() -> dict:
    """Look for Home Assistant at its usual addresses on this PC and network.

    Use when the user wants to connect Home Assistant but doesn't know its
    address. Only reads the public start page; never sends a token.
    """
    found: List[str] = []
    for base in _CANDIDATES:
        host = base.split("//", 1)[1].rsplit(":", 1)[0]
        try:
            socket.getaddrinfo(host, 8123)
        except OSError:
            continue
        if _looks_like_home_assistant(base):
            found.append(base)
    if not found:
        return {
            "found": [],
            "message": (
                "No Home Assistant answered at its usual addresses. Ask the "
                "user for the address they open Home Assistant at."
            ),
        }
    return {
        "found": found,
        "message": f"Home Assistant answers at {found[0]}. {_WHERE_TO_CONNECT}",
    }
