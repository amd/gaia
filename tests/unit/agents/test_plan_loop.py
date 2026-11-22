# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The plan in the agent loop: written once by the model, ticked by the loop."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.plan_tracker import PLAN_HINT, PLAN_REMINDER
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.task_list_tools import (
    COMPLETED,
    FAILED,
    IN_PROGRESS,
    PENDING,
    TaskListMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.ui.sse_handler import SSEOutputHandler

PLAN = (
    "Plan:\n"
    "1. Read notes.txt\n"
    "2. Merge the action items\n"
    "3. Write the list to actions.md\n\n"
)
READ = ("read_file", {"file_path": "Documents/notes.txt"})
WRITE = ("write_file", {"file_path": "Documents/actions.md", "content": "- one"})
LOCKED = ("write_file", {"file_path": "locked/actions.md", "content": "- one"})


class _PlanAgent(TaskListMixin, Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        self.register_task_list_tools()

        @tool
        def read_file(file_path: str) -> dict:
            """Read a file."""
            return {"status": "success", "content": f"text of {file_path}"}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            del content
            if file_path.startswith("locked/"):
                return {"status": "error", "error": f"permission denied: {file_path}"}
            return {"status": "success", "file_path": file_path}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture(autouse=True)
def _clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _agent(streaming=False):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _PlanAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = streaming
    agent.verification_scope_statement = lambda: None
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    events = []
    handler._emit = events.append
    agent.console = handler
    return agent, events


def _calls(*calls, content=None) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for i, (name, args) in enumerate(calls)
            ],
            "finish_reason": "tool_calls",
            "content": content,
        }
    )


def _script(agent, replies):
    """Script the model. A reply is text, or ``(text, reasoning)``. Returns what was sent."""
    queue = list(replies)
    sent = []

    def _next(kwargs):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        sent.append(copy.deepcopy(kwargs.get("messages")))
        reply = queue.pop(0)
        return reply if isinstance(reply, tuple) else (reply, None)

    def _send(*_, **kwargs):
        text, reasoning = _next(kwargs)
        return SimpleNamespace(text=text, stats={}, reasoning=reasoning)

    def _stream(*_, **kwargs):
        text, reasoning = _next(kwargs)
        if text.startswith('{"__tool_calls__"'):
            prose = json.loads(text).get("content") or ""
            for i in range(0, len(prose), 5):
                yield SimpleNamespace(
                    text=prose[i : i + 5], is_complete=False, stats=None
                )
            yield SimpleNamespace(
                text=text, is_complete=True, stats={}, reasoning=reasoning
            )
            return
        for i in range(0, len(text), 5):
            yield SimpleNamespace(text=text[i : i + 5], is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={}, reasoning=reasoning)

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    return sent


def _lists(events):
    return [e for e in events if e.get("type") == "task_list"]


def _statuses(agent):
    return [item["status"] for item in agent.task_list.items]


def _texts(sent):
    return json.dumps(sent, ensure_ascii=False)


# -- the plan is written once and ticked by the loop ----------------------------


def test_plan_with_the_first_tool_call_is_ticked_without_extra_model_turns():
    agent, events = _agent()
    sent = _script(
        agent,
        [
            _calls(READ, content=PLAN),
            _calls(WRITE),
            "The merged list is in actions.md.",
        ],
    )
    result = agent.process_query("merge the action items into actions.md")

    assert len(sent) == 3  # the scripted replies and no more
    assert [i["content"] for i in agent.task_list.items] == [
        "Read notes.txt",
        "Merge the action items",
        "Write the list to actions.md",
    ]
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]
    first = _lists(events)[0]
    assert [i["status"] for i in first["items"]] == [IN_PROGRESS, PENDING, PENDING]
    assert "Plan:" not in result["result"]
    assert "actions.md" in result["result"]


def test_a_simple_request_gets_no_plan_and_no_event():
    agent, events = _agent()
    _script(agent, [_calls(READ), "It says hello."])
    agent.process_query("what is in notes.txt?")
    assert _lists(events) == []
    assert agent.task_list.items == []


def test_fewer_than_three_steps_is_not_a_plan():
    agent, events = _agent()
    _script(
        agent,
        [
            _calls(READ, content="Plan:\n1. Read notes.txt\n2. Answer\n"),
            "It says hello.",
        ],
    )
    agent.process_query("what is in notes.txt?")
    assert _lists(events) == []


def test_the_plan_text_never_reaches_the_screen_but_stays_in_the_history():
    agent, events = _agent(streaming=True)
    sent = _script(
        agent,
        [
            _calls(READ, content=PLAN + "Reading the notes first."),
            _calls(WRITE),
            "The merged list is in actions.md.",
        ],
    )
    result = agent.process_query("merge the action items into actions.md")

    shown = "".join(
        str(e.get("content") or e.get("text") or "")
        for e in events
        if e.get("type") in ("chunk", "commentary", "answer")
    )
    assert "Plan:" not in shown
    assert "1. Read notes.txt" not in shown
    assert "Plan:" not in result["result"]
    assert "1. Read notes.txt" in _texts(sent[1])  # the model still sees its own plan


def test_the_instruction_is_in_the_system_prompt_and_the_update_tool_is_gone():
    agent, _ = _agent()
    assert "==== PLANS ====" in agent._compose_system_prompt()
    assert "update_tasks" not in agent._tools_registry


def test_turned_off_there_is_no_instruction_no_plan_and_the_update_tool_is_back(
    monkeypatch,
):
    monkeypatch.setenv("GAIA_AGENT_PLANS", "0")
    agent, events = _agent()
    assert "==== PLANS ====" not in agent._compose_system_prompt()
    assert "update_tasks" in agent._tools_registry
    _script(agent, [_calls(READ, content=PLAN), _calls(WRITE), "Done, see actions.md."])
    agent.process_query("merge the action items into actions.md")
    assert _lists(events) == []


# -- honesty --------------------------------------------------------------------


def test_a_failed_write_shows_its_step_as_failed_and_is_not_sent_back():
    agent, _ = _agent()
    sent = _script(
        agent,
        [
            _calls(READ, content=PLAN.replace("actions.md", "locked/actions.md")),
            _calls(LOCKED),
            "I could not save the file: permission denied.",
        ],
    )
    agent.process_query("merge the action items into locked/actions.md")
    assert _statuses(agent) == [COMPLETED, COMPLETED, FAILED]
    assert "not done yet" not in _texts(sent)


def test_a_retry_that_succeeds_clears_the_failed_step():
    agent, _ = _agent()
    _script(
        agent,
        [
            _calls(READ, content=PLAN),
            _calls(("write_file", {"file_path": "locked/actions.md", "content": "x"})),
            _calls(WRITE),
            "Saved to actions.md after a retry.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]


def test_the_model_cannot_tick_a_step_by_saying_it_did():
    agent, _ = _agent()
    _script(
        agent,
        [
            _calls(READ, content=PLAN),
            "I merged the items and saved them to actions.md.",
            "They are saved in actions.md.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    assert _statuses(agent)[2] != COMPLETED


# -- the send-back --------------------------------------------------------------


def test_an_early_answer_is_sent_back_once_and_the_work_gets_done():
    agent, _ = _agent()
    sent = _script(
        agent,
        [
            _calls(READ, content=PLAN),
            "I have merged the action items.",
            _calls(WRITE),
            "The merged list is in actions.md.",
        ],
    )
    result = agent.process_query("merge the action items into actions.md")

    assert len(sent) == 4
    note = sent[2][-1]
    assert note["role"] == "user"
    assert "not done yet" in note["content"]
    assert "Write the list to actions.md" in note["content"]
    assert "Read notes.txt" not in note["content"]
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]
    assert "actions.md" in result["result"]


def test_the_send_back_happens_at_most_once():
    agent, _ = _agent()
    sent = _script(
        agent,
        [
            _calls(READ, content=PLAN),
            "I have merged the action items.",
            "The file is not needed; here is the list: one.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    assert len(sent) == 3
    assert _texts(sent).count("not done yet") == 1
    assert _statuses(agent)[2] in (PENDING, IN_PROGRESS)


# -- other ways a plan arrives ---------------------------------------------------


def test_one_reminder_after_two_tool_calls_without_a_plan():
    agent, _ = _agent()
    sent = _script(
        agent,
        [
            _calls(READ),
            _calls(("read_file", {"file_path": "Documents/meeting.txt"})),
            _calls(
                WRITE,
                content="Plan:\n1. Read notes.txt\n2. Read meeting.txt\n3. Write actions.md\n",
            ),
            "The merged list is in actions.md.",
        ],
    )
    agent.process_query("merge the action items from both files into actions.md")

    assert PLAN_REMINDER not in _texts(sent[1])
    assert PLAN_REMINDER in _texts(sent[2])
    assert _texts(sent[3]).count(PLAN_REMINDER) == 1
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]


def test_a_plan_listed_only_in_the_first_thinking_is_adopted():
    agent, _ = _agent()
    thinking = (
        "The user wants me to:\n1. Read notes.txt\n2. Merge the items\n"
        "3. Write actions.md\n\nLet me start."
    )
    _script(
        agent,
        [(_calls(READ), thinking), _calls(WRITE), "The merged list is in actions.md."],
    )
    agent.process_query("merge the action items into actions.md")
    assert [i["content"] for i in agent.task_list.items] == [
        "Read notes.txt",
        "Merge the items",
        "Write actions.md",
    ]
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]


def test_thinking_after_the_first_reply_is_not_read_as_a_plan():
    agent, events = _agent()
    later = "Files found:\n1. a.txt\n2. b.txt\n3. c.txt\n"
    _script(agent, [_calls(READ), (_calls(READ), later), "It says hello."])
    agent.process_query("what is in notes.txt?")
    assert _lists(events) == []


# -- across requests --------------------------------------------------------------


def test_a_new_plan_replaces_the_old_one_and_a_simple_follow_up_leaves_it_alone():
    agent, events = _agent()
    _script(
        agent,
        [
            _calls(READ, content=PLAN),
            _calls(WRITE),
            "The merged list is in actions.md.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    after_first = len(_lists(events))

    _script(agent, [_calls(READ), "The first line is hello."])
    agent.process_query("what is the first line of notes.txt?")
    assert len(_lists(events)) == after_first
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]

    second = (
        "Plan:\n1. Read notes.txt\n2. Count the lines\n3. Write the count to count.md\n"
    )
    _script(
        agent,
        [
            _calls(READ, content=second),
            _calls(("write_file", {"file_path": "count.md", "content": "3"})),
            "The count is in count.md.",
        ],
    )
    agent.process_query("count the lines of notes.txt into count.md")
    assert [i["content"] for i in agent.task_list.items] == [
        "Read notes.txt",
        "Count the lines",
        "Write the count to count.md",
    ]
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]


def test_reads_from_an_earlier_request_do_not_tick_a_new_plan():
    agent, _ = _agent()
    _script(agent, [_calls(READ), "It says hello."])
    agent.process_query("what is in notes.txt?")

    _script(
        agent,
        [
            _calls(("read_file", {"file_path": "Documents/other.txt"}), content=PLAN),
            "Reading done.",
            "Stopping here.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    assert _statuses(agent)[0] != COMPLETED


# -- a plan the user asked to read ------------------------------------------------


def test_a_plan_in_an_answer_is_left_for_the_user_to_read():
    agent, events = _agent(streaming=True)
    reply = PLAN + "Shall I go ahead?"
    sent = _script(agent, [reply, reply, reply, reply])
    result = agent.process_query("how would you merge the action items?")
    assert _lists(events) == []
    assert "not done yet" not in _texts(sent)
    assert "1. Read notes.txt" in result["result"]


def test_asked_for_a_plan_only_nothing_is_tracked_or_sent_back():
    agent, events = _agent()
    reply = PLAN + "Tell me when to start."
    sent = _script(agent, [reply, reply, reply, reply])
    result = agent.process_query(
        "Don't do anything yet, just give me a plan for merging the action items."
    )
    assert _lists(events) == []
    assert "not done yet" not in _texts(sent)
    assert PLAN_REMINDER not in _texts(sent)
    assert "1. Read notes.txt" in result["result"]


def test_a_later_request_does_not_re_tick_an_earlier_plan():
    agent, events = _agent()
    _script(
        agent,
        [
            _calls(READ, content=PLAN.replace("actions.md", "locked/actions.md")),
            _calls(LOCKED),
            "I could not save the file: permission denied.",
        ],
    )
    agent.process_query("merge the action items into locked/actions.md")
    assert _statuses(agent) == [COMPLETED, COMPLETED, FAILED]
    before = len(_lists(events))

    done = "Saved a copy to Documents/actions.md."
    _script(agent, [_calls(WRITE), done, done, done])
    agent.process_query("save one line to Documents/actions.md")
    assert _statuses(agent) == [COMPLETED, COMPLETED, FAILED]
    assert len(_lists(events)) == before


# -- findings of the review ---------------------------------------------------------


def test_a_plan_the_user_asked_for_stays_in_the_answer():
    agent, _ = _agent()
    week = "Plan:\n1. Monday: review the notes\n2. Tuesday: write the draft\n3. Wednesday: ship it\n"
    _script(
        agent,
        [
            _calls(
                READ,
                content="Plan:\n1. Read notes.txt\n2. Think about the week\n3. Answer with the plan\n",
            ),
            week,
            week,
        ],
    )
    result = agent.process_query("read notes.txt and give me a plan for my week")
    assert "Monday: review the notes" in result["result"]


def test_the_send_back_asks_for_the_whole_answer_again():
    agent, _ = _agent()
    sent = _script(
        agent,
        [
            _calls(READ, content=PLAN),
            "I have merged the action items.",
            _calls(WRITE),
            "The merged list is in actions.md.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    assert "complete answer again" in sent[2][-1]["content"]


def test_a_question_to_the_user_is_not_sent_back():
    agent, _ = _agent()
    question = "Should the list go in actions.md or in a new file?"
    sent = _script(agent, [_calls(READ, content=PLAN), question, question, question])
    result = agent.process_query("merge the action items into actions.md")
    assert "not done yet" not in _texts(sent)
    assert "new file?" in result["result"]


def test_no_reminder_names_a_tool_that_is_not_there():
    agent, _ = _agent()
    reply = PLAN + "Tell me when to start."
    sent = _script(
        agent,
        [
            _calls(READ),
            _calls(("read_file", {"file_path": "Documents/meeting.txt"})),
            _calls(("read_file", {"file_path": "Documents/other.txt"})),
            _calls(("read_file", {"file_path": "Documents/more.txt"})),
            reply,
            reply,
            reply,
        ],
    )
    agent.process_query(
        "Don't change anything, just give me a plan: read the four files first."
    )
    assert "update_tasks" not in agent._tools_registry
    assert "update_tasks" not in _texts(sent)


def test_the_instruction_survives_a_turn_that_only_asked_for_a_plan():
    agent, _ = _agent()
    reply = PLAN + "Tell me when to start."
    _script(agent, [reply, reply, reply, reply])
    agent.process_query("Don't do anything yet, just give me a plan.")
    agent.rebuild_system_prompt()
    assert "==== PLANS ====" in agent.system_prompt

    _script(
        agent,
        [
            _calls(READ, content=PLAN),
            _calls(WRITE),
            "The merged list is in actions.md.",
        ],
    )
    agent.process_query("merge the action items into actions.md")
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]


def test_a_streamed_answer_that_opens_with_a_plan_is_shown_whole():
    agent, events = _agent(streaming=True)
    reply = PLAN + "Shall I go ahead?"
    _script(agent, [reply, reply, reply, reply])
    result = agent.process_query("how would you merge the action items?")
    assert "1. Read notes.txt" in result["result"]
    assert "Shall I go ahead?" in result["result"]
    assert _lists(events) == []


# -- the hint with each request, and a plan that opens a final answer ----------


def test_the_hint_goes_with_the_request_but_not_into_the_saved_history():
    agent, _ = _agent()
    sent = _script(agent, [_calls(READ), "It says hello."])
    agent.process_query("what is in notes.txt?")
    assert PLAN_HINT in sent[0][-1]["content"]
    saved = [m for m in agent.conversation_history if m.get("role") == "user"]
    assert all(PLAN_HINT not in str(m.get("content")) for m in saved)


def test_no_hint_when_plans_are_off_or_only_a_plan_was_asked_for(monkeypatch):
    agent, _ = _agent()
    reply = PLAN + "Tell me when to start."
    sent = _script(agent, [reply, reply, reply, reply])
    agent.process_query("Don't do anything yet, just give me a plan.")
    assert PLAN_HINT not in _texts(sent)

    monkeypatch.setenv("GAIA_AGENT_PLANS", "0")
    agent, _ = _agent()
    sent = _script(agent, [_calls(READ), "It says hello."])
    agent.process_query("what is in notes.txt?")
    assert PLAN_HINT not in _texts(sent)


def test_a_plan_that_opens_the_final_answer_becomes_the_list():
    agent, events = _agent(streaming=True)
    sent = _script(
        agent,
        [
            _calls(READ),
            _calls(WRITE),
            PLAN + "The merged list is in actions.md.",
        ],
    )
    result = agent.process_query("merge the action items into actions.md")
    assert len(sent) == 3
    assert _statuses(agent) == [COMPLETED, COMPLETED, COMPLETED]
    assert "1. Read notes.txt" not in result["result"]
    assert "actions.md" in result["result"]
    shown = "".join(
        str(e.get("content") or e.get("text") or "")
        for e in events
        if e.get("type") in ("chunk", "commentary", "answer")
    )
    assert "1. Read notes.txt" not in shown


def test_a_plan_the_user_asked_for_after_some_work_stays_in_the_answer():
    agent, events = _agent()
    week = "Plan:\n1. Monday: review the notes\n2. Tuesday: write the draft\n3. Wednesday: ship it\n"
    _script(agent, [_calls(READ), week, week])
    result = agent.process_query("read notes.txt and make me a plan for the week")
    assert "Monday: review the notes" in result["result"]
    assert _lists(events) == []
