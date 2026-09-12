# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mid-turn follow-ups (#3620).

A local model takes 60-120s per turn, and an agentic turn with tools runs for
minutes. Before this, anything the user typed in that window sat in the TUI's
own queue until the whole turn finished — so a correction ("actually, only the
unread ones") arrived after the work it was meant to redirect was already done.

The agent now drains a per-run queue at its step boundary, beside the cancel
check, and the text joins the turn's context as a user message before the next
model call. These tests pin the three things that make that safe:

* it is folded in at the boundary, so nothing is half-applied;
* it is LABELLED as arriving mid-task, or the model reads it as a replacement
  request and abandons the work already done;
* an agent with no queue wired (every direct/CLI/MCP caller) is untouched.
"""

import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent — same pattern as test_cancel_midstream."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
    a.console = MagicMock()
    return a


def test_no_queue_wired_is_a_no_op(agent):
    """Every direct caller (CLI, MCP, a test) runs without one."""
    messages, conversation = [], []

    assert agent._drain_followups(messages, conversation) == 0
    assert messages == [] and conversation == []
    # And nothing can be handed to an agent that has no run to take it.
    assert agent.queue_followup("anything") is False


def test_a_queued_followup_joins_the_turn_as_the_user_speaking(agent):
    agent._followup_queue = queue.Queue()
    assert agent.queue_followup("only the unread ones") is True

    messages, conversation = [], []
    assert agent._drain_followups(messages, conversation) == 1

    assert len(messages) == 1 and len(conversation) == 1
    assert messages[0]["role"] == "user"
    assert "only the unread ones" in messages[0]["content"]
    # Both lists, not just the one sent to the model: the conversation record is
    # what a later turn and the turn log read back.
    assert conversation[0] == messages[0]


def test_the_followup_says_it_arrived_mid_task(agent):
    """Unlabelled, the model reads it as "instead of that" and drops the work.

    The wording is the whole safety property here — a bare user message in the
    middle of a tool sequence is indistinguishable from a new request.
    """
    agent._followup_queue = queue.Queue()
    agent.queue_followup("also check my calendar")

    messages = []
    agent._drain_followups(messages, [])

    content = messages[0]["content"]
    assert content.startswith(agent.FOLLOWUP_PREAMBLE)
    assert "Finish that request first" in content


def test_several_followups_keep_the_order_they_were_typed_in(agent):
    agent._followup_queue = queue.Queue()
    for text in ("first", "second", "third"):
        agent.queue_followup(text)

    messages = []
    assert agent._drain_followups(messages, []) == 3
    assert [m["content"].replace(agent.FOLLOWUP_PREAMBLE, "") for m in messages] == [
        "first",
        "second",
        "third",
    ]


def test_the_user_is_told_their_message_landed(agent):
    """Silence here is indistinguishable from the message being dropped."""
    agent._followup_queue = queue.Queue()
    agent.queue_followup("also check my calendar")

    agent._drain_followups([], [])

    said = " ".join(str(c) for c in agent.console.print_info.call_args_list)
    assert "also check my calendar" in said


def test_an_empty_followup_is_refused_rather_than_queued(agent):
    """Whitespace in the composer must not become a turn in the model's context."""
    agent._followup_queue = queue.Queue()

    assert agent.queue_followup("   ") is False
    assert agent.queue_followup("") is False
    assert agent._drain_followups([], []) == 0


def test_draining_twice_does_not_repeat_a_followup(agent):
    """The loop calls this once per step, for as many steps as the turn runs."""
    agent._followup_queue = queue.Queue()
    agent.queue_followup("only the unread ones")

    messages = []
    assert agent._drain_followups(messages, []) == 1
    assert agent._drain_followups(messages, []) == 0
    assert len(messages) == 1


def test_the_queue_is_safe_to_write_from_the_http_thread(agent):
    """The sidecar's request thread enqueues while the agent thread drains."""
    agent._followup_queue = queue.Queue()
    stop = threading.Event()
    drained = []

    def drain():
        while not stop.is_set() or not agent._followup_queue.empty():
            agent._drain_followups(drained, [])

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    for i in range(50):
        agent.queue_followup(f"note {i}")
    stop.set()
    t.join(timeout=10)

    assert len(drained) == 50


# --------------------------------------------------------------------------
# The wiring, not just the helper
# --------------------------------------------------------------------------
#
# Everything above tests ``_drain_followups`` by calling it. That proves the
# helper works and proves nothing about whether the agent loop ever reaches it
# — delete the call site in ``_process_query_impl`` and every test above still
# passes while the feature is silently dead. These drive the real loop.


def _reply(text: str):
    """One complete non-streaming reply, the shape the loop's reader expects."""
    return SimpleNamespace(text=text, stats={})


@pytest.fixture
def loop_agent(agent):
    """The same agent with a REAL console.

    The MagicMock console above exists so a test can read back what the user
    was told; it cannot drive the loop, which reads real strings off the
    handler. These tests run the loop, so they need the real thing.
    """
    from gaia.agents.base.console import SilentConsole

    agent.console = SilentConsole()
    return agent


def test_the_agent_loop_hands_a_followup_to_the_model(loop_agent):
    """The wiring: a queued follow-up reaches the NEXT model call of this turn."""
    sent = []

    def capture(messages, **kwargs):
        sent.append([dict(m) for m in messages])
        return _reply("All done.")

    loop_agent.chat.send_messages = MagicMock(side_effect=capture)
    loop_agent._followup_queue = queue.Queue()
    loop_agent.queue_followup("only the unread ones")

    loop_agent.process_query("triage my inbox")

    assert sent, "the loop never called the model"
    first_call = sent[0]
    assert any(
        m["role"] == "user" and "only the unread ones" in str(m["content"])
        for m in first_call
    ), f"the follow-up never reached the model: {first_call}"
    # And it arrives AFTER the original request, not instead of it — the whole
    # point is that the turn in progress is continued, not replaced.
    users = [str(m["content"]) for m in first_call if m["role"] == "user"]
    assert "triage my inbox" in users[0]
    assert "only the unread ones" in users[-1]


def test_a_cancelled_turn_does_not_swallow_a_followup(loop_agent):
    """A cancelled turn breaks out without running another step.

    Draining before the cancel check consumed a message the turn could no
    longer answer — gone from the queue and never sent to the model.
    """
    loop_agent.chat.send_messages = MagicMock(side_effect=lambda *a, **k: _reply("x"))
    loop_agent._cancel_event = threading.Event()
    loop_agent._cancel_event.set()
    loop_agent._followup_queue = queue.Queue()
    loop_agent.queue_followup("only the unread ones")

    loop_agent.process_query("triage my inbox")

    assert not loop_agent._followup_queue.empty(), (
        "a cancelled turn consumed the follow-up; it is now in no queue and no "
        "conversation"
    )


def test_a_turn_with_nothing_queued_is_untouched(loop_agent):
    """Every ordinary turn now runs this on every step. It must be inert."""
    loop_agent.chat.send_messages = MagicMock(
        side_effect=lambda *a, **k: _reply("All done.")
    )
    loop_agent._followup_queue = queue.Queue()

    result = loop_agent.process_query("triage my inbox")

    assert result["status"] != "error", result
