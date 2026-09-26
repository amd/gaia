# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The step-adaptive reasoning policy, from the rule to the wire.

Off by default: nothing is sent. ``none`` sends ``reasoning_effort="none"`` on
every step. ``adaptive`` decides each step from the previous step's tool
results, before the call: the model's default for the first step of a turn and
after an error, a test-runner summary, an edit or a delegated subtask; off
after a plain read. The provider forwards the field only when set and only to
a cloud model, and a backend that rejects it fails with a named fix.
"""

# pylint: disable=protected-access,unused-argument

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import responses

from gaia.agents.base.agent import Agent
from gaia.agents.base.checks import CheckResult, attach_check
from gaia.agents.base.reasoning_policy import (
    REASONING_ENV_VAR,
    ReasoningPolicy,
    reasoning_policy_from_env,
    result_needs_reasoning,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LemonadeClient,
    LemonadeClientError,
)
from gaia.llm.providers.lemonade import LemonadeProvider

_CLOUD_MODEL = "fireworks.deepseek-v4p1-flash"
_ANSWER = "Done."
_MESSAGES = [{"role": "user", "content": "hi"}]
_REPLY = {
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Sunny."},
            "finish_reason": "stop",
        }
    ]
}

# Scripted results, one per probe tool, so each step's trigger is explicit.
_PLAIN = {"status": "success", "content": "def f(): pass"}
_ERROR = {"status": "error", "error": "no such file"}
_TEST_RUN = attach_check(
    {"status": "success", "stdout": "3 passed in 0.1s", "return_code": 0},
    CheckResult("pytest", "tests/", "test", True, "3 passed in 0.1s"),
)
_TEST_TEXT = {
    "status": "success",
    "stdout": "=== 2 passed in 0.5s ===",
    "return_code": 0,
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv(REASONING_ENV_VAR, raising=False)


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


class _ProbeAgent(Agent):
    """Tools named for the trigger their result exercises."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_probe(path: str) -> dict:
            """A plain read."""
            return dict(_PLAIN)

        @tool
        def failing_probe(path: str) -> dict:
            """An errored call."""
            return dict(_ERROR)

        @tool
        def check_probe(command: str) -> dict:
            """A run that reports a test check on its result."""
            return dict(_TEST_RUN)

        @tool
        def edit_probe(target: str) -> dict:
            """An edit, judged by its name like every write_*/edit_* tool."""
            return {"status": "success", "target": target}

        @tool
        def delegate_task(task: str) -> dict:
            """A worker's result."""
            return {"status": "success", "result": "worker done"}


def _make_agent(**kwargs) -> _ProbeAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ProbeAgent(silent_mode=True, skip_lemonade=True, **kwargs)


def _stub_chat(agent, replies):
    """Script the model and keep the kwargs of every call it received."""
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


_ARGS = {
    "read_probe": {"path": "f.py"},
    "failing_probe": {"path": "f.py"},
    "check_probe": {"command": "pytest"},
    "edit_probe": {"target": "f.py"},
    "delegate_task": {"task": "t"},
}


def _script(*tool_names):
    return [
        _call(n, name, **_ARGS[name]) for n, name in enumerate(tool_names, start=1)
    ] + [_ANSWER]


def _efforts(calls):
    return [c.get("reasoning_effort", "unset") for c in calls]


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


@pytest.mark.parametrize(
    "name, result, expected",
    [
        ("read_file", _PLAIN, False),
        ("search_file", {"status": "success", "matches": []}, False),
        (
            "run_shell_command",
            {"status": "success", "stdout": "a.py", "return_code": 0},
            False,
        ),
        ("read_file", _ERROR, True),
        ("run_shell_command", {"status": "denied", "error": "declined"}, True),
        ("run_shell_command", {"ran": False, "status": "error"}, True),
        ("run_shell_command", {"status": "success", "return_code": 1}, True),
        ("run_shell_command", _TEST_RUN, True),
        ("run_shell_command", _TEST_TEXT, True),
        ("run_python", _TEST_TEXT, True),
        ("edit_file", {"status": "success"}, True),
        ("write_file", {"status": "success"}, True),
        ("delegate_task", {"status": "success"}, True),
    ],
)
def test_result_needs_reasoning(name, result, expected):
    assert result_needs_reasoning(name, result) is expected


def test_policy_values_are_validated():
    with pytest.raises(ValueError, match="reasoning_policy must be one of"):
        ReasoningPolicy("sometimes")
    with pytest.raises(ValueError, match="reasoning_policy must be one of"):
        ReasoningPolicy(None)


def test_env_override_reads_and_rejects(monkeypatch):
    assert reasoning_policy_from_env() is None
    monkeypatch.setenv(REASONING_ENV_VAR, " Adaptive ")
    assert reasoning_policy_from_env() == "adaptive"
    monkeypatch.setenv(REASONING_ENV_VAR, "yes")
    with pytest.raises(ValueError, match=f"{REASONING_ENV_VAR} must be one of"):
        reasoning_policy_from_env()


def test_agent_rejects_a_malformed_policy_at_construction(monkeypatch):
    with pytest.raises(ValueError, match="reasoning_policy must be one of"):
        _make_agent(reasoning_policy="high")
    monkeypatch.setenv(REASONING_ENV_VAR, "medium")
    with pytest.raises(ValueError, match=f"{REASONING_ENV_VAR} must be one of"):
        _make_agent()


def test_env_wins_over_the_argument(monkeypatch):
    monkeypatch.setenv(REASONING_ENV_VAR, "none")
    assert _make_agent(reasoning_policy="off")._reasoning_policy.policy == "none"


# ---------------------------------------------------------------------------
# The loop: what each step's call carries, and what the record says
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_off_sends_no_field_and_records_nothing():
    agent = _make_agent()
    calls = _stub_chat(agent, _script("read_probe", "read_probe"))

    result = agent.process_query("go")

    assert result["result"].startswith(_ANSWER)
    assert _efforts(calls) == ["unset"] * 3
    assert not any("reasoning_effort" in s for s in _stats_entries(result))


@pytest.mark.usefixtures("clean_registry")
def test_none_turns_reasoning_off_on_every_step():
    agent = _make_agent(reasoning_policy="none")
    calls = _stub_chat(agent, _script("read_probe", "failing_probe", "edit_probe"))

    result = agent.process_query("go")

    assert _efforts(calls) == ["none"] * 4
    # The errored step's record is skipped by the loop's recovery path today.
    recorded = [s["reasoning_effort"] for s in _stats_entries(result)]
    assert recorded and set(recorded) == {"none"}


@pytest.mark.usefixtures("clean_registry")
def test_adaptive_follows_the_previous_step():
    agent = _make_agent(reasoning_policy="adaptive")
    calls = _stub_chat(
        agent,
        _script(
            "read_probe",  # step 1: first step, default; result plain
            "failing_probe",  # step 2: after plain -> none; result error
            "read_probe",  # step 3: after error -> default; result plain
            "check_probe",  # step 4: after plain -> none; test summary
            "read_probe",  # step 5: after test summary -> default
            "edit_probe",  # step 6: after plain -> none; edit
            "read_probe",  # step 7: after edit -> default
            "delegate_task",  # step 8: after plain -> none; delegate
            "read_probe",  # step 9: after delegate -> default
        ),
    )

    result = agent.process_query("go")

    assert result["result"].startswith(_ANSWER)
    expected = [
        "unset",  # 1 first step of the turn
        "none",  # 2 after a plain read
        "unset",  # 3 after a tool error
        "none",  # 4 after a plain read
        "unset",  # 5 after a test-runner summary
        "none",  # 6 after a plain read
        "unset",  # 7 after an edit
        "none",  # 8 after a plain read
        "unset",  # 9 after a delegate_task result
        "none",  # 10 the answer, after a plain read
    ]
    assert _efforts(calls) == expected
    # The errored step (2) writes no stats record on the loop's recovery path
    # today; every recorded step names the choice that was sent.
    recorded = {s["step"]: s["reasoning_effort"] for s in _stats_entries(result)}
    assert set(recorded) == {1, 3, 4, 5, 6, 7, 8, 9, 10}
    assert recorded == {
        step: expected[step - 1].replace("unset", "default") for step in recorded
    }


@pytest.mark.usefixtures("clean_registry")
def test_adaptive_starts_each_turn_with_reasoning_on():
    agent = _make_agent(reasoning_policy="adaptive")
    calls = _stub_chat(agent, _script("read_probe") + _script("read_probe"))

    agent.process_query("first")
    agent.process_query("second")

    assert _efforts(calls) == ["unset", "none", "unset", "none"]


# ---------------------------------------------------------------------------
# The provider and the wire
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    client = LemonadeClient(verbose=False)
    monkeypatch.setattr(client, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(client, "_model_slot_lease", lambda model: nullcontext())
    return client


def _provider(client, model) -> LemonadeProvider:
    provider = LemonadeProvider(model=model)
    provider._backend = client
    return provider


@responses.activate
def test_provider_sends_the_field_to_a_cloud_model_only_when_set(client):
    responses.post(f"{client.base_url}/chat/completions", json=_REPLY)

    _provider(client, _CLOUD_MODEL).chat(_MESSAGES, reasoning_effort="none")
    _provider(client, _CLOUD_MODEL).chat(_MESSAGES)

    assert json.loads(responses.calls[0].request.body)["reasoning_effort"] == "none"
    assert "reasoning_effort" not in json.loads(responses.calls[1].request.body)


@responses.activate
def test_provider_drops_the_field_for_a_local_model(client):
    responses.post(f"{client.base_url}/chat/completions", json=_REPLY)

    _provider(client, DEFAULT_MODEL_NAME).chat(_MESSAGES, reasoning_effort="none")

    assert "reasoning_effort" not in json.loads(responses.calls[0].request.body)


def test_provider_rejects_an_unknown_effort_before_sending(client):
    client.chat_completions = MagicMock(return_value=_REPLY)

    with pytest.raises(ValueError, match="reasoning_effort must be one of"):
        _provider(client, _CLOUD_MODEL).chat(_MESSAGES, reasoning_effort="max")
    client.chat_completions.assert_not_called()


@responses.activate
def test_backend_rejecting_the_field_is_a_named_error_not_a_retry(client):
    responses.post(
        f"{client.base_url}/chat/completions",
        status=400,
        json={"error": {"message": "Unknown parameter: reasoning_effort"}},
    )

    with pytest.raises(LemonadeClientError) as info:
        _provider(client, _CLOUD_MODEL).chat(_MESSAGES, reasoning_effort="none")

    assert len(responses.calls) == 1
    text = str(info.value)
    assert "rejected reasoning_effort='none'" in text
    assert "GAIA_REASONING=off" in text
    assert "Unknown parameter" not in text


@responses.activate
def test_an_unrelated_cloud_400_keeps_its_generic_message(client):
    responses.post(
        f"{client.base_url}/chat/completions",
        status=400,
        json={"error": {"message": "messages must not be empty"}},
    )

    with pytest.raises(LemonadeClientError) as info:
        _provider(client, _CLOUD_MODEL).chat(_MESSAGES, reasoning_effort="none")
    assert "reasoning_effort" not in str(info.value)


# ---------------------------------------------------------------------------
# Reasoning history: re-sent within a request, or only logged
# ---------------------------------------------------------------------------


def _stub_reasoning_chat(agent, replies, reasoning="because"):
    """Like ``_stub_chat``, with reasoning on every reply."""
    queue = list(replies)
    calls = []
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _send(*_, **kwargs):
        calls.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        return SimpleNamespace(
            text=queue.pop(0),
            stats={"prompt_tokens": 100, "completion_tokens": 10},
            reasoning=reasoning,
            finish_reason="stop",
        )

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return calls


def _assistant_sent(calls):
    return [m for m in calls[-1]["messages"] if m.get("role") == "assistant"]


def _assistant_logged(result):
    return [m for m in result["conversation"] if m.get("role") == "assistant"]


@pytest.mark.usefixtures("clean_registry")
def test_send_keeps_reasoning_on_the_resent_assistant_messages():
    agent = _make_agent()
    assert agent.reasoning_history == "send"
    calls = _stub_reasoning_chat(agent, _script("read_probe", "read_probe"))

    result = agent.process_query("go")

    sent = _assistant_sent(calls)
    assert len(sent) == 2
    assert all(m["reasoning_content"] == "because" for m in sent)
    assert all("tool_calls" in m for m in sent)
    assert [m.get("reasoning") for m in _assistant_logged(result)] == ["because"] * 3
    assert _stats_entries(result)[0]["reasoning_history"] == "send"
    assert "reasoning_history" not in _stats_entries(result)[1]


@pytest.mark.usefixtures("clean_registry")
def test_drop_sends_no_reasoning_but_logs_every_step():
    agent = _make_agent(reasoning_history="drop")
    calls = _stub_reasoning_chat(agent, _script("read_probe", "read_probe"))

    result = agent.process_query("go")

    sent = _assistant_sent(calls)
    assert len(sent) == 2
    assert not any("reasoning_content" in m for m in sent)
    assert all("tool_calls" in m for m in sent)
    assert [m.get("reasoning") for m in _assistant_logged(result)] == ["because"] * 3
    assert _stats_entries(result)[0]["reasoning_history"] == "drop"


def test_reasoning_history_env_wins_and_malformed_fails(monkeypatch):
    monkeypatch.setenv("GAIA_REASONING_HISTORY", "drop")
    assert _make_agent(reasoning_history="send").reasoning_history == "drop"
    monkeypatch.setenv("GAIA_REASONING_HISTORY", "never")
    with pytest.raises(ValueError, match="GAIA_REASONING_HISTORY must be one of"):
        _make_agent()
    monkeypatch.delenv("GAIA_REASONING_HISTORY")
    with pytest.raises(ValueError, match="reasoning_history must be one of"):
        _make_agent(reasoning_history="off")
