# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for TalkConfig mic_threshold and AudioClient configuration threading."""

from unittest.mock import MagicMock, patch

from gaia.audio.audio_client import AudioClient
from gaia.talk.sdk import TalkConfig, TalkSDK


def test_talk_config_mic_threshold_default():
    """TalkConfig default mic_threshold is 0.003."""
    config = TalkConfig()
    assert config.mic_threshold == 0.003


def test_talk_config_mic_threshold_custom():
    """TalkConfig accepts a custom mic_threshold."""
    config = TalkConfig(mic_threshold=0.01)
    assert config.mic_threshold == 0.01


def test_talk_sdk_passes_mic_threshold_to_audio_client():
    """TalkSDK passes mic_threshold from TalkConfig through to AudioClient."""
    with (
        patch("gaia.talk.sdk.AudioClient") as MockAudioClient,
        patch("gaia.talk.sdk.AgentSDK"),
    ):
        MockAudioClient.return_value = MagicMock()
        config = TalkConfig(mic_threshold=0.007, enable_tts=False)
        TalkSDK(config)
        call_kwargs = MockAudioClient.call_args[1]
        assert call_kwargs["mic_threshold"] == 0.007


def test_audio_client_stores_mic_threshold():
    """AudioClient stores a custom mic_threshold attribute."""
    with patch("gaia.audio.audio_client.create_client"):
        client = AudioClient(mic_threshold=0.005)
        assert client.mic_threshold == 0.005


def test_audio_client_default_mic_threshold():
    """AudioClient default mic_threshold is 0.003."""
    with patch("gaia.audio.audio_client.create_client"):
        client = AudioClient()
        assert client.mic_threshold == 0.003


def test_talk_sdk_threads_backend_selection_to_both_clients():
    """model, claude_model and base_url reach AgentSDK and AudioClient."""
    with (
        patch("gaia.talk.sdk.AudioClient") as MockAudioClient,
        patch("gaia.talk.sdk.AgentSDK") as MockAgentSDK,
    ):
        TalkSDK(
            TalkConfig(
                model="local-m",
                max_tokens=9,
                use_claude=True,
                claude_model="claude-x",
                base_url="http://h:1/api/v1",
                show_stats=True,
                enable_tts=False,
            )
        )
        chat_config = MockAgentSDK.call_args[0][0]
        assert chat_config.model == "local-m"
        assert chat_config.max_tokens == 9
        assert chat_config.use_claude is True
        assert chat_config.claude_model == "claude-x"
        assert chat_config.base_url == "http://h:1/api/v1"
        assert chat_config.show_stats is True

        audio_kwargs = MockAudioClient.call_args[1]
        assert audio_kwargs["model"] == "local-m"
        assert audio_kwargs["claude_model"] == "claude-x"
        assert audio_kwargs["base_url"] == "http://h:1/api/v1"


def test_audio_client_passes_model_and_base_url_to_provider():
    with patch("gaia.audio.audio_client.create_client") as mock_create:
        AudioClient(model="local-m", base_url="http://h:1/api/v1")
        kwargs = mock_create.call_args[1]
        assert kwargs["model"] == "local-m"
        assert kwargs["base_url"] == "http://h:1/api/v1"


def test_audio_client_uses_claude_model_with_claude():
    with patch("gaia.audio.audio_client.create_client") as mock_create:
        AudioClient(use_claude=True, model="local-m", claude_model="claude-x")
        assert mock_create.call_args[1]["model"] == "claude-x"


def test_agent_sdk_passes_model_and_base_url_to_provider():
    """The conversation client is built with the model and URL talk hands it."""
    from gaia.chat.sdk import AgentConfig, AgentSDK

    with patch("gaia.chat.sdk.create_client") as mock_create:
        AgentSDK(AgentConfig(model="local-m", base_url="http://h:1/api/v1"))
        kwargs = mock_create.call_args[1]
        assert kwargs["model"] == "local-m"
        assert kwargs["base_url"] == "http://h:1/api/v1"
