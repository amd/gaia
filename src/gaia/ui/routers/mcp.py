# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP server status endpoints for GAIA Agent UI.

Configuration of MCP servers (add / remove / enable / disable / catalog)
moved to the connectors framework in #927. This module retains only the
read-only runtime-status endpoints used by the UI's MCP status panel.

Mutating operations now route through:
  * Catalog tiles  → POST /api/connectors/{id}/configure
  * Custom servers → gaia connectors mcp add (CLI; UI work in #977)
  * Catalog        → GET  /api/connectors/catalog (filter by type='mcp_server')
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gaia.mcp.client.config import MCPConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MCPServerInfo(BaseModel):
    name: str
    command: str
    args: List[str]
    env: Dict[str, str]  # values masked
    enabled: bool


class MCPServerStatus(BaseModel):
    name: str
    connected: bool
    tool_count: int
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> MCPConfig:
    """Return MCPConfig pointing at the global ~/.gaia/mcp_servers.json."""
    from pathlib import Path

    global_path = Path.home() / ".gaia" / "mcp_servers.json"
    global_path.parent.mkdir(parents=True, exist_ok=True)
    return MCPConfig(config_file=str(global_path))


def _mask_env(env: Dict[str, str]) -> Dict[str, str]:
    """Replace non-empty env values with '***' to avoid leaking secrets."""
    return {k: ("***" if v else "") for k, v in env.items()}


# ---------------------------------------------------------------------------
# Read-only endpoints
# ---------------------------------------------------------------------------


@router.get("/api/mcp/servers")
async def list_mcp_servers():
    """List all configured MCP servers and their enabled/disabled state."""
    config = _load_config()
    servers = config.get_servers()
    result = []
    for name, cfg in servers.items():
        result.append(
            MCPServerInfo(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=_mask_env(cfg.get("env", {})),
                enabled=not cfg.get("disabled", False),
            )
        )
    return {"servers": [s.model_dump() for s in result]}


@router.get("/api/mcp/servers/{name}/tools")
async def list_mcp_server_tools(name: str):
    """List tools provided by an MCP server (requires a transient connection)."""
    config = _load_config()
    if not config.server_exists(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    cfg = config.get_server(name)
    if cfg.get("disabled", False):
        raise HTTPException(status_code=400, detail=f"Server '{name}' is disabled")

    # Attempt a transient connection to list tools
    try:
        from gaia.mcp.client.mcp_client import MCPClient

        client = MCPClient.from_config(name, cfg)
        if not client.connect():
            raise HTTPException(
                status_code=503,
                detail=f"Could not connect to server '{name}': {client.last_error}",
            )
        tools = client.list_tools()
        client.disconnect()
        return {
            "name": name,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                }
                for t in tools
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to list tools for MCP server '%s': %s", name, exc)
        raise HTTPException(
            status_code=503, detail=f"Failed to connect to server '{name}': {exc}"
        )


@router.get("/api/mcp/status")
async def get_mcp_runtime_status():
    """Return runtime MCP server connection status from the most recent chat session.

    Only populated after the first chat message is sent.  Returns an empty list
    before any chat has started.
    """
    from gaia.ui._chat_helpers import get_cached_mcp_status

    servers = get_cached_mcp_status()
    return {"servers": [MCPServerStatus(**s).model_dump() for s in servers]}
