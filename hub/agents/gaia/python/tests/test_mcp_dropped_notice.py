# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Configured MCP servers that can't be offered are named to the user (#7074).

Each way the flagship drops its MCP servers — the client can't be built, the
servers can't be loaded, or their tools pass the tool limit — leaves a notice
that the next turn shows through the console, while the agent keeps running.
"""

from unittest.mock import MagicMock, patch

import pytest
from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.console import SilentConsole
from gaia.mcp.client.mcp_client import MCPTool


class _Console:
    def __init__(self):
        self.warnings = []

    def print_warning(self, message):
        self.warnings.append(message)

    def print_info(self, message):
        pass


class _Client:
    name = prefix = "fake"

    def __init__(self, n_tools):
        # Real MCPTools: the agent converts each one before counting it.
        self._tools = [
            MCPTool(name=f"t{i}", description="d", input_schema={})
            for i in range(n_tools)
        ]

    def list_tools(self):
        return self._tools


class _Manager:
    """The slice of MCPClientManager that ``_load_mcp_tools`` uses."""

    def __init__(self, servers, load_error=None, failed=None):
        self._clients = {name: _Client(n) for name, n in servers.items()}
        self._load_error = load_error
        self._failed = dict(failed or {})
        self.config = MagicMock(load_report={})
        self.config.get_servers.return_value = dict.fromkeys(servers, {})

    def load_from_config(self):
        if self._load_error:
            raise self._load_error

    def list_servers(self):
        return list(self._clients)

    def servers_for_agent(self, _agent_id):
        return list(self._clients)

    def get_client(self, name):
        return self._clients.get(name)

    def failed_servers_for_agent(self, _agent_id):
        return dict(self._failed)


@pytest.fixture
def configured_home(tmp_path):
    (tmp_path / "mcp_servers.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    with patch("gaia_agent.chat_agent.gaia_home", return_value=tmp_path):
        yield tmp_path


def _skeleton(manager, console=None, tool_limit=50):
    agent = ChatAgent.__new__(ChatAgent)
    agent.config = ChatAgentConfig(mcp_tool_limit=tool_limit)
    agent._mcp_manager = manager
    agent.console = console or _Console()
    return agent


def test_manager_construction_failure_reaches_the_user(configured_home):
    """A corrupt config breaks the MCP client; __init__ carries on and says so."""
    built = []

    def stop_at_agent_init(self, *args, **kwargs):
        built.append(self)
        raise RuntimeError("stop after MCP setup")

    corrupt = RuntimeError("mcp_servers.json at /x is corrupt: Expecting value")
    with (
        patch("gaia.mcp.client.config.MCPConfig", side_effect=corrupt),
        patch("gaia.agents.base.agent.Agent.__init__", stop_at_agent_init),
    ):
        with pytest.raises(RuntimeError, match="stop after MCP setup"):
            ChatAgent(ChatAgentConfig(silent_mode=True))

    agent = built[0]
    assert agent._mcp_manager is None
    agent.console = _Console()
    agent._load_mcp_tools()
    assert agent.report_mcp_dropped() is True
    [warning] = agent.console.warnings
    assert "MCP servers are unavailable" in warning
    assert "is corrupt" in warning


def test_config_load_failure_names_the_servers(configured_home):
    manager = _Manager({"github": 3, "slack": 2}, load_error=OSError("pipe closed"))
    agent = _skeleton(manager)

    agent._load_mcp_tools()  # must not raise
    agent.report_mcp_dropped()

    [warning] = agent.console.warnings
    assert "github, slack" in warning
    assert "pipe closed" in warning


def test_tool_limit_skip_names_each_server_and_count(configured_home):
    manager = _Manager({"github": 30, "slack": 25})
    agent = _skeleton(manager, tool_limit=50)

    with patch.object(ChatAgent, "_register_mcp_tools") as register:
        agent._load_mcp_tools()
    register.assert_not_called()
    agent.report_mcp_dropped()

    [warning] = agent.console.warnings
    assert "github (30 tools), slack (25 tools)" in warning
    assert "55 tools, over the limit of 50" in warning
    assert "Settings > Connectors" in warning
    assert "gaia " not in warning  # no CLI command as the remedy


def test_a_server_that_fails_to_start_is_named(configured_home):
    """The rest still load; the one that didn't start is named with why."""
    manager = _Manager(
        {"github": 3},
        failed={"weatherbox": "[WinError 2] The system cannot find the file"},
    )
    agent = _skeleton(manager)
    with patch.object(ChatAgent, "_register_mcp_tools") as register:
        agent._load_mcp_tools()
    register.assert_called_once()
    assert agent.report_mcp_dropped() is True

    [warning] = agent.console.warnings
    assert "weatherbox" in warning
    assert "cannot find the file" in warning
    assert "github" not in warning
    # A hand-added server isn't listed in Settings > Connectors: name its file.
    assert "Settings > Connectors" not in warning
    assert f"weatherbox is set up in {configured_home / 'mcp_servers.json'}" in warning


def test_a_failed_connector_points_at_settings_and_a_hand_added_one_at_its_file(
    configured_home,
):
    manager = _Manager(
        {},
        failed={"mcp-github": "exited with code 1", "weatherbox": "no such command"},
    )
    agent = _skeleton(manager)
    agent._load_mcp_tools()
    agent.report_mcp_dropped()

    [warning] = agent.console.warnings
    assert "Check GitHub in Settings > Connectors." in warning
    assert f"weatherbox is set up in {configured_home / 'mcp_servers.json'}" in warning


def test_servers_within_the_limit_load_without_a_notice(configured_home):
    agent = _skeleton(_Manager({"github": 3}))
    with patch.object(ChatAgent, "_register_mcp_tools") as register:
        agent._load_mcp_tools()
    register.assert_called_once()
    assert agent.report_mcp_dropped() is False
    assert agent.console.warnings == []


def test_notice_is_shown_once_and_not_through_a_silent_console(configured_home):
    """Tools re-register every turn; the same problem is reported once."""
    agent = _skeleton(_Manager({"a": 60}), console=SilentConsole())
    agent._load_mcp_tools()
    assert agent.report_mcp_dropped() is False  # silent console: not spent

    agent.console = _Console()
    assert agent.report_mcp_dropped() is True
    with patch.object(ChatAgent, "_register_mcp_tools"):
        agent._load_mcp_tools()
    assert agent.report_mcp_dropped() is False
    assert len(agent.console.warnings) == 1


def test_no_config_file_means_no_notice(tmp_path):
    with patch("gaia_agent.chat_agent.gaia_home", return_value=tmp_path):
        agent = _skeleton(None)
        agent._mcp_unavailable_error = "boom"
        agent._load_mcp_tools()
    assert agent.report_mcp_dropped() is False


def test_process_query_reports_before_the_turn(configured_home):
    agent = _skeleton(_Manager({"a": 60}))
    agent._load_mcp_tools()
    with patch(
        "gaia.agents.base.memory.MemoryMixin.process_query", return_value="ok"
    ) as turn:
        assert agent.process_query("hi") == "ok"
    turn.assert_called_once_with("hi")
    assert len(agent.console.warnings) == 1
