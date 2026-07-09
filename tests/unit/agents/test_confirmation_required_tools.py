# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the per-agent confirmation-required tool mechanism (#1440).

The base ``Agent`` keeps only generic dangerous tools (shell / file
mutation) in the module-level ``TOOLS_REQUIRING_CONFIRMATION`` set. Each
agent declares its own destructive tools on the class attribute
``CONFIRMATION_REQUIRED_TOOLS``; ``Agent.confirmation_required_tools()``
merges the two (union), and ``_execute_tool`` gates on that merged set.
"""

from unittest.mock import patch

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION, Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import tool


class _DenyingConsole(AgentConsole):
    """Console whose confirmation prompt always denies — proves the gate fires
    without ever running the tool body."""

    def confirm_tool_execution(self, tool_name, tool_args):  # noqa: D401
        return False


class _BareAgent(Agent):
    """Agent that declares no extra confirmation tools."""

    def _get_system_prompt(self) -> str:
        return "bare"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        return AgentConsole()


class _GatedAgent(Agent):
    """Agent that declares an agent-specific gated tool."""

    CONFIRMATION_REQUIRED_TOOLS = frozenset({"launch_missiles"})

    def __init__(self, **kwargs):
        self._counter = {"n": 0}
        self._console_override = _DenyingConsole()
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "gated"

    def _create_console(self):
        return self._console_override

    def _register_tools(self) -> None:
        counter = self._counter

        @tool
        def launch_missiles(target: str) -> str:
            """Irreversible. Requires confirmation."""
            counter["n"] += 1
            return "FIRED"

        @tool
        def read_status() -> str:
            """Read-only; never gated."""
            return "OK"


def _make(cls):
    with patch("gaia.agents.base.agent.AgentSDK"):
        return cls(silent_mode=True, skip_lemonade=True)


class TestBaseDefault:
    def test_base_set_is_generic_only(self):
        """Email/calendar-specific names no longer live in the base set (#1440)."""
        for name in ("send_draft", "send_now", "quarantine_phishing_message"):
            assert name not in TOOLS_REQUIRING_CONFIRMATION
        for name in ("run_shell_command", "write_file", "edit_file"):
            assert name in TOOLS_REQUIRING_CONFIRMATION

    def test_bare_agent_confirmation_set_equals_base(self):
        """An agent that declares nothing gates exactly the base set."""
        assert Agent.confirmation_required_tools() == frozenset(
            TOOLS_REQUIRING_CONFIRMATION
        )
        assert _BareAgent.confirmation_required_tools() == frozenset(
            TOOLS_REQUIRING_CONFIRMATION
        )


class TestMerge:
    def test_declared_tools_are_merged_with_base(self):
        merged = _GatedAgent.confirmation_required_tools()
        assert "launch_missiles" in merged
        # The generic base tools are still present — the agent never re-lists them.
        assert TOOLS_REQUIRING_CONFIRMATION <= merged

    def test_subclass_declaration_does_not_mutate_base(self):
        """Declaring on a subclass must not leak into the shared base set."""
        assert "launch_missiles" not in TOOLS_REQUIRING_CONFIRMATION
        assert "launch_missiles" not in Agent.confirmation_required_tools()
        assert "launch_missiles" not in _BareAgent.confirmation_required_tools()


class TestExecuteToolGate:
    def test_declared_tool_is_gated_and_body_never_runs_on_denial(self):
        agent = _make(_GatedAgent)
        result = agent._execute_tool("launch_missiles", {"target": "moon"})
        assert result.get("status") == "denied"
        assert agent._counter["n"] == 0

    def test_undeclared_tool_is_not_gated(self):
        agent = _make(_GatedAgent)
        # read_status is not in the confirmation set — the denying console is
        # never consulted, so it executes normally.
        result = agent._execute_tool("read_status", {})
        assert result != {"status": "denied"}
