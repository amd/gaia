# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The task list under load: long plans, long chats, bursts, and many chats.

Bounds are loose enough for a busy CI runner and tight enough that a quadratic
slip (re-sending or re-saving more than the change) fails them.
"""

import json
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.history import SessionHistory
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.task_list_tools import (
    COMPLETED,
    IN_PROGRESS,
    MAX_ITEMS,
    PENDING,
    TaskList,
    TaskListMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


def _one_running(items):
    s = [i["status"] for i in items]
    return s.count(IN_PROGRESS) == (1 if (PENDING in s or IN_PROGRESS in s) else 0)


def test_a_200_step_plan_is_refused_whole_and_a_200_step_envelope_plan_stays_bounded():
    tl = TaskList()
    with pytest.raises(ValueError, match="too many"):
        tl.update(items=[f"step {i}" for i in range(200)])
    tl.begin_turn()
    steps = [f"step {i}" for i in range(200)]
    for current in range(0, 201, 7):
        tl.mirror_plan(steps, current)
        items = tl.items
        assert len(items) <= MAX_ITEMS
        assert _one_running(items)
        if current < 200:
            assert any(
                i["id"] == current + 1 and i["status"] == IN_PROGRESS for i in items
            )


def test_a_burst_of_updates_arrives_complete_and_in_order():
    """1,000 changes as fast as the agent can make them (well past 100/s)."""
    handler = SSEOutputHandler()
    seen = []
    handler._emit = seen.append
    tl = TaskList(on_change=handler.print_task_list)
    steps = [f"s{i}" for i in range(MAX_ITEMS)]
    start = time.perf_counter()
    for n in range(1000):
        if n % MAX_ITEMS == 0:
            tl.update(
                items=steps if n % (2 * MAX_ITEMS) == 0 else list(reversed(steps))
            )
        else:
            tl.update(advance=True)
    elapsed = time.perf_counter() - start
    assert elapsed < 5, f"1,000 updates took {elapsed:.2f}s"
    assert all(_one_running(e["items"]) for e in seen)
    stamps = [e["updated_at"] for e in seen]
    assert stamps == sorted(stamps)


def test_fifty_turns_of_saves_stay_cheap(tmp_path):
    history = SessionHistory(tmp_path / "s.sqlite3")
    tl = TaskList(on_change=history.save_task_list)
    start = time.perf_counter()
    for turn in range(50):
        tl.begin_turn()
        tl.update(items=[f"turn {turn} step {i}" for i in range(6)])
        for _ in range(6):
            tl.action_started("read_file", {"file_path": "x"})
            tl.update(advance=True)
        tl.end_turn()
    elapsed = time.perf_counter() - start
    assert elapsed < 15, f"50 turns of saves took {elapsed:.2f}s"
    saved = history.load_task_list()
    assert [i["status"] for i in saved["items"]] == [COMPLETED] * 6
    assert saved["items"][0]["content"] == "turn 49 step 0"


def test_concurrent_lists_never_cross():
    """Eight chats, each its own list and wire, updating at the same time."""
    results = {}
    errors = []

    def chat(n):
        try:
            handler = SSEOutputHandler()
            out = []
            handler._emit = out.append
            tl = TaskList(on_change=handler.print_task_list)
            mine = [f"chat {n} step {i}" for i in range(5)]
            for _ in range(200):
                tl.update(items=mine)
                for _ in range(5):
                    tl.update(advance=True)
            results[n] = out
        except Exception as exc:  # pylint: disable=broad-except
            errors.append(exc)

    threads = [threading.Thread(target=chat, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    for n, events in results.items():
        assert events
        for e in events:
            assert all(i["content"].startswith(f"chat {n} ") for i in e["items"])


def test_a_slow_bursty_canonical_reader_loses_nothing():
    """The stdio drain reads the handler's queue on its own clock."""
    handler = SSEOutputHandler()
    tl = TaskList(on_change=handler.print_task_list)
    translator = CanonicalTranslator(run_id=None, agent_id="gaia")
    got = []
    done = threading.Event()

    def reader():
        while not (done.is_set() and handler.event_queue.empty()):
            try:
                event = handler.event_queue.get(timeout=0.01)
            except queue.Empty:
                continue
            if event is None:
                continue
            got.extend(
                e for e in translator.translate(event) if e["type"] == "task_list"
            )
            if len(got) % 50 == 0:
                time.sleep(0.02)  # a reader that stalls now and then

    thread = threading.Thread(target=reader)
    thread.start()
    tl.update(items=[f"s{i}" for i in range(MAX_ITEMS)])
    for _ in range(MAX_ITEMS - 1):
        for _ in range(20):
            tl.action_started("read_file", {"file_path": "x"})
        tl.update(advance=True)
    done.set()
    thread.join(10)
    assert got[-1]["items"][-1]["status"] == IN_PROGRESS
    assert len(got) == 1 + (MAX_ITEMS - 1) * 21


class _TaskAgent(TaskListMixin, Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        self.register_task_list_tools()
        # Per-instance registry, as ChatAgent does: each agent's update_tasks
        # must edit its own list even while another chat registers.
        self._snapshot_tools()

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def _calls(name, args):
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
            "finish_reason": "tool_calls",
            "content": None,
        }
    )


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_concurrent_agent_turns_each_keep_their_own_list(clean_registry):
    # pylint: disable=unused-argument
    agents = []
    for n in range(4):
        with patch("gaia.agents.base.agent.AgentSDK"):
            a = _TaskAgent(
                silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
            )
        a.streaming = False
        a.verification_scope_statement = lambda: None
        replies = [_calls("update_tasks", {"items": [f"agent {n} a", f"agent {n} b"]})]
        replies += [_calls("update_tasks", {"advance": True})] * 2 + ["done"]

        def send(*_, _r=replies, **__):
            return SimpleNamespace(text=_r.pop(0), stats={})

        a.chat = MagicMock(
            get_stats=MagicMock(return_value={}),
            send_messages=MagicMock(side_effect=send),
        )
        a.console = MagicMock()
        agents.append(a)

    threads = [threading.Thread(target=a.process_query, args=("go",)) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    for n, a in enumerate(agents):
        items = a.task_list.items
        assert [i["content"] for i in items] == [f"agent {n} a", f"agent {n} b"]
        assert [i["status"] for i in items] == [COMPLETED, COMPLETED]
