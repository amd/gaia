# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP server management endpoints for GAIA Agent UI."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gaia.mcp.client.config import MCPConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

# ---------------------------------------------------------------------------
# Curated MCP server catalog (Tier 1–4 popular servers)
# ---------------------------------------------------------------------------

_CATALOG: List[Dict[str, Any]] = [
    # ── Tier 1 — Essential ──────────────────────────────────────────────────
    {
        "name": "filesystem",
        "display_name": "File System",
        "description": "Secure file read/write/search with configurable access controls.",
        "category": "system",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "~"],
        "requires_config": ["allowed_directories"],
        "env": {},
    },
    {
        "name": "playwright",
        "display_name": "Browser (Playwright)",
        "description": "Web browsing and interaction via accessibility snapshots.",
        "category": "browser",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-playwright"],
        "requires_config": [],
        "env": {},
    },
    {
        "name": "github",
        "display_name": "GitHub",
        "description": "Repos, PRs, issues, workflows — full GitHub access.",
        "category": "dev-tools",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "requires_config": ["GITHUB_TOKEN"],
        "env": {"GITHUB_TOKEN": ""},
    },
    {
        "name": "fetch",
        "display_name": "Web Fetch",
        "description": "Fetch web content and convert it to Markdown.",
        "category": "web",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-fetch"],
        "requires_config": [],
        "env": {},
    },
    {
        "name": "memory",
        "display_name": "Memory",
        "description": "Knowledge graph-based persistent memory for agents.",
        "category": "context",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "requires_config": [],
        "env": {},
    },
    {
        "name": "git",
        "display_name": "Git",
        "description": "Git repository tools: log, diff, status, blame.",
        "category": "dev-tools",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-git"],
        "requires_config": [],
        "env": {},
    },
    {
        "name": "desktop-commander",
        "display_name": "Desktop Commander",
        "description": "Terminal command execution + file operations with user control.",
        "category": "system",
        "tier": 1,
        "command": "npx",
        "args": ["-y", "desktop-commander"],
        "requires_config": [],
        "env": {},
    },
    # ── Tier 2 — High Value ─────────────────────────────────────────────────
    {
        "name": "brave-search",
        "display_name": "Brave Search",
        "description": "Web search via Brave Search API.",
        "category": "web-search",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-brave-search"],
        "requires_config": ["BRAVE_API_KEY"],
        "env": {"BRAVE_API_KEY": ""},
    },
    {
        "name": "postgres",
        "display_name": "PostgreSQL",
        "description": "Read-only database queries against a PostgreSQL database.",
        "category": "database",
        "tier": 2,
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-postgres",
            "postgresql://localhost/mydb",
        ],
        "requires_config": ["connection_string"],
        "env": {},
    },
    {
        "name": "context7",
        "display_name": "Context7 Docs",
        "description": "Inject fresh, version-specific library docs into agent context.",
        "category": "documentation",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "context7-mcp"],
        "requires_config": [],
        "env": {},
    },
    # ── Tier 3 — Windows Desktop Automation ────────────────────────────────
    {
        "name": "windows-automation",
        "display_name": "Windows Automation",
        "description": "Native Windows UI automation: open apps, control windows, simulate input.",
        "category": "computer-use",
        "tier": 3,
        "command": "npx",
        "args": ["-y", "mcp-windows-automation"],
        "requires_config": [],
        "env": {},
    },
    # ── Tier 4 — Microsoft Ecosystem ────────────────────────────────────────
    {
        "name": "microsoft-learn",
        "display_name": "Microsoft Learn",
        "description": "Real-time access to Microsoft documentation.",
        "category": "documentation",
        "tier": 4,
        "command": "npx",
        "args": ["-y", "@microsoft/mcp-docs"],
        "requires_config": [],
        "env": {},
    },
    # ── Tier 2 — Email & Calendar ───────────────────────────────────────────
    {
        "name": "gmail",
        "display_name": "Gmail",
        "description": "Read, search, send, label, and archive Gmail messages.",
        "category": "email",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "gmail-mcp-server"],
        "requires_config": ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET"],
        "env": {"GMAIL_CLIENT_ID": "", "GMAIL_CLIENT_SECRET": ""},
    },
    {
        "name": "google-calendar",
        "display_name": "Google Calendar",
        "description": "Events, scheduling, availability, and RSVP management.",
        "category": "calendar",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "google-calendar-mcp"],
        "requires_config": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "env": {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""},
    },
    {
        "name": "outlook",
        "display_name": "Outlook / Microsoft 365",
        "description": "Outlook email and calendar via Microsoft Graph API.",
        "category": "email",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "outlook-mcp-server"],
        "requires_config": ["MS_CLIENT_ID", "MS_CLIENT_SECRET"],
        "env": {"MS_CLIENT_ID": "", "MS_CLIENT_SECRET": ""},
    },
    # ── Tier 2 — Popular App Control ────────────────────────────────────────
    {
        "name": "spotify",
        "display_name": "Spotify",
        "description": "Play, pause, skip, search tracks, and manage playlists.",
        "category": "media",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "spotify-mcp-server"],
        "requires_config": ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"],
        "env": {"SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""},
    },
    {
        "name": "slack",
        "display_name": "Slack",
        "description": "Channel management, messaging, and conversation history.",
        "category": "communication",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "slack-mcp-server"],
        "requires_config": ["SLACK_BOT_TOKEN"],
        "env": {"SLACK_BOT_TOKEN": ""},
    },
    {
        "name": "notion",
        "display_name": "Notion",
        "description": "Workspace pages, databases, and task management.",
        "category": "productivity",
        "tier": 2,
        "command": "npx",
        "args": ["-y", "notion-mcp"],
        "requires_config": ["NOTION_API_KEY"],
        "env": {"NOTION_API_KEY": ""},
    },
]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class MCPServerCreateRequest(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None


class MCPServerInfo(BaseModel):
    name: str
    command: str
    args: List[str]
    env: Dict[str, str]  # values masked
    enabled: bool


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
# Endpoints
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


@router.post("/api/mcp/servers", status_code=201)
async def add_mcp_server(body: MCPServerCreateRequest):
    """Add a new MCP server configuration (persisted to ~/.gaia/mcp_servers.json)."""
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="Server name must not be empty")
    if not body.command or not body.command.strip():
        raise HTTPException(status_code=400, detail="Command must not be empty")

    config = _load_config()
    if config.server_exists(body.name):
        raise HTTPException(
            status_code=409, detail=f"Server '{body.name}' already exists"
        )

    server_cfg: Dict[str, Any] = {"command": body.command, "args": body.args or []}
    if body.env:
        server_cfg["env"] = body.env

    config.add_server(body.name, server_cfg)
    logger.info("Added MCP server '%s' (command: %s)", body.name, body.command)
    return {"status": "added", "name": body.name}


@router.delete("/api/mcp/servers/{name}")
async def remove_mcp_server(name: str):
    """Remove an MCP server configuration."""
    config = _load_config()
    if not config.server_exists(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    config.remove_server(name)
    logger.info("Removed MCP server '%s'", name)
    return {"status": "removed", "name": name}


@router.post("/api/mcp/servers/{name}/enable")
async def enable_mcp_server(name: str):
    """Enable a previously disabled MCP server."""
    config = _load_config()
    if not config.server_exists(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    cfg = config.get_server(name)
    cfg.pop("disabled", None)
    config.add_server(name, cfg)
    logger.info("Enabled MCP server '%s'", name)
    return {"status": "enabled", "name": name}


@router.post("/api/mcp/servers/{name}/disable")
async def disable_mcp_server(name: str):
    """Disable an MCP server without removing its configuration."""
    config = _load_config()
    if not config.server_exists(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    cfg = config.get_server(name)
    cfg["disabled"] = True
    config.add_server(name, cfg)
    logger.info("Disabled MCP server '%s'", name)
    return {"status": "disabled", "name": name}


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
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
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


@router.get("/api/mcp/catalog")
async def get_mcp_catalog():
    """Return the curated list of popular MCP servers."""
    return {"catalog": _CATALOG}
