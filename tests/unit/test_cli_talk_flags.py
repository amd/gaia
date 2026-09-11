# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every `gaia talk` model/backend flag must reach the client that uses it (#124).

These drive the real CLI parser and entry point and build a real TalkSDK; only
the two clients that consume the flags (AgentSDK for the conversation,
AudioClient for voice) are replaced, so an unwired flag fails its own case.
"""

import sys
from unittest.mock import AsyncMock, patch

import pytest

from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.talk.sdk import TalkSDK

# asyncio uses a local socketpair for its Windows event-loop wakeup.
pytestmark = pytest.mark.allow_network

BASE_URL = "http://10.0.0.5:13305/api/v1"


def _run_talk(argv, monkeypatch):
    """Run `gaia talk <argv>`; return (AgentSDK mock, AudioClient mock)."""
    monkeypatch.setattr(sys, "argv", ["gaia", "talk", "--no-lemonade-check", *argv])
    with (
        patch("gaia.talk.sdk.AgentSDK") as agent_sdk,
        patch("gaia.talk.sdk.AudioClient") as audio_client,
        patch.object(TalkSDK, "start_voice_session", AsyncMock()),
    ):
        from gaia.cli import main

        main()
    return agent_sdk, audio_client


FLAG_CASES = [
    pytest.param(
        ["--model", "Qwen3-Coder-30B-A3B-Instruct-GGUF"],
        {"model": "Qwen3-Coder-30B-A3B-Instruct-GGUF"},
        {"model": "Qwen3-Coder-30B-A3B-Instruct-GGUF"},
        id="--model",
    ),
    pytest.param(["--max-tokens", "9"], {"max_tokens": 9}, {}, id="--max-tokens"),
    pytest.param(
        ["--use-claude"], {"use_claude": True}, {"use_claude": True}, id="--use-claude"
    ),
    pytest.param(
        ["--use-chatgpt", "--model", "gpt-4o"],
        {"use_chatgpt": True, "model": "gpt-4o"},
        {"use_chatgpt": True, "model": "gpt-4o"},
        id="--use-chatgpt",
    ),
    pytest.param(
        ["--claude-model", "claude-x"],
        {"claude_model": "claude-x"},
        {"claude_model": "claude-x"},
        id="--claude-model",
    ),
    pytest.param(
        ["--base-url", BASE_URL],
        {"base_url": BASE_URL},
        {"base_url": BASE_URL},
        id="--base-url",
    ),
    pytest.param(["--stats"], {"show_stats": True}, {}, id="--stats"),
    pytest.param(["--show-stats"], {"show_stats": True}, {}, id="--show-stats"),
]


@pytest.mark.parametrize("argv, chat_expected, audio_expected", FLAG_CASES)
def test_each_talk_flag_reaches_the_client_that_uses_it(
    argv, chat_expected, audio_expected, monkeypatch
):
    agent_sdk, audio_client = _run_talk(argv, monkeypatch)

    chat_config = agent_sdk.call_args[0][0]
    for field, value in chat_expected.items():
        assert getattr(chat_config, field) == value, f"AgentConfig.{field}"
    audio_kwargs = audio_client.call_args[1]
    for field, value in audio_expected.items():
        assert audio_kwargs[field] == value, f"AudioClient({field}=)"


def test_defaults_without_backend_flags(monkeypatch):
    agent_sdk, audio_client = _run_talk([], monkeypatch)

    chat_config = agent_sdk.call_args[0][0]
    assert chat_config.model == DEFAULT_MODEL_NAME
    assert chat_config.max_tokens == 512
    assert chat_config.show_stats is False
    assert chat_config.use_claude is False and chat_config.use_chatgpt is False
    assert chat_config.base_url is None
    assert audio_client.call_args[1]["model"] == DEFAULT_MODEL_NAME


def test_use_chatgpt_without_model_is_refused(monkeypatch, capsys):
    """The local default model id would only produce a confusing OpenAI 404."""
    with pytest.raises(SystemExit) as exc:
        _run_talk(["--use-chatgpt"], monkeypatch)
    assert exc.value.code != 0
    assert "--use-chatgpt needs --model" in capsys.readouterr().out
