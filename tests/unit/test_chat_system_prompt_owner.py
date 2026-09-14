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
