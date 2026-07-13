# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ConnectorsDemoAgent — a built-in agent that exercises the per-agent
grant flow end-to-end.

Why this exists
---------------
The connectors framework introduced in #926 adds three things that
needed a real consumer to validate:

1. ``REQUIRED_CONNECTORS`` declarations — the agent advertises the
   connectors and scopes it needs.
2. ``get_credential_sync(connector_id, agent_id, required_scopes)``
   — the central entrypoint that fires the grant-ledger check before
   returning a usable credential.
3. The Settings → Connections per-agent grants UI — the user must be
   able to grant scopes from inside the AgentUI.

This agent ships four tools that fan out across two connector kinds:

- Google (``oauth_pkce``): ``gmail_recent_subjects``, ``calendar_today``,
  ``drive_recent_files``. Each tool calls ``get_credential_sync`` with
  the matching Google scope, then makes a one-shot REST call to the
  Google API with the returned access_token.
- GitHub (``mcp_server``): ``github_my_repos``. Pulls the GitHub PAT
  out of the keyring via the same dispatcher and calls
  api.github.com directly.

We do **not** spin up the GitHub MCP server (npx) here on purpose —
that would add a Node dependency to the demo, and direct REST calls
make the grant flow more obvious.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, ClassVar, Dict, List, Optional

import httpx

from gaia.agents.base.agent import Agent, default_max_steps
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error as _format_connector_error
from gaia.connectors.handler import get_credential_sync
from gaia.connectors.providers.base import ConnectorRequirement
from gaia.logger import get_logger

logger = get_logger(__name__)


# Public namespace this agent uses for grant-ledger lookups. Must agree
# with the registration's ``namespaced_agent_id`` in
# ``gaia_agent_connectors_demo.build_registration``. The agent now ships as a
# standalone hub wheel (#1102), so it is discovered as an ``installed:`` agent
# rather than a framework ``builtin:``.
AGENT_NAMESPACED_ID = "installed:connectors-demo"

# OAuth scopes the four tools need. Declared in one place so the
# REQUIRED_CONNECTORS block and the per-tool calls can't drift apart.
SCOPE_GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_CALENDAR_READ = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_DRIVE_READ = "https://www.googleapis.com/auth/drive.readonly"

# Symbolic scope for the GitHub MCP connector. v1 grants the entire
# PAT as a unit — fine-grained per-tool grants are a v2 follow-up
# (would require knowing the MCP server's tool list ahead of time,
# which currently lives behind the npx process).
SCOPE_MCP_USE = "use"


_SYSTEM_PROMPT = """\
You are GAIA's Connectors Demo Agent. Your job is to demonstrate the
connectors framework by retrieving real data from the user's connected
services when they ask.

You have four tools:

- gmail_recent_subjects(limit) — pulls the most recent N email subjects
  and senders from the user's Gmail inbox.
- calendar_today() — lists today's Google Calendar events.
- drive_recent_files(limit) — lists the user's most recently modified
  Google Drive files.
- github_my_repos(limit) — lists the user's GitHub repositories.

Behavior:
- Call exactly the tool that matches the question. Don't speculate;
  if the user asks "what's in my inbox?" call gmail_recent_subjects.
- If a tool returns an error mentioning "AGENT_NOT_GRANTED", tell the
  user which scope they need to grant in Settings → Connections.
- If a tool returns an error mentioning "NOT_CONNECTED", tell them to
  connect that service in Settings → Connections first.
- Summarize tool output in 1–3 sentences. Don't recite raw JSON.
- Do NOT make up data. If a tool fails, say so.
"""


# ---------------------------------------------------------------------------
# Helpers — kept module-level so they can be unit-tested without
# instantiating the full Agent (which spins up the LLM client).
# ---------------------------------------------------------------------------


def _gmail_token() -> str:
    """Return a Gmail access token via the standard grant-checked path."""
    cred = get_credential_sync(
        "google",
        agent_id=AGENT_NAMESPACED_ID,
        required_scopes=[SCOPE_GMAIL_READ],
    )
    return cred["access_token"]


def _calendar_token() -> str:
    cred = get_credential_sync(
        "google",
        agent_id=AGENT_NAMESPACED_ID,
        required_scopes=[SCOPE_CALENDAR_READ],
    )
    return cred["access_token"]


def _drive_token() -> str:
    cred = get_credential_sync(
        "google",
        agent_id=AGENT_NAMESPACED_ID,
        required_scopes=[SCOPE_DRIVE_READ],
    )
    return cred["access_token"]


def _github_pat() -> str:
    """Return the GitHub PAT via the MCP credential dispatcher."""
    cred = get_credential_sync(
        "mcp-github",
        agent_id=AGENT_NAMESPACED_ID,
        required_scopes=[SCOPE_MCP_USE],
    )
    env = cred.get("env") or {}
    token = env.get("GITHUB_TOKEN")
    if not token:
        raise ConnectorsError(
            "GitHub MCP credential resolved but GITHUB_TOKEN was empty. "
            "Re-run Settings → Connections → GitHub → Configure to set the "
            "Personal Access Token."
        )
    return token


def _http_get_json(
    url: str, *, headers: Dict[str, str], params: Optional[dict] = None
) -> Any:
    """Tiny synchronous JSON GET. Raises on non-200; returns parsed JSON."""
    resp = httpx.get(url, headers=headers, params=params, timeout=10.0)
    if resp.status_code != 200:
        raise ConnectorsError(f"{url} returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _today_window_iso() -> tuple[str, str]:
    """RFC3339 timestamps for [today 00:00 local, tomorrow 00:00 local]."""
    now = datetime.now().astimezone()
    start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    end = datetime.combine(now.date(), time.max, tzinfo=now.tzinfo)
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# Tool implementations — pure functions so they can be tested independently
# of the Agent class.
# ---------------------------------------------------------------------------


def _gmail_recent_subjects_impl(limit: int) -> Dict[str, Any]:
    try:
        token = _gmail_token()
        headers = {"Authorization": f"Bearer {token}"}
        listing = _http_get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params={"maxResults": limit},
        )
        messages = []
        for msg in (listing.get("messages") or [])[:limit]:
            detail = _http_get_json(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
            )
            hdrs = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            messages.append(
                {
                    "id": msg["id"],
                    "from": hdrs.get("From", ""),
                    "subject": hdrs.get("Subject", "(no subject)"),
                }
            )
        return {"ok": True, "count": len(messages), "messages": messages}
    except BaseException as e:  # noqa: BLE001 — translated below
        return {"ok": False, "error": _format_connector_error(e)}


def _calendar_today_impl() -> Dict[str, Any]:
    try:
        token = _calendar_token()
        time_min, time_max = _today_window_iso()
        data = _http_get_json(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        events = [
            {
                "summary": e.get("summary", "(untitled)"),
                "start": (e.get("start") or {}).get("dateTime")
                or (e.get("start") or {}).get("date"),
                "end": (e.get("end") or {}).get("dateTime")
                or (e.get("end") or {}).get("date"),
                "location": e.get("location"),
            }
            for e in (data.get("items") or [])
        ]
        return {"ok": True, "count": len(events), "events": events}
    except BaseException as e:  # noqa: BLE001
        return {"ok": False, "error": _format_connector_error(e)}


def _drive_recent_files_impl(limit: int) -> Dict[str, Any]:
    try:
        token = _drive_token()
        data = _http_get_json(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "orderBy": "modifiedTime desc",
                "pageSize": limit,
                "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            },
        )
        files = data.get("files") or []
        return {"ok": True, "count": len(files), "files": files}
    except BaseException as e:  # noqa: BLE001
        return {"ok": False, "error": _format_connector_error(e)}


def _github_my_repos_impl(limit: int) -> Dict[str, Any]:
    try:
        token = _github_pat()
        data = _http_get_json(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"per_page": limit, "sort": "updated"},
        )
        repos = [
            {
                "full_name": r.get("full_name"),
                "private": r.get("private"),
                "description": r.get("description"),
                "html_url": r.get("html_url"),
                "updated_at": r.get("updated_at"),
            }
            for r in data
        ]
        return {"ok": True, "count": len(repos), "repos": repos}
    except BaseException as e:  # noqa: BLE001
        return {"ok": False, "error": _format_connector_error(e)}


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


@dataclass
class ConnectorsDemoAgentConfig:
    """Configuration for ConnectorsDemoAgent — same shape as ChatAgentConfig
    so the registry's kwarg-filtering pattern works without special-casing."""

    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = field(default_factory=default_max_steps)
    streaming: bool = False
    debug: bool = False
    show_stats: bool = False
    silent_mode: bool = False
    output_dir: Optional[str] = None


class ConnectorsDemoAgent(Agent):
    """Demo agent that uses Google + GitHub connector grants end-to-end."""

    AGENT_ID = "connectors-demo"
    AGENT_NAME = "Connectors Demo"
    AGENT_DESCRIPTION = (
        "Demonstrates the connectors framework — pulls real data from "
        "your connected Google account and GitHub PAT."
    )
    CONVERSATION_STARTERS = [
        "What's in my inbox?",
        "What's on my calendar today?",
        "List my recent Drive files",
        "List my GitHub repositories",
    ]

    REQUIRED_CONNECTORS: ClassVar[List[ConnectorRequirement]] = [
        ConnectorRequirement(
            connector_id="google",
            scopes=(SCOPE_GMAIL_READ, SCOPE_CALENDAR_READ, SCOPE_DRIVE_READ),
            reason="Read recent Gmail / Calendar / Drive entries on the user's behalf.",
        ),
        ConnectorRequirement(
            connector_id="mcp-github",
            scopes=(SCOPE_MCP_USE,),
            reason="Access the GitHub PAT to list the user's repositories.",
        ),
    ]

    def __init__(self, config: Optional[ConnectorsDemoAgentConfig] = None):
        config = config or ConnectorsDemoAgentConfig()
        self.config = config

        effective_model_id = config.model_id or "Qwen3.5-35B-A3B-GGUF"
        effective_base_url = (
            config.base_url
            if config.base_url is not None
            else os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")
        )

        self.response_mode = "conversational"
        super().__init__(
            base_url=effective_base_url,
            model_id=effective_model_id,
            max_steps=config.max_steps,
            streaming=config.streaming,
            show_stats=config.show_stats,
            silent_mode=config.silent_mode,
            debug=config.debug,
            output_dir=config.output_dir,
        )

    def _create_console(self) -> AgentConsole:
        return AgentConsole()

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def _register_tools(self) -> None:

        @tool
        def gmail_recent_subjects(limit: int = 5) -> str:
            """Return the most recent emails from the user's Gmail inbox.

            Args:
                limit: How many messages to return. Default 5; max 25.

            Returns:
                JSON string with either {"ok": true, "messages": [...]}
                listing each message's id/from/subject, or
                {"ok": false, "error": "..."} if the connector isn't
                connected, isn't granted, or the API call fails.
            """
            limit = max(1, min(int(limit or 5), 25))
            return json.dumps(_gmail_recent_subjects_impl(limit))

        @tool
        def calendar_today() -> str:
            """Return today's Google Calendar events on the user's primary calendar.

            Returns:
                JSON string with {"ok": true, "events": [...]} listing
                each event's summary/start/end/location, or an error
                envelope on failure.
            """
            return json.dumps(_calendar_today_impl())

        @tool
        def drive_recent_files(limit: int = 5) -> str:
            """Return the user's most recently modified Google Drive files.

            Args:
                limit: How many files to return. Default 5; max 25.

            Returns:
                JSON string with file metadata or an error envelope.
            """
            limit = max(1, min(int(limit or 5), 25))
            return json.dumps(_drive_recent_files_impl(limit))

        @tool
        def github_my_repos(limit: int = 10) -> str:
            """Return the user's most recently updated GitHub repositories.

            Args:
                limit: How many repos to return. Default 10; max 50.

            Returns:
                JSON string with repo metadata or an error envelope.
            """
            limit = max(1, min(int(limit or 10), 50))
            return json.dumps(_github_my_repos_impl(limit))

        # Snapshot: isolate this agent's tools from other agents in the
        # same process. Replaces the old _TOOL_REGISTRY.clear() pattern.
        self._snapshot_tools()
