# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for GET /api/connectors/agent-mcps (#1020).

Coverage:
- Returns empty list when registry is not initialised
- Built-in agents (agent_dir=None) are skipped
- Custom agent with no mcp_servers.json is skipped
- Valid mcp_servers.json entries are returned, sorted (enabled first, alphabetical)
- Malformed JSON is skipped; other agents are still returned
- Non-dict mcpServers value is skipped
- Non-dict server entry is skipped (per-server guard)
- Disabled flag is correctly reflected
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal AgentRegistration stub so the test does not import the real module
# ---------------------------------------------------------------------------


@dataclass
class _Reg:
    id: str
    name: str
    source: str
    agent_dir: Optional[Path]
    hidden: bool = False
    models: List[str] = field(default_factory=list)
    conversation_starters: List[str] = field(default_factory=list)
    description: str = ""
    required_connections: List = field(default_factory=list)
    namespaced_agent_id: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(*regs: _Reg):
    """Return a duck-typed registry whose list() returns *regs*."""
    m = MagicMock()
    m.list.return_value = list(regs)
    return m


def _write_mcp_json(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(monkeypatch):
    """TestClient with a controllable app.state.agent_registry."""
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from gaia.ui.server import create_app

    app = create_app()
    client = TestClient(app)
    return client, app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentMcpsRoute:
    def test_no_registry_returns_empty(self, app_client):
        """When app.state has no agent_registry, return empty list."""
        client, app = app_client
        # Default TestClient has no agent_registry set.
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        assert resp.json() == {"agent_mcps": []}

    def test_builtin_agents_skipped(self, app_client, tmp_path):
        """Built-in agents (agent_dir=None) never appear in the output."""
        client, app = app_client
        builtin = _Reg(id="chat", name="Chat", source="builtin", agent_dir=None)
        app.state.agent_registry = _make_registry(builtin)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        assert resp.json()["agent_mcps"] == []

    def test_custom_agent_without_config_skipped(self, app_client, tmp_path):
        """Custom agent with no mcp_servers.json is silently skipped."""
        client, app = app_client
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        reg = _Reg(
            id="my-agent", name="My Agent", source="custom_python", agent_dir=agent_dir
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        assert resp.json()["agent_mcps"] == []

    def test_returns_servers_from_valid_config(self, app_client, tmp_path):
        """Servers from a well-formed mcp_servers.json are returned."""
        client, app = app_client
        agent_dir = tmp_path / "time-agent"
        agent_dir.mkdir()
        _write_mcp_json(
            agent_dir / "mcp_servers.json",
            {
                "mcpServers": {
                    "time": {
                        "command": "uvx",
                        "args": ["mcp-server-time"],
                    }
                }
            },
        )
        reg = _Reg(
            id="time-agent",
            name="Time Agent",
            source="custom_python",
            agent_dir=agent_dir,
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        data = resp.json()["agent_mcps"]
        assert len(data) == 1
        entry = data[0]
        assert entry["agent_id"] == "time-agent"
        assert entry["agent_name"] == "Time Agent"
        assert entry["server_name"] == "time"
        assert entry["command"] == "uvx"
        assert entry["args"] == ["mcp-server-time"]
        assert entry["disabled"] is False

    def test_disabled_flag_reflected(self, app_client, tmp_path):
        """Disabled flag in the JSON is surfaced in the response."""
        client, app = app_client
        agent_dir = tmp_path / "myagent"
        agent_dir.mkdir()
        _write_mcp_json(
            agent_dir / "mcp_servers.json",
            {
                "mcpServers": {
                    "paused": {
                        "command": "python",
                        "args": ["-m", "server"],
                        "disabled": True,
                    }
                }
            },
        )
        reg = _Reg(
            id="myagent", name="My Agent", source="custom_python", agent_dir=agent_dir
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        entry = resp.json()["agent_mcps"][0]
        assert entry["disabled"] is True

    def test_sort_enabled_first_then_alphabetical(self, app_client, tmp_path):
        """Enabled servers come before disabled; each group is alphabetical."""
        client, app = app_client
        agent_dir = tmp_path / "multi"
        agent_dir.mkdir()
        _write_mcp_json(
            agent_dir / "mcp_servers.json",
            {
                "mcpServers": {
                    "zebra": {"command": "z", "args": [], "disabled": False},
                    "alpha": {"command": "a", "args": [], "disabled": True},
                    "beta": {"command": "b", "args": [], "disabled": False},
                    "omega": {"command": "o", "args": [], "disabled": True},
                }
            },
        )
        reg = _Reg(
            id="multi", name="Multi", source="custom_python", agent_dir=agent_dir
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        names = [e["server_name"] for e in resp.json()["agent_mcps"]]
        # Enabled (beta, zebra) alphabetical first, then disabled (alpha, omega) alphabetical.
        assert names == ["beta", "zebra", "alpha", "omega"]

    def test_malformed_json_skipped_other_agents_returned(self, app_client, tmp_path):
        """Bad JSON for one agent does not prevent other agents from appearing."""
        client, app = app_client

        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "mcp_servers.json").write_text("NOT JSON {{{", encoding="utf-8")

        good_dir = tmp_path / "good"
        good_dir.mkdir()
        _write_mcp_json(
            good_dir / "mcp_servers.json",
            {"mcpServers": {"srv": {"command": "ok", "args": []}}},
        )

        app.state.agent_registry = _make_registry(
            _Reg(id="bad", name="Bad", source="custom_python", agent_dir=bad_dir),
            _Reg(id="good", name="Good", source="custom_python", agent_dir=good_dir),
        )
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        data = resp.json()["agent_mcps"]
        assert len(data) == 1
        assert data[0]["agent_id"] == "good"

    def test_non_dict_mcp_servers_value_skipped(self, app_client, tmp_path):
        """If mcpServers is not a dict (e.g. null), the whole file is skipped."""
        client, app = app_client
        agent_dir = tmp_path / "null-mcp"
        agent_dir.mkdir()
        _write_mcp_json(
            agent_dir / "mcp_servers.json",
            {"mcpServers": None},
        )
        reg = _Reg(
            id="null-mcp", name="NullMCP", source="custom_python", agent_dir=agent_dir
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        assert resp.json()["agent_mcps"] == []

    def test_non_dict_server_entry_skipped(self, app_client, tmp_path):
        """A non-object server value is skipped; other servers in the same file pass."""
        client, app = app_client
        agent_dir = tmp_path / "mixed"
        agent_dir.mkdir()
        _write_mcp_json(
            agent_dir / "mcp_servers.json",
            {
                "mcpServers": {
                    "bad-entry": "should be an object",
                    "good-entry": {"command": "ok", "args": []},
                }
            },
        )
        reg = _Reg(
            id="mixed", name="Mixed", source="custom_python", agent_dir=agent_dir
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        data = resp.json()["agent_mcps"]
        assert len(data) == 1
        assert data[0]["server_name"] == "good-entry"

    def test_legacy_servers_key_also_accepted(self, app_client, tmp_path):
        """Files using the legacy 'servers' key (without 'mcpServers') are read."""
        client, app = app_client
        agent_dir = tmp_path / "legacy"
        agent_dir.mkdir()
        _write_mcp_json(
            agent_dir / "mcp_servers.json",
            {"servers": {"legacy-srv": {"command": "old", "args": []}}},
        )
        reg = _Reg(
            id="legacy", name="Legacy", source="custom_python", agent_dir=agent_dir
        )
        app.state.agent_registry = _make_registry(reg)
        resp = client.get("/api/connectors/agent-mcps")
        assert resp.status_code == 200
        data = resp.json()["agent_mcps"]
        assert len(data) == 1
        assert data[0]["server_name"] == "legacy-srv"
