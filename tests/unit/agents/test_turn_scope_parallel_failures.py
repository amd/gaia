# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One bad batch of parallel calls must not trip the failure breaker.

A local model sent four parallel ``read_file`` calls with ``start_line`` past
the end of four large files. Each failure counted on its own, so that single
response reached the limit, and the corrected calls that followed were refused
until the turn ended with no answer.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.turn_scope import TurnScopeGuard
from gaia.agents.base.verification import check_was_executed

LINES = {"a.txt": 100000, "b.txt": 126055, "c.txt": 56482, "d.txt": 74380}


def past_end(path, start):
    return {
        "status": "error",
        "error": f"start_line {start} is past the end: the file has "
        f"{LINES[path]} lines.",
    }


def ok():
    return {"status": "success", "content": "..."}


@pytest.fixture
def guard(tmp_path):
    scope = TurnScopeGuard(failure_limit=4)
    scope.begin_turn("Summarize the four logs.", str(tmp_path))
    return scope


def run_batch(guard, calls):
    """Check and record one response's calls; the refusals, in order."""
    refusals = []
    guard.begin_batch()
    for args, result in calls:
        refused = guard.check("read_file", args)
        if refused is not None:
            refusals.append(refused)
            continue
        guard.record("read_file", args, result)
    guard.end_batch()
    return refusals


def bad_batch(offset=0):
    return [
        (
            {"file_path": path, "start_line": total + 1 + offset},
            past_end(path, total + 1 + offset),
        )
        for path, total in LINES.items()
    ]


def test_a_failed_parallel_batch_counts_once(guard):
    assert run_batch(guard, bad_batch()) == []
    assert guard.failures["read_file"] == 1
    assert not guard.turn_should_end


def test_corrected_calls_after_bad_batches_still_run(guard):
    for offset in range(3):
        assert run_batch(guard, bad_batch(offset)) == []
    corrected = [
        ({"file_path": "a.txt", "start_line": 99951}, ok()),
        ({"file_path": "b.txt", "start_line": 126006}, ok()),
    ]
    assert run_batch(guard, corrected) == []
    assert "read_file" not in guard.failures
    assert not guard.turn_should_end


def test_the_batch_does_not_refuse_its_own_later_calls(guard):
    """Three failing responses, then a fourth: its calls all run."""
    for offset in range(3):
        run_batch(guard, bad_batch(offset))
    assert run_batch(guard, bad_batch(3)) == []
    assert guard.failures["read_file"] == 4
    assert not guard.turn_should_end


def test_once_tripped_a_correction_runs_but_a_repeat_does_not(guard):
    for offset in range(4):
        run_batch(guard, bad_batch(offset))
    assert guard.failures["read_file"] == 4
    # The exact call that failed is refused.
    again = guard.check("read_file", {"file_path": "a.txt", "start_line": 100001})
    assert again is not None and not check_was_executed(again)
    # So is wandering to a file that never failed.
    assert guard.check("read_file", {"file_path": "e.txt", "start_line": 1})
    assert guard.turn_should_end and guard.end_reason == "failures"


def test_a_correction_runs_even_after_the_streak_is_reached(guard):
    for offset in range(3):
        run_batch(guard, bad_batch(offset))
    run_batch(guard, bad_batch(3)[2:])  # a.txt failed three times, not four
    assert guard.failures["read_file"] == 4
    fixed = {"file_path": "a.txt", "start_line": 99951}
    assert guard.check("read_file", fixed) is None
    guard.record("read_file", fixed, ok())
    assert "read_file" not in guard.failures
    assert guard.check("read_file", {"file_path": "e.txt"}) is None


def test_one_target_failing_out_is_no_longer_a_correction(guard):
    for n in range(4):
        args = {"file_path": "a.txt", "start_line": 100001 + n}
        assert guard.check("read_file", args) is None
        guard.record("read_file", args, past_end("a.txt", 100001 + n))
    assert guard.check("read_file", {"file_path": "a.txt", "start_line": 5}) is not None


def test_sequential_failures_with_new_arguments_still_stop(guard):
    """Outside a batch every call is its own attempt, as before."""
    for n, path in enumerate(LINES):
        args = {"file_path": path, "start_line": 10**6 + n}
        assert guard.check("read_file", args) is None
        guard.record("read_file", args, past_end(path, 10**6 + n))
    assert guard.check("read_file", {"file_path": "e.txt"}) is not None
    assert guard.check("read_file", {"file_path": "f.txt"}) is not None
    assert guard.turn_should_end


def test_an_error_that_names_the_fix_does_not_move_the_streak(guard):
    for n in range(6):
        args = {"file_path": f"f{n}.txt", "start_line": 10**6}
        guard.record(
            "read_file", args, {**past_end("a.txt", 10**6), "correctable": True}
        )
    assert "read_file" not in guard.failures
    assert guard.check("read_file", {"file_path": "g.txt"}) is None


def test_correctable_failures_on_one_target_still_count_in_the_end(guard):
    for n in range(5):
        args = {"file_path": "a.txt", "start_line": 10**6 + n}
        guard.record(
            "read_file", args, {**past_end("a.txt", 10**6), "correctable": True}
        )
    assert guard.failures["read_file"] == 2


# ---------------------------------------------------------------------------
# The real loop
# ---------------------------------------------------------------------------


class LogAgent(Agent):
    def _get_system_prompt(self):
        return "Use the tools."

    def _register_tools(self):
        @tool
        def read_file(file_path: str, start_line: int = 1) -> dict:
            """Read lines of a file."""
            self.reads.append((file_path, start_line))
            if start_line > LINES[file_path]:
                return past_end(file_path, start_line)
            return {"status": "success", "content": f"{file_path} tail"}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = LogAgent(silent_mode=True, skip_lemonade=True)
    agent.reads = []
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def native_calls(step, calls):
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{step}_{n}",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps(args)},
                }
                for n, args in enumerate(calls)
            ],
            "finish_reason": "tool_calls",
        }
    )


def script(agent, *replies):
    queue = list(replies)

    def send(*_, **__):
        assert queue, "unexpected model call"
        return MagicMock(text=queue.pop(0), stats={})

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return queue


def test_real_loop_runs_the_corrected_calls_after_a_bad_parallel_batch(agent):
    bad = [{"file_path": p, "start_line": n + 50} for p, n in LINES.items()]
    worse = [{"file_path": p, "start_line": n + 500} for p, n in LINES.items()]
    fixed = [
        {"file_path": "a.txt", "start_line": 99951},
        {"file_path": "b.txt", "start_line": 126006},
    ]
    answer = "a.txt and b.txt end with the shutdown entries."
    queue = script(
        agent,
        native_calls(1, bad),
        native_calls(2, worse),
        native_calls(3, fixed),
        json.dumps({"thought": "done", "answer": answer}),
    )

    result = agent.process_query("Read the end of the four logs and summarize.")

    assert ("a.txt", 99951) in agent.reads and ("b.txt", 126006) in agent.reads
    assert len(agent.reads) == 10
    assert result["result"].startswith(answer)
    assert not queue


def test_a_correctable_failure_with_no_target_still_counts(tmp_path):
    """``correctable`` only spares a target the model can retry; no target, no pass."""
    scope = TurnScopeGuard(failure_limit=2)
    scope.begin_turn("Look it up.", str(tmp_path))
    hint = {"status": "error", "error": "bad page size", "correctable": True}
    for size in (1, 2):
        assert scope.check("search", {"page_size": size}) is None
        scope.record("search", {"page_size": size}, hint)
    assert scope.failures["search"] == 2
    assert scope.check("search", {"page_size": 3}) is not None
