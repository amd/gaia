# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Retired providers fail before imports, model loading, or remote requests."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.llm.factory import REMOVED_PROVIDER_MESSAGE, create_client


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "openai"},
        {"provider": "LITELLM"},
        {"provider": "openai", "use_claude": True},
    ],
)
def test_retired_selection_fails_before_provider_import(kwargs):
    with patch("importlib.import_module") as load:
        with pytest.raises(ValueError) as error:
            create_client(**kwargs)
    assert str(error.value) == REMOVED_PROVIDER_MESSAGE
    load.assert_not_called()


def test_factory_no_longer_accepts_the_boolean_flag():
    with pytest.raises(TypeError, match="use_openai"):
        create_client(use_openai=True)


def test_agent_sdk_config_no_longer_has_the_flag():
    from gaia.chat.sdk import AgentConfig

    with pytest.raises(TypeError, match="use_chatgpt"):
        AgentConfig(use_chatgpt=True)


def test_base_agent_rejects_the_flag_before_initializing_lemonade():
    from gaia.agents.base.agent import Agent

    class TestAgent(Agent):
        def _get_system_prompt(self):
            return "test"

        def _register_tools(self):
            pass

    with patch("gaia.llm.lemonade_client.LemonadeClient") as client:
        with pytest.raises(TypeError, match="use_chatgpt"):
            TestAgent(use_chatgpt=True)
    client.assert_not_called()


def test_create_session_rejects_the_flag_instead_of_running_locally():
    from gaia.chat.sdk import AgentSession

    with pytest.raises(TypeError, match="use_chatgpt"):
        AgentSession().create_session("s", use_chatgpt=True)


def test_create_session_rejects_overrides_it_would_ignore():
    from gaia.chat.sdk import AgentConfig, AgentSession

    with pytest.raises(TypeError, match="not both"):
        AgentSession().create_session("s", config=AgentConfig(), model="m")


def test_cli_initialization_no_longer_accepts_the_flag():
    from gaia.cli import initialize_lemonade_for_agent

    with pytest.raises(TypeError, match="use_chatgpt"):
        initialize_lemonade_for_agent("chat", use_chatgpt=True)


def test_real_cli_returns_actionable_migration_error():
    root = Path(__file__).resolve().parents[2]
    # Inherit the root conftest's PYTHONPATH (src plus hub agent roots).
    result = subprocess.run(
        [sys.executable, "-m", "gaia.cli", "llm", "--use-chatgpt", "hello"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "--use-chatgpt was removed" in result.stderr
    assert "discarded tool calls" in result.stderr
    assert "gateway-migration" in result.stderr


def test_supported_provider_forwards_endpoint_and_model():
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        create_client(
            "lemonade", base_url="http://127.0.0.1:1234/api/v1", model="amd.model"
        )
    backend.assert_called_once_with(
        base_url="http://127.0.0.1:1234/api/v1", model="amd.model"
    )


@pytest.mark.parametrize("surface", ["audio", "talk"])
def test_voice_sdk_no_longer_accepts_the_flag(surface):
    with pytest.raises(TypeError, match="use_chatgpt"):
        if surface == "audio":
            from gaia.audio.audio_client import AudioClient

            AudioClient(use_chatgpt=True)
        else:
            from gaia.talk.sdk import TalkConfig

            TalkConfig(use_chatgpt=True)
