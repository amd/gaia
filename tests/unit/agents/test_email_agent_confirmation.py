# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests that destructive tools are correctly registered for confirmation
gating, and that draft/reply/forward tools record proper threading
metadata.

The actual SSE confirmation flow is exercised in
``tests/integration/test_email_router_confirmation_flow.py``; here we
verify the agent-side contracts at unit level.
"""

from __future__ import annotations

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102).
pytest.importorskip("gaia_agent_email")
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402


def _no_terminal(monkeypatch) -> None:
    """No interactive terminal — piped stdin, CI, or a subprocess host."""
    monkeypatch.setattr(
        "gaia.agents.base.console.terminal_is_interactive", lambda: False
    )


def _at_a_terminal(monkeypatch, *answers: str) -> None:
    """A user sitting at a terminal, answering the prompt in order."""
    monkeypatch.setattr(
        "gaia.agents.base.console.terminal_is_interactive", lambda: True
    )
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt="": queue.pop(0))


def _clear_env_opt_in(monkeypatch) -> None:
    import gaia
    from gaia.agents.base.console import AUTO_APPROVE_ENV_VAR

    monkeypatch.delenv(AUTO_APPROVE_ENV_VAR, raising=False)
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, AUTO_APPROVE_ENV_VAR, raising=False)


class TestConfirmationGatingAtBaseLevel:
    """The EmailTriageAgent's confirmation set (its own
    ``CONFIRMATION_REQUIRED_TOOLS`` merged with the generic base set via
    ``confirmation_required_tools()`` — #1440) must list every email tool
    that has external side effects.
    """

    @pytest.mark.parametrize(
        "tool_name",
        [
            "send_draft",
            "send_now",
            "schedule_send",
            "forward_message",
            "accept_invite",
            "decline_invite",
            "create_event_from_email",
        ],
    )
    def test_destructive_tool_is_gated(self, tool_name):
        assert tool_name in EmailTriageAgent.confirmation_required_tools()

    def test_permanent_delete_is_not_registered_or_gated(self):
        """#2533: permanent_delete is gone outright, not merely ungated."""
        assert "permanent_delete" not in EmailTriageAgent.confirmation_required_tools()

    @pytest.mark.parametrize(
        "tool_name",
        [
            "list_inbox",
            "get_message",
            "get_thread",
            "search_messages",
            "list_labels",
            "triage_inbox",
            # Reversible-via-undo organize tools — NOT confirmation-gated.
            "archive_message",
            "mark_read",
            "mark_unread",
            "add_star",
            "remove_star",
            "label_message",
            "move_to_label",
            # Reversible-within-window soft-delete — NOT confirmation-gated.
            # The user can ``restore_message`` instead.
            "trash_message",
            # Drafting is harmless; only sending requires confirmation.
            "draft_reply",
            "draft_forward",
            # restore_message is the undo-window path — never gated.
            "restore_message",
            # restore_trashed_message / search_trash (#2523) — the
            # state-reconciling restore path and its lookup tool. Both
            # reversible/read-only, same as restore_message and
            # search_messages above — never gated.
            "restore_trashed_message",
            "search_trash",
        ],
    )
    def test_safe_tool_is_NOT_gated(self, tool_name):
        assert tool_name not in EmailTriageAgent.confirmation_required_tools()


class TestGatedEmailToolsCannotRunUnprompted:
    """The gate must hold on the CLI/subprocess path too (#2210).

    Before the fix, ``AgentConsole`` inherited an ``OutputHandler`` default that
    approved everything, so ``gaia email -i`` (and any subprocess host) executed
    send / forward / permanent-delete / RSVP with no prompt at all. These tests
    drive the REAL email gate set through the REAL default CLI console with a
    non-interactive stdin — the exact shape of a piped or daemon-hosted run.
    """

    @staticmethod
    def _probe_agent(console):
        """A real ``Agent`` carrying the email agent's own gate set.

        ``EmailTriageAgent`` needs live mail/calendar backends to construct, so
        the probe borrows its ``CONFIRMATION_REQUIRED_TOOLS`` verbatim and runs
        the identical inherited ``Agent._execute_tool`` gate (asserted below).
        """
        from unittest.mock import patch

        from gaia.agents.base.agent import Agent
        from gaia.agents.base.tools import tool

        class _EmailProbeAgent(Agent):
            AGENT_ID = "email-confirmation-probe"
            CONFIRMATION_REQUIRED_TOOLS = EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS

            def __init__(self, **kwargs):
                self.executed = []
                self._console_override = console
                super().__init__(**kwargs)

            def _create_console(self):
                return self._console_override

            def _get_system_prompt(self):
                return "email confirmation probe"

            def _register_tools(self):
                executed = self.executed

                for name in sorted(EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS):

                    def _make(tool_name):
                        def _fn(**kwargs):
                            executed.append(tool_name)
                            return "EXECUTED"

                        _fn.__name__ = tool_name
                        _fn.__doc__ = f"Stand-in for the gated {tool_name} tool."
                        return _fn

                    tool(_make(name))

        with patch("gaia.agents.base.agent.AgentSDK"):
            return _EmailProbeAgent(silent_mode=True, skip_lemonade=True)

    def test_email_agent_uses_the_inherited_gate_and_the_cli_console(self):
        """Pins the two assumptions the probe rests on."""
        import typing

        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import AgentConsole

        assert EmailTriageAgent._execute_tool is Agent._execute_tool
        hints = typing.get_type_hints(EmailTriageAgent._create_console)
        assert hints["return"] is AgentConsole

    def test_send_draft_is_denied_without_a_terminal(self, monkeypatch):
        from gaia.agents.base.console import AUTO_APPROVE_ENV_VAR, AgentConsole

        _clear_env_opt_in(monkeypatch)
        _no_terminal(monkeypatch)

        agent = self._probe_agent(AgentConsole())
        result = agent._execute_tool("send_draft", {"draft_id": "d1", "confirm": True})

        assert result["status"] == "denied"
        assert agent.executed == [], "send_draft ran without any user approval"
        assert AUTO_APPROVE_ENV_VAR in result["error"]

    @pytest.mark.parametrize(
        "tool_name", sorted(EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS)
    )
    def test_every_gated_email_tool_is_denied_without_a_terminal(
        self, tool_name, monkeypatch
    ):
        from gaia.agents.base.console import AgentConsole

        _clear_env_opt_in(monkeypatch)
        _no_terminal(monkeypatch)

        agent = self._probe_agent(AgentConsole())
        result = agent._execute_tool(tool_name, {})

        assert result["status"] == "denied"
        assert agent.executed == []

    def test_interactive_no_denies_and_yes_approves(self, monkeypatch):
        from gaia.agents.base.console import AgentConsole

        _clear_env_opt_in(monkeypatch)
        _at_a_terminal(monkeypatch, "n", "y")

        agent = self._probe_agent(AgentConsole())
        denied = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert denied["status"] == "denied"
        assert agent.executed == []

        approved = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert approved == "EXECUTED"
        assert agent.executed == ["send_now"]
