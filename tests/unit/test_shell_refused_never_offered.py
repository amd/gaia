# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A refused command reads as refused, never as waiting for approval.

The agent relays the tool's words. When ``gh issue close`` came back as "needs
your approval", the agent told the user to approve a close GAIA never runs
(``shell_denial_reported_as_denial``). These drive the real
``Agent._execute_tool`` through the real ``SSEOutputHandler`` and check that
an irreversible command raises no prompt, on any transport, and that what the
model reads says nothing can approve it.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.denied_effects import DeniedEffects
from gaia.agents.tools.shell_tools import TIER_REFUSE, ShellToolsMixin
from gaia.skills.binaries import BinaryGrants
from gaia.ui.sse_handler import SSEOutputHandler

#: Commands that end, land or delete someone else's work. Never offered.
IRREVERSIBLE_GH = [
    "gh issue close 137 --repo acme-labs/widgetworks",
    "gh pr merge 12 --repo acme-labs/widgetworks",
    "gh pr close 12 --repo acme-labs/widgetworks",
    "gh label delete bug --repo acme-labs/widgetworks",
    "gh repo delete acme-labs/widgetworks",
    "gh release delete v1 --repo acme-labs/widgetworks",
    "gh api -X DELETE repos/acme-labs/widgetworks",
]


class _ShellAgent(Agent, ShellToolsMixin):
    """The flagship's shell tool on the base agent, and nothing else."""

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_shell_tools()

    def _create_console(self):
        return self._console_override


def _agent(console, *granted: str) -> _ShellAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        _ShellAgent._console_override = console
        agent = _ShellAgent(silent_mode=True, skip_lemonade=True)
    agent.max_commands_per_minute = 1000
    agent.max_commands_per_10_seconds = 1000
    agent._denied_effects = DeniedEffects(os.getcwd())
    agent._granted_binaries = BinaryGrants()
    for binary in granted:
        agent._granted_binaries.grant(binary, skill_name="github-triage")
    return agent


def _drive(agent, handler, command: str, cwd) -> tuple:
    """Run one shell call on a worker; return (result, events). Answers nothing."""
    outcome = {}

    def call():
        outcome["result"] = agent._execute_tool(
            "run_shell_command", {"command": command, "working_directory": str(cwd)}
        )

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    events = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            events.append(handler.event_queue.get(timeout=0.05))
        except queue.Empty:
            if not worker.is_alive():
                break
    worker.join(timeout=5)
    assert not worker.is_alive(), "the call never finished"
    return outcome["result"], events


def _prompts(events) -> list:
    return [e for e in events if e.get("type") == "permission_request"]


def _assert_reads_as_refused(result: dict) -> None:
    """What the model reads must rule out approval and point the user at doing it."""
    assert result["status"] == "error"
    assert result["tier"] == TIER_REFUSE
    assert result["executed"] is False
    text = f"{result['error']} {result.get('hint', '')}"
    assert "needs your approval" not in text
    assert "approves this exact command" not in text
    assert "Nothing ran" in text
    assert "no approval can change that" in text
    assert "run it themselves" in text


@pytest.fixture(name="gh_on_path")
def _gh_on_path(monkeypatch):
    monkeypatch.setattr(
        "gaia.agents.tools.shell_tools.shutil.which", lambda name: f"/usr/bin/{name}"
    )


@pytest.mark.parametrize("command", IRREVERSIBLE_GH)
@pytest.mark.parametrize("granted", [("gh",), ()], ids=["granted", "ungranted"])
def test_an_irreversible_gh_command_is_refused_without_a_prompt(
    command, granted, tmp_path, gh_on_path
):
    handler = SSEOutputHandler()

    result, events = _drive(_agent(handler, *granted), handler, command, tmp_path)

    assert _prompts(events) == []
    _assert_reads_as_refused(result)


def test_an_unattended_run_reports_the_close_as_refused_not_pending(
    tmp_path, gh_on_path, monkeypatch
):
    """The eval transport auto-approves. A pending-approval sentence there is the
    one the agent repeated to the user."""
    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: True
    )
    handler = SSEOutputHandler()

    result, events = _drive(
        _agent(handler, "gh"),
        handler,
        "gh issue close 137 --repo acme-labs/widgetworks",
        tmp_path,
    )

    assert _prompts(events) == []
    _assert_reads_as_refused(result)
    assert "not permitted" in result["error"]


def test_a_refusal_outside_gh_also_rules_out_approval(tmp_path):
    """The wording belongs to the tier, not to one CLI."""
    handler = SSEOutputHandler()

    result, events = _drive(
        _agent(handler), handler, "git push --force origin main", tmp_path
    )

    assert _prompts(events) == []
    assert result["tier"] == TIER_REFUSE
    assert "no approval can change that" in result["hint"]


def test_a_command_the_user_may_approve_once_still_asks(tmp_path, gh_on_path):
    """Tightening the irreversible set must not turn the one-time tier into a no."""
    handler = SSEOutputHandler()
    agent = _agent(handler, "gh")
    seen = []

    def deny(event):
        seen.append(event)
        handler.resolve_tool_confirmation(
            approved=False, always=False, confirm_id=event["confirm_id"]
        )

    outcome = {}

    def call():
        outcome["result"] = agent._execute_tool(
            "run_shell_command",
            {"command": "gh issue reopen 137", "working_directory": str(tmp_path)},
        )

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    deadline = time.monotonic() + 30
    while worker.is_alive() and time.monotonic() < deadline:
        try:
            event = handler.event_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        if event.get("type") == "permission_request":
            deny(event)
    worker.join(timeout=5)

    assert len(seen) == 1
    assert outcome["result"]["status"] == "denied"
