# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Home Assistant setup, shared by the Agent UI backend and the flagship sidecar.

Both apps offer the same three actions, so they call these functions rather
than composing configure + activate + test themselves:

- :func:`status` — is Home Assistant connected for the flagship, and where.
- :func:`connect` — check the address and token against the live server,
  save them, and turn the connector on for the flagship.
- :func:`disconnect` — forget the address and token.

:func:`check` runs the live test again for a connection that already exists.
Every failure is a :class:`ConnectorsError` whose message tells the user what
to do; the routers pass it through unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.activations import is_agent_active
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import configure
from gaia.connectors.handler import disconnect as _disconnect
from gaia.connectors.handler import health_check
from gaia.connectors.mcp_server import _read_mcp_servers_json
from gaia.connectors.spec import MCP_SERVER_SCOPES

CONNECTOR_ID = "mcp-home-assistant"
#: The flagship's id in the activation ledger.
FLAGSHIP_AGENT_ID = "installed:gaia"


def _entry() -> Optional[Dict[str, Any]]:
    entry = _read_mcp_servers_json().get(CONNECTOR_ID)
    return entry if isinstance(entry, dict) else None


def status(agent_id: str = FLAGSHIP_AGENT_ID) -> Dict[str, Any]:
    """Saved state only; never contacts Home Assistant (see :func:`check`)."""
    entry = _entry()
    if entry is None:
        return {"connected": False, "url": None, "active": False}
    return {
        "connected": True,
        "url": entry.get("base_url"),
        "active": is_agent_active(CONNECTOR_ID, agent_id),
        "enabled": not entry.get("disabled", False),
    }


async def connect(
    url: str, token: str, agent_id: str = FLAGSHIP_AGENT_ID
) -> Dict[str, Any]:
    """Save a working Home Assistant connection and turn it on for *agent_id*.

    Nothing is saved unless Home Assistant answers with this token.
    """
    # Deferred: api imports the handler registry at module load.
    from gaia.connectors.api import activate

    if not isinstance(token, str) or not token.strip():
        raise ConnectorsError(
            "Paste a long-lived access token from your Home Assistant profile "
            "(Security tab)."
        )
    result = await configure(CONNECTOR_ID, {"HA_URL": url, "HA_TOKEN": token})
    activate(CONNECTOR_ID, agent_id, scopes_for_grant=list(MCP_SERVER_SCOPES))
    return {
        "connected": True,
        "url": result["url"],
        "active": True,
        "enabled": True,
        "tool_count": result["tool_count"],
        "detail": result["detail"],
    }


async def check() -> Dict[str, Any]:
    """Contact the saved Home Assistant and report whether it still works."""
    return await health_check(CONNECTOR_ID)


async def disconnect() -> Dict[str, Any]:
    """Forget the address and token. The token stays valid in Home Assistant."""
    await _disconnect(CONNECTOR_ID)
    return {
        "connected": False,
        "url": None,
        "active": False,
        "detail": (
            "GAIA forgot this Home Assistant. The token still works until you "
            "delete it in your Home Assistant profile (Security tab)."
        ),
    }
