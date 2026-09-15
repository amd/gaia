# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Inspect requests after the real SDK and provider assemble their prompts."""

import json
from unittest.mock import patch

import pytest

from gaia.chat.sdk import AgentConfig, AgentSDK


@pytest.fixture
def sdk():
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_type:
        backend = backend_type.return_value

        def completion(**kwargs):
            if kwargs["stream"]:
                return iter([{"choices": [{"delta": {"content": "answer"}}]}])
            return {"choices": [{"message": {"content": "answer"}}]}

        backend.chat_completions.side_effect = completion
        yield AgentSDK(
            AgentConfig(system_prompt="SYS-ORIGINAL", show_stats=False)
        ), backend


def _send(chat, mode, **kwargs):
    if mode == "send":
        return chat.send("hello", **kwargs)
    if mode == "stream":
        return list(chat.send_stream("hello", **kwargs))
    if mode == "messages":
        return chat.send_messages([{"role": "user", "content": "hello"}], **kwargs)
    return list(
        chat.send_messages_stream([{"role": "user", "content": "hello"}], **kwargs)
    )


@pytest.mark.parametrize("mode", ["send", "stream", "messages", "messages_stream"])
@pytest.mark.parametrize("update", ["set", "config"])
def test_system_prompt_occurs_once_and_updates_without_old_copy(sdk, mode, update):
    chat, backend = sdk
    _send(chat, mode)
    request = json.dumps(backend.chat_completions.call_args.kwargs["messages"])
    assert request.count("SYS-ORIGINAL") == 1
    assert backend.chat_completions.call_args.kwargs["messages"][0] == {
        "role": "system",
        "content": "SYS-ORIGINAL",
    }
    if update == "set":
        chat.set_system_prompt("SYS-UPDATED")
    else:
        chat.update_config(system_prompt="SYS-UPDATED")
    _send(chat, mode)
    request = json.dumps(backend.chat_completions.call_args.kwargs["messages"])
    assert request.count("SYS-UPDATED") == 1
    assert "SYS-ORIGINAL" not in request


@pytest.mark.parametrize("mode", ["messages", "messages_stream"])
def test_per_request_override_does_not_mutate_default(sdk, mode):
    chat, backend = sdk
    _send(chat, mode, system_prompt="SYS-OVERRIDE")
    request = json.dumps(backend.chat_completions.call_args.kwargs["messages"])
    assert request.count("SYS-OVERRIDE") == 1
    assert "SYS-ORIGINAL" not in request
    _send(chat, mode)
    request = json.dumps(backend.chat_completions.call_args.kwargs["messages"])
    assert request.count("SYS-ORIGINAL") == 1
    assert "SYS-OVERRIDE" not in request


@pytest.mark.parametrize("stream", [False, True])
def test_custom_prompt_and_rag_use_structured_history(sdk, stream):
    chat, backend = sdk
    chat.chat_history.extend(["user: earlier", "assistant: previous answer"])
    chat.rag_enabled = True
    with patch.object(
        chat, "_enhance_with_rag", return_value=("retrieved context", {})
    ):
        _send(chat, "stream" if stream else "send")
    messages = backend.chat_completions.call_args.kwargs["messages"]
    assert messages == [
        {"role": "system", "content": "SYS-ORIGINAL"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "retrieved context"},
    ]
    assert chat.get_history()[-2] == "user: hello"


def test_custom_prompt_no_history_omits_previous_turns(sdk):
    chat, backend = sdk
    chat.chat_history.extend(["user: earlier", "assistant: previous answer"])
    _send(chat, "send", no_history=True)
    assert backend.chat_completions.call_args.kwargs["messages"] == [
        {"role": "system", "content": "SYS-ORIGINAL"},
        {"role": "user", "content": "hello"},
    ]
    assert chat.get_history() == ["user: earlier", "assistant: previous answer"]


@pytest.mark.parametrize("provider_name", ["openai", "claude"])
def test_cloud_provider_receives_native_system_prompt(provider_name):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gaia.llm.providers.claude import ClaudeProvider
    from gaia.llm.providers.openai_provider import OpenAIProvider

    provider_type = ClaudeProvider if provider_name == "claude" else OpenAIProvider
    provider = provider_type.__new__(provider_type)
    provider._model = "claude-test" if provider_name == "claude" else "gpt-test"
    provider._system_prompt = None
    provider._client = Mock()
    provider._tool_name_map = {}
    if provider_name == "claude":
        provider._parse_response = Mock(return_value="answer")
        create = provider._client.messages.create
    else:
        create = provider._client.chat.completions.create
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
        )
    with patch("gaia.chat.sdk.create_client", return_value=provider):
        chat = AgentSDK(AgentConfig(system_prompt="SYS-ORIGINAL", show_stats=False))
    chat.send("hello")
    params = create.call_args.kwargs
    native = params["system"] if provider_name == "claude" else params["messages"][0]
    assert "SYS-ORIGINAL" in json.dumps(native)
    assert json.dumps(params).count("SYS-ORIGINAL") == 1
    chat.set_system_prompt("SYS-UPDATED")
    chat.send("hello")
    params = create.call_args.kwargs
    assert "SYS-ORIGINAL" not in json.dumps(params)
    assert json.dumps(params).count("SYS-UPDATED") == 1


def test_assistant_display_name_change_keeps_valid_native_roles(sdk):
    chat, backend = sdk
    chat.send("first")
    chat.update_config(assistant_name="new-name")
    chat.send("second")
    messages = backend.chat_completions.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[2]["content"] == "answer"
