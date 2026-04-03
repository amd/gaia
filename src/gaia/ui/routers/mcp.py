# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP server management endpoints for GAIA Agent UI."""

import logging
import subprocess
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gaia.mcp.client.config import MCPConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

# ---------------------------------------------------------------------------
# Agent UI MCP Server — process lifecycle tracking
# ---------------------------------------------------------------------------

# Module-level state for the Agent UI MCP server subprocess.
# The server exposes the GAIA Agent UI as MCP tools for clients like Claude Code.
_agent_mcp_process: Optional[subprocess.Popen] = None
_agent_mcp_port: int = 8765

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


@router.get("/api/mcp/catalog")
async def get_mcp_catalog():
    """Return the curated list of popular MCP servers."""
    return {"catalog": _CATALOG}


@router.get("/api/mcp/status")
async def get_mcp_runtime_status():
    """Return runtime MCP server connection status from the most recent chat session.

    Only populated after the first chat message is sent.  Returns an empty list
    before any chat has started.
    """
    from gaia.ui._chat_helpers import get_cached_mcp_status

    servers = get_cached_mcp_status()
    return {"servers": [MCPServerStatus(**s).model_dump() for s in servers]}


# ---------------------------------------------------------------------------
# Agent UI MCP Server endpoints
# ---------------------------------------------------------------------------


class StartAgentServerRequest(BaseModel):
    port: int = Field(default=8765, ge=1024, le=65535)
    backend_url: str = "http://localhost:4200"


async def _probe_mcp_port(port: int) -> bool:
    """Return True if the Agent UI MCP server is already responding on *port*."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            # The FastMCP streamable-http transport responds to GET /mcp with 405
            # or 200; any HTTP response means the server is live.
            resp = await client.get(f"http://localhost:{port}/mcp")
            return resp.status_code in (200, 405, 404)
    except Exception:
        return False


@router.get("/api/mcp/agent-server/status")
async def get_agent_mcp_server_status():
    """Return the current status of the GAIA Agent UI MCP server.

    The server exposes Agent UI tools (sessions, chat, files, memory) to MCP
    clients such as Claude Code. It is started separately from the chat backend.
    """
    global _agent_mcp_process, _agent_mcp_port  # noqa: PLW0603

    running = False
    pid = None

    # Check tracked subprocess first
    if _agent_mcp_process is not None:
        retcode = _agent_mcp_process.poll()
        if retcode is None:
            running = True
            pid = _agent_mcp_process.pid
        else:
            logger.info(
                "Agent UI MCP server process exited (code %d); clearing reference",
                retcode,
            )
            _agent_mcp_process = None

    # Fallback: probe the port in case the server was started externally
    if not running:
        running = await _probe_mcp_port(_agent_mcp_port)
        if running:
            logger.debug(
                "Agent UI MCP server detected externally on port %d", _agent_mcp_port
            )

    return {
        "running": running,
        "port": _agent_mcp_port,
        "pid": pid,
        "url": f"http://localhost:{_agent_mcp_port}/mcp" if running else None,
    }


@router.post("/api/mcp/agent-server/start", status_code=200)
async def start_agent_mcp_server(body: Optional[StartAgentServerRequest] = None):
    """Start the GAIA Agent UI MCP server on the given port.

    The server makes Agent UI tools available to MCP clients (e.g., Claude Code).
    It is started as a background subprocess and connects to the Agent UI backend.

    Request body is optional — omit or send ``{}`` to use defaults (port 8765).
    """
    effective = body or StartAgentServerRequest()
    global _agent_mcp_process, _agent_mcp_port  # noqa: PLW0603

    # Already running?
    if _agent_mcp_process is not None and _agent_mcp_process.poll() is None:
        logger.info(
            "Agent UI MCP server already running (pid=%d, port=%d)",
            _agent_mcp_process.pid,
            _agent_mcp_port,
        )
        return {
            "status": "already_running",
            "port": _agent_mcp_port,
            "pid": _agent_mcp_process.pid,
            "url": f"http://localhost:{_agent_mcp_port}/mcp",
        }

    # Check if the port is already occupied (e.g., externally started server)
    if await _probe_mcp_port(effective.port):
        _agent_mcp_port = effective.port
        logger.info(
            "Agent UI MCP server already responding on port %d (external)",
            effective.port,
        )
        return {
            "status": "already_running",
            "port": effective.port,
            "pid": None,
            "url": f"http://localhost:{effective.port}/mcp",
        }

    _agent_mcp_port = effective.port

    try:
        cmd = [
            sys.executable,
            "-m",
            "gaia.mcp.servers.agent_ui_mcp",
            "--port",
            str(effective.port),
            "--backend",
            effective.backend_url,
        ]
        logger.info("Starting Agent UI MCP server: %s", " ".join(cmd))
        _agent_mcp_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        _agent_mcp_process = None
        raise HTTPException(
            status_code=500, detail=f"Failed to launch Agent UI MCP server: {exc}"
        ) from exc

    # Brief grace period — if the process exits immediately it failed to start
    import asyncio

    await asyncio.sleep(1.0)

    retcode = _agent_mcp_process.poll()
    if retcode is not None:
        stderr_out = ""
        try:
            stderr_out = (_agent_mcp_process.stderr.read() or "")[:500]
        except Exception:
            pass
        _agent_mcp_process = None
        raise HTTPException(
            status_code=500,
            detail=f"Agent UI MCP server exited immediately (code {retcode}): {stderr_out}",
        )

    logger.info(
        "Agent UI MCP server started (pid=%d, port=%d)",
        _agent_mcp_process.pid,
        effective.port,
    )
    return {
        "status": "started",
        "port": effective.port,
        "pid": _agent_mcp_process.pid,
        "url": f"http://localhost:{effective.port}/mcp",
    }


@router.post("/api/mcp/agent-server/stop", status_code=200)
async def stop_agent_mcp_server():
    """Stop the GAIA Agent UI MCP server (if started by the UI)."""
    global _agent_mcp_process  # noqa: PLW0603

    if _agent_mcp_process is None:
        return {"status": "not_running"}

    retcode = _agent_mcp_process.poll()
    if retcode is not None:
        _agent_mcp_process = None
        return {"status": "not_running"}

    pid = _agent_mcp_process.pid
    try:
        _agent_mcp_process.terminate()
        try:
            _agent_mcp_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Agent UI MCP server did not stop gracefully; killing pid=%d", pid
            )
            _agent_mcp_process.kill()
            _agent_mcp_process.wait(timeout=3)
    except Exception as exc:
        logger.warning("Error stopping Agent UI MCP server (pid=%d): %s", pid, exc)
    finally:
        _agent_mcp_process = None

    logger.info("Agent UI MCP server stopped (pid=%d)", pid)
    return {"status": "stopped", "pid": pid}
