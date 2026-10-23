# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent's task list: the plan it keeps up to date while it works.

Pins the state machine (exactly one step running while any is unfinished), the
``update_tasks`` tool, persistence with the session, the plan-field fallback,
and the ``task_list`` event on both wires (the Agent UI's handler vocabulary and
the canonical stream the TUI reads).
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
    MAX_ITEMS,
    PENDING,
    SOURCE_AGENT,
    SOURCE_PLAN,
    TaskList,
    TaskListMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME


def _statuses(task_list):
    return [item["status"] for item in task_list.items]


def _one_running_rule_holds(task_list):
    running = _statuses(task_list).count(IN_PROGRESS)
    return running == (1 if task_list.is_active() else 0)


# -- state machine ------------------------------------------------------------


class TestTaskList:
    def test_a_new_list_starts_its_first_step(self):
        tl = TaskList()
        tl.update(items=["Read the config", "Fix the bug", "Run the tests"])
        assert _statuses(tl) == [IN_PROGRESS, PENDING, PENDING]
        assert tl.items[0]["started_at"] is not None

    def test_advance_finishes_the_current_step_and_starts_the_next(self):
        tl = TaskList()
        tl.update(items=["a", "b", "c"])
        tl.update(advance=True)
        assert _statuses(tl) == [COMPLETED, IN_PROGRESS, PENDING]
        tl.update(advance=True)
        tl.update(advance=True)
        assert _statuses(tl) == [COMPLETED] * 3
        assert not tl.is_active()
        done = tl.items[0]
        assert done["completed_at"] >= done["started_at"]

    def test_marking_the_running_step_done_starts_the_next_pending_one(self):
        tl = TaskList()
        tl.update(items=["a", "b", "c"])
        tl.update(done=[1])
        assert _statuses(tl) == [COMPLETED, IN_PROGRESS, PENDING]

    def test_start_moves_the_running_marker(self):
        tl = TaskList()
        tl.update(items=["a", "b", "c"])
        tl.update(start=3)
        assert _statuses(tl) == [PENDING, PENDING, IN_PROGRESS]
        assert tl.items[0]["started_at"] is None

    def test_done_and_start_in_one_call(self):
        tl = TaskList()
        tl.update(items=["a", "b", "c", "d"])
        tl.update(done=[1, 2], start=4)
        assert _statuses(tl) == [COMPLETED, COMPLETED, PENDING, IN_PROGRESS]

    @pytest.mark.parametrize(
        "edits",
        [
            [{"advance": True}] * 5,
            [{"done": [3]}, {"start": 1}, {"done": [1, 2]}],
            [{"items": ["x", "a"]}, {"advance": True}, {"done": [1, 2]}],
        ],
    )
    def test_exactly_one_step_runs_after_every_edit(self, edits):
        tl = TaskList()
        tl.update(items=["a", "b", "c"])
        for edit in edits:
            tl.update(**edit)
            assert _one_running_rule_holds(tl), tl.items

    def test_replacing_the_list_keeps_the_status_of_unchanged_steps(self):
        tl = TaskList()
        tl.update(items=["Read the config", "Fix the bug", "Run the tests"])
        tl.update(advance=True)
        tl.update(
            items=["Read the config", "Fix the bug", "Update docs", "Run the tests"]
        )
        assert _statuses(tl) == [COMPLETED, IN_PROGRESS, PENDING, PENDING]
        assert [item["id"] for item in tl.items] == [1, 2, 3, 4]

    def test_a_bad_edit_changes_nothing(self):
        tl = TaskList()
        tl.update(items=["a", "b"])
        before = tl.items
        with pytest.raises(ValueError, match="no step 5"):
            tl.update(done=[1], start=5)
        assert tl.items == before

    def test_too_many_steps_is_refused(self):
        with pytest.raises(ValueError, match=str(MAX_ITEMS)):
            TaskList().update(items=[f"step {i}" for i in range(MAX_ITEMS + 1)])

    def test_an_empty_step_is_refused(self):
        with pytest.raises(ValueError, match="is empty"):
            TaskList().update(items=["a", "  "])

    @pytest.mark.parametrize(
        "items",
        [
            '["Read the config", "Fix the bug"]',
            "1. Read the config\n2. Fix the bug",
            "- Read the config\n- Fix the bug",
            [{"content": "Read the config"}, {"content": "Fix the bug"}],
        ],
    )
    def test_the_shapes_models_send_all_mean_the_same_list(self, items):
        tl = TaskList()
        tl.update(items=items)
        assert [item["content"] for item in tl.items] == [
            "Read the config",
            "Fix the bug",
        ]

    def test_a_number_that_is_part_of_the_step_is_kept(self):
        tl = TaskList()
        tl.update(items=["2FA setup", "3 files to fix"])
        assert [item["content"] for item in tl.items] == ["2FA setup", "3 files to fix"]

    def test_step_numbers_arrive_as_strings_too(self):
        tl = TaskList()
        tl.update(items=["a", "b", "c"])
        tl.update(done="[1, 2]")
        assert _statuses(tl) == [COMPLETED, COMPLETED, IN_PROGRESS]

    def test_every_change_reports_the_whole_state_once(self):
        events = []
        tl = TaskList(on_change=events.append)
        tl.update(items=["a", "b"])
        tl.update(advance=True)
        assert len(events) == 2
        assert events[-1]["items"][1]["status"] == IN_PROGRESS
        assert events[-1]["source"] == SOURCE_AGENT

    def test_the_action_line_follows_tool_calls_but_not_its_own_tool(self):
        events = []
        tl = TaskList(
            on_change=events.append, narrate=lambda tool, args: f"Running {tool}"
        )
        tl.action_started("run_shell_command", {})  # no list yet: nothing to show
        assert events == []
        tl.update(items=["a"])
        tl.action_started("update_tasks", {})
        assert len(events) == 1
        tl.action_started("run_shell_command", {"command": "ls"})
        assert events[-1]["current_action"] == "Running run_shell_command"
        assert events[-1]["action_started_at"] is not None
        tl.end_turn()
        assert events[-1]["current_action"] is None
        assert events[-1]["action_started_at"] is None

    def test_restore_loads_a_saved_list_without_a_running_action(self):
        tl = TaskList()
        tl.update(items=["a", "b"])
        tl.action_started("x", {})
        saved = json.loads(json.dumps(tl.state()))

        restored = TaskList()
        restored.restore(saved)
        assert restored.items == tl.items
        assert restored.state()["current_action"] is None

    def test_a_malformed_saved_list_fails_loudly(self):
        with pytest.raises(ValueError, match="malformed"):
            TaskList().restore({"items": [{"content": "a", "status": "running"}]})


# -- the plan-field fallback --------------------------------------------------


class TestPlanFallback:
    def test_the_plan_is_mirrored_when_the_model_kept_no_list(self):
        events = []
        tl = TaskList(on_change=events.append)
        tl.begin_turn()
        tl.mirror_plan(["Search the web", "Summarize"], 0)
        assert _statuses(tl) == [IN_PROGRESS, PENDING]
        assert tl.state()["source"] == SOURCE_PLAN
        tl.mirror_plan(["Search the web", "Summarize"], 1)
        assert _statuses(tl) == [COMPLETED, IN_PROGRESS]
        tl.mirror_plan(["Search the web", "Summarize"], 2)
        assert _statuses(tl) == [COMPLETED, COMPLETED]
        assert len(events) == 3

    def test_a_list_the_model_keeps_always_wins(self):
        tl = TaskList()
        tl.begin_turn()
        tl.update(items=["My own step"])
        tl.mirror_plan(["Plan step"], 0)
        assert [item["content"] for item in tl.items] == ["My own step"]
        assert tl.state()["source"] == SOURCE_AGENT


# -- the tool -----------------------------------------------------------------


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


class _Host(TaskListMixin):
    def __init__(self):
        self.console = MagicMock()


@pytest.fixture
def host(clean_registry):  # pylint: disable=unused-argument
    h = _Host()
    h.register_task_list_tools()
    return h


class TestUpdateTasksTool:
    def test_is_registered_with_a_schema_the_model_can_fill(self, host):
        entry = _TOOL_REGISTRY["update_tasks"]
        params = entry["parameters"]
        assert params["items"]["type"] == "array"
        assert params["done"]["type"] == "array"
        assert params["advance"]["type"] == "boolean"
        assert params["start"]["type"] == "integer"
        assert not any(p["required"] for p in params.values())
        assert host.task_list is not None

    def test_a_call_reports_the_list_back_and_emits_the_event(self, host):
        update = _TOOL_REGISTRY["update_tasks"]["function"]
        result = update(items=["Read the config", "Fix the bug"])
        assert result["status"] == "success"
        assert "1. [>] Read the config" in result["tasks"]
        host.console.print_task_list.assert_called_once()
        state = host.console.print_task_list.call_args.args[0]
        assert [item["content"] for item in state["items"]] == [
            "Read the config",
            "Fix the bug",
        ]

    def test_a_bad_call_is_an_error_result_the_model_can_correct(self, host):
        update = _TOOL_REGISTRY["update_tasks"]["function"]
        update(items=["a"])
        result = update(done=[4])
        assert result["status"] == "error"
        assert "no step 4" in result["error"]
        assert "not changed" in result["error"]

    def test_no_arguments_reads_the_list(self, host):
        update = _TOOL_REGISTRY["update_tasks"]["function"]
        update(items=["a", "b"])
        host.console.print_task_list.reset_mock()
        result = update()
        assert "0 of 2 done" in result["tasks"]
        host.console.print_task_list.assert_not_called()

    def test_an_empty_list_clears_it(self, host):
        update = _TOOL_REGISTRY["update_tasks"]["function"]
        update(items=["a"])
        result = update(items=[])
        assert result["tasks"] == "The task list is empty."
        assert host.console.print_task_list.call_args.args[0]["items"] == []

    def test_a_bound_store_restores_and_saves(self, host):
        saved = TaskList()
        saved.update(items=["Earlier step", "Next step"])
        writes = []
        host.bind_task_list_store(saved.state, writes.append)
        assert [item["content"] for item in host.task_list.items] == [
            "Earlier step",
            "Next step",
        ]
        _TOOL_REGISTRY["update_tasks"]["function"](advance=True)
        assert writes[-1]["items"][0]["status"] == COMPLETED


def test_the_prompt_rule_is_short():
    """Local models pay for every prompt token (#5020/#5288)."""
    from gaia.agents.tools.task_list_tools import PROMPT_RULES

    assert len(PROMPT_RULES.split()) < 70


# -- persistence with the TUI's session transcript ----------------------------


class TestSessionHistory:
    def test_the_list_survives_reopening_the_transcript(self, tmp_path):
        path = tmp_path / "s.sqlite3"
        history = SessionHistory(path)
        history.save_task_list({"items": [{"content": "a", "status": "pending"}]})
        history.close()
        assert SessionHistory(path).load_task_list()["items"][0]["content"] == "a"

    def test_clear_forgets_the_list(self, tmp_path):
        history = SessionHistory(tmp_path / "s.sqlite3")
        history.save_task_list({"items": []})
        history.clear()
        assert history.load_task_list() is None

    def test_the_turn_worker_thread_can_save(self, tmp_path):
        history = SessionHistory(tmp_path / "s.sqlite3")
        errors = []

        def save():
            try:
                history.save_task_list({"items": [], "source": "agent"})
            except Exception as exc:  # pylint: disable=broad-except
                errors.append(exc)

        worker = threading.Thread(target=save)
        worker.start()
        worker.join()
        assert errors == []
        assert history.load_task_list() == {"items": [], "source": "agent"}


# -- the event on both wires --------------------------------------------------


class TestEventContract:
    def _state(self):
        tl = TaskList()
        tl.update(items=["Read the config", "Fix the bug"])
        tl.action_started("read_file", {})
        return tl.state()

    def test_the_ui_handler_emits_one_typed_event(self):
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        events = []
        handler._emit = events.append
        handler.print_task_list(self._state())
        [event] = events
        assert event["type"] == "task_list"
        assert set(event) >= {
            "items",
            "source",
            "current_action",
            "action_started_at",
            "updated_at",
        }

    def test_the_canonical_stream_carries_it_unchanged(self):
        from gaia.ui.sse_translation import CanonicalTranslator

        state = self._state()
        [canonical] = CanonicalTranslator(run_id=None, agent_id="gaia").translate(
            {"type": "task_list", **state}
        )
        assert canonical == {"type": "task_list", **state}

    def test_it_does_not_split_a_tool_call_from_its_arguments(self):
        """The list updates between ``tool_start`` and ``tool_args``."""
        from gaia.ui.sse_translation import CanonicalTranslator

        translator = CanonicalTranslator(run_id=None, agent_id="gaia")
        out = []
        for event in (
            {"type": "tool_start", "tool": "read_file"},
            {"type": "task_list", **self._state()},
            {"type": "tool_args", "args": {"file_path": "a.txt"}},
            {"type": "tool_end"},
        ):
            out.extend(translator.translate(event))
        [call] = [e for e in out if e["type"] == "tool_call"]
        assert call["args"] == {"file_path": "a.txt"}

    def test_the_agent_ui_relay_maps_it_back(self):
        from gaia.ui.email_sidecar import relay as relay_module
        from gaia.ui.email_sidecar.profiles import GAIA_PROFILE

        emitted = []
        handler = SimpleNamespace(_emit=emitted.append)
        event = {"type": "task_list", **self._state()}
        assert relay_module._dispatch_one(handler, event, GAIA_PROFILE) is False
        assert emitted == [event]


# -- persistence with the Agent UI's session row ------------------------------


class TestChatDatabase:
    def test_the_list_is_saved_with_the_session_and_reaches_the_api(self):
        from gaia.ui.database import ChatDatabase
        from gaia.ui.utils import session_to_response

        db = ChatDatabase(":memory:")
        session = db.create_session(title="t")
        before = db.get_session(session["id"])["updated_at"]
        state = {
            "items": [{"content": "a", "status": "in_progress"}],
            "source": "agent",
        }
        db.set_session_task_list(session["id"], state)
        row = db.get_session(session["id"])
        assert ChatDatabase.decode_task_list(row) == state
        assert row["updated_at"] == before
        assert session_to_response(row).task_list == state

    def test_an_empty_list_is_forgotten(self):
        from gaia.ui.database import ChatDatabase

        db = ChatDatabase(":memory:")
        session = db.create_session(title="t")
        db.set_session_task_list(session["id"], {"items": [{"content": "a"}]})
        db.set_session_task_list(session["id"], {"items": []})
        assert ChatDatabase.decode_task_list(db.get_session(session["id"])) is None


# -- a real agent turn --------------------------------------------------------


class _TaskAgent(TaskListMixin, Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        self.register_task_list_tools()

        @tool
        def read_tl_test(path: str) -> str:
            """Read a file."""
            del path
            return "hello"

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


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


def test_a_turn_keeps_the_list_and_ends_with_nothing_running(clean_registry):
    # pylint: disable=unused-argument
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _TaskAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = False
    agent.verification_scope_statement = lambda: None
    events = []
    agent.console = MagicMock()
    agent.console.print_task_list.side_effect = events.append

    replies = [
        _calls(("update_tasks", {"items": ["Read the file", "Report what it says"]})),
        _calls(("read_tl_test", {"path": "notes.txt"})),
        _calls(("update_tasks", {"advance": True})),
        _calls(("update_tasks", {"advance": True})),
        "It says hello.",
    ]
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=replies.pop(0), stats={})
    )
    agent.chat = chat

    agent.process_query("what does notes.txt say?")

    assert [item["status"] for item in events[-1]["items"]] == [COMPLETED, COMPLETED]
    assert events[-1]["current_action"] is None
    assert any(e["current_action"] for e in events), "the action line never showed"
