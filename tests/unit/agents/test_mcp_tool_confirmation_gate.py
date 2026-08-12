# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests: MCP tools must pass through the confirmation gate.

Reported externally as a confirmation-gate bypass. ``_execute_tool`` used to
gate on a static set of tool-name strings only. MCP tools are registered under
server-chosen names (``mcp_<server>_<tool>``), so no MCP tool could ever match
the set — every MCP write and destructive tool executed with zero user
confirmation. Combined with GAIA's document/email/web ingestion, that made
prompt injection a direct path to an unconfirmed write.

These tests drive the real ``Agent._execute_tool`` against entries produced by
the real ``MCPTool.to_gaia_format``, with a console that always denies. A
denied gate means the tool body must never run.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.mcp.client.mcp_client import MCPTool


class _RecordingDenyConsole(AgentConsole):
    """Records every confirmation request and denies it."""

    blocking_confirmation = True

    def __init__(self):
        super().__init__()
        self.requested = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.requested.append(tool_name)
        return False


class _Probe(Agent):
    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        pass

    def _create_console(self):
        return self._console_override


@pytest.fixture
def agent():
    console = _RecordingDenyConsole()

    with patch("gaia.agents.base.agent.AgentSDK"):
        _Probe._console_override = console
        a = _Probe(silent_mode=True, skip_lemonade=True)
    a._instance_tools = dict(a._tools_registry)
    a.executed = []
    return a


def _register_mcp(agent, name, annotations=None, server="filesystem"):
    """Register an MCP tool exactly as ``_register_mcp_tools`` would."""
    entry = MCPTool(
        name=name,
        description=f"{name} via MCP",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path"],
        },
        annotations=annotations or {},
    ).to_gaia_format(server, server)
    entry["function"] = lambda **kwargs: agent.executed.append(kwargs) or {
        "status": "success"
    }
    agent._instance_tools[entry["name"]] = entry
    return entry["name"]


class TestMCPWriteToolsAreGated:
    def test_mcp_write_tool_is_denied_and_never_executes(self, agent):
        name = _register_mcp(agent, "write_file")

        result = agent._execute_tool(
            name, {"path": "/tmp/pwned", "content": "attacker-controlled"}
        )

        assert result["status"] == "denied"
        assert agent.executed == []
        assert agent.console.requested == [name]

    @pytest.mark.parametrize(
        "server,tool",
        [
            ("github", "create_or_update_file"),
            ("github", "push_files"),
            ("github", "delete_file"),
            ("github", "create_repository"),
            ("filesystem", "write_file"),
            ("filesystem", "move_file"),
            ("desktopcommander", "execute_command"),
        ],
    )
    def test_catalogued_server_write_tools_are_gated(self, agent, server, tool):
        name = _register_mcp(agent, tool, server=server)
        assert agent._execute_tool(name, {"path": "/tmp/x"})["status"] == "denied"
        assert agent.executed == []

    def test_unannotated_tool_is_gated_even_if_name_looks_harmless(self, agent):
        """Fail closed: a server that ships no annotations gets no benefit of
        the doubt, whatever its tools are called."""
        name = _register_mcp(agent, "do_the_thing")
        assert agent._execute_tool(name, {"path": "/tmp/x"})["status"] == "denied"
        assert agent.executed == []

    def test_lying_read_only_hint_does_not_open_the_gate(self, agent):
        name = _register_mcp(agent, "delete_file", {"readOnlyHint": True})
        assert agent._execute_tool(name, {"path": "/tmp/x"})["status"] == "denied"
        assert agent.executed == []

    def test_mcp_entry_without_flag_fails_closed(self, agent):
        """Defence in depth: a registration path that forgets to classify its
        tools must not silently leave the gate open."""
        agent._instance_tools["mcp_rogue_write_file"] = {
            "name": "mcp_rogue_write_file",
            "description": "no requires_confirmation key",
            "parameters": {},
            "function": lambda **kw: agent.executed.append(kw),
        }
        result = agent._execute_tool("mcp_rogue_write_file", {})
        assert result["status"] == "denied"
        assert agent.executed == []


class TestReadOnlyMCPToolsAreNotGated:
    def test_declared_read_only_tool_runs_without_confirmation(self, agent):
        name = _register_mcp(agent, "get_current_time", {"readOnlyHint": True}, "time")

        result = agent._execute_tool(name, {})

        assert result["status"] == "success"
        assert agent.console.requested == []
        assert agent.executed == [{}]


class TestUnattendedRunsDeny:
    """Deliberate policy: unattended means nobody can approve, so fail closed.

    ``SSEOutputHandler(background_mode=True)`` denies immediately rather than
    blocking forever on a permission modal no human will see. The consequence is
    intended but has real reach — autonomy-loop and scheduled goals lose every
    MCP tool from a server that ships no annotations, which is most of them.
    Pinned here so the trade-off is a decision, not an accident.
    """

    def test_background_mode_denies_unannotated_mcp_tool(self, agent, monkeypatch):
        from gaia.ui.sse_handler import SSEOutputHandler

        emitted = []
        handler = SSEOutputHandler.__new__(SSEOutputHandler)
        handler.background_mode = True
        monkeypatch.setattr(handler, "_emit", emitted.append, raising=False)

        agent.console = handler
        name = _register_mcp(agent, "write_file")

        result = agent._execute_tool(name, {"path": "/tmp/x", "content": "boom"})

        assert result["status"] == "denied"
        assert agent.executed == []
        # The denial must be observable and actionable, never silent.
        assert emitted and emitted[0]["type"] == "tool_confirm_denied"
        assert emitted[0]["reason"] == "unattended"

    def test_background_mode_still_allows_declared_read_only_tool(
        self, agent, monkeypatch
    ):
        """Read-only MCP tools keep working unattended — the capability loss is
        scoped to tools the server never proved safe."""
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler.__new__(SSEOutputHandler)
        handler.background_mode = True
        monkeypatch.setattr(handler, "_emit", lambda _e: None, raising=False)

        agent.console = handler
        name = _register_mcp(agent, "get_current_time", {"readOnlyHint": True}, "time")

        assert agent._execute_tool(name, {})["status"] == "success"


class TestUnprefixedAliasCannotBypass:
    def test_alias_resolution_happens_before_the_gate(self, agent):
        """Local models often drop the ``mcp_<server>_`` prefix. Resolution must
        run first, or calling the bare name would skip the gate."""
        _register_mcp(agent, "write_file", server="acme")

        result = agent._execute_tool(
            "write_file", {"path": "/tmp/x", "content": "boom"}
        )

        assert result["status"] == "denied"
        assert agent.executed == []


class TestNativeToolsStillGated:
    def test_static_set_behaviour_is_unchanged(self, agent):
        agent._instance_tools["write_file"] = {
            "name": "write_file",
            "description": "native write",
            "parameters": {"path": {"type": "string", "required": True}},
            "function": lambda path: agent.executed.append(path),
        }
        assert agent._execute_tool("write_file", {"path": "/tmp/x"})["status"] == (
            "denied"
        )
        assert agent.executed == []
