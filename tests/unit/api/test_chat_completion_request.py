# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a ``POST /v1/chat/completions`` request actually delivers to the LLM (#4202).

Each test drives a real ``Agent`` and stubs only the provider's ``chat`` call,
then asserts on the messages and kwargs that call received — the request the
backend would really get — rather than on what the server handed a mock.
"""

import json

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.agent import Agent
from gaia.api import openai_server
from gaia.api.openai_server import app
from gaia.api.sse_handler import SSEOutputHandler


class _ProbeAgent(Agent):
    def _register_tools(self):
        pass


@pytest.fixture
def llm_calls(monkeypatch, mocker):
    """Serve a real agent whose provider records every chat call."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    calls = []

    def build_agent(_model_id):
        agent = _ProbeAgent(
            skip_lemonade=True, silent_mode=True, output_handler=SSEOutputHandler()
        )

        def fake_chat(messages, model=None, stream=False, **kwargs):
            calls.append({"messages": messages, "kwargs": kwargs})
            return "probe answer"

        agent.chat.llm_client.chat = fake_chat
        return agent

    mocker.patch.object(openai_server.registry, "get_agent", side_effect=build_agent)
    return calls


@pytest.fixture
def client():
    return TestClient(app)


def _post(client, messages, **extra):
    return client.post(
        "/v1/chat/completions",
        json={"model": "gaia", "messages": messages, "stream": False, **extra},
    )


def _roles_and_text(call):
    return [(m["role"], m["content"]) for m in call["messages"]]


def test_prior_turns_reach_the_llm(client, llm_calls):
    response = _post(
        client,
        [
            {"role": "user", "content": "My name is Ada."},
            {"role": "assistant", "content": "Nice to meet you, Ada."},
            {"role": "user", "content": "What is my name?"},
        ],
    )

    assert response.status_code == 200, response.text
    sent = _roles_and_text(llm_calls[0])[1:]  # [0] is the agent's system prompt
    assert sent == [
        ("user", "My name is Ada."),
        ("assistant", "Nice to meet you, Ada."),
        ("user", "What is my name?"),
    ]


def test_streaming_request_also_carries_prior_turns(client, llm_calls):
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "gaia",
            "stream": True,
            "messages": [
                {"role": "user", "content": "My name is Ada."},
                {"role": "assistant", "content": "Nice to meet you, Ada."},
                {"role": "user", "content": "What is my name?"},
            ],
        },
    ) as response:
        assert response.status_code == 200
        "".join(response.iter_text())

    sent = _roles_and_text(llm_calls[0])[1:]
    assert sent[0] == ("user", "My name is Ada.")
    assert sent[-1] == ("user", "What is my name?")


def test_system_and_developer_messages_reach_the_system_prompt(client, llm_calls):
    response = _post(
        client,
        [
            {"role": "system", "content": "Always answer in French."},
            {"role": "developer", "content": "Keep answers under ten words."},
            {"role": "user", "content": "hello"},
        ],
    )

    assert response.status_code == 200, response.text
    system = llm_calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "Always answer in French." in system["content"]
    assert "Keep answers under ten words." in system["content"]
    # Only the agent's one system message is sent; the caller's are folded in.
    assert [m["role"] for m in llm_calls[0]["messages"]].count("system") == 1


def test_content_part_arrays_are_flattened_to_text(client, llm_calls):
    response = _post(
        client,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first part"},
                    {"type": "text", "text": "second part"},
                ],
            }
        ],
    )

    assert response.status_code == 200, response.text
    assert llm_calls[0]["messages"][-1] == {
        "role": "user",
        "content": "first part\nsecond part",
    }


def test_non_text_content_part_is_rejected_by_name(client, llm_calls):
    response = _post(
        client,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": "data:,"}},
                ],
            }
        ],
    )

    assert response.status_code == 400
    assert "image_url" in response.json()["detail"]
    assert llm_calls == []


def test_last_message_must_be_from_the_user(client, llm_calls):
    response = _post(
        client,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    )

    assert response.status_code == 400
    assert "last message" in response.json()["detail"].lower()
    assert llm_calls == []


def test_sampling_parameters_reach_the_llm_call(client, llm_calls):
    response = _post(
        client,
        [{"role": "user", "content": "hello"}],
        temperature=0.2,
        top_p=0.9,
        max_tokens=256,
    )

    assert response.status_code == 200, response.text
    kwargs = llm_calls[0]["kwargs"]
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
    assert kwargs["max_tokens"] == 256


def test_unset_sampling_parameters_keep_the_agent_defaults(client, llm_calls):
    response = _post(client, [{"role": "user", "content": "hello"}])

    assert response.status_code == 200, response.text
    kwargs = llm_calls[0]["kwargs"]
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert kwargs["max_tokens"] == 8192


def test_caller_supplied_history_is_marked_as_external(client, llm_calls, mocker):
    spy = mocker.spy(_ProbeAgent, "mark_external_content")

    _post(
        client,
        [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "now"},
        ],
    )

    assert spy.call_count == 1


def test_single_user_message_is_not_marked_external(client, llm_calls, mocker):
    spy = mocker.spy(_ProbeAgent, "mark_external_content")

    _post(client, [{"role": "user", "content": "now"}])

    assert spy.call_count == 0


def test_debug_logging_handles_content_part_arrays(
    client, llm_calls, monkeypatch, caplog
):
    monkeypatch.setenv("GAIA_API_DEBUG", "1")
    caplog.set_level("DEBUG", logger="gaia.api.openai_server")

    response = _post(
        client,
        [{"role": "user", "content": [{"type": "text", "text": "secret words"}]}],
    )

    assert response.status_code == 200, response.text
    assert "secret words" not in caplog.text
    assert json.dumps("secret words") not in caplog.text
