# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The duplicate tool-call short-circuit, from the rule to the turn's end.

A call identical to one that already ran this turn, within the window, with
no error and no change to what it looks at, does not run again: the model gets
the step it ran at and a handle to the archived output. Repeats past the limit
warn, and past twice the limit the turn ends the way the loop guard ends it.
"""

# pylint: disable=protected-access,unused-argument

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.artifacts import store_for
from gaia.agents.base.duplicate_guard import (
    DUPLICATE_GUARD_ENV_VAR,
    DUPLICATE_LIMIT_ENV_VAR,
    DUPLICATE_WINDOW_ENV_VAR,
    DuplicateCallGuard,
    duplicate_guard_from_env,
    duplicate_limit_from_env,
    result_text,
)
from gaia.agents.base.reasoning_policy import tool_result_failed
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

_ANSWER = "Done."
_GREP = {"command": 'grep -rn "gfx90a" build_tools/configure'}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    for var in (
        DUPLICATE_GUARD_ENV_VAR,
        DUPLICATE_WINDOW_ENV_VAR,
        DUPLICATE_LIMIT_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


class _ProbeAgent(Agent):
    """Tools that count their executions, so a short-circuit is observable."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self
        agent.runs = []

        @tool
        def run_shell_command(command: str) -> dict:
            """A shell look."""
            agent.runs.append(("run_shell_command", command))
            return {
                "status": "success",
                "stdout": f"out of {command}",
                "return_code": 0,
            }

        @tool
        def read_file(file_path: str) -> dict:
            """A file read."""
            agent.runs.append(("read_file", file_path))
            return {"status": "success", "content": f"text of {file_path}"}

        @tool
        def edit_file(file_path: str, old: str, new: str) -> dict:
            """A file edit."""
            agent.runs.append(("edit_file", file_path))
            return {
                "status": "success",
                "operation": "edit_file",
                "file_path": file_path,
            }

        @tool
        def flaky(path: str) -> dict:
            """Fails the first time, then works."""
            agent.runs.append(("flaky", path))
            if len([r for r in agent.runs if r[0] == "flaky"]) == 1:
                return {"status": "error", "error": "not yet"}
            return {"status": "success", "content": "now"}

        @tool
        def sleep(seconds: int) -> dict:
            """Always a fresh side effect."""
            agent.runs.append(("sleep", seconds))
            return {"status": "success"}


def _make_agent(**kwargs) -> _ProbeAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _ProbeAgent(silent_mode=True, skip_lemonade=True, **kwargs)
    # The probe's shell tool is confirmation-gated by name; no terminal here.
    agent.console.auto_approve_gated_tools = True
    return agent


def _stub_chat(agent, replies):
    """Script the model and keep every request it received."""
    queue = list(replies)
    calls = []
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _send(*_, **kwargs):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        calls.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        stats = {"prompt_tokens": 100, "completion_tokens": 10}
        return SimpleNamespace(text=queue.pop(0), stats=stats)

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return calls


def _call(number: int, name: str, **arguments) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{number}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _script(*calls):
    return [_call(n, name, **args) for n, (name, args) in enumerate(calls, 1)] + [
        _ANSWER
    ]


def _tool_results(calls, name):
    """Every result message the model saw for *name*, parsed, in order."""
    seen = []
    for msg in calls[-1]["messages"]:
        if msg.get("role") == "tool" and msg.get("name") == name:
            text = "".join(b.get("text", "") for b in msg["content"])
            try:
                seen.append(json.loads(text))
            except ValueError:
                seen.append(text)
    return seen


def _stats_entries(result):
    return [
        m["content"]
        for m in result["conversation"]
        if m.get("role") == "system"
        and isinstance(m.get("content"), dict)
        and m["content"].get("type") == "stats"
    ]


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_identical_call_in_window_is_short_circuited_with_a_fetchable_handle():
    agent = _make_agent()
    calls = _stub_chat(
        agent, _script(("run_shell_command", _GREP), ("run_shell_command", _GREP))
    )

    result = agent.process_query("go")

    assert result["result"].startswith(_ANSWER)
    assert agent.runs == [("run_shell_command", _GREP["command"])]
    seen = _tool_results(calls, "run_shell_command")
    assert len(seen) == 2
    first, second = seen[0], seen[1]
    assert second["status"] == "duplicate"
    assert second["executed"] is False
    assert second["previous_step"] == 1
    assert "read_tool_output(artifact=" in second["message"]
    page = store_for(agent).read(second["artifact"], entry=1)
    assert page["content"] == result_text(first)
    assert json.loads(page["content"]) == first


@pytest.mark.usefixtures("clean_registry")
def test_different_args_execute():
    agent = _make_agent()
    _stub_chat(
        agent,
        _script(
            ("run_shell_command", _GREP),
            ("run_shell_command", {"command": "ls"}),
            ("read_file", {"file_path": "a.py"}),
            ("read_file", {"file_path": "b.py"}),
        ),
    )

    agent.process_query("go")

    assert len(agent.runs) == 4


@pytest.mark.usefixtures("clean_registry")
def test_argument_order_does_not_make_a_call_different():
    guard = DuplicateCallGuard(store=lambda: store_for(SimpleNamespace()))
    guard.begin_turn()
    guard.begin_step(1)
    guard.record("t", {"a": 1, "b": 2}, {"status": "success"})
    assert guard.check("t", {"b": 2, "a": 1}) is not None


@pytest.mark.usefixtures("clean_registry")
def test_a_call_runs_again_after_an_edit_to_a_path_it_references():
    agent = _make_agent()
    _stub_chat(
        agent,
        _script(
            ("read_file", {"file_path": "src/a.py"}),
            ("read_file", {"file_path": "src/b.py"}),
            ("edit_file", {"file_path": "src/a.py", "old": "x", "new": "y"}),
            ("read_file", {"file_path": "src/a.py"}),  # changed: runs
            ("read_file", {"file_path": "src/b.py"}),  # untouched: short-circuit
            ("run_shell_command", {"command": "ls src"}),  # its directory: runs
        ),
    )

    agent.process_query("go")

    assert [r for r in agent.runs if r[0] == "read_file"] == [
        ("read_file", "src/a.py"),
        ("read_file", "src/b.py"),
        ("read_file", "src/a.py"),
    ]
    assert ("run_shell_command", "ls src") in agent.runs


@pytest.mark.usefixtures("clean_registry")
def test_a_shell_command_runs_again_after_any_edit():
    agent = _make_agent()
    _stub_chat(
        agent,
        _script(
            ("run_shell_command", {"command": "pytest tests/"}),
            ("edit_file", {"file_path": "src/a.py", "old": "x", "new": "y"}),
            ("run_shell_command", {"command": "pytest tests/"}),
        ),
    )

    agent.process_query("go")

    assert len([r for r in agent.runs if r[0] == "run_shell_command"]) == 2


@pytest.mark.usefixtures("clean_registry")
def test_exception_tools_always_execute():
    agent = _make_agent()
    _stub_chat(
        agent,
        _script(
            ("sleep", {"seconds": 1}),
            ("sleep", {"seconds": 1}),
            ("read_tool_output", {"artifact": "output_missing", "entry": 1}),
            ("read_tool_output", {"artifact": "output_missing", "entry": 1}),
        ),
    )

    agent.process_query("go")

    assert agent.runs.count(("sleep", 1)) == 2


@pytest.mark.usefixtures("clean_registry")
def test_a_retry_after_an_error_executes():
    agent = _make_agent()
    calls = _stub_chat(
        agent, _script(("flaky", {"path": "p"}), ("flaky", {"path": "p"}))
    )

    agent.process_query("go")

    assert agent.runs == [("flaky", "p"), ("flaky", "p")]
    assert [r["status"] for r in _tool_results(calls, "flaky")] == [
        "error",
        "success",
    ]


@pytest.mark.usefixtures("clean_registry")
def test_a_call_outside_the_window_runs_again():
    agent = _make_agent(duplicate_window=2)
    others = [("read_file", {"file_path": f"f{n}.py"}) for n in range(3)]
    _stub_chat(
        agent,
        _script(("run_shell_command", _GREP), *others, ("run_shell_command", _GREP)),
    )

    agent.process_query("go")

    assert agent.runs.count(("run_shell_command", _GREP["command"])) == 2


# ---------------------------------------------------------------------------
# Escalation and the end of the turn
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_escalation_message_and_stats_after_the_limit():
    agent = _make_agent(duplicate_limit=2)
    calls = _stub_chat(
        agent,
        _script(
            ("run_shell_command", _GREP),
            ("read_file", {"file_path": "a.py"}),
            ("run_shell_command", _GREP),  # duplicate 1
            ("read_file", {"file_path": "a.py"}),  # duplicate 2: the limit
        ),
    )

    result = agent.process_query("go")

    results = _tool_results(calls, "run_shell_command") + _tool_results(
        calls, "read_file"
    )
    duplicates = [r for r in results if r.get("status") == "duplicate"]
    assert len(duplicates) == 2
    assert "loop guard will end the turn" not in duplicates[0]["message"]
    assert (
        "You have repeated calls 2 times this turn; the loop guard will end "
        "the turn if it continues." in duplicates[1]["message"]
    )
    by_step = {s["step"]: s for s in _stats_entries(result)}
    assert by_step[3]["duplicates_short_circuited"] == 1
    assert "loop_suspected" not in by_step[3]
    assert by_step[4]["duplicates_short_circuited"] == 1
    assert by_step[4]["loop_suspected"] is True
    assert "duplicates_short_circuited" not in by_step[1]


@pytest.mark.usefixtures("clean_registry")
def test_turn_ends_through_the_loop_guard_after_twice_the_limit():
    agent = _make_agent(duplicate_limit=1)
    twin = {"command": "grep -rn gfx90a build_tools/tests"}
    calls = _stub_chat(
        agent,
        [
            _call(1, "run_shell_command", **_GREP),
            _call(2, "run_shell_command", **twin),
            _call(3, "run_shell_command", **_GREP),  # duplicate 1
            _call(4, "run_shell_command", **twin),  # duplicate 2: the end
            "I found no gfx90a references outside build_tools.",
        ],
    )

    result = agent.process_query("go")

    assert len(agent.runs) == 2
    assert result["result"].startswith("I found no gfx90a references")
    closing = calls[-1]["messages"][-1]
    assert closing["role"] == "user"
    assert "called `run_shell_command` 2 times" in closing["content"]
    assert result["steps_taken"] == 5
    assert isinstance(result["conversation"], list)


@pytest.mark.usefixtures("clean_registry")
def test_disabled_by_env_executes_everything(monkeypatch):
    monkeypatch.setenv(DUPLICATE_GUARD_ENV_VAR, "0")
    agent = _make_agent()
    _stub_chat(
        agent,
        _script(
            ("run_shell_command", _GREP),
            ("run_shell_command", _GREP),
            ("run_shell_command", _GREP),
        ),
    )

    result = agent.process_query("go")

    assert len(agent.runs) == 3
    assert all("duplicates_short_circuited" not in s for s in _stats_entries(result))


@pytest.mark.usefixtures("clean_registry")
def test_the_reasoning_policy_sees_a_non_error_observation():
    agent = _make_agent(reasoning_policy="adaptive")
    observed = []
    real = agent._reasoning_policy.observe
    agent._reasoning_policy.observe = lambda name, result: (
        observed.append((name, result)),
        real(name, result),
    )
    _stub_chat(
        agent, _script(("run_shell_command", _GREP), ("run_shell_command", _GREP))
    )

    agent.process_query("go")

    assert [name for name, _ in observed] == ["run_shell_command"] * 2
    duplicate = observed[1][1]
    assert duplicate["status"] == "duplicate"
    assert not tool_result_failed(duplicate)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_env_overrides_read_and_reject(monkeypatch):
    assert duplicate_guard_from_env() is None
    monkeypatch.setenv(DUPLICATE_GUARD_ENV_VAR, "off")
    assert duplicate_guard_from_env() is False
    monkeypatch.setenv(DUPLICATE_GUARD_ENV_VAR, "maybe")
    with pytest.raises(ValueError, match=DUPLICATE_GUARD_ENV_VAR):
        duplicate_guard_from_env()
    monkeypatch.setenv(DUPLICATE_LIMIT_ENV_VAR, "4")
    assert duplicate_limit_from_env() == 4
    monkeypatch.setenv(DUPLICATE_LIMIT_ENV_VAR, "0")
    with pytest.raises(ValueError, match=DUPLICATE_LIMIT_ENV_VAR):
        duplicate_limit_from_env()


@pytest.mark.usefixtures("clean_registry")
def test_agent_rejects_malformed_settings(monkeypatch):
    with pytest.raises(ValueError, match="duplicate_window"):
        _make_agent(duplicate_window=0)
    monkeypatch.setenv(DUPLICATE_WINDOW_ENV_VAR, "six")
    with pytest.raises(ValueError, match=DUPLICATE_WINDOW_ENV_VAR):
        _make_agent()


@pytest.mark.usefixtures("clean_registry")
def test_env_wins_over_the_argument(monkeypatch):
    monkeypatch.setenv(DUPLICATE_GUARD_ENV_VAR, "1")
    monkeypatch.setenv(DUPLICATE_LIMIT_ENV_VAR, "7")
    agent = _make_agent(duplicate_call_guard=False, duplicate_limit=2)
    assert agent._duplicate_guard.enabled is True
    assert agent._duplicate_guard.limit == 7
