# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A non-object tool call is corrected by the model instead of crashing the turn."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


class _StatusAgent(Agent):
    def _get_system_prompt(self):
        return "Check the status with check_status, then answer."

    def _register_tools(self):
        self.calls = []

        @tool
        def check_status(label: str = "default") -> dict:
            """Check the status for a label."""
            self.calls.append(label)
            return {"status": "success", "state": "ok", "label": label}


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request, monkeypatch):
    monkeypatch.setenv("GAIA_CTX_SIZE", "32768")
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _StatusAgent(
            silent_mode=True, skip_lemonade=True, model_id="probe-model"
        )
    built.streaming = request.param
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _native(*arguments):
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": "check_status",
                        "arguments": json.dumps(args),
                    },
                }
                for i, args in enumerate(arguments)
            ]
        }
    )


def _json(arguments):
    return json.dumps({"tool": "check_status", "tool_args": arguments})


def _script(agent, *replies):
    pending = iter(replies)
    requests = []

    def next_reply(messages):
        requests.append(copy.deepcopy(messages))
        return next(pending)

    def send(messages, *_, **__):
        return SimpleNamespace(text=next_reply(messages), stats={})

    def stream(messages, *_, **__):
        text = next_reply(messages)
        yield SimpleNamespace(text=text, is_complete=True, stats={})

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    agent.chat.send_messages_stream.side_effect = stream
    agent.chat.get_stats.return_value = {}
    return requests


_ANSWER = json.dumps({"answer": "The status is ok."})
_BAD_ARGS = ["oops", ["a"], [], None, False, 0, 42]


@pytest.mark.parametrize("arguments", _BAD_ARGS)
@pytest.mark.parametrize(
    "encode",
    [_json, _native, lambda args: "Checking now.\n" + _json(args)],
    ids=["json", "native", "embedded-json"],
)
def test_non_object_arguments_are_reported_to_the_model_and_retried(
    agent, arguments, encode
):
    requests = _script(agent, encode(arguments), _json({"label": "corrected"}), _ANSWER)

    result = agent.process_query("Check the status.")

    assert result["status"] == "success"
    assert "The status is ok." in result["result"]
    assert agent.calls == ["corrected"]
    retry = requests[1][-1]["content"]
    assert "check_status" in retry
    assert "expected a JSON object" in retry
    assert type(arguments).__name__ in retry
    assert len(requests) == 3
    assert any(
        entry["type"] == "tool_call_parse_error" and entry.get("recovered")
        for entry in result["error_history"]
    )


@pytest.mark.parametrize(
    "call",
    [_json({}), _native({}), json.dumps({"tool": "check_status"})],
    ids=["json-empty", "native-empty", "json-missing"],
)
def test_a_no_argument_call_still_runs(agent, call):
    requests = _script(agent, call, _ANSWER)

    result = agent.process_query("Check the status.")

    assert result["status"] == "success"
    assert agent.calls == ["default"]
    assert len(requests) == 2


@pytest.mark.parametrize("name", [None, ""])
@pytest.mark.parametrize("arguments", [None, "oops", []])
def test_an_answer_without_a_tool_name_is_not_rejected(agent, name, arguments):
    requests = _script(
        agent,
        json.dumps({"tool": name, "tool_args": arguments, "answer": "Hello there!"}),
    )

    result = agent.process_query("Hello.")

    assert result["status"] == "success"
    assert "Hello there!" in result["result"]
    assert agent.calls == []
    assert len(requests) == 1


def test_a_bad_parallel_call_does_not_run_the_earlier_valid_call(agent):
    _script(agent, _native({"label": "must not run"}, "oops"), _ANSWER)

    result = agent.process_query("Check the status.")

    assert result["status"] == "success"
    assert agent.calls == []


@pytest.mark.parametrize("encode", [_json, _native], ids=["json", "native"])
def test_repeated_non_object_arguments_exhaust_the_existing_retry_budget(agent, encode):
    requests = _script(agent, *([encode("oops")] * 3))

    result = agent.process_query("Check the status.")

    assert result["status"] == "failed"
    assert "trouble formatting my tool call" in result["result"]
    assert agent.calls == []
    assert len(requests) == 3


@pytest.mark.parametrize("encode", [_json, _native], ids=["json", "native"])
def test_a_non_object_closing_call_retries_without_tools(agent, encode):
    requests = _script(agent, _json({}), encode("oops"), _ANSWER)

    result = agent.process_query("Check the status.", max_steps=1)

    assert "The status is ok." in result["result"]
    assert agent.calls == ["default"]
    assert len(requests) == 3
