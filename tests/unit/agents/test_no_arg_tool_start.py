# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool called with no arguments is announced before it runs, not after.

The canonical translator holds ``tool_start`` until the call's arguments arrive,
so the loop must say "these are the arguments" even when there are none.
Otherwise the TUI, the Agent UI and the messaging bridge see nothing while the
tool runs, and the bridge's idle clock counts the running tool as silence.

Each test drives the real agent loop with a scripted model, and inside the
tool's body checks what the wire has carried so far.
"""

from __future__ import annotations

import json
import queue
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


class _Probe(Agent):
    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        agent = self

        @tool
        def check_status() -> dict:
            """Report the status. Takes no arguments."""
            agent.wire_during_tool.append(agent.drain_wire())
            return {"status": "success", "state": "ok"}


@pytest.fixture(autouse=True)
def _no_server_lookups(monkeypatch):
    # A pinned window and an unregistered model: sizing asks no server.
    monkeypatch.setenv("GAIA_CTX_SIZE", "32768")


def _agent():
    handler = SSEOutputHandler()
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _Probe(silent_mode=True, skip_lemonade=True, output_handler=handler)
    agent.streaming = False
    agent.model_id = "probe-model"
    translator = CanonicalTranslator(run_id=None, agent_id="probe")
    wire: list = []

    def drain_wire():
        while True:
            try:
                event = handler.event_queue.get_nowait()
            except queue.Empty:
                return list(wire)
            if event is not None:
                wire.extend(translator.translate(event))

    agent.drain_wire = drain_wire
    agent.wire_during_tool = []
    return agent


def _script(agent, *responses):
    pending = list(responses)

    def _send(messages, *_, **__):
        if not pending:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = pending.pop(0)
        resp.stats = {}
        return resp

    chat = MagicMock()
    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat


_ANSWER = json.dumps({"thought": "done", "answer": "All good."})


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            json.dumps({"thought": "check", "tool": "check_status", "tool_args": {}}),
            id="json-call",
        ),
        pytest.param(
            json.dumps({"thought": "check", "tool": "check_status"}),
            id="json-call-without-tool_args",
        ),
        pytest.param(
            json.dumps(
                {
                    "__tool_calls__": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "check_status", "arguments": "{}"},
                        }
                    ]
                }
            ),
            id="native-call",
        ),
        pytest.param(
            json.dumps(
                {
                    "thought": "plan it",
                    "goal": "check",
                    "plan": [
                        {"tool": "check_status", "tool_args": {}},
                        {"tool": "check_status", "tool_args": {}},
                    ],
                    "tool": "check_status",
                    "tool_args": {},
                }
            ),
            id="plan-step",
        ),
    ],
)
def test_a_no_argument_tool_is_on_the_wire_while_it_runs(call):
    agent = _agent()
    _script(agent, call, _ANSWER, _ANSWER, _ANSWER)

    agent.process_query("is everything ok?")

    assert agent.wire_during_tool, "the tool never ran"
    for run, wire in enumerate(agent.wire_during_tool, start=1):
        calls = [e for e in wire if e["type"] == "tool_call"]
        # Every earlier run was flushed by its own result; this run's call must
        # already be out too, while its body is still executing.
        assert len(calls) == run, (
            f"run {run} of check_status was not on the wire while it ran: "
            f"{[e['type'] for e in wire]}"
        )
        assert calls[-1]["tool"] == "check_status"
        assert calls[-1]["args"] == {}


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


@pytest.mark.parametrize("args", [{}, None], ids=["empty", "none"])
def test_empty_arguments_are_a_tool_args_event_not_a_result(args):
    handler = SSEOutputHandler()
    handler.print_tool_usage("list_directory")
    handler.pretty_print_json(args, "Arguments")

    events = _drain(handler)
    assert [e["type"] for e in events] == ["tool_start", "tool_args"]
    assert events[1]["args"] == {}
    assert events[1]["tool"] == "list_directory"
    # The step keeps its label rather than being blanked by an empty detail.
    assert events[1]["detail"] == events[0]["detail"] != ""

    translator = CanonicalTranslator(run_id=None, agent_id="probe")
    assert translator.translate(events[0]) == []
    [call] = translator.translate(events[1])
    assert call["type"] == "tool_call"
    assert (call["tool"], call["args"]) == ("list_directory", {})


@pytest.mark.parametrize("args", [{}, None], ids=["empty", "none"])
def test_the_terminal_shows_no_panel_for_a_call_without_arguments(args):
    from gaia.agents.base.console import AgentConsole

    console = AgentConsole()
    console.console = MagicMock()

    console.pretty_print_json(args, "Arguments")

    console.console.print.assert_not_called()
