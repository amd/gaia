# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The task list through real agent turns, both wires, and the session stores.

The model is scripted (native tool calls), everything else is real: the agent
loop, the tool registry, the SSE handler, the canonical translator, the chat
database and the TUI's transcript store.
"""

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.history import SessionHistory
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.task_list_tools import (
    COMPLETED,
    IN_PROGRESS,
    PENDING,
    SOURCE_AGENT,
    SOURCE_PLAN,
    TaskListMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

STEPS = ["Read the file", "Write the summary", "Report the count"]


class _TaskAgent(TaskListMixin, Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        self.register_task_list_tools()

        @tool
        def read_tli(path: str) -> str:
            """Read a file."""
            del path
            return "hello world"

        @tool
        def broken_tli(path: str) -> str:
            """A tool that fails."""
            raise RuntimeError(f"disk on fire reading {path}")

        @tool
        def stop_tli(path: str) -> str:
            """A tool during which the user presses Stop."""
            del path
            agent = _current["agent"]
            agent._cancel_event.set()
            return "partial"

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


_current = {}


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


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


def _agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _TaskAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = False
    agent.verification_scope_statement = lambda: None
    _current["agent"] = agent
    return agent


def _wire(agent):
    """Attach a real SSE handler; returns (handler events, canonical events)."""
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    ui_events = []
    handler._emit = ui_events.append
    agent.console = handler
    return ui_events


def _script(agent, replies):
    replies = list(replies)

    def send(*_, **__):
        if not replies:
            raise AssertionError("the model was asked more often than scripted")
        return SimpleNamespace(text=replies.pop(0), stats={})

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(side_effect=send)
    agent.chat = chat


def _lists(events):
    return [e for e in events if e.get("type") == "task_list"]


def _statuses(state):
    return [i["status"] for i in state["items"]]


def _one_running(state):
    s = _statuses(state)
    return s.count(IN_PROGRESS) == (1 if (PENDING in s or IN_PROGRESS in s) else 0)


# -- contract ------------------------------------------------------------------


def test_both_wires_carry_the_same_state_in_the_same_order(clean_registry):
    # pylint: disable=unused-argument
    agent = _agent()
    ui_events = _wire(agent)
    _script(
        agent,
        [
            _calls(("update_tasks", {"items": STEPS})),
            _calls(("read_tli", {"path": "a.txt"})),
            _calls(
                ("update_tasks", {"advance": True}), ("read_tli", {"path": "b.txt"})
            ),
            _calls(("update_tasks", {"done": [2, 3]})),
            "It says hello world.",
        ],
    )
    agent.process_query("summarize a.txt")

    translator = CanonicalTranslator(run_id=None, agent_id="gaia")
    canonical = [c for e in ui_events for c in translator.translate(e)]
    ui_lists = [{k: v for k, v in e.items() if k != "type"} for e in _lists(ui_events)]
    wire_lists = [
        {k: v for k, v in e.items() if k != "type"} for e in _lists(canonical)
    ]

    # Everything up to the terminal event reaches the TUI unchanged; the one
    # sent after it (the action line clearing) is the receiver's to drop.
    assert wire_lists == ui_lists[: len(wire_lists)]
    assert len(ui_lists) - len(wire_lists) <= 1
    assert all(_one_running(s) for s in ui_lists)
    # The tool calls kept their arguments through the interleaving.
    calls = [
        c for c in canonical if c["type"] == "tool_call" and c["tool"] == "read_tli"
    ]
    assert [c["args"] for c in calls] == [{"path": "a.txt"}, {"path": "b.txt"}]
    # The live action line followed each tool call.
    actions = [s["current_action"] for s in ui_lists if s["current_action"]]
    assert actions and all("a.txt" in a or "b.txt" in a for a in actions)
    assert ui_lists[-1]["current_action"] is None
    assert _statuses(ui_lists[-1]) == [COMPLETED] * 3
    json.dumps(ui_lists)


# -- turns ------------------------------------------------------------------------


def test_a_mid_task_plan_change_keeps_finished_work(clean_registry):
    # pylint: disable=unused-argument
    agent = _agent()
    events = _wire(agent)
    _script(
        agent,
        [
            _calls(("update_tasks", {"items": STEPS})),
            _calls(("update_tasks", {"advance": True})),
            _calls(
                (
                    "update_tasks",
                    {
                        "items": [
                            "Read the file",
                            "Check the encoding",
                            "Write the summary",
                            "Report the count",
                        ]
                    },
                )
            ),
            "Done.",
        ],
    )
    agent.process_query("go")
    final = _lists(events)[-1]
    assert [i["content"] for i in final["items"]][1] == "Check the encoding"
    assert _statuses(final) == [COMPLETED, PENDING, IN_PROGRESS, PENDING]


def test_a_follow_up_turn_continues_the_same_list(clean_registry):
    # pylint: disable=unused-argument
    agent = _agent()
    events = _wire(agent)
    _script(agent, [_calls(("update_tasks", {"items": STEPS})), "Started."])
    agent.process_query("start")
    _script(agent, [_calls(("update_tasks", {"advance": True})), "Next."])
    agent.process_query("keep going")
    assert _statuses(_lists(events)[-1]) == [COMPLETED, IN_PROGRESS, PENDING]


def test_a_failing_tool_leaves_the_list_intact_and_the_turn_ends_clean(clean_registry):
    # pylint: disable=unused-argument
    agent = _agent()
    events = _wire(agent)
    _script(
        agent,
        [
            _calls(("update_tasks", {"items": STEPS})),
            _calls(("broken_tli", {"path": "x"})),
            "The read failed.",
        ],
    )
    agent.process_query("go")
    final = _lists(events)[-1]
    assert _statuses(final) == [IN_PROGRESS, PENDING, PENDING]
    assert final["current_action"] is None


def test_stop_mid_step_keeps_the_step_unfinished(clean_registry):
    # pylint: disable=unused-argument
    agent = _agent()
    agent._cancel_event = threading.Event()
    events = _wire(agent)
    _script(
        agent,
        [
            _calls(("update_tasks", {"items": STEPS})),
            _calls(("stop_tli", {"path": "x"})),
            "should not be asked",
            "should not be asked",
        ],
    )
    agent.process_query("go")
    final = _lists(events)[-1]
    assert _statuses(final)[0] == IN_PROGRESS
    assert COMPLETED not in _statuses(final)
    assert final["current_action"] is None


def test_the_plan_field_and_the_tool_together_leave_the_tool_in_charge():
    from gaia.agents.tools.task_list_tools import TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.mirror_plan(["Plan a", "Plan b"], 0)
    assert tl.state()["source"] == SOURCE_PLAN
    tl.update(items=["Mine 1", "Mine 2"])
    tl.mirror_plan(["Plan a", "Plan b"], 1)
    assert [i["content"] for i in tl.items] == ["Mine 1", "Mine 2"]
    assert tl.state()["source"] == SOURCE_AGENT


def test_a_later_turns_plan_does_not_overwrite_an_unfinished_kept_list():
    from gaia.agents.tools.task_list_tools import TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.update(items=STEPS)
    tl.begin_turn()
    tl.mirror_plan(["Something else"], 0)
    assert [i["content"] for i in tl.items] == STEPS
    # Once everything is done, a new plan may take over.
    tl.update(done=[1, 2, 3])
    tl.begin_turn()
    tl.mirror_plan(["Something else"], 0)
    assert tl.state()["source"] == SOURCE_PLAN


def test_clearing_the_list_stops_the_plan_from_rebuilding_it():
    from gaia.agents.tools.task_list_tools import TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.clear(by_model=True)
    tl.mirror_plan(["a"], 0)
    assert tl.items == []


def test_a_mid_turn_follow_up_does_not_disturb_the_list(clean_registry):
    # pylint: disable=unused-argument
    import queue

    agent = _agent()
    agent._followup_queue = queue.Queue()
    events = _wire(agent)
    _script(
        agent,
        [
            _calls(("update_tasks", {"items": STEPS})),
            _calls(("read_tli", {"path": "a"})),
            _calls(("update_tasks", {"advance": True})),
            "ok",
        ],
    )
    agent.queue_followup("also check b.txt")
    agent.process_query("go")
    assert _statuses(_lists(events)[-1]) == [COMPLETED, IN_PROGRESS, PENDING]


def test_the_model_can_read_the_list_back_however_far_its_context_was_cut(
    clean_registry,
):
    """Context eviction (#5310) can drop old tool results; the list itself stays."""
    # pylint: disable=unused-argument
    agent = _agent()
    _wire(agent)
    _script(agent, [_calls(("update_tasks", {"items": STEPS})), "ok"])
    agent.process_query("go")
    agent.conversation_history = []
    result = _TOOL_REGISTRY["update_tasks"]["function"]()
    assert "1. [>] Read the file" in result["tasks"]


# -- the stores -------------------------------------------------------------------


def test_the_tui_transcript_brings_the_list_back_after_a_restart(
    tmp_path, clean_registry
):
    # pylint: disable=unused-argument
    path = tmp_path / "s.sqlite3"
    agent = _agent()
    history = SessionHistory(path)
    agent.bind_task_list_store(history.load_task_list, history.save_task_list)
    _wire(agent)
    _script(agent, [_calls(("update_tasks", {"items": STEPS})), "ok"])
    agent.process_query("go")
    history.close()

    again = _agent()
    reopened = SessionHistory(path)
    again.bind_task_list_store(reopened.load_task_list, reopened.save_task_list)
    assert [i["content"] for i in again.task_list.items] == STEPS
    assert again.task_list.state()["current_action"] is None


def test_the_agent_ui_session_row_round_trips_and_rewind_clears_it(clean_registry):
    # pylint: disable=unused-argument
    from gaia.ui._chat_helpers import _bind_task_list
    from gaia.ui.database import ChatDatabase

    db = ChatDatabase(":memory:")
    sid = db.create_session(title="t")["id"]
    first = db.add_message(sid, "user", "go")
    agent = _agent()
    _bind_task_list(agent, db, sid)
    _wire(agent)
    _script(agent, [_calls(("update_tasks", {"items": STEPS})), "ok"])
    agent.process_query("go")
    saved = ChatDatabase.decode_task_list(db.get_session(sid))
    assert [i["content"] for i in saved["items"]] == STEPS

    # A rebuilt agent (eviction, server restart) gets the same list.
    rebuilt = _agent()
    _bind_task_list(rebuilt, db, sid)
    assert rebuilt.task_list.items == agent.task_list.items

    # Rewinding the chat drops the list those turns wrote.
    from fastapi.testclient import TestClient

    from gaia.ui.dependencies import get_db
    from gaia.ui.server import create_app

    app = create_app(db_path=":memory:")
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app).delete(f"/api/sessions/{sid}/messages/{first}/and-below")
    assert response.status_code == 200, response.text
    assert db.get_session(sid)["task_list"] is None
    _bind_task_list(rebuilt, db, sid)
    assert rebuilt.task_list.items == []


def test_a_corrupt_session_row_is_cleared_with_a_warning(clean_registry):
    # pylint: disable=unused-argument
    from gaia.ui._chat_helpers import _bind_task_list
    from gaia.ui.database import ChatDatabase

    db = ChatDatabase(":memory:")
    sid = db.create_session(title="t")["id"]
    db._conn.execute(
        "UPDATE sessions SET task_list = ? WHERE id = ?", ("{not json", sid)
    )
    agent = _agent()
    agent.console = MagicMock()
    _bind_task_list(agent, db, sid)
    agent.console.print_warning.assert_called_once()
    assert "unreadable task_list" in agent.console.print_warning.call_args.args[0]
    assert agent.task_list.items == []
    assert db.get_session(sid)["task_list"] is None


# -- reminders ------------------------------------------------------------------


def test_the_reminder_fires_once_after_three_calls_without_a_list():
    """Not after two: a two-call lookup is usually about to answer."""
    from gaia.agents.tools.task_list_tools import NUDGE_START, TaskList

    tl = TaskList()
    tl.begin_turn()
    assert tl.nudge() is None
    for _ in range(2):
        tl.action_started("read_file", {})
        assert tl.nudge() is None
    tl.action_started("read_file", {})
    assert tl.nudge() == NUDGE_START
    tl.action_started("read_file", {})
    assert tl.nudge() is None
    tl.begin_turn()
    assert tl.nudge() is None


def test_a_kept_list_gets_an_advance_reminder_only_when_it_falls_behind():
    from gaia.agents.tools.task_list_tools import NUDGE_ADVANCE, TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.update(items=STEPS)
    for _ in range(2):
        tl.action_started("read_file", {})
        assert tl.nudge() is None
    tl.action_started("read_file", {})
    assert tl.nudge() == NUDGE_ADVANCE
    tl.action_started("read_file", {})
    assert tl.nudge() is None


def test_a_finished_list_from_an_earlier_turn_does_not_count_as_kept():
    from gaia.agents.tools.task_list_tools import NUDGE_START, TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.update(items=["a"])
    tl.update(advance=True)
    tl.begin_turn()
    events = []
    tl._on_change = events.append
    for _ in range(3):
        tl.action_started("x", {})
    assert tl.nudge() == NUDGE_START
    # Nor is it this turn's work: no live action, nothing re-sent.
    assert events == []
    assert tl.state()["current_action"] is None


def test_an_earlier_requests_unfinished_list_gets_no_advance_reminder():
    from gaia.agents.tools.task_list_tools import TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.update(items=STEPS)
    tl.begin_turn()
    for _ in range(5):
        tl.action_started("read_file", {})
    assert tl.nudge() is None


def test_the_agent_sends_the_reminder_to_the_model_but_not_into_history(clean_registry):
    # pylint: disable=unused-argument
    from gaia.agents.tools.task_list_tools import NUDGE_START

    agent = _agent()
    _wire(agent)
    seen = []
    replies = [
        _calls(("read_tli", {"path": "a"})),
        _calls(("read_tli", {"path": "b"})),
        _calls(("read_tli", {"path": "c"})),
        _calls(("update_tasks", {"items": STEPS, "done": [1]})),
        "done",
    ]

    def send(messages, *_, **__):
        seen.append([m.get("content") for m in messages if m.get("role") == "user"])
        return SimpleNamespace(text=replies.pop(0), stats={})

    agent.chat = MagicMock(
        get_stats=MagicMock(return_value={}), send_messages=MagicMock(side_effect=send)
    )
    result = agent.process_query("go")
    reminded = [i for i, users in enumerate(seen) if NUDGE_START in users]
    assert reminded and reminded[0] == 3, seen
    assert [i["status"] for i in agent.task_list.items] == [
        COMPLETED,
        IN_PROGRESS,
        PENDING,
    ]
    saved = json.dumps(result.get("model_messages") or [])
    assert NUDGE_START not in saved


# -- other readers of the wire ----------------------------------------------------


def test_gaia_agent_query_prints_one_line_per_step_started():
    import io

    from gaia.agents.tools.task_list_tools import TaskList
    from gaia.daemon.agent_query import ConsoleRenderer

    err = io.StringIO()
    renderer = ConsoleRenderer(out=io.StringIO(), err=err)
    tl = TaskList(on_change=lambda s: renderer.render({"type": "task_list", **s}))
    tl.begin_turn()
    tl.update(items=STEPS)
    for _ in range(3):
        tl.action_started("read_file", {})
    tl.update(advance=True)
    lines = err.getvalue().splitlines()
    assert lines == ["  [>] 1/3 Read the file", "  [>] 2/3 Write the summary"]
    assert "unknown event" not in err.getvalue()


def test_an_unreadable_task_list_never_breaks_the_session_list():
    from gaia.ui.database import ChatDatabase
    from gaia.ui.utils import session_to_response

    db = ChatDatabase(":memory:")
    sid = db.create_session(title="t")["id"]
    db._conn.execute(
        "UPDATE sessions SET task_list = ? WHERE id = ?", ("{not json", sid)
    )
    [row] = [s for s in db.list_sessions() if s["id"] == sid]
    assert session_to_response(row).task_list is None


def test_an_earlier_requests_unfinished_list_is_left_alone_by_new_work():
    from gaia.agents.tools.task_list_tools import TaskList

    tl = TaskList()
    tl.begin_turn()
    tl.update(items=STEPS)
    tl.action_started("read_file", {})
    events = []
    tl._on_change = events.append
    tl.begin_turn()
    assert tl.state()["steps_since_update"] == 0
    for _ in range(4):
        tl.action_started("read_file", {})
    assert events == []
    # Once the model picks it up again, it is this turn's list.
    tl.update(advance=True)
    tl.action_started("read_file", {"file_path": "x"})
    assert events[-1]["current_action"]


def test_a_failed_save_never_fails_the_tool_call_or_the_turn(clean_registry):
    # pylint: disable=unused-argument
    import sqlite3

    agent = _agent()
    _wire(agent)
    agent.console = MagicMock()

    def broken(_state):
        raise sqlite3.OperationalError("database is locked")

    agent.bind_task_list_store(lambda: None, broken)
    _script(
        agent,
        [
            _calls(("update_tasks", {"items": STEPS})),
            _calls(("read_tli", {"path": "a"})),
            "It says hello world.",
        ],
    )
    result = agent.process_query("go")
    assert "hello world" in str(result.get("result"))
    assert agent.console.print_warning.call_count == 1
    assert "could not be saved" in agent.console.print_warning.call_args.args[0]


def test_a_saved_list_that_is_not_an_object_is_refused_loudly():
    from gaia.ui.database import ChatDatabase

    with pytest.raises(ValueError, match="not an object"):
        ChatDatabase.decode_task_list({"id": "s", "task_list": "[]"})
